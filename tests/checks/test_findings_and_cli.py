from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agentlint import cli
from agentlint.checks import Check, CheckResults, NotEvaluated, UnknownCheck, run_checks, select
from agentlint.checks import token_budget
from agentlint.config.assemble import assemble
from agentlint.config.model import Declared
from agentlint.config.taxonomy import Authority, Load
from agentlint.report.finding import Confidence, Figure, Finding, FindingKind, Severity
from agentlint.report.terminal import render

from ..fakes import NoTranscripts, Readers
from ..config.helpers import HARNESS, PROJECT, PROMPT, delivery, file_source, observable_source, ref, session


def finding(**overrides) -> Finding:
    base = dict(check="demo-check", kind=FindingKind.COLLISION, severity=Severity.WARN,
                confidence=Confidence.CERTAIN, title="Two things collide", detail="detail",
                discriminator="name:x", sources=("a", "b"))
    return Finding(**{**base, **overrides})


def test_finding_identity_is_check_plus_discriminator() -> None:
    a = finding()
    assert a.id == finding(sources=("a",), title="other words", severity=Severity.INFO).id  # membership/wording don't matter
    assert a.id != finding(discriminator="name:y").id and a.id != finding(check="other-check").id
    assert len(a.id) == 16


def test_finding_invariants() -> None:
    with pytest.raises(ValueError):
        finding(check="Not Kebab")
    with pytest.raises(ValueError):
        finding(discriminator=" ")
    with pytest.raises(ValueError):
        finding(confidence=Confidence.MEASURED)  # measured is for behavioural findings only
    assert finding(kind=FindingKind.BEHAVIOUR, confidence=Confidence.MEASURED).kind is FindingKind.BEHAVIOUR


def declared() -> Declared:
    sources = (file_source("notes", "## A\nsome words here\n"), observable_source("start-output"),
               observable_source("prompt", point=PROMPT, authority=Authority.HARNESS),
               observable_source("nudge", load=Load.PER_MESSAGE))
    return Declared(HARNESS, PROJECT, "2.0", "model-x", sources, ())


def snapshot(total: int | None):
    s = session(ref(1), delivery("start-output", "hello"), delivery("prompt", "You are an agent."),
                delivery("nudge", "remember", load=Load.PER_MESSAGE), total=total)
    return assemble(declared(), [s])


def test_token_budget_states_the_total_honestly() -> None:
    reconciled = token_budget.run(snapshot(10_000))[0]
    assert reconciled.title == "Always-on context: 10,000 tokens every session (observed)"
    assert {f.label for f in reconciled.figures} == {"observed total", "attributed", "not attributed"}
    assert "hook output, every message" in reconciled.detail and "hook output, once per session" in reconciled.detail
    unobserved = token_budget.run(snapshot(None))[0]
    assert unobserved.title.startswith("Always-on context: up to ≈") and unobserved.title.endswith("(estimated)")
    assert "No observed total" in unobserved.detail and "only sometimes" in unobserved.detail  # an upper estimate, said so
    assert "exceed" in token_budget.run(snapshot(1))[0].detail
    assert reconciled.id == unobserved.id  # the numbers changing is not a different finding


def test_token_budget_groups_the_outcomes_own_attribution() -> None:
    """The breakdown and the attributed figure count what the anchor session delivered, not each source's latest
    observation: pairing one with the other's remainder is the mixing reconciliation exists to avoid."""
    anchor = session(ref(1), delivery("start-output", "short"), delivery("prompt", "You are an agent."), total=10_000)
    later = session(ref(2), delivery("start-output", "a much longer text " * 200))  # no total
    snap = assemble(declared(), [anchor, later])
    found = token_budget.run(snap)[0]
    figures = {f.label: f.value for f in found.figures}
    assert figures["attributed"] == snap.always_on.attributed
    assert figures["attributed"] + figures["not attributed"] == figures["observed total"]
    lines = [line for line in found.detail.splitlines() if line.startswith("≈ ")]  # "≈  1,234  group"
    breakdown = sum(int(line[1:].split()[0].replace(",", "")) for line in lines)
    assert breakdown == snap.always_on.attributed
    assert snap.source("start-output").tokens.value > sum(a.tokens for a in snap.always_on.attribution
                                                           if a.source_id == "start-output")  # latest ≠ anchor


def test_registry_rejects_mislabelled_and_duplicate_findings() -> None:
    from agentlint.config.reading import Reading

    snap = Reading(snapshot(None))
    with pytest.raises(AssertionError):
        run_checks(snap, [Check("demo-check", "x", lambda s: [finding(check="other-check")])])
    with pytest.raises(AssertionError):
        run_checks(snap, [Check("demo-check", "x", lambda s: [finding(), finding(title="again")])])
    ordered = run_checks(snap, [Check("demo-check", "x", lambda s: [finding(severity=Severity.INFO, discriminator="i"),
                                                                    finding(severity=Severity.ERROR, discriminator="e")])])
    assert [f.severity for f in ordered.findings] == [Severity.ERROR, Severity.INFO]

    def lacking(reading):
        raise NotEvaluated("no recent sessions")
    mixed = run_checks(snap, [Check("demo-check", "x", lambda s: [finding()]), Check("shared", "a", lambda s: [finding(check="shared")]),
                              Check("shared", "b", lacking)])
    assert len(mixed.findings) == 2 and mixed.evaluated == ("demo-check",)  # a partly evaluated id is not evaluated
    assert mixed.not_evaluated == {"shared": "no recent sessions"}
    with pytest.raises(UnknownCheck):
        select([token_budget.CHECK], ["missing"])
    core, adapter_part = Check("shared", "core part", lambda s: []), Check("shared", "adapter part", lambda s: [])
    assert select([token_budget.CHECK, core, adapter_part], ["shared"]) == [core, adapter_part]  # one id, all contributors


def test_terminal_render_groups_by_severity() -> None:
    snap = snapshot(None)
    info = finding(severity=Severity.INFO, discriminator="i", confidence=Confidence.SUGGESTIVE, kind=FindingKind.EMPHASIS,
                   detail="hidden unless asked")
    results = CheckResults([finding(severity=Severity.ERROR, discriminator="e", suggested_action="Fix it"), info], ("demo-check",),
                   {"hook-health": "no recent sessions to read"})
    text = render(snap, results)
    assert "2 findings (1 error, 1 info)" in text
    assert text.index("ERROR") < text.index("INFO") and "→ Fix it" in text and "[suggestive]" in text
    assert "hidden unless asked" not in text and f"· {info.id[:8]}" in text  # info is one line, with a short id
    assert "hidden unless asked" in render(snap, results, detail=True)
    assert "NOT EVALUATED" in text and "no recent sessions to read" in text


class FakeAdapter(Readers, NoTranscripts):
    name = HARNESS

    def read_declared(self, cwd: Path) -> Declared:
        return declared()

    def read_sessions(self, cwd: Path, limit: int):
        return [session(ref(1), delivery("start-output", "hi"), total=5_000)]

    def checks(self) -> list[Check]:
        return [Check("demo-check", "a broken thing", lambda s: [finding(severity=Severity.ERROR)])]



def test_cli_check(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    args = ["check", "--cwd", str(PROJECT)]
    assert cli.main(args) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "token-budget" in out and "demo-check" in out
    assert cli.main([*args, "--fail-on", "error"]) == cli.EXIT_FINDINGS
    assert cli.main([*args, "--only", "token-budget", "--fail-on", "warn"]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main([*args, "--json", "--only", "demo-check"]) == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["checks"] == ["demo-check"] and data["findings"][0]["id"] == finding(severity=Severity.ERROR).id
    # the lifecycle keys, named as their fields are (dismissed_before_inputs_changed became suppression_lapsed)
    assert {"suppressed", "suppression_lapsed"} <= data["findings"][0].keys()
    assert "dismissed_before_inputs_changed" not in data["findings"][0]
    assert cli.main([*args, "--only", "nope"]) == cli.EXIT_TOOL_ERROR
    assert cli.main([*args, "--list"]) == cli.EXIT_OK


def test_token_budget_rests_on_declared_sources_whether_observed_or_not() -> None:
    observed = token_budget.run(snapshot(None))[0]
    unobserved = token_budget.run(assemble(declared(), []))[0]  # no session: nothing observed
    assert set(observed.sources) == set(unobserved.sources)  # the same set, so a suppression can't lapse by itself


STORE_COMMANDS = [["check"], ["show"], ["rank"], ["usage"], ["sync"], ["suppress", "--list"], ["forget", "--yes"]]


@pytest.mark.parametrize("argv", STORE_COMMANDS)
def test_a_store_that_cannot_be_opened_or_fails_in_use_is_a_tool_error(
        argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Every command opens the database through cli.store(): one that can't be opened, or fails while in use, is said
    in one line and exits 2 — never a traceback, never 1 (findings)."""
    import sqlite3

    from agentlint import store
    from agentlint.store import StoreError

    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    argv = [arg.replace("{tmp}", str(tmp_path)) for arg in argv]
    cwd = ["--cwd", str(PROJECT)] if argv[0] in ("check", "show", "rank", "usage", "suppress") else []

    def unopenable():
        raise StoreError("can't open the agentlint database: locked")

    with monkeypatch.context() as patched:
        patched.setattr(store, "connect", unopenable)
        assert cli.main([*argv, *cwd]) == cli.EXIT_TOOL_ERROR
    err = capsys.readouterr().err
    assert err.strip().splitlines()[-1] == "agentlint: can't open the agentlint database: locked" and "Traceback" not in err

    real = store.connect

    def failing():
        db = real()

        class Failing:  # sqlite3.Connection can't be patched: a stand-in whose every query fails
            def __getattr__(self, name):
                if name == "close":
                    return db.close
                raise sqlite3.OperationalError("disk I/O error")

        return Failing()

    monkeypatch.setattr(store, "connect", failing)
    assert cli.main([*argv, *cwd]) == cli.EXIT_TOOL_ERROR
    err = capsys.readouterr().err
    assert err.strip().splitlines()[-1] == "agentlint: the agentlint database: disk I/O error" and "Traceback" not in err


def test_each_command_names_its_function() -> None:
    parser = cli.build_parser()
    extra = {"unsuppress": ["x"], "config": ["list"]}
    for command in ("check", "suppress", "unsuppress", "sync", "forget", "config", "rank", "ui", "usage", "show"):
        args = parser.parse_args([command, *extra.get(command, [])])
        assert args.func.__name__ == ("cmd_suppress" if command == "unsuppress" else f"cmd_{command}"), command


def test_the_offline_html_report_is_gone(capsys: pytest.CaptureFixture[str]) -> None:
    """Dropped 2026-10-04: the live UI shows everything, and a file holding the configuration's text invites sharing."""
    with pytest.raises(SystemExit) as stopped:
        cli.main(["report", "--html", "r.html"])
    assert stopped.value.code == cli.EXIT_TOOL_ERROR and "invalid choice: 'report'" in capsys.readouterr().err
    assert not hasattr(cli, "cmd_report")
