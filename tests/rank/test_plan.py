"""The ranker: a filter, a sort, a budget in runs — and every skip with its reason (docs/tech_impl/03-ranker.md)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from datetime import UTC, datetime, timedelta

import pytest

from agentlint import cli
from agentlint.config.assemble import assemble
from agentlint.config.entity import Entity
from agentlint.config.model import Ablation, AblationPolicy, Declared
from agentlint.config.taxonomy import Load
from agentlint.config.sessions import SessionHeader
from agentlint.config.usage import Turn, build_index
from agentlint.rank.plan import Phase, Skip, TargetStatus, plan, targets
from agentlint.report.finding import Confidence, Finding, FindingKind, Location, Severity
from agentlint.report.rank import _LOAD, explain, render
from agentlint.store import connect

from ..checks.test_core_checks import SKILL
from ..checks.test_findings_and_cli import FakeAdapter
from ..config.helpers import HARNESS, PROJECT, delivery, file_source, observable_source, ref, session
from ..store.helpers import rows

NOW = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
NOTES = "# Notes\n## Testing\nRun the tests before committing, every time, without fail.\n## Links\nSee [the guide](guide.md).\n"


def skill(source_id: str, text: str):
    return replace(file_source(source_id, text, load=Load.ON_INVOKE), ablation=AblationPolicy(Ablation.FILE_REMOVAL))


def snapshot():
    sources = (
        file_source("notes", NOTES),  # two sections a variant can cut out one at a time
        observable_source("hook"),  # its output is the hook's to print: removed whole
        skill("used-skill", "---\ndescription: used\n---\n# Use\nDo the used thing.\n## More\nAnd more of it.\n"),
        skill("idle-skill", "---\ndescription: idle\n---\nNever called.\n"),
        replace(file_source("policy", "Managed words."), ablation=AblationPolicy.blocked("managed by your organisation")),
        file_source("quiet", "Printed to a log.", load=Load.NOT_IN_CONTEXT),
    )
    entities = (Entity("skill:used", "used", SKILL, "used-skill", "used"), Entity("skill:idle", "idle", SKILL, "idle-skill", "idle"))
    hook_says = "First paragraph of the hook.\n\nSecond paragraph, longer than the first one by a little.\n"
    return assemble(Declared(HARNESS, PROJECT, "2.0", "m", sources, entities), [session(ref(1), delivery("hook", hook_says))])


USAGE = build_index([Turn(SessionHeader("s", PROJECT, "2.0", "m", NOW), 1, NOW, "m", False, ("skills/used",))])
BROKEN = Finding("broken-reference", FindingKind.BROKEN, Severity.ERROR, Confidence.CERTAIN, "Link to guide.md points at nothing",
                 "d", "link:guide.md", ("notes",), locations=(Location("notes", 5),))


def test_a_line_of_the_plan_is_what_one_variant_removes() -> None:
    by_source = {}
    for target in targets(snapshot()):
        by_source.setdefault(target.source.id, []).append(target)
    assert [len(u.rules) for u in by_source["notes"]] == [1, 1, 1]  # cut out one section at a time
    [hook] = by_source["hook"]
    assert hook.whole and len(hook.rules) == 2 and hook.id == "hook"  # one question: does this hook matter
    [used] = by_source["used-skill"]
    assert used.whole and used.id == "used-skill"  # a skill file is dropped whole


def test_skips_say_why_and_the_rest_are_ordered_by_exposure_then_cost() -> None:
    result = plan(snapshot(), findings=[BROKEN], usage=USAGE, statuses={}, budget=10_000)
    why = {item.target.id: item.why for item in result.skipped}
    assert why["policy"] is Skip.UNTESTABLE and why["quiet#whole"] is Skip.NOT_IN_CONTEXT and why["idle-skill"] is Skip.UNUSED
    assert why["notes#section:/Notes/Links"] is Skip.SETTLED  # proven broken: no runs to confirm it
    order = [item.target.id for item in result.planned]
    assert order[-1] == "used-skill"  # on invoke comes after everything in every session
    first = [item.target for item in result.planned[:-1]]
    assert all(u.source.activation.load.in_first_turn for u in first)
    assert [u.tokens for u in first] == sorted((u.tokens for u in first), reverse=True)


def test_a_source_with_no_text_to_divide_is_skipped_with_its_reason_not_left_out() -> None:
    sources = (file_source("notes", NOTES), observable_source("quiet-hook"))  # no session examined showed its output
    result = plan(assemble(Declared(HARNESS, PROJECT, "2.0", "m", sources, ()), []), findings=[], usage=None, statuses={}, budget=600)
    [quiet] = [item for item in result.skipped if item.target.id == "quiet-hook"]
    assert quiet.why is Skip.NO_TEXT and quiet.detail == "only a session can show this text"
    assert "quiet-hook" not in {item.target.id for item in [*result.planned, *result.deferred]}
    assert "SKIPPED: no text to test" in explain(result, "quiet-hook", {})
    assert "quiet-hook" in render(result, {}, everything=True)


def test_a_proven_break_settles_its_section_by_source_id_whatever_its_path_holds() -> None:
    notes = replace(file_source("notes", NOTES), provenance=replace(file_source("notes", NOTES).provenance,
                                                                    file=Path("/home/u/proj#1/CLAUDE.md")))
    broken = replace(BROKEN, locations=(Location("notes", 5),))
    result = plan(assemble(Declared(HARNESS, PROJECT, "2.0", "m", (notes,), ()), []), findings=[broken], usage=None,
                  statuses={}, budget=600)
    assert {item.target.id: item.why for item in result.skipped}["notes#section:/Notes/Links"] is Skip.SETTLED


def test_the_budget_cuts_the_ranked_list_and_says_what_waits() -> None:
    result = plan(snapshot(), findings=[], usage=USAGE, statuses={}, budget=130)
    assert [p.runs for p in result.planned] == [60, 60] and result.runs == 120
    assert len(result.deferred) == 3  # testable, ranked, past the budget (nothing settled without findings)
    assert plan(snapshot(), findings=[], usage=USAGE, statuses={}, budget=130, phase=Phase.STABILITY).planned[0].runs == 9


def test_a_verdict_counts_only_under_the_configuration_it_was_measured_in() -> None:
    configured = snapshot()
    def status(fingerprint: str) -> TargetStatus:
        return TargetStatus("hook", NOW - timedelta(days=1), fingerprint, "job-1", {"measured": "effect"}, 3)
    kept = plan(configured, findings=[], usage=USAGE, statuses={"hook": status(configured.fingerprint)}, budget=600)
    assert {item.target.id: item.why for item in kept.skipped}["hook"] is Skip.MEASURED
    stale = plan(configured, findings=[], usage=USAGE, statuses={"hook": status("sha256:other")}, budget=600)
    assert "hook" in {item.target.id for item in [*stale.planned, *stale.deferred]}  # re-queued


def test_when_a_target_was_measured_is_a_local_date(local_zone) -> None:
    """"measured on" is the day in local time, like every other time shown (round 12)."""
    configured = snapshot()
    late = TargetStatus("hook", datetime(2026, 10, 1, 23, 30, tzinfo=UTC), configured.fingerprint, "job-1", {"measured": "effect"}, 3)
    local_zone("Europe/London")  # BST: 23:30Z on 1 Oct is 00:30 on 2 Oct
    kept = plan(configured, findings=[], usage=USAGE, statuses={"hook": late}, budget=600)
    assert {item.target.id: item.detail for item in kept.skipped}["hook"].startswith("measured on 2026-10-02;")


def test_without_usage_history_nothing_on_invoke_is_planned() -> None:
    result = plan(snapshot(), findings=[], usage=None, statuses={}, budget=600)
    assert {item.target.id: item.detail for item in result.skipped}["used-skill"] == "no usage history yet"


def test_the_plan_reads_plainly() -> None:
    result = plan(snapshot(), findings=[BROKEN], usage=USAGE, statuses={}, budget=130)
    text = render(result, {"used-skill": ["used"]})
    assert "2 planned, 120 runs (60 each: 3 situations × 2 variants × 10 runs)" in text
    assert "NOT THIS TIME  2 more need 120 more runs (--runs 250 plans them all)" in text
    assert "can't be tested by removal — managed by your organisation" in text
    assert "(whole, 2 parts)" in text
    assert "SKIPPED: on invoke, with no record of use" in explain(result, "idle-skill", {})
    assert "removed whole" in explain(result, "hook", {}) and explain(result, "nothing-like-this", {}) is None
    assert "NOT THIS TIME: #4 of 4" in explain(result, "used-skill", {})


def test_cli_rank(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    assert cli.main(["rank", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    assert "agentlint rank — phase 2, ablation · budget 60 runs" in capsys.readouterr().out  # the runs setting's default
    assert cli.main(["rank", "--explain", "notes", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    assert "PLANNED #" in capsys.readouterr().out
    assert db_has_verdicts()


def db_has_verdicts() -> bool:
    return rows(connect(), "SELECT count(*) FROM verdicts") == [(0,)]  # the table exists; the runner fills it


def test_every_load_has_its_words_keyed_by_member() -> None:
    assert set(_LOAD) == set(Load)  # a new or renamed Load fails here, not as a KeyError in rank
