from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from agentlint import cli
from agentlint.report.finding import Severity
from agentlint.store import DB_SCHEMA_VERSION, connect, home, transaction
from agentlint.store._time import encode
from agentlint.store.findings import record

from ..checks.test_findings_and_cli import PROJECT, FakeAdapter, finding
from .helpers import check_at, configuration, ran, rows

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def analyse(db, findings, *, project="/p", evaluated=("demo-check", "other-check"), at=T0):
    declared = {source: "sha256:x" for f in findings for source in f.sources}
    return check_at(db, at, findings, declared, project=project, evaluated=evaluated)


def statuses(db, project="/p") -> dict[str, str]:
    return dict(rows(db, "SELECT id, status FROM findings WHERE project = ?", (project,)))


def test_open_closed_and_open_again() -> None:
    db = connect()
    a, b, c = finding(discriminator="a"), finding(discriminator="b"), finding(discriminator="c")
    analyse(db, [a, b])
    assert statuses(db) == {a.id: "open", b.id: "open"}
    analyse(db, [b, c], at=T0 + timedelta(hours=1))
    assert statuses(db) == {a.id: "closed", b.id: "open", c.id: "open"}
    analyse(db, [a, b, c], at=T0 + timedelta(hours=2))
    assert set(statuses(db).values()) == {"open"}  # it came back
    assert not db.execute("SELECT name FROM sqlite_master WHERE name = 'history'").fetchall()


def test_the_same_configuration_judged_by_the_same_checks_moves_its_analysis_rather_than_adding_one() -> None:
    """A page reloaded, or `check` run twice, with nothing changed: the latest analysis moves to now. Other checks or
    another configuration are an analysis of their own, which suppressions read for the checks it ran."""
    db = connect()
    a = finding(discriminator="a")
    for hours in (0, 1, 2):
        analyse(db, [a], at=T0 + timedelta(hours=hours))
    assert rows(db, "SELECT at FROM analyses") == [(encode(T0 + timedelta(hours=2)),)]
    analyse(db, [a], evaluated=("demo-check",), at=T0 + timedelta(hours=3))  # `check --only`
    analyse(db, [a], at=T0 + timedelta(hours=4))
    assert rows(db, "SELECT count(*) FROM analyses") == [(3,)]
    assert statuses(db) == {a.id: "open"}


def test_only_evaluated_checks_can_close_their_findings() -> None:
    db = connect()
    mine, theirs = finding(discriminator="m"), finding(check="other-check", discriminator="t")
    analyse(db, [mine, theirs])
    analyse(db, [mine], evaluated=("demo-check",), at=T0 + timedelta(hours=1))  # other-check didn't run
    assert statuses(db)[theirs.id] == "open"


def test_projects_have_separate_lifecycles() -> None:
    db = connect()
    shared = finding(discriminator="s")
    analyse(db, [shared], project="/a")
    analyse(db, [], project="/b", at=T0 + timedelta(hours=1))
    assert statuses(db, "/a") == {shared.id: "open"} and statuses(db, "/b") == {}


def test_a_reading_is_recorded_without_a_note_or_says_why_not() -> None:
    db = connect()
    for write, note in ((True, "why"), (False, None)):
        with pytest.raises(ValueError):
            record(db, project="/p", results=ran([]), snapshot=configuration({}), at=T0, write=write, note=note)


def test_a_failed_write_leaves_nothing_behind() -> None:
    db = connect()
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(db):
            row = "INSERT INTO analyses (project, at, fingerprint, checks) VALUES ('/p', 'now', 'fp', '[]')"
            db.execute(row)
            db.execute(row)  # same key: fails
    assert rows(db, "SELECT count(*) FROM analyses") == [(0,)]


def test_cli_says_nothing_about_what_is_new_or_gone(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Judging what is new or gone from presence and absence proved brittle: neither is reported (2026-10-03)."""
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    args = ["check", "--cwd", str(PROJECT)]
    for _ in range(2):
        assert cli.main(args) == cli.EXIT_OK
        out = capsys.readouterr().out.lower()
        assert not any(word in out for word in ("new", "recurring", "fixed", "first check"))
    assert cli.main([*args, "--json"]) == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert not {"state", "since"} & set(data["findings"][0]) and "previous_analysis" not in data
    demo = finding(severity=Severity.ERROR)
    assert {f["id"] for f in data["findings"]} >= {demo.id}


def test_a_source_seen_without_its_text_does_not_close_what_rests_on_it() -> None:
    db = connect()
    loud_hook = finding(discriminator="h", sources=("hook-output",))
    analyse(db, [loud_hook])
    check_at(db, T0 + timedelta(hours=1), [], {"hook-output": "sha256:x"}, unobserved={"hook-output"})
    assert statuses(db)[loud_hook.id] == "open"  # still configured, just not observed: not evidence it went
    check_at(db, T0 + timedelta(hours=2), [], {"hook-output": "sha256:x"})
    assert statuses(db)[loud_hook.id] == "closed"  # observed and quiet
    analyse(db, [loud_hook], at=T0 + timedelta(hours=3))
    check_at(db, T0 + timedelta(hours=4), [], {})
    assert statuses(db)[loud_hook.id] == "closed"  # removed


def test_a_check_that_judges_by_runs_closes_what_rests_on_a_source_seen_without_its_text() -> None:
    """hook-health rests on the hooks that ran a failing command, but judges by their recorded runs, not by what they
    printed: an unobserved hook doesn't keep its finding open once the check judged it gone."""
    db = connect()
    failing = finding(discriminator="timeout:guard.py", sources=("hook",))
    analyse(db, [failing])
    check_at(db, T0 + timedelta(hours=1), [], {"hook": "sha256:x"}, unobserved={"hook"}, not_by_text={"demo-check"})
    assert statuses(db)[failing.id] == "closed"
    analyse(db, [failing], at=T0 + timedelta(hours=2))
    check_at(db, T0 + timedelta(hours=3), [], {"hook": "sha256:x"}, unobserved={"hook"}, not_by_text={"demo-check"},
             judged={"demo-check": lambda discriminator: False})
    assert statuses(db)[failing.id] == "open"  # its check couldn't tell (the hook didn't run): still open


def test_a_finding_outside_this_runs_evidence_is_not_closed() -> None:
    db = connect()
    seen, unseen = finding(discriminator="error:hook-a"), finding(discriminator="error:hook-b")
    analyse(db, [seen, unseen])
    check_at(db, T0 + timedelta(hours=1), [], {}, judged={"demo-check": lambda d: d == "error:hook-a"})
    assert statuses(db) == {seen.id: "closed", unseen.id: "open"}  # hook-b didn't run in the window: can't say


def test_a_ranked_finding_is_closed_only_when_its_own_inputs_change() -> None:
    db = connect()
    loud = finding(discriminator="source:mine", sources=("mine",))
    analyse(db, [loud])  # stored with input hash sha256:x
    check_at(db, T0 + timedelta(hours=1), [], {"mine": "sha256:x"}, needs_change={"demo-check"})
    assert statuses(db)[loud.id] == "open"  # unchanged, merely outranked
    check_at(db, T0 + timedelta(hours=2), [], {"mine": "sha256:y"}, needs_change={"demo-check"})
    assert statuses(db)[loud.id] == "closed"


def test_a_finding_quoting_a_lone_surrogate_is_stored_and_read_back() -> None:
    db = connect()
    odd = finding(title="Matcher Bash\ud800 never matches", discriminator="m\ud800")
    check_at(db, T0, [odd], {})
    (data,) = db.execute("SELECT data FROM findings WHERE id = ?", (odd.id,)).fetchone()
    assert json.loads(data)["title"] == "Matcher Bash\ud800 never matches"


def test_a_schema_7_database_closes_rather_than_fixes_and_keeps_no_transitions() -> None:
    path = home() / "agentlint.db"
    connect(path).close()
    old = sqlite3.connect(path)
    old.executescript("""
        DROP TABLE findings;
        CREATE TABLE findings (project TEXT NOT NULL, id TEXT NOT NULL, check_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('open', 'fixed')), first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            fingerprint TEXT NOT NULL, input_hashes TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (project, id)) STRICT;
        CREATE TABLE history (project TEXT NOT NULL, id TEXT NOT NULL, state TEXT NOT NULL, at TEXT NOT NULL,
            fingerprint TEXT NOT NULL) STRICT;
        INSERT INTO findings VALUES ('/p', 'aaaa', 'demo-check', 'open', 't', 't', 'fp', '{}', '{}');
        INSERT INTO findings VALUES ('/p', 'bbbb', 'demo-check', 'fixed', 't', 't', 'fp', '{}', '{}');
        INSERT INTO history VALUES ('/p', 'bbbb', 'fixed', 't', 'fp');
        ALTER TABLE verdicts RENAME COLUMN target_id TO unit_id;
        ALTER TABLE archive_files RENAME COLUMN bytes_read TO size;
        ALTER TABLE archive_files RENAME COLUMN mtime_epoch_s TO mtime;
        ALTER TABLE archive_files RENAME COLUMN forgotten_bytes TO start;
        ALTER TABLE archive_files RENAME COLUMN tail_sha256 TO tail;
        DROP TABLE runs;
        DROP TABLE jobs;
        DROP TABLE probes;
        DROP TABLE diagnostics;
        DROP TABLE settings;
        DROP TABLE state;
        PRAGMA user_version = 7;""")
    old.close()
    db = connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == DB_SCHEMA_VERSION == 17
    assert statuses(db) == {"aaaa": "open", "bbbb": "closed"}
    assert not db.execute("SELECT name FROM sqlite_master WHERE name = 'history'").fetchall()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE findings SET status = 'fixed'")


def test_a_schema_10_finding_reads_back_without_the_path_its_location_named() -> None:
    """A stored location named a path or a label; now it names a source by id, which the row can't supply."""
    from agentlint.config.serialize import as_model
    from agentlint.report.finding import Finding
    from agentlint.store.suppressions import latest

    path = home() / "agentlint.db"
    connect(path).close()
    old = sqlite3.connect(path)
    stored = {"check": "broken-reference", "kind": "broken", "severity": "error", "confidence": "certain",
              "title": "Link to a.md points at nothing", "detail": "d", "discriminator": "link:s:a.md", "sources": ["s"],
              "locations": [{"path": "/home/u/CLAUDE.md#claudeMd", "line": 3}], "figures": [],
              "suggested_action": None, "members": []}
    old.execute("INSERT INTO findings VALUES ('/p', 'aaaa', 'broken-reference', 'open', 't', 't', 'fp', '{}', ?)",
                (json.dumps(stored),))
    old.commit()
    old.executescript("DROP TABLE runs; DROP TABLE jobs; DROP TABLE probes; DROP TABLE diagnostics; DROP TABLE settings; DROP TABLE state; PRAGMA user_version = 10;")  # as schema 10 had them: none
    old.close()
    db = connect(path)
    (data,) = db.execute("SELECT data FROM findings").fetchone()
    finding = as_model(Finding, json.loads(data))
    assert finding.locations == () and (finding.discriminator, finding.sources) == ("link:s:a.md", ("s",))
    assert latest(db, "aaaa", "/p") == ("broken-reference", "Link to a.md points at nothing", {})


def test_nothing_a_close_depends_on_can_be_left_out() -> None:
    """Every guard was once optional and defaulted to closing (design review 2.1): now each is derived or required."""
    import inspect

    parameters = list(inspect.signature(record).parameters.values())[1:]
    assert {p.name for p in parameters} == {"project", "results", "snapshot", "at", "write", "note"}
    assert all(p.default is inspect.Parameter.empty and p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters)
