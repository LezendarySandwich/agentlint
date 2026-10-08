"""The job engine: runs a job's frozen plan, one run at a time — or ``at_once`` (runs_at_once), each in a thread of its
own while the engine alone writes the database — so that a kill at any moment loses at most the runs out.

Core and harness-agnostic. Running one situation is an Executor's job — a harness's adapter supplies the real one,
``runner/fake.py`` a fake — and the engine owns everything else (05-runner.md):

- **The plan is frozen at creation and replayed verbatim.** ``run_job`` starts a job and resumes one the same way:
  it skips every key already recorded and runs the rest in the plan's order.
- **Write order, per run:** ``launch`` (the key in flight, one more launch) commits; the executor runs; ``record``
  (the run, nothing in flight) commits. A kill in between leaves the key in flight: on resume it is reported —
  "1 run was interrupted" — and run again, since it is the first key not recorded.
- **Resume refuses another environment**, raising rather than warning: EnvironmentChanged when the harness version
  or model differs from the job's, ConfigChanged when only the fingerprint does. The real executor pins the job's
  harness version on every launch (D5), so ``run_test`` passes the job's own version, and a fingerprint computed with
  it: for it only the model or the configuration can differ. ``salvage`` closes a job that can't
  be extended; nothing is ever appended to it.
- **One runner per job:** a lock of pid and process start time (``runner/lock.py``); a dead runner's is taken over.
- **The run ceiling** is checked before every launch against a Counter. By default it counts recorded runs — runs
  with an outcome — so a ceiling of N asks for N results (decision D8, 2026-10-04); ``launches`` counts every
  invocation instead and remains one argument away. Retries are bounded on their own (below). A counter only ever
  grows and nothing raises a job's ceiling, so a job that reaches it is closed as ``ceiling``: it could never launch
  again, and an open job is one that can.
- **A retry** is an executor saying a launch failed for reasons outside the agent (raising Retry): the launch counts
  in ``launches``, no run is recorded, and the same key is tried again after a backoff. Two caps bound them apart
  from the ceiling: one key is retried at most ``max_retries`` (3) times in a row, and one resume launches at most
  ``retry_cap`` runs that record nothing (by default a tenth of the ceiling, at least ``max_retries + 1``, so one key
  alone always reaches its own limit first). Either cap reached pauses the job: RetriesExhausted, a Paused, and the job stays open to resume. Which failures are retries is the
  executor's call (D7: API errors, for a harness that records them).
- **A pause** is any Paused an executor raises (an authentication failure, say): nothing is recorded, nothing is left
  in flight, and the job stays open.
- **An interruption is reported exactly once**: in the Report the resume returns, or — when the resume ends in an
  exception instead (RetriesExhausted, an executor's error, a forgotten job, Ctrl-C) — on that exception, as its
  ``interrupted`` attribute and a note (``interrupted_before``). Either way the key is no longer in flight after, so
  the next resume doesn't report it again; a key Ctrl-C leaves in flight is a new interruption, the next resume's.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

from agentlint.runner import gate, lock
from agentlint.runner.plan import Comparison, Plan, PlannedRun
from agentlint.runner.status import ComparisonStatus, statuses
from agentlint.store import JobStatus, jobs
from agentlint.trace.terms import Terms


class ConfigChanged(Exception):
    """The configuration's fingerprint differs from the job's: its results can't be extended."""


class EnvironmentChanged(Exception):
    """The harness version or the model differs from the job's: its results can't be extended."""


class JobClosed(Exception):
    """The job is complete, salvaged or stopped at its run ceiling: nothing more runs in it."""


class JobNotFound(Exception):
    pass


class Paused(Exception):
    """The job stops for now and stays open: something outside the agent must change before it can go on (retries
    used up, an authentication failure). Resume it later."""

    interrupted: tuple[str, ...] = ()  # set by run_job when the resume this ends had found a run interrupted


class RetriesExhausted(Paused):
    """One key failed for reasons outside the agent more than ``max_retries`` times in a row, or one resume reached
    its cap on launches that recorded nothing. The job stays open."""

    interrupted: tuple[str, ...] = ()  # set by run_job when the resume this ends had found a run interrupted


class Retry(Exception):
    """Raised by an executor: this launch failed for reasons outside the agent, and recorded nothing."""


@dataclass(frozen=True)
class Environment:
    """What a job's results are comparable under. The fingerprint already covers the other two; they are kept
    apart so that a change of harness or model is named as such."""

    fingerprint: str
    harness_version: str
    model: str

    def __post_init__(self) -> None:
        if not (self.fingerprint and self.harness_version and self.model):
            raise ValueError("a job needs a known fingerprint, harness version and model: drift couldn't be seen")


@dataclass(frozen=True)
class RunRequest:
    job_id: str
    project: str
    model: str
    timeout_s: int
    run: PlannedRun
    harness_version: str | None = None  # the job's pin: every launch runs this version (D5)
    removed_tools: tuple[str, ...] = ()  # the job's frozen list: offered to no run (D6)


@dataclass(frozen=True)
class RunResult:
    outcome: str
    trace: Any  # JSON-serialisable: the raw trace
    session_id: str | None = None
    harness_version: str | None = None  # as observed in the run
    model: str | None = None
    cost_usd: float | None = None


class Executor(Protocol):
    def run(self, request: RunRequest) -> RunResult:
        """Run one situation once. Raise Retry for a failure outside the agent; return every other ending."""
        ...


Counter = Callable[[sqlite3.Connection, str], int]


def launches(db: sqlite3.Connection, job_id: str) -> int:
    """Every invocation started, retries and interrupted runs included: the alternative counter."""
    return jobs.launches(db, job_id)


def recorded_runs(db: sqlite3.Connection, job_id: str) -> int:
    """Only runs that recorded an outcome: the default ceiling counter (D8). Retries and interrupted runs launch
    beyond the ceiling, bounded by the retry caps instead."""
    return jobs.recorded_count(db, job_id)


def default_retry_cap(run_ceiling: int, max_retries: int = 3) -> int:
    """How many launches that record nothing one resume allows by default: a tenth of the ceiling, and never fewer than
    one key's own ``max_retries + 1`` attempts — so a single key's retries always reach their own limit first."""
    return max(max_retries + 1, run_ceiling // 10)


class Stopped(Enum):
    COMPLETE = "complete"  # every run of the plan is recorded
    CEILING = "ceiling"  # the run ceiling was reached first


@dataclass(frozen=True)
class Report:
    job_id: str
    stopped: Stopped
    interrupted: tuple[str, ...]  # the run in flight when the last runner died: run again now, unless the ceiling
    not_rerun: tuple[str, ...]  # those of ``interrupted`` the ceiling stopped from running again
    launched: int  # this time
    recorded: int  # this time
    retried: int  # this time: launches that recorded nothing and were tried again
    remaining: int  # runs of the plan still unrecorded

    def messages(self) -> list[str]:
        said = []
        if self.interrupted:
            said.append(_interrupted_message(self.interrupted))
            if self.not_rerun:
                said[-1] += " and not run again: the run ceiling was reached"
        if self.stopped is Stopped.CEILING:
            said.append(f"stopped at the run ceiling with {self.remaining} "
                        f"{'run' if self.remaining == 1 else 'runs'} of the plan not run")
        return said


def _interrupted_message(interrupted: tuple[str, ...]) -> str:
    n = len(interrupted)
    return f"{n} run was interrupted" if n == 1 else f"{n} runs were interrupted"


def interrupted_before(error: BaseException) -> tuple[str, ...]:
    """The runs a resume found interrupted, when that resume ended in ``error`` rather than a Report: they were
    reported only here, and the next resume won't report them again."""
    return tuple(getattr(error, "interrupted", ()) or ())


def _carry(error: BaseException, interrupted: tuple[str, ...]) -> None:
    """Hang the interruption a resume found on the exception ending it, the one place left to report it."""
    if not interrupted:
        return
    try:
        error.interrupted = interrupted  # type: ignore[attr-defined]
    except (AttributeError, TypeError):  # an exception that takes no attributes still gets the note
        pass
    error.add_note(f"{_interrupted_message(interrupted)} before this resume: {', '.join(interrupted)}")


def _now() -> datetime:
    return datetime.now(UTC)


def backoff(attempt: int) -> float:
    """Seconds to wait before attempt ``attempt + 1``: 30 s, doubling, at most 15 minutes."""
    return min(30.0 * 2 ** (attempt - 1), 900.0)


def new_job_id(now: datetime) -> str:
    return f"{now.astimezone(UTC).strftime('%Y-%m-%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"


def create_job(db: sqlite3.Connection, *, project: str, environment: Environment, plan: Plan, run_ceiling: int,
               timeout_s: int, now: datetime | None = None, job_id: str | None = None,
               harness: str | None = None) -> str:
    """Freeze the plan into a new open job; nothing runs yet. Its id. ``harness``: the name of the harness whose adapter
    runs it, for a worker that resumes it (``runner/worker.py``)."""
    if run_ceiling < 1 or timeout_s < 1:
        raise ValueError("a job needs a run ceiling and a timeout of at least 1")
    at = now or _now()
    job = jobs.create(db, job_id=job_id or new_job_id(at), project=project, fingerprint=environment.fingerprint,
                      harness_version=environment.harness_version, model=environment.model, run_ceiling=run_ceiling,
                      timeout_s=timeout_s, plan=plan.to_json(), now=at, harness=harness)
    return job.job_id


def _open(db: sqlite3.Connection, job_id: str) -> jobs.Job:
    job = jobs.load(db, job_id)
    if job is None:
        raise JobNotFound(f"no job {job_id}")
    if job.status is not JobStatus.OPEN:
        said = {JobStatus.COMPLETE: "complete", JobStatus.SALVAGED: "salvaged",
                JobStatus.CEILING: "stopped at its run ceiling"}[job.status]
        raise JobClosed(f"job {job_id} is {said}: nothing more runs in it")
    return job


def check_environment(job: jobs.Job, current: Environment) -> None:
    """Raise, never warn, when the job's results couldn't be extended under ``current``."""
    if (current.harness_version, current.model) != (job.harness_version, job.model):
        raise EnvironmentChanged(
            f"job {job.job_id} ran under harness {job.harness_version} with model {job.model}; now it would be "
            f"harness {current.harness_version} with model {current.model}. Its results can't be extended: start a "
            "new job, or salvage this one to analyse what completed.")
    if current.fingerprint != job.fingerprint:
        raise ConfigChanged(
            f"the configuration changed since job {job.job_id} started; its results can't be extended. Start a new "
            "job, or salvage this one to analyse what completed.")


def run_job(db: sqlite3.Connection, job_id: str, executor: Executor, *, environment: Environment,
            counter: Counter = recorded_runs, max_retries: int = 3, retry_cap: int | None = None, wait: Callable[[float], None] = time.sleep,
            now: Callable[[], datetime] = _now, process: lock.Process | None = None,
            stale: Callable[[int, str], bool] = lock.stale, prepare: Callable[[], None] = lambda: None,
            finish: Callable[[], None] = lambda: None, terms: Terms | None = None, at_once: int = 1) -> Report:
    """Start or resume a job: run every key of its frozen plan not yet recorded, in order, until the plan is done or
    the ceiling is reached. A comparison with a gate (D9) runs only if its gate opens when the job reaches it — its
    situation's stability runs, recorded earlier in this job, agreed (``runner/gate.py``); one whose gate is closed
    spends no runs, and the job is complete once nothing it may run is left. ``terms``, the adapter's action terms,
    let the gate also check a situation's runs did what its what matters names (2026-10-07). ``prepare`` runs once, holding the job's lock, just before the first launch (an executor's
    checks and cleaning that must not race another runner of the job); what it raises stops the resume, nothing
    launched. ``finish`` runs once the job's lock was taken, still holding it, however the resume ends (an executor
    waiting for its deletions, so no other runner sweeps what it is still deleting); what it raises never hides what
    is propagating."""
    job = _open(db, job_id)
    check_environment(job, environment)
    plan = Plan.from_json(job.plan)
    cap = default_retry_cap(job.run_ceiling, max_retries) if retry_cap is None else retry_cap
    me = process or lock.current()
    jobs.claim(db, job_id, me.pid, me.started, stale)
    try:
        job = _open(db, job_id)  # as the lock found it: a runner that died since the first read left its key
        interrupted = job.running
        try:
            done = jobs.recorded_keys(db, job_id)
            launched = recorded = retried = 0
            prepared = False
            stopped = Stopped.COMPLETE
            attempts: dict[str, int] = {}
            not_before: dict[str, float] = {}  # a retried key's backoff, when others run meanwhile (at_once > 1)
            running: dict[Future[RunResult], tuple[PlannedRun, datetime, float]] = {}
            pool = ThreadPoolExecutor(max_workers=at_once, thread_name_prefix="agentlint-run") if at_once > 1 else None
            try:
                while True:
                    at_ceiling = False
                    while len(running) < at_once:
                        clock_now = time.monotonic()
                        planned = next((p for p in _to_run(db, job_id, plan, done, job.fingerprint, terms)
                                        if p.key not in {r.key for r, _, _ in running.values()}
                                        and not_before.get(p.key, 0.0) <= clock_now), None)
                        if planned is None:
                            break
                        if counter(db, job_id) + len(running) >= job.run_ceiling:
                            at_ceiling = True
                            break
                        if not prepared:
                            try:
                                prepare()
                            except BaseException:
                                if interrupted:  # carried on the exception, once: not left in flight to be found again
                                    jobs.clear_in_flight(db, job_id)
                                raise
                            prepared = True
                        jobs.launch(db, job_id, planned.key, now(), first=launched == 0)
                        launched += 1
                        attempts[planned.key] = attempts.get(planned.key, 0) + 1
                        request = RunRequest(job_id, job.project, job.model, job.timeout_s, planned, job.harness_version,
                                             plan.removed_tools)
                        started, clock = now(), time.monotonic()
                        future: Future[RunResult] = Future()
                        if pool is None:  # one at a time: in this thread, so Ctrl-C reaches the run itself
                            try:
                                future.set_result(executor.run(request))
                            except Exception as failure:  # noqa: BLE001 - handled below, as a thread's would be
                                future.set_exception(failure)
                        else:
                            future = pool.submit(executor.run, request)
                        running[future] = (planned, started, clock)
                    if not running:
                        if at_ceiling:
                            # Closed, which drops what is in flight: it is reported once, in this report, and never again.
                            jobs.close(db, job_id, JobStatus.CEILING)
                            stopped = Stopped.CEILING
                            break
                        backing_off = [t for k, t in not_before.items() if k not in done]
                        if backing_off and any(True for _ in _to_run(db, job_id, plan, done, job.fingerprint, terms)):
                            wait(max(0.0, min(backing_off) - time.monotonic()))
                            continue
                        break  # nothing it may run is left: every key recorded, or gated off
                    finished, _ = futures_wait(running, return_when=FIRST_COMPLETED,
                                               timeout=_until(not_before, done) if len(running) < at_once else None)
                    for future in finished:
                        planned, started, clock = running.pop(future)
                        try:
                            result = future.result()
                        except Retry as failure:
                            jobs.clear_in_flight(db, job_id, planned.key)
                            retried += 1
                            attempt = attempts[planned.key]
                            if attempt > max_retries:
                                raise RetriesExhausted(f"{planned.key} failed outside the agent {attempt} times in a row "
                                                       f"({failure}); the job is paused and kept: resume it later") from None
                            if retried >= cap:
                                raise RetriesExhausted(f"{retried} launches recorded nothing in this resume, the most it "
                                                       f"allows ({failure}); the job is paused and kept: resume it "
                                                       "later") from None
                            if pool is None:
                                wait(backoff(attempt))  # then the same key again: it is still the first to run
                            else:
                                not_before[planned.key] = time.monotonic() + backoff(attempt)
                            continue
                        except Exception:
                            jobs.clear_in_flight(db, job_id, planned.key)  # reported now, as it propagates: not an
                            raise  # interruption
                        duration_ms = max(0, math.floor((time.monotonic() - clock) * 1000))
                        jobs.record(db, jobs.Run(job_id, planned.key, result.session_id, result.outcome,
                                                 json.dumps(result.trace, sort_keys=True), result.harness_version,
                                                 result.model, duration_ms, result.cost_usd, started, now()),
                                    complete=False)
                        done.add(planned.key)
                        recorded += 1
                if stopped is Stopped.COMPLETE:  # nothing it may run is left: every key recorded, or gated off
                    jobs.close(db, job_id, JobStatus.COMPLETE)
            finally:
                if running:  # ended by an exception with runs still out: stop them, and wait for their threads
                    stop = getattr(executor, "interrupt", None)
                    if stop is not None:
                        stop()
                    futures_wait(running)
                if pool is not None:
                    pool.shutdown(wait=True)
        except BaseException as error:
            # No Report will carry the interruption, and nothing leaves it in flight: the key re-ran, or the
            # executor's ending, a retry's included, cleared it. So Ctrl-C too — in a retry's backoff nothing is in
            # flight at all — carries it: this is the last place it can be reported.
            _carry(error, interrupted)
            raise
    finally:
        try:
            finish()
        except Exception:  # noqa: BLE001 - never hide what is propagating; the lock is released below regardless
            pass
        try:
            jobs.release(db, job_id, me.pid, me.started)
        except sqlite3.Error:
            pass  # never hide what is propagating
    done = jobs.recorded_keys(db, job_id)
    not_rerun = tuple(key for key in interrupted if key not in done)
    return Report(job_id, stopped, interrupted, not_rerun, launched, recorded, retried,
                  _remaining(db, job_id, plan, done, job.fingerprint, terms))


def _until(not_before: Mapping[str, float], done: set[str]) -> float | None:
    """How long until the soonest retried key's backoff ends (a free slot can take it then); None with none waiting."""
    waiting = [t for key, t in not_before.items() if key not in done]
    return max(0.0, min(waiting) - time.monotonic()) if waiting else None


def _gate_open(db: sqlite3.Connection, job_id: str, plan: Plan, comparison: Comparison, fingerprint: str,
               terms: Terms | None) -> bool | None:
    rows = gate.by_comparison(jobs.runs(db, job_id)).get(comparison.gate or "", [])
    return gate.gate_open(plan.comparison(comparison.gate or ""), rows, fingerprint, terms)


def _to_run(db: sqlite3.Connection, job_id: str, plan: Plan, done: set[str], fingerprint: str,
            terms: Terms | None = None) -> Iterator[PlannedRun]:
    """The keys the job runs, in order: every one not yet recorded, a gated comparison's only once its gate opens —
    decided then, from the runs recorded by then (its stability comparison comes first, so they are all there)."""
    for comparison in plan.comparisons:
        pending = [run for run in comparison.planned() if run.key not in done]
        if not pending:
            continue
        if comparison.gate is not None and not _gate_open(db, job_id, plan, comparison, fingerprint, terms):
            continue  # its situation's stability runs didn't agree: it spends no runs
        yield from pending


def _remaining(db: sqlite3.Connection, job_id: str, plan: Plan, done: set[str], fingerprint: str,
               terms: Terms | None = None) -> int:
    """Keys the job may still run: not recorded, and not of a comparison whose gate is closed."""
    shut = gate.closed(plan, gate.by_comparison(jobs.runs(db, job_id)), fingerprint, terms)
    return sum(run.key not in done for c in plan.comparisons if c.id not in shut for run in c.planned())


def salvage(db: sqlite3.Connection, job_id: str, *, process: lock.Process | None = None,
            stale: Callable[[int, str], bool] = lock.stale) -> list[ComparisonStatus]:
    """Close a job that can't be extended — its configuration or environment changed, or it is simply finished
    with — keeping what completed. Never appends a run. Refused while a live runner holds it."""
    job = _open(db, job_id)
    me = process or lock.current()
    jobs.claim(db, job_id, me.pid, me.started, stale)
    try:
        jobs.close(db, job_id, JobStatus.SALVAGED)
    finally:
        jobs.release(db, job_id, me.pid, me.started)
    plan = Plan.from_json(job.plan)
    return statuses(plan, jobs.recorded_keys(db, job_id), gate.closed(plan, gate.by_comparison(jobs.runs(db, job_id)),
                                                                      job.fingerprint))


def job_statuses(db: sqlite3.Connection, job_id: str) -> list[ComparisonStatus]:
    job = jobs.load(db, job_id)
    if job is None:
        raise JobNotFound(f"no job {job_id}")
    plan = Plan.from_json(job.plan)
    return statuses(plan, jobs.recorded_keys(db, job_id), gate.closed(plan, gate.by_comparison(jobs.runs(db, job_id)),
                                                                      job.fingerprint))
