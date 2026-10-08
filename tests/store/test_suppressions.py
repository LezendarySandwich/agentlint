from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from agentlint import cli
from agentlint.report.finding import Severity
from agentlint.store import DB_SCHEMA_VERSION, connect, home
from agentlint.store.findings import inputs_of, record
from agentlint.store.snapshots import save
from agentlint.store.suppressions import (Suppression, SuppressionState, UnknownFinding, load, resolve, status, suppress,
                                          unsuppress)

from ..checks.test_findings_and_cli import PROJECT, FakeAdapter, finding
from .helpers import check_at, configuration, ran, resting_on, rows
from .test_schema import _V1
from .test_snapshots import snapshot

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
ACTIVE, LAPSED, MOOT = SuppressionState.ACTIVE, SuppressionState.LAPSED, SuppressionState.MOOT


def analyse(db, findings, declared, at, project="/p", write=True):
    """A check that found ``findings``, each resting on every source the configuration declares."""
    return check_at(db, at, [resting_on(f, declared) for f in findings], declared, project=project, write=write)


def recorded(db, project: str, configured, findings, at) -> None:
    record(db, project=project, results=ran(findings), snapshot=configured, at=at, write=True, note=None)


def test_suppressed_until_what_it_rests_on_changes() -> None:
    db = connect()
    noisy = finding(discriminator="n")
    analyse(db, [noisy], {"a": "sha256:1", "b": "sha256:2"}, T0)
    suppress(db, noisy.id, "intentional", T0)
    hidden = analyse(db, [noisy], {"a": "sha256:1", "b": "sha256:2"}, T0 + timedelta(hours=1))
    assert hidden.suppressed_count == 1 and hidden.shown == ()
    edited = analyse(db, [noisy], {"a": "sha256:1", "b": "sha256:9"}, T0 + timedelta(hours=2))
    [shown] = edited.shown
    assert shown.suppression_lapsed  # dismissed before
    suppress(db, noisy.id, "still intentional", T0 + timedelta(hours=3))  # re-arm against it now
    assert analyse(db, [noisy], {"a": "sha256:1", "b": "sha256:9"}, T0 + timedelta(hours=4)).suppressed_count == 1
    assert unsuppress(db, noisy.id) and not unsuppress(db, noisy.id)
    assert len(analyse(db, [noisy], {"a": "sha256:1", "b": "sha256:9"}, T0 + timedelta(hours=5)).shown) == 1


def test_a_suppression_is_global_but_armed_per_project() -> None:
    db = connect()
    budget = finding(discriminator="always-on")  # one id, resting on each project's own files
    in_a, in_b = {"user": "1", "a/CLAUDE.md": "A"}, {"user": "1", "b/CLAUDE.md": "B"}
    analyse(db, [budget], in_a, T0, project="/a")
    analyse(db, [budget], in_b, T0 + timedelta(minutes=1), project="/b")
    suppress(db, budget.id, "known", T0 + timedelta(minutes=2))
    assert analyse(db, [budget], in_b, T0 + timedelta(minutes=3), project="/b").suppressed_count == 1  # armed on sight
    assert analyse(db, [budget], in_a, T0 + timedelta(minutes=4), project="/a").suppressed_count == 1  # no edit: hidden
    edited_b = analyse(db, [budget], {**in_b, "b/CLAUDE.md": "B2"}, T0 + timedelta(minutes=5), project="/b")
    assert edited_b.shown and edited_b.shown[0].suppression_lapsed  # lapsed only where something changed
    assert analyse(db, [budget], in_a, T0 + timedelta(minutes=6), project="/a").suppressed_count == 1
    assert set(load(db)[budget.id].baselines) == {"/a", "/b"}


def test_suppressing_arms_every_project_where_the_finding_is_open() -> None:
    db = connect()
    budget = finding(discriminator="always-on")
    analyse(db, [budget], {"a/CLAUDE.md": "A"}, T0, project="/a")
    analyse(db, [budget], {"b/CLAUDE.md": "B"}, T0, project="/b")
    suppress(db, budget.id, "known", T0 + timedelta(minutes=1))  # run from neither project
    edited = analyse(db, [budget], {"a/CLAUDE.md": "A2"}, T0 + timedelta(minutes=2), project="/a")
    assert edited.shown and edited.shown[0].suppression_lapsed  # an edit before /a's next check is still noticed


def test_a_reading_that_is_not_recorded_still_hides_and_writes_nothing() -> None:
    db = connect()
    noisy = finding(discriminator="n")
    analyse(db, [noisy], {"a": "1"}, T0)
    suppress(db, noisy.id, "intentional", T0)
    before = rows(db, "SELECT count(*) FROM analyses")
    view = analyse(db, [], {}, T0 + timedelta(hours=1), write=False)
    assert not view.recorded  # and the absent finding is not closed by a reading not recorded
    assert rows(db, "SELECT status FROM findings WHERE id = ?", (noisy.id,)) == [("open",)]
    assert rows(db, "SELECT count(*) FROM analyses") == before
    assert analyse(db, [noisy], {"a": "1"}, T0 + timedelta(hours=2), write=False).suppressed_count == 1


def test_ids_resolve_from_unique_prefixes() -> None:
    db = connect()
    for finding_id in ("abcd1111aaaa0000", "abcd2222bbbb0000"):
        db.execute("INSERT INTO findings (project, id, check_id, status, first_seen, last_seen, fingerprint, input_hashes, "
                   "data) VALUES ('/p', ?, 'demo-check', 'open', 'now', 'now', 'fp', '{}', ?)",
                   (finding_id, json.dumps({"title": "t", "discriminator": finding_id})))
    with pytest.raises(UnknownFinding, match="matches 2"):
        resolve(db, "abcd")
    assert resolve(db, "abcd1") == "abcd1111aaaa0000"
    for bad, complaint in (("ab", "at least 4"), ("zzzz", "hex"), ("0000000", "no finding id")):
        with pytest.raises(UnknownFinding, match=complaint):
            resolve(db, bad)


def test_a_schema_1_database_is_migrated() -> None:
    path = home() / "agentlint.db"
    old = sqlite3.connect(path)
    old.executescript(_V1 + """
        INSERT INTO suppressions (id, reason, at, input_hashes)
            VALUES ('abcd1111aaaa0000', 'old reason', '2026-10-01T09:00:00+00:00', '{"x": "1"}');
        PRAGMA user_version = 1;""")
    old.close()
    db = connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == DB_SCHEMA_VERSION == 17
    assert rows(db, "SELECT count(*) FROM observations") == [(0,)]  # every step applied
    assert rows(db, "SELECT count(*) FROM archive_files") == [(0,)]
    assert "derived" in [c[1] for c in db.execute("PRAGMA table_info(archive_files)")]
    assert not db.execute("SELECT name FROM sqlite_master WHERE name IN ('usage_read', 'history')").fetchall()
    [kept] = load(db).values()
    assert kept.reason == "old reason" and kept.baselines == {}  # re-armed on first sight in each project


def test_cli_suppress_flow(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    check = ["check", "--cwd", str(PROJECT)]
    assert cli.main([*check, "--fail-on", "error"]) == cli.EXIT_FINDINGS
    error_id = finding(severity=Severity.ERROR).id
    capsys.readouterr()
    assert cli.main(["suppress", error_id[:8]]) == cli.EXIT_TOOL_ERROR  # a reason is required
    assert "--reason" in capsys.readouterr().err
    assert cli.main(["suppress", error_id[:8], "--reason", "known; tracked elsewhere"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "in every project" in out and "armed now in the 1 project where it is open" in out
    assert cli.main([*check, "--fail-on", "error"]) == cli.EXIT_OK  # suppressed findings never fail a build
    assert "1 suppressed" in capsys.readouterr().out
    assert cli.main([*check, "--sessions", "3"]) == cli.EXIT_OK  # not recorded, still hidden
    out = capsys.readouterr().out
    assert "Not recorded: --sessions 3" in out and "1 suppressed" in out
    assert cli.main(["suppress", "--list"]) == cli.EXIT_OK
    listing = capsys.readouterr().out
    assert "known; tracked elsewhere" in listing and "[active in 1 project]" in listing
    assert cli.main(["unsuppress", error_id[:8]]) == cli.EXIT_OK
    assert cli.main([*check, "--fail-on", "error"]) == cli.EXIT_FINDINGS


def test_suppress_never_reads_a_harness_the_setting_leaves_out(monkeypatch: pytest.MonkeyPatch,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    assert cli.main(["check", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    error_id = finding(severity=Severity.ERROR).id
    assert cli.main(["suppress", error_id[:8], "--reason", "known", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    assert cli.main(["config", "set", "harnesses", "codex"]) == cli.EXIT_OK
    counted = "SELECT (SELECT count(*) FROM snapshots), (SELECT count(*) FROM analyses)"
    db = connect()
    before = rows(db, counted)
    db.close()
    read: list[object] = []
    monkeypatch.setattr(cli, "read_config", lambda *a, **k: read.append(a))
    capsys.readouterr()
    assert cli.main(["suppress", error_id[:8], "--reason", "probe", "--cwd", str(PROJECT)]) == cli.EXIT_TOOL_ERROR
    err = capsys.readouterr().err
    assert "harnesses setting (codex)" in err and "config unset harnesses" in err
    assert read == []  # nothing read, so nothing stored
    db = connect()
    assert rows(db, counted) == before
    assert load(db)[error_id].reason == "known"  # and the suppression left as it was
    db.close()
    # What only touches what agentlint keeps still runs.
    assert cli.main(["suppress", "--list"]) == cli.EXIT_OK
    assert cli.main(["unsuppress", error_id[:8]]) == cli.EXIT_OK


def test_a_suppression_lapses_on_configuration_not_on_membership() -> None:
    db = connect()
    group = finding(discriminator="skills:review", sources=("a", "b"))
    configuration = {"a": "sha256:1", "b": "sha256:2", "listing": "sha256:L"}

    def check_now(rests_on: tuple, config: dict, hours: int):
        return check_at(db, T0 + timedelta(hours=hours), [resting_on(group, rests_on)], config)

    check_now(("a", "b"), configuration, 0)
    suppress(db, group.id, "known", T0 + timedelta(hours=1))
    assert check_now(("a",), configuration, 2).suppressed_count == 1  # b left the finding; nothing was edited
    edited = check_now(("a", "b"), {**configuration, "b": "sha256:3"}, 3)
    assert edited.shown and edited.shown[0].suppression_lapsed  # b was edited: lapsed
    assert status(db, load(db)[group.id]) == {"/p": ACTIVE}  # no stored configuration to judge against


def test_a_namesake_only_sessions_show_can_leave_without_fixing_the_group() -> None:
    db = connect()
    group = finding(discriminator="skills:review", sources=("cmd-review",))
    check_at(db, T0, [group], {"cmd-review": "sha256:1"}, needs_change={"demo-check"})
    check_at(db, T0 + timedelta(hours=1), [], {"cmd-review": "sha256:1"}, needs_change={"demo-check"})
    assert rows(db, "SELECT status FROM findings WHERE id = ?", (group.id,)) == [("open",)]  # the session-only namesake left the window; the file is unchanged
    check_at(db, T0 + timedelta(hours=2), [], {}, needs_change={"demo-check"})
    assert rows(db, "SELECT status FROM findings WHERE id = ?", (group.id,)) == [("closed",)]  # removed


def test_only_a_newly_configured_source_joining_the_finding_lapses_it() -> None:
    dismissed = Suppression("f", "known", T0, {"/p": {"user": "1"}}, {"/p": frozenset({"user", "hook", "cmd"})})
    configured = {"user": "1", "hook": "H", "cmd": "C"}
    assert not dismissed.lapsed("/p", configured, {"user", "hook"})  # already configured: a session showed it
    assert not dismissed.lapsed("/p", configured, {"cmd"})  # a namesake the listing switched to: still not new
    assert dismissed.lapsed("/p", {**configured, "local": "L"}, {"user", "local"})  # a new file in the budget
    assert dismissed.lapsed("/p", {**configured, "user": "2"}, {"user"})  # an edit, as before
    older = Suppression("f", "known", T0, {"/p": {"user": "1"}}, {"/p": None})  # armed before what was configured was kept
    assert not older.lapsed("/p", {**configured, "local": "L"}, {"user", "local"})


def test_first_sight_arms_with_what_is_configured_then() -> None:
    db = connect()
    budget = finding(discriminator="always-on", sources=("user",))
    check_at(db, T0, [budget], {"user": "1"}, project="/a")
    suppress(db, budget.id, "known", T0 + timedelta(minutes=1))

    def check_b(rests_on: tuple, configured: dict, minutes: int):
        return check_at(db, T0 + timedelta(minutes=minutes), [resting_on(budget, rests_on)], configured, project="/b")

    assert check_b(("user",), {"user": "1", "hook": "H"}, 2).suppressed_count == 1  # armed on sight in /b
    assert check_b(("user", "hook"), {"user": "1", "hook": "H"}, 3).suppressed_count == 1  # the hook was configured
    grown = check_b(("user", "new-hook"), {"user": "1", "hook": "H", "new-hook": "N"}, 4)
    assert grown.shown and grown.shown[0].suppression_lapsed  # a hook added since: what it rests on grew


def test_status_says_where_a_suppression_hides_where_it_lapsed_and_where_it_was_fixed() -> None:
    db = connect()
    project = str(PROJECT)
    stale = finding(discriminator="link:missing.md", sources=("rules",))

    def check_now(configured, findings, hours: int) -> None:
        at = T0 + timedelta(hours=hours)
        save(db, configured, at)
        recorded(db, project, configured, findings, at)

    check_now(snapshot("Read missing.md first."), [stale], 0)
    suppress(db, stale.id, "known", T0 + timedelta(hours=1))
    assert status(db, load(db)[stale.id]) == {project: ACTIVE}
    check_now(snapshot("Read missing.md, then the rest."), [stale], 2)
    assert status(db, load(db)[stale.id]) == {project: LAPSED}  # edited, still broken: shown again
    check_now(snapshot("Read the rest."), [], 3)
    assert status(db, load(db)[stale.id]) == {project: MOOT}  # no longer produced: nothing to show




def test_the_command_the_ui_offers_works_before_any_check(monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str], tmp_path) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    monkeypatch.chdir(tmp_path)  # copied into a terminal anywhere: --cwd names the project
    error_id = finding(severity=Severity.ERROR).id
    assert rows(connect(), "SELECT count(*) FROM findings") == [(0,)]  # nothing recorded yet, by a check or the UI
    capsys.readouterr()
    assert cli.main(["suppress", error_id[:12], "--cwd", str(PROJECT), "--reason", "known"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "armed now in the 1 project where it is open" in out
    assert cli.main(["check", "--cwd", str(PROJECT), "--fail-on", "error"]) == cli.EXIT_OK
    assert "1 suppressed" in capsys.readouterr().out
    assert cli.main(["suppress", "--list"]) == cli.EXIT_OK and "[active in 1 project]" in capsys.readouterr().out
    assert cli.main(["suppress", "abcdef12", "--cwd", str(PROJECT), "--reason", "x"]) == cli.EXIT_TOOL_ERROR
    assert "no finding id starts with abcdef12" in capsys.readouterr().err


def test_re_suppressing_counts_only_the_projects_where_it_is_open(monkeypatch: pytest.MonkeyPatch,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)  # the project it runs in is checked first; it doesn't find this
    db = connect()
    budget = finding(discriminator="always-on")
    analyse(db, [budget], {"a/CLAUDE.md": "A"}, T0, project="/a")
    analyse(db, [budget], {"b/CLAUDE.md": "B"}, T0, project="/b")
    assert cli.main(["suppress", budget.id[:8], "--reason", "known"]) == cli.EXIT_OK
    assert "armed now in the 2 projects where it is open" in capsys.readouterr().out
    analyse(db, [], {}, T0 + timedelta(hours=1), project="/b")  # fixed in /b; its baseline stays
    assert cli.main(["suppress", budget.id[:8], "--reason", "still known"]) == cli.EXIT_OK
    assert "armed now in the 1 project where it is open" in capsys.readouterr().out
    analyse(db, [], {}, T0 + timedelta(hours=2), project="/a")
    assert cli.main(["suppress", budget.id[:8], "--reason", "again"]) == cli.EXIT_OK
    assert "No check finds it in any project now" in capsys.readouterr().out


def test_a_newly_installed_package_lapses_what_rests_on_its_listing_share() -> None:
    from agentlint.config.assemble import assemble
    from agentlint.config.model import Declared, Provenance
    from agentlint.config.taxonomy import InjectionPoint, Mechanism

    from ..config.helpers import HARNESS, file_source, observable_source

    point = InjectionPoint(HARNESS, "listing", "skill listing", Mechanism.HARNESS_GENERATED)

    def share(package: str | None):  # the harness's listing, or one package's share of it: declared from disk
        source = observable_source(f"listing:{package}" if package else "listing", point=point, declared="constant")
        return replace(source, provenance=Provenance(point, None, source.provenance.authority, package))

    def reading(*packages: str):
        sources = (file_source("rules", "Run the tests."), share(None), *map(share, packages))
        return assemble(Declared(HARNESS, PROJECT, "2.0", "m", sources, ()), [])

    db = connect()
    project = str(PROJECT)

    def check_now(configured, hours: int):
        budget = finding(discriminator="always-on", sources=tuple(s.id for s in configured.sources))
        at = T0 + timedelta(hours=hours)
        save(db, configured, at)
        return budget, record(db, project=project, results=ran([budget]), snapshot=configured, at=at, write=True, note=None)

    budget, _ = check_now(reading("pkg"), 0)
    assert set(inputs_of(budget, reading("pkg"))) == {"rules", "listing:pkg"}  # the harness's own listing is not
    suppress(db, budget.id, "known", T0 + timedelta(hours=1))
    assert check_now(reading("pkg"), 2)[1].suppressed_count == 1
    _, grown = check_now(reading("pkg", "pkg2"), 3)
    assert grown.shown and grown.shown[0].suppression_lapsed  # pkg2's skills joined the always-on budget
    assert status(db, load(db)[budget.id]) == {project: LAPSED}


def test_status_never_says_shown_again_for_what_no_check_shows(capsys: pytest.CaptureFixture[str]) -> None:
    from agentlint.config.assemble import assemble
    from agentlint.config.model import Declared
    from agentlint.store import retention

    from ..config.helpers import HARNESS, file_source, observable_source

    db = connect()
    project = str(PROJECT)
    stale = finding(discriminator="link:missing.md", sources=("rules",))
    loud = finding(discriminator="emphasis:hook", sources=("hook",))

    def check_now(configured, findings, hours: int) -> None:
        at = T0 + timedelta(hours=hours)
        save(db, configured, at)
        recorded(db, project, configured, findings, at)

    check_now(snapshot("Read missing.md first."), [stale, loud], 0)
    suppress(db, stale.id, "known", T0 + timedelta(hours=1))
    suppress(db, loud.id, "known", T0 + timedelta(hours=1))
    # The rules are fixed; the hook is edited and no session has shown its output since: neither is found, but only
    # the first could be closed.
    edited_unseen = assemble(Declared(HARNESS, PROJECT, "2.0", "m", (
        file_source("rules", "Read the rest."), observable_source("hook", declared="command: echo bye")), ()), [])
    check_now(edited_unseen, [], 2)
    assert rows(db, "SELECT status FROM findings WHERE id = ?", (loud.id,)) == [("open",)]
    assert rows(db, "SELECT status FROM findings WHERE id = ?", (stale.id,)) == [("closed",)]
    assert status(db, load(db)[loud.id]) == {project: MOOT}  # not LAPSED: no check shows it
    retention.prune(db, T0 + timedelta(days=3), 1)  # the closed row goes; the suppression stays
    assert rows(db, "SELECT count(*) FROM findings WHERE id = ?", (stale.id,)) == [(0,)]
    assert status(db, load(db)[stale.id]) == {project: MOOT}
    assert cli.main(["suppress", "--list"]) == cli.EXIT_OK
    listing = capsys.readouterr().out
    assert "shown again" not in listing and "never seen" not in listing
    assert listing.count(f"[no longer found in: {project}]") == 2


def test_forgetting_deletes_every_reading_and_finding_but_keeps_your_suppressions() -> None:
    """What a session showed lives in stored readings (their texts) and findings (what they quote): forget deletes
    both. Suppressions are your decisions, not transcripts: they stay, and hide the finding when it comes back."""
    from agentlint.store import usage

    db = connect()
    save(db, snapshot("Run the tests.", hook_says="Primary working directory: /projects/secret"), T0)
    noisy = finding(discriminator="n")
    analyse(db, [noisy], {"a": "sha256:1"}, T0)
    suppress(db, noisy.id, "intentional", T0)
    usage.forget(db, T0 + timedelta(hours=1))
    assert [rows(db, f"SELECT count(*) FROM {table}")[0][0] for table in ("snapshots", "blobs", "findings")] == [0, 0, 0]
    assert rows(db, "SELECT id FROM suppressions") == [(noisy.id,)]
    assert rows(db, "SELECT project FROM suppression_baselines") == [("/p",)]
    assert analyse(db, [noisy], {"a": "sha256:1"}, T0 + timedelta(hours=2)).suppressed_count == 1


def test_suppressing_from_a_project_arms_it_though_a_check_recorded_the_finding_elsewhere(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    db = connect()
    shown = finding(severity=Severity.ERROR)  # what the project's checks find
    analyse(db, [shown], {"a/CLAUDE.md": "A"}, T0, project="/elsewhere")  # PROJECT itself never checked
    assert cli.main(["suppress", shown.id[:12], "--cwd", str(PROJECT), "--reason", "known"]) == cli.EXIT_OK
    assert "armed now in the 2 projects where it is open" in capsys.readouterr().out
    assert set(load(db)[shown.id].baselines) == {"/elsewhere", str(PROJECT)}  # so it can lapse where it was seen


def test_suppressing_from_a_project_arms_it_though_the_finding_was_closed_there(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    db = connect()
    shown = finding(severity=Severity.ERROR)
    analyse(db, [shown], {"a": "A"}, T0, project=str(PROJECT))
    analyse(db, [], {"a": "A2"}, T0 + timedelta(hours=1), project=str(PROJECT))  # closed; it came back since
    assert cli.main(["suppress", shown.id[:12], "--cwd", str(PROJECT), "--reason", "known"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "armed now in the 1 project where it is open" in out and "No check finds it" not in out
    assert set(load(db)[shown.id].baselines) == {str(PROJECT)}
    assert cli.main(["suppress", "--list"]) == cli.EXIT_OK and "[active in 1 project]" in capsys.readouterr().out


def test_a_fresh_suppression_is_armed_against_the_project_now_not_its_last_check(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    db = connect()
    shown = finding(severity=Severity.ERROR)  # rests on "a": edited since the last check, as the UI shows it now
    check_at(db, T0, [resting_on(shown, ["a"])], {"a": "before the edit"}, project=str(PROJECT))
    assert cli.main(["suppress", shown.id[:12], "--cwd", str(PROJECT), "--reason", "known"]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["check", "--cwd", str(PROJECT), "--fail-on", "error"]) == cli.EXIT_OK  # hidden, not shown again
    assert "1 suppressed" in capsys.readouterr().out


def test_re_arming_pairs_the_stored_configuration_with_itself_not_an_older_finding_row() -> None:
    db = connect()
    project = str(PROJECT)
    stale = finding(discriminator="link:missing.md", sources=("rules",))
    first, edited = snapshot("Read missing.md first."), snapshot("Read missing.md, then the rest.")
    save(db, first, T0)
    recorded(db, project, first, [stale], T0)
    save(db, edited, T0 + timedelta(hours=1))  # a later `check --only` of another check: the row is left as it was
    suppress(db, stale.id, "known", T0 + timedelta(hours=2))
    assert status(db, load(db)[stale.id]) == {project: ACTIVE}  # not "inputs changed" the moment it is made
    view = record(db, project=project, results=ran([stale]), snapshot=edited, at=T0 + timedelta(hours=3), write=False,
                  note="the UI")
    assert view.suppressed_count == 1



def _checked(db, findings, declared, hours: int):
    """A recorded check of /p that stored its configuration, as `check` does, so a suppression knows what was configured."""
    configured, at = configuration(declared), T0 + timedelta(hours=hours)
    save(db, configured, at)
    return record(db, project="/p", results=ran(findings), snapshot=configured, at=at, write=True, note=None)


def test_a_suppression_armed_on_nothing_takes_up_the_sources_its_finding_now_rests_on() -> None:
    """Hook-health findings rested on no source before 2026-10-04; one suppressed then has an empty baseline. Resting
    now on the hook that ran the command, already configured when it was armed, doesn't lapse it — and from then on,
    editing that hook does, as for one suppressed today."""
    db = connect()
    failing = finding(discriminator="timeout:guard.py", sources=())
    configured = {"hook": "H", "other": "O"}
    _checked(db, [failing], configured, 0)
    suppress(db, failing.id, "known slow", T0)
    assert load(db)[failing.id].baselines["/p"] == {}
    assert load(db)[failing.id].known["/p"] == frozenset(configured)
    resting = resting_on(failing, ["hook"])
    assert _checked(db, [resting], configured, 1).suppressed_count == 1  # joined, configured already: not lapsed
    assert set(load(db)[failing.id].baselines["/p"]) == {"hook"}
    assert load(db)[failing.id].known["/p"] == frozenset(configured)  # still what was configured when it was armed
    assert status(db, load(db)[failing.id]) == {"/p": ACTIVE}
    assert _checked(db, [resting], configured, 2).suppressed_count == 1
    edited = _checked(db, [resting], {**configured, "hook": "H2"}, 3)
    assert edited.shown and edited.shown[0].suppression_lapsed  # what it rests on now changed


def test_a_suppression_armed_on_nothing_lapses_when_its_finding_rests_on_a_newly_configured_hook() -> None:
    db = connect()
    failing = finding(discriminator="error:gone.sh", sources=())
    _checked(db, [failing], {"other": "O"}, 0)  # nothing declared the command when it was suppressed
    suppress(db, failing.id, "it's gone", T0)
    shown = _checked(db, [resting_on(failing, ["hook"])], {"other": "O", "hook": "H"}, 1)  # configured again since
    assert shown.shown and shown.shown[0].suppression_lapsed
    assert load(db)[failing.id].baselines["/p"] == {}  # a lapsed baseline isn't rewritten: suppress again to re-arm
