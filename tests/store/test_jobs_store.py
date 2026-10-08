"""The runner's jobs and runs tables: what one write leaves, the lock, and what forget and keep_days delete."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from agentlint.store import DB_SCHEMA_VERSION, JobStatus, connect, home, jobs, retention, usage

T0 = datetime(2026, 10, 4, 1, 0, tzinfo=UTC)


def _job(db: sqlite3.Connection, job_id: str = "j1", **overrides) -> jobs.Job:
    fields = dict(job_id=job_id, project="/p", fingerprint="fp", harness_version="1.0", model="m", run_ceiling=10,
                  timeout_s=300, plan='{"format": 1, "comparisons": []}', now=T0)
    return jobs.create(db, **{**fields, **overrides})


def _run(key: str, job_id: str = "j1", at: datetime = T0) -> jobs.Run:
    return jobs.Run(job_id, key, "s-" + key, "ok", "[]", "1.0", "m", 1200, 0.01, at, at + timedelta(seconds=1))


def test_a_new_job_is_open_with_nothing_launched_or_held() -> None:
    db = connect()
    job = _job(db)
    assert (job.status, job.launches, job.in_flight, job.runner_pid) == (JobStatus.OPEN, 0, None, None)
    assert jobs.load(db, "j1") == job and jobs.load(db, "nope") is None
    assert job.created == job.last_progress == T0


def test_launch_then_record_leaves_the_run_and_nothing_in_flight() -> None:
    db = connect()
    _job(db)
    jobs.launch(db, "j1", "c1:baseline:0", T0)
    held = jobs.load(db, "j1")
    assert (held.in_flight, held.launches) == ("c1:baseline:0", 1)
    jobs.record(db, _run("c1:baseline:0"), complete=False)
    after = jobs.load(db, "j1")
    assert (after.in_flight, after.launches, after.status) == (None, 1, JobStatus.OPEN)
    assert jobs.recorded_keys(db, "j1") == {"c1:baseline:0"} and jobs.recorded_count(db, "j1") == 1
    assert jobs.runs(db, "j1") == [_run("c1:baseline:0")]
    jobs.launch(db, "j1", "c1:baseline:1", T0)
    jobs.record(db, _run("c1:baseline:1"), complete=True)
    assert jobs.load(db, "j1").status is JobStatus.COMPLETE


def test_a_closed_job_takes_no_launch_and_no_run() -> None:
    db = connect()
    _job(db)
    jobs.close(db, "j1", JobStatus.SALVAGED)
    with pytest.raises(jobs.JobGone):
        jobs.launch(db, "j1", "c1:baseline:0", T0)
    with pytest.raises(jobs.JobGone):
        jobs.record(db, _run("c1:baseline:0"), complete=False)
    assert jobs.recorded_count(db, "j1") == 0  # the insert rolled back with the refused update


def test_a_run_is_recorded_once() -> None:
    db = connect()
    _job(db)
    jobs.record(db, _run("c1:baseline:0"), complete=False)
    with pytest.raises(sqlite3.IntegrityError):
        jobs.record(db, _run("c1:baseline:0"), complete=False)


def test_a_live_holder_keeps_the_lock_and_a_stale_one_loses_it() -> None:
    db = connect()
    _job(db)
    jobs.claim(db, "j1", 100, "start-a", stale=lambda pid, started: False)
    with pytest.raises(jobs.JobLocked) as raised:
        jobs.claim(db, "j1", 200, "start-b", stale=lambda pid, started: False)
    assert raised.value.pid == 100
    seen = []
    jobs.claim(db, "j1", 200, "start-b", stale=lambda pid, started: seen.append((pid, started)) or True)
    assert seen == [(100, "start-a")]
    jobs.release(db, "j1", 100, "start-a")  # no longer its lock: nothing changes
    assert (jobs.load(db, "j1").runner_pid, jobs.load(db, "j1").runner_started) == (200, "start-b")
    jobs.release(db, "j1", 200, "start-b")
    assert jobs.load(db, "j1").runner_pid is None


def test_forget_deletes_every_job_and_run_and_a_running_job_stops_at_its_next_write() -> None:
    db = connect()
    _job(db)
    _job(db, "j2")
    jobs.close(db, "j2", JobStatus.SALVAGED)
    jobs.launch(db, "j1", "c1:baseline:0", T0)
    jobs.record(db, _run("c1:baseline:0"), complete=False)
    usage.forget(db, T0)
    assert jobs.listed(db) == [] and db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    with pytest.raises(jobs.JobGone):
        jobs.launch(db, "j1", "c1:baseline:1", T0)
    with pytest.raises(jobs.JobGone):
        jobs.launches(db, "j1")


def test_keep_days_deletes_closed_jobs_with_their_runs_and_keeps_every_open_one() -> None:
    db = connect()
    old = T0 - timedelta(days=40)
    closed = (("open", None), ("done", JobStatus.COMPLETE), ("salvaged", JobStatus.SALVAGED),
              ("ceiling", JobStatus.CEILING))
    for job_id, status in closed:
        _job(db, job_id, now=old)
        jobs.record(db, _run("c1:baseline:0", job_id, old), complete=status is JobStatus.COMPLETE)
        if status in (JobStatus.SALVAGED, JobStatus.CEILING):
            jobs.close(db, job_id, status)
    # open with its ceiling launched: whether it can launch again is its counter's call, which the job doesn't record
    _job(db, "killed-on-its-last-launch", now=old, run_ceiling=1)
    jobs.launch(db, "killed-on-its-last-launch", "c1:baseline:0", old)
    _job(db, "recent", now=T0, run_ceiling=1)
    jobs.launch(db, "recent", "c1:baseline:0", T0)
    jobs.record(db, _run("c1:baseline:0", "recent", T0), complete=False)
    jobs.close(db, "recent", JobStatus.CEILING)
    retention.prune(db, T0, keep_days=30)
    assert sorted(job.job_id for job in jobs.listed(db)) == ["killed-on-its-last-launch", "open", "recent"]
    assert {row[0] for row in db.execute("SELECT job_id FROM runs")} == {"open", "recent"}


def test_listed_is_newest_first_and_by_project() -> None:
    db = connect()
    _job(db, "a", now=T0)
    _job(db, "b", now=T0 + timedelta(hours=1), project="/q")
    assert [job.job_id for job in jobs.listed(db)] == ["b", "a"]
    assert [job.job_id for job in jobs.listed(db, "/p")] == ["a"]


def test_a_schema_11_database_gains_empty_jobs_and_runs_and_keeps_everything_else() -> None:
    path = home() / "agentlint.db"
    connect(path).close()
    old = sqlite3.connect(path)
    old.executescript("""
        DROP TABLE runs;
        DROP TABLE jobs;
        DROP TABLE probes;
        DROP TABLE diagnostics;
        DROP TABLE settings;
        DROP TABLE state;
        INSERT INTO suppressions (id, reason, at) VALUES ('f', 'known', 't');
        PRAGMA user_version = 11;""")
    old.close()
    db = connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == DB_SCHEMA_VERSION == 17
    assert [tuple(row) for row in db.execute("SELECT id, reason FROM suppressions")] == [("f", "known")]
    assert jobs.listed(db) == []
    _job(db)
    assert jobs.load(db, "j1").status is JobStatus.OPEN


def test_status_and_ceiling_are_checked_by_the_database() -> None:
    db = connect()
    with pytest.raises(sqlite3.IntegrityError):
        _job(db, run_ceiling=0)
    _job(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE jobs SET status = 'running'")
    for status in JobStatus:
        db.execute("UPDATE jobs SET status = ?", (status.value,))  # every status the code writes, the table takes
