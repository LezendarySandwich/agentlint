"""The probes and diagnostics tables (schema 13; written kept since schema 16): what a write leaves, review, and what
forget and keep_days delete."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from agentlint.store import DB_SCHEMA_VERSION, ReviewStatus, connect, diagnostics, home, probes, retention, usage
from agentlint.store.diagnostics import Diagnostic

T0 = datetime(2026, 10, 4, 1, 0, tzinfo=UTC)


def _probe(probe_id: str = "p1", target_hash: str = "sha256:t", situation: str = "Find the billing calls.",
           **overrides) -> probes.Probe:
    fields = dict(target_id="src#a", matters="which_tools", model="m-1", prompt_version=1, created=T0)
    return probes.Probe(probe_id, target_hash=target_hash, situation=situation, **{**fields, **overrides})


def test_a_target_texts_probes_read_back_in_the_order_written() -> None:
    db = connect()
    written = [_probe("p1"), _probe("p2", situation="Why does the login test fail?"), _probe("q1", "sha256:other")]
    probes.replace(db, "sha256:t", written[:2])
    probes.replace(db, "sha256:other", written[2:])
    assert probes.for_hash(db, "sha256:t") == written[:2]
    assert probes.load(db, "q1") == written[2] and probes.load(db, "nope") is None
    assert written[0].text == "Find the billing calls." and written[0].review is ReviewStatus.ACCEPTED  # written kept
    assert written[0].reviewed is None  # and nobody reviewed it


def test_replace_drops_the_old_situations_and_their_reviews_even_for_the_same_text() -> None:
    db = connect()
    probes.replace(db, "sha256:t", [_probe("p1"), _probe("p2", situation="Rename the loader please.")])
    probes.set_review(db, "p1", ReviewStatus.REJECTED, T0)
    probes.replace(db, "sha256:t", [_probe("p1", created=T0 + timedelta(days=1))])
    (again,) = probes.for_hash(db, "sha256:t")
    assert (again.probe_id, again.review, again.reviewed) == ("p1", ReviewStatus.ACCEPTED, None)  # kept as written
    with pytest.raises(ValueError):
        probes.replace(db, "sha256:t", [_probe("x", "sha256:other")])


def test_review_records_accept_reject_and_an_edit_with_its_text() -> None:
    db = connect()
    probes.replace(db, "sha256:t", [_probe("p1")])
    edited = probes.set_review(db, "p1", ReviewStatus.EDITED, T0, edited_text="Find every billing call in src.")
    assert (edited.review, edited.text, edited.situation, edited.reviewed) == (
        ReviewStatus.EDITED, "Find every billing call in src.", "Find the billing calls.", T0)
    rejected = probes.set_review(db, "p1", ReviewStatus.REJECTED, T0)
    assert (rejected.review, rejected.edited_text, rejected.text) == (ReviewStatus.REJECTED, None, "Find the billing calls.")
    kept = probes.set_review(db, "p1", ReviewStatus.ACCEPTED, T0 + timedelta(hours=1))
    assert (kept.review, kept.reviewed) == (ReviewStatus.ACCEPTED, T0 + timedelta(hours=1))  # kept by you: a time
    assert [status.value for status in ReviewStatus] == ["accepted", "rejected", "edited"]  # 'pending' is no state
    with pytest.raises(ValueError):
        probes.set_review(db, "p1", ReviewStatus.EDITED, T0)
    with pytest.raises(ValueError):
        probes.set_review(db, "p1", ReviewStatus.ACCEPTED, T0, edited_text="x")
    with pytest.raises(probes.NoSuchProbe):
        probes.set_review(db, "gone", ReviewStatus.ACCEPTED, T0)


def test_the_database_checks_the_review_status_and_the_edited_text() -> None:
    db = connect()
    probes.replace(db, "sha256:t", [_probe("p1")])
    for status in ReviewStatus:  # every status the code writes, the table takes
        db.execute("UPDATE probes SET review = ?, edited_text = ?", (status.value,
                                                                     "t" if status is ReviewStatus.EDITED else None))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE probes SET review = 'maybe', edited_text = NULL")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE probes SET review = 'accepted', edited_text = 'x'")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE probes SET review = 'edited', edited_text = NULL")


def test_diagnostics_are_listed_newest_first_and_by_subject_or_kind() -> None:
    db = connect()
    first = diagnostics.add(db, Diagnostic("generator_empty", "src#a", "The generator wrote nothing.", {"note": "vague"}), T0)
    diagnostics.add(db, Diagnostic("situation_contaminated", "src#a", "Dropped one.", {"hits": ["x"]}),
                    T0 + timedelta(hours=1))
    diagnostics.add(db, Diagnostic("generator_empty", "src#b", "Nothing again."), T0 + timedelta(hours=2))
    assert [d.subject for d in diagnostics.listed(db)] == ["src#b", "src#a", "src#a"]
    (only,) = diagnostics.listed(db, subject="src#a", kind="generator_empty")
    assert (only.id, only.data, only.created, only.message) == (first, {"note": "vague"}, T0, "The generator wrote nothing.")
    assert len(diagnostics.listed(db, kind="generator_empty")) == 2
    with pytest.raises(ValueError):
        Diagnostic("", "s", "m")


def test_diagnostics_never_reach_the_findings_table() -> None:
    db = connect()
    diagnostics.add(db, Diagnostic("generator_empty", "src#a", "The generator wrote nothing."), T0)
    assert db.execute("SELECT count(*) FROM findings").fetchone()[0] == 0


def test_forget_deletes_every_probe_and_diagnostic() -> None:
    db = connect()
    probes.replace(db, "sha256:t", [_probe("p1")])
    diagnostics.add(db, Diagnostic("generator_empty", "src#a", "Nothing."), T0)
    usage.forget(db, T0)
    assert probes.for_hash(db, "sha256:t") == [] and diagnostics.listed(db) == []


def test_keep_days_prunes_old_diagnostics_and_keeps_every_probe() -> None:
    db = connect()
    old = T0 - timedelta(days=40)
    probes.replace(db, "sha256:t", [_probe("p1", created=old)])
    diagnostics.add(db, Diagnostic("generator_empty", "src#a", "Old."), old)
    diagnostics.add(db, Diagnostic("generator_empty", "src#a", "Recent."), T0 - timedelta(days=1))
    retention.prune(db, T0, keep_days=30)
    assert [d.message for d in diagnostics.listed(db)] == ["Recent."]
    assert [p.probe_id for p in probes.for_hash(db, "sha256:t")] == ["p1"]


def test_a_schema_12_database_gains_empty_probes_and_diagnostics_and_keeps_everything_else() -> None:
    path = home() / "agentlint.db"
    connect(path).close()
    old = sqlite3.connect(path)
    old.executescript("""
        DROP TABLE probes;
        DROP TABLE diagnostics;
        DROP TABLE settings;
        DROP TABLE state;
        ALTER TABLE jobs DROP COLUMN harness;
        INSERT INTO suppressions (id, reason, at) VALUES ('f', 'known', 't');
        INSERT INTO jobs (job_id, project, fingerprint, harness_version, model, run_ceiling, timeout_s, plan, status,
                          created, last_progress)
            VALUES ('j1', '/p', 'fp', '1.0', 'm', 10, 300, '{}', 'open', 't', 't');
        PRAGMA user_version = 12;""")
    old.close()
    db = connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == DB_SCHEMA_VERSION == 17
    assert [tuple(row) for row in db.execute("SELECT id, reason FROM suppressions")] == [("f", "known")]
    assert [row[0] for row in db.execute("SELECT job_id FROM jobs")] == ["j1"]
    assert probes.for_hash(db, "sha256:t") == [] and diagnostics.listed(db) == []
    probes.replace(db, "sha256:t", [_probe("p1")])
    assert probes.load(db, "p1") is not None
