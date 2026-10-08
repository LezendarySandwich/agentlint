"""The runner's jobs and runs in the database (docs/tech_impl/05-runner.md, "Job state").

A ``jobs`` row is a job's header and its plan, frozen as JSON when it was created. A ``runs`` row is one run
recorded. How far a job got is its runs and nothing else — the runs recorded, which comparisons are done — so no
count of progress is kept beside them (design review, "one rule per fact"). ``launches`` is a different fact: every
invocation started, including the interrupted ones and the retries that recorded nothing.

The order of writes is the engine's (``runner/engine.py``): ``launch`` (the key in flight, one more launch) commits
before the run starts, and ``record`` (the run, nothing in flight) commits after it. The database is WAL with
synchronous FULL, so every commit is on disk. A job deleted while a runner holds it — ``forget`` deletes every job —
raises JobGone at the runner's next write, which then stops.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from agentlint.store import JobStatus, StoreError, transaction
from agentlint.store._time import decode, encode

_JOB_COLUMNS = ("job_id, project, fingerprint, harness_version, model, run_ceiling, timeout_s, plan, status, launches, "
                "in_flight, runner_pid, runner_started, created, last_progress, harness")
_RUN_COLUMNS = ("job_id, run_key, session_id, outcome, trace, harness_version, model, duration_ms, cost_usd, started, "
                "finished")


class JobGone(StoreError):
    """The job is no longer in the database: forgotten, or pruned, while it was being run."""


class JobLocked(StoreError):
    """Another live process is running this job."""

    def __init__(self, job_id: str, pid: int) -> None:
        super().__init__(f"job {job_id} is being run by process {pid}; wait for it, or stop it first")
        self.job_id, self.pid = job_id, pid


@dataclass(frozen=True)
class Job:
    job_id: str
    project: str
    fingerprint: str
    harness_version: str
    model: str
    run_ceiling: int
    timeout_s: int
    plan: str  # JSON, as frozen: ``runner/plan.py`` reads it
    status: JobStatus
    launches: int
    in_flight: str | None  # the run keys launched and not yet recorded, space-separated (several with runs_at_once)
    runner_pid: int | None
    runner_started: str | None
    created: datetime
    last_progress: datetime
    harness: str | None = None  # the harness whose adapter runs it; None for a job made before schema 15

    @property
    def running(self) -> tuple[str, ...]:
        """The run keys in flight, in the order they were launched."""
        return tuple(self.in_flight.split()) if self.in_flight else ()


@dataclass(frozen=True)
class Run:
    job_id: str
    run_key: str
    session_id: str | None
    outcome: str
    trace: str  # JSON
    harness_version: str | None
    model: str | None
    duration_ms: int
    cost_usd: float | None
    started: datetime
    finished: datetime


def _job(row: sqlite3.Row) -> Job:
    return Job(row["job_id"], row["project"], row["fingerprint"], row["harness_version"], row["model"],
               row["run_ceiling"], row["timeout_s"], row["plan"], JobStatus(row["status"]), row["launches"],
               row["in_flight"], row["runner_pid"], row["runner_started"], decode(row["created"]),
               decode(row["last_progress"]), row["harness"])


def _run(row: sqlite3.Row) -> Run:
    return Run(row["job_id"], row["run_key"], row["session_id"], row["outcome"], row["trace"], row["harness_version"],
               row["model"], row["duration_ms"], row["cost_usd"], decode(row["started"]), decode(row["finished"]))


def create(db: sqlite3.Connection, *, job_id: str, project: str, fingerprint: str, harness_version: str, model: str,
           run_ceiling: int, timeout_s: int, plan: str, now: datetime, harness: str | None = None) -> Job:
    with transaction(db):
        db.execute(f"INSERT INTO jobs ({_JOB_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, NULL, ?, ?, ?)",
                   (job_id, project, fingerprint, harness_version, model, run_ceiling, timeout_s, plan,
                    JobStatus.OPEN.value, encode(now), encode(now), harness))
    job = load(db, job_id)
    assert job is not None
    return job


def load(db: sqlite3.Connection, job_id: str) -> Job | None:
    row = db.execute(f"SELECT {_JOB_COLUMNS} FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return _job(row) if row else None


def listed(db: sqlite3.Connection, project: str | None = None) -> list[Job]:
    """Every job, or a project's, the most recently created first."""
    query, args = f"SELECT {_JOB_COLUMNS} FROM jobs", ()
    if project is not None:
        query, args = query + " WHERE project = ?", (project,)
    return [_job(row) for row in db.execute(query + " ORDER BY created DESC, job_id DESC", args)]


def runs(db: sqlite3.Connection, job_id: str) -> list[Run]:
    return [_run(row) for row in db.execute(f"SELECT {_RUN_COLUMNS} FROM runs WHERE job_id = ? ORDER BY started, run_key",
                                            (job_id,))]


def recorded_keys(db: sqlite3.Connection, job_id: str) -> set[str]:
    return {row["run_key"] for row in db.execute("SELECT run_key FROM runs WHERE job_id = ?", (job_id,))}


def recorded_count(db: sqlite3.Connection, job_id: str) -> int:
    return db.execute("SELECT count(*) AS n FROM runs WHERE job_id = ?", (job_id,)).fetchone()["n"]


def launches(db: sqlite3.Connection, job_id: str) -> int:
    row = db.execute("SELECT launches FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        raise JobGone(f"job {job_id} is no longer in the database")
    return row["launches"]


def _open_job(db: sqlite3.Connection, job_id: str, statement: str, args: tuple) -> None:
    """Run an UPDATE of one open job; JobGone when there is no such open job."""
    if db.execute(statement + " WHERE job_id = ? AND status = ?", (*args, job_id, JobStatus.OPEN.value)).rowcount != 1:
        raise JobGone(f"job {job_id} is no longer open in the database")


def _without(db: sqlite3.Connection, job_id: str, key: str | None) -> str | None:
    """The job's keys in flight less ``key`` (all of them when None), as stored."""
    if key is None:
        return None
    row = db.execute("SELECT in_flight FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    left = [k for k in (row["in_flight"] or "").split() if k != key] if row else []
    return " ".join(left) or None


def launch(db: sqlite3.Connection, job_id: str, key: str, now: datetime, *, first: bool = False) -> None:
    """Step 1 of a run: the key is in flight (beside any others out at once) and one more launch is counted,
    committed before the run starts. ``first``: a runner's first launch, which replaces what an earlier runner left
    in flight — reported by then, as this runner's interruption."""
    if not key or any(c.isspace() for c in key):
        raise ValueError(f"a run key has no spaces: {key!r}")
    with transaction(db):
        row = db.execute("SELECT in_flight FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        out = [] if first or row is None else [k for k in (row["in_flight"] or "").split() if k != key]
        _open_job(db, job_id, "UPDATE jobs SET in_flight = ?, launches = launches + 1, last_progress = ?",
                  (" ".join([*out, key]), encode(now)))


def clear_in_flight(db: sqlite3.Connection, job_id: str, key: str | None = None) -> None:
    """A launch that recorded nothing and isn't an interruption — a retry, or a failure that was reported: ``key``
    is no longer in flight; every key, when None."""
    with transaction(db):
        db.execute("UPDATE jobs SET in_flight = ? WHERE job_id = ?", (_without(db, job_id, key), job_id))


def record(db: sqlite3.Connection, run: Run, *, complete: bool) -> None:
    """Step 3 of a run, in one transaction: the run, its key no longer in flight, and — for the plan's last missing
    run — the job complete. JobGone, with nothing written, when the job went meanwhile."""
    with transaction(db):
        status = JobStatus.COMPLETE if complete else JobStatus.OPEN
        _open_job(db, run.job_id, "UPDATE jobs SET in_flight = ?, last_progress = ?, status = ?",
                  (_without(db, run.job_id, run.run_key), encode(run.finished), status.value))
        db.execute(f"INSERT INTO runs ({_RUN_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (run.job_id, run.run_key, run.session_id, run.outcome, run.trace, run.harness_version, run.model,
                    run.duration_ms, run.cost_usd, encode(run.started), encode(run.finished)))


def close(db: sqlite3.Connection, job_id: str, status: JobStatus) -> None:
    """Close an open job: nothing more is ever run in it. What was in flight is dropped with it."""
    with transaction(db):
        _open_job(db, job_id, "UPDATE jobs SET status = ?, in_flight = NULL", (status.value,))


def claim(db: sqlite3.Connection, job_id: str, pid: int, started: str, stale: Callable[[int, str], bool]) -> None:
    """Take the job's lock for this process. A lock held by a process that is gone — ``stale(pid, started)``: no such
    process, or another one that reused its pid — is taken over; one held by a live process raises JobLocked."""
    with transaction(db):
        row = db.execute("SELECT runner_pid, runner_started FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobGone(f"job {job_id} is no longer in the database")
        held = row["runner_pid"]
        if held is not None and not stale(held, row["runner_started"] or ""):
            raise JobLocked(job_id, held)
        db.execute("UPDATE jobs SET runner_pid = ?, runner_started = ? WHERE job_id = ?", (pid, started, job_id))


def release(db: sqlite3.Connection, job_id: str, pid: int, started: str) -> None:
    """Give the lock back, if this process still holds it."""
    with transaction(db):
        db.execute("UPDATE jobs SET runner_pid = NULL, runner_started = NULL "
                   "WHERE job_id = ? AND runner_pid = ? AND runner_started = ?", (job_id, pid, started))


def held(db: sqlite3.Connection, stale: Callable[[int, str], bool]) -> list[Job]:
    """Every job whose lock a live process holds — ``stale(pid, started)`` false: that process, not one that reused its
    pid — whatever the job's status. What ``forget`` refuses to run under."""
    rows = db.execute(f"SELECT {_JOB_COLUMNS} FROM jobs WHERE runner_pid IS NOT NULL ORDER BY created, job_id")
    return [job for job in map(_job, rows) if not stale(job.runner_pid or 0, job.runner_started or "")]


def delete_all(db: sqlite3.Connection) -> int:
    """``forget``: every job and every run, whatever its state. How many jobs went."""
    with transaction(db):
        db.execute("DELETE FROM runs")
        return db.execute("DELETE FROM jobs").rowcount


def prune(db: sqlite3.Connection, before: datetime) -> int:
    """``keep_days``: closed jobs (complete, salvaged, stopped at its ceiling) last touched before ``before``, with
    their runs. An open job is kept whatever its age, even one that has launched its ceiling: whether it can launch
    again depends on the counter it runs under (``runner/engine.py``), which the job doesn't record — under
    ``recorded_runs`` it can — and a resume closes one that can't as ``ceiling``, reporting its interruption.
    ``--salvage`` or ``forget`` removes it too. How many rows went."""
    with transaction(db):
        stale = "SELECT job_id FROM jobs WHERE status != ? AND last_progress < ?"
        args = (JobStatus.OPEN.value, encode(before))
        removed = db.execute(f"DELETE FROM runs WHERE job_id IN ({stale})", args).rowcount
        removed += db.execute(f"DELETE FROM jobs WHERE job_id IN ({stale})", args).rowcount
    return removed
