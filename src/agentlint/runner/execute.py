"""Running a job for real: each run in a fresh variant home and an empty working directory under agentlint's home,
handed to a harness's launcher (docs/tech_impl/05-runner.md "The executor").

Core and harness-agnostic. The engine (``engine.py``) owns the job — its frozen plan, write order, lock, ceiling and
retries; the adapter owns the harness — its command line, how its trace is read and which failures are outside the
agent (``Launcher``). This module joins them, per run:

1. **A fresh run directory** at ``AGENTLINT_HOME/runs.noindex/<job>/<run key>/{home,work}`` (D2): the variant home built with
   ``build_variant`` — the baseline, or with the run's target cut out — and the empty working directory with the
   project's configuration placed in it (D1).
2. **A without-variant is verified** against the baseline reading before it is run (``verify``: AblationFailed).
3. **The launcher runs the situation** with that home and directory, the job's model, the harness version the job pins
   (D5: every launch, resumes included, runs that version) and a time limit, and reads the run's trace. It raises
   ``Retry`` for a failure outside the agent (D7: an API error), ``Paused`` for one that needs you (an authentication
   failure), and returns every other ending as an outcome — a timeout included. The version and model each run
   recorded are stored with it; nothing stops the job on them (the analysis refuses distributions that mix them).
4. **The run directory is deleted in the background** once the trace is read: renamed into the job's trash at once
   (a rename, so the next run's fresh directory never meets it) and removed by a worker thread, since deleting a home
   takes 24-45 s here against 1-5 s to build one.

Once per runner, holding the job's lock and before its first launch (``prepare``): any harness a killed runner left
running is stopped (from the ``harness.json`` its launch recorded), the leftovers in the job's directory are swept
into the trash, and **the baseline check** (D4) builds a baseline variant and compares
its reading with the real configuration's — BaselineMismatch, listing every difference, stops the job before any run.
A resume that closes the job before any launch (at its ceiling: a killed runner had used the last launch) stops and
sweeps the same way as it ends, still holding the lock (``finish``), and reports what it found in ``Tested``.

As ``run_test`` starts, **every other job's leftovers** are stopped and swept the same way (``clean_runs``): a job
complete, stopped at its ceiling or salvaged is never resumed, and a forgotten or pruned one is no longer in the
database, so a deletion that failed on its last run (a signal during the final wait) would otherwise stay. A job a
live runner holds is left to it. ``forget_runs`` and ``salvage`` do the same for what they close or delete.

``run_test`` is the entry point: no command calls it yet (``agentlint test`` waits on the generator and the CLI/UI
review).
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol, TypeVar

from agentlint import settings, store
from agentlint.store import JobStatus, jobs, usage
from agentlint.config.assemble import fingerprint
from agentlint.config.model import ConfigSnapshot
from agentlint.config.reading import Reading
from agentlint.harness import ToolVerdict, VariantHomes, read_config
from agentlint.rank.plan import AblationTarget, targets
from agentlint.runner import engine, lock
from agentlint.runner.engine import Environment, JobNotFound, Report, RunRequest, RunResult
from agentlint.runner.plan import Plan
from agentlint.runner.status import ComparisonStatus
from agentlint.runner.variant import build_variant, guard, remove_home
from agentlint.runner.verify import BaselineChecked, VariantReading, check_baseline, read_real, read_variant, verify
from agentlint.trace.model import Trace

TRASH = ".trash"
CHECK = "baseline-check"
RUNNING = "harness.json"  # in a run's directory while its harness runs: pid, process group, start time
HOME, WORK = "home", "work"  # in a run's directory: its variant home (HOME while it runs) and its working directory
T = TypeVar("T")
STOP_GRACE_S = 10  # a left-running harness: SIGTERM to its group, then SIGKILL this long after


@dataclass(frozen=True)
class Launch:
    """One run for a launcher: the situation, the run's home and working directory, the model, the time limit, and the
    harness version the job pins (D5)."""

    situation: str
    home: Path
    workdir: Path
    model: str
    timeout_s: int
    record: Path | None = None  # where the launcher records the running harness (``record_running``)
    harness_version: str | None = None  # the job's: the launcher runs exactly this version of the harness
    removed_tools: tuple[str, ...] = ()  # the job's: tools the run is never offered (D6), by the harness's names


@dataclass(frozen=True)
class Launched:
    """How one run ended, as the launcher saw it: an outcome (never an infrastructure failure: those raise), the trace
    read from the harness's own record of the run (None only when there is none to read, as after a timeout before the
    harness wrote anything), and what the harness reported it cost."""

    outcome: str
    session_id: str
    trace: Trace | None
    cost_usd: float | None = None


class Launcher(Protocol):
    """A harness's way of running one situation (the adapter supplies it)."""

    def launch(self, launch: Launch) -> Launched:
        """Run ``launch.situation`` once with ``launch.home`` as HOME in ``launch.workdir``, with ``launch.model`` and
        the harness version ``launch.harness_version``, without the tools ``launch.removed_tools`` (not offered to
        the model at all), at most ``launch.timeout_s`` seconds, and read its trace. Raise ``engine.Retry`` for a failure outside the agent and
        ``engine.Paused`` for one only you can fix; return every other ending. The harness runs in a process group of
        its own, recorded with ``record_running(launch.record, pid)`` as soon as it starts, so a runner killed
        meanwhile leaves the next one a way to stop it."""
        ...


def record_running(record: Path | None, pid: int) -> None:
    """Write down a just-started harness — its pid, process group and start time (so a reused pid is never taken for
    it) — at ``record``, for ``stop_left_running`` to find if this runner dies before stopping it itself."""
    if record is None:
        return
    started = lock.process_start(pid)
    if started is None:  # already gone
        return
    try:
        group = os.getpgid(pid)
    except ProcessLookupError:
        return
    written = record.with_name(record.name + ".tmp")
    written.write_text(json.dumps({"pid": pid, "group": group, "started": started}))
    os.replace(written, record)


def _signal_group(group: int, sig: int) -> None:
    try:
        os.killpg(group, sig)
    except (ProcessLookupError, PermissionError):
        pass


def stop_left_running(directory: Path, grace_s: float = STOP_GRACE_S) -> int:
    """Stop every harness a killed runner left running in ``directory``'s run directories (and its trash): each one
    whose recorded process is still the same live process gets SIGTERM to its whole group, then SIGKILL after
    ``grace_s``. Returns how many were found running. A record whose process is gone, or whose pid now belongs to
    another process (another start time), is left alone."""
    records = sorted(directory.glob(f"*/{RUNNING}")) + sorted((directory / TRASH).glob(f"*/{RUNNING}"))
    alive: list[tuple[int, int, str]] = []
    for record in records:
        try:
            data = json.loads(record.read_text())
            pid, group, started = int(data["pid"]), int(data["group"]), str(data["started"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if group > 1 and lock.process_start(pid) == started:
            alive.append((pid, group, started))
            _signal_group(group, signal.SIGTERM)
    deadline = time.monotonic() + grace_s
    waiting = list(alive)
    while waiting and time.monotonic() < deadline:
        time.sleep(0.1)
        waiting = [entry for entry in waiting if lock.process_start(entry[0]) == entry[2]]
    for _, group, _ in alive:  # what each group still holds — its leader, or a hook it started
        _signal_group(group, signal.SIGKILL)
    return len(alive)


class Terminated(BaseException):
    """The runner was asked to stop (SIGTERM, SIGHUP): raised where it was, like Ctrl-C, so the run in progress is
    stopped by the launcher on the way out instead of left running."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"stopped by {signal.Signals(signum).name}")
        self.signum = signum


@contextlib.contextmanager
def stopping_on_signals() -> Iterator[None]:
    """SIGTERM and SIGHUP raise Terminated while inside (main thread only: Python delivers signals there). A SIGKILL
    can't be caught: what it leaves running is stopped by the next runner's ``stop_left_running``."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def raise_terminated(signum: int, frame: object) -> None:
        raise Terminated(signum)

    previous = {sig: signal.signal(sig, raise_terminated) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


RUNS = "runs.noindex"  # macOS's Spotlight leaves a .noindex directory alone: every run builds a whole variant home
LEGACY_RUNS = "runs"  # where runs lived until 2026-10-07: still swept, and still how older jobs' traces spell paths


def runs_root() -> Path:
    """Where run directories live: under agentlint's home, resolved (the harness records a working directory by its
    real path, and keeps its memory under that path's slug)."""
    return Path(os.path.realpath(store.home())) / RUNS


def legacy_runs_root() -> Path:
    return Path(os.path.realpath(store.home())) / LEGACY_RUNS


def run_dir_name(key: str) -> str:
    """A run key as a directory name: ``cmp-0001:baseline:0`` -> ``cmp-0001.baseline.0`` (no ':' in a path)."""
    return key.replace(":", ".")


class Disposal:
    """Deletes run directories off the run's path: renamed into a trash directory at once, removed by one worker."""

    def __init__(self, trash: Path) -> None:
        self.trash = trash
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agentlint-disposal")
        self._pending: list[Future[None]] = []
        self._lock = threading.Lock()
        self.failures: list[str] = []  # directories a deletion couldn't remove: swept by the next clean_runs

    def discard(self, path: Path) -> None:
        if not os.path.lexists(path):
            return
        store.private_dirs(self.trash)  # runs/, the job's directory and its trash: each made 0700
        moved = guard(self.trash / uuid.uuid4().hex, self.trash)
        os.rename(guard(path, path.parent), moved)
        self._submit(moved)

    def sweep(self, directory: Path, keep: frozenset[str] = frozenset()) -> int:
        """Discard everything in ``directory`` (the leftovers of a killed run), and resubmit what its trash holds."""
        if not directory.is_dir():
            return 0
        swept = 0
        if self.trash.is_dir():  # what a killed runner had discarded and not yet deleted
            for child in sorted(self.trash.iterdir()):
                self._submit(child)
                swept += 1
        for child in sorted(directory.iterdir()):
            if child.name in keep or child == self.trash:
                continue
            self.discard(child)
            swept += 1
        return swept

    def _submit(self, path: Path) -> None:
        def remove() -> None:
            try:
                remove_home(path)
            except Exception as error:  # noqa: BLE001 - kept to report; the next clean_runs sweeps it again
                if not os.path.lexists(path):  # deleted meanwhile by another sweep of the same trash
                    return
                with self._lock:
                    self.failures.append(f"{path}: {error!r}")

        with self._lock:
            self._pending.append(self._pool.submit(remove))

    def close(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)


def _holds_anything(directory: Path) -> bool:
    """Whether a job's run directory holds more than an empty trash."""
    for child in directory.iterdir():
        if child.name != TRASH or not child.is_dir() or child.is_symlink() or any(child.iterdir()):
            return True
    return False


def _remove_empty(directory: Path) -> None:
    """Remove a closed or forgotten job's run directory once nothing is left in it but an empty trash."""
    for empty in (directory / TRASH, directory):
        with contextlib.suppress(OSError):
            os.rmdir(guard(empty, directory))


@dataclass
class Cleaning:
    """What ``clean_runs`` started: the harness runs it stopped, the leftovers it is deleting in the background."""

    stopped: int = 0
    swept: int = 0
    _started: list[tuple[Path, Disposal]] = field(default_factory=list)

    def close(self, db: sqlite3.Connection, wait: bool = True) -> tuple[str, ...]:
        """Wait for the deletions (unless ``wait`` is false), then remove each cleaned directory whose job is now
        closed or gone. Returns what a deletion failed on (the next ``clean_runs`` sweeps it again)."""
        failures: list[str] = []
        for directory, disposal in self._started:
            disposal.close(wait)
            failures.extend(disposal.failures)
            job = jobs.load(db, directory.name)
            if wait and (job is None or job.status is not JobStatus.OPEN):
                _remove_empty(directory)
        return tuple(failures)


def clean_runs(db: sqlite3.Connection, *, root: Path | None = None, only: frozenset[str] | None = None,
               skip: frozenset[str] = frozenset(), process: lock.Process | None = None,
               stale: Callable[[int, str], bool] = lock.stale) -> Cleaning:
    """Stop and sweep what a run left in the run directory of every job no live runner holds (or of the jobs in
    ``only``), leaving out ``skip``: a job complete, stopped at its ceiling or salvaged can't be resumed, and a job
    forgotten or pruned is no longer in the database, so no resume of theirs would ever sweep it. Each job still in the
    database is claimed first — a job a live runner holds (JobLocked) is left to it, since it deletes its own
    directories — and released once its leftovers are in its trash; a directory holding nothing but an empty trash is
    not claimed at all. Deletion runs in the background: ``Cleaning.close`` waits for it. Called as ``run_test``
    starts, and by ``forget_runs`` and ``salvage``. With no ``root``, both where runs live and where they lived
    before (``LEGACY_RUNS``)."""
    cleaning = Cleaning()
    roots = [root] if root is not None else [runs_root(), legacy_runs_root()]
    me: lock.Process | None = process
    for directory in sorted(d for each in roots if each.is_dir() for d in each.iterdir()):
        name = directory.name
        if name in skip or (only is not None and name not in only) or directory.is_symlink() or not directory.is_dir():
            continue
        job = jobs.load(db, name)
        if not _holds_anything(directory):
            if job is None or job.status is not JobStatus.OPEN:
                _remove_empty(directory)
            continue
        held = False
        if job is not None:
            me = me or lock.current()
            try:
                jobs.claim(db, name, me.pid, me.started, stale)
                held = True
            except jobs.JobLocked:
                continue
            except jobs.JobGone:
                pass
        try:
            cleaning.stopped += stop_left_running(directory)
            disposal = Disposal(directory / TRASH)
            cleaning._started.append((directory, disposal))
            cleaning.swept += disposal.sweep(directory)
        finally:
            if held and me is not None:
                jobs.release(db, name, me.pid, me.started)
    return cleaning


def forget_runs(db: sqlite3.Connection, forget: Callable[[], T], *,
                stale: Callable[[int, str], bool] = lock.stale) -> tuple[T, tuple[str, ...]]:
    """``forget`` with the test runs' directories: what every job not held by a live runner left in
    ``AGENTLINT_HOME/runs.noindex`` (and ``runs``, before 2026-10-07: whole variant homes, each run's transcript among them) stopped and swept while the jobs'
    locks still exist, then ``forget`` (which deletes the jobs), then the deletions waited for. A live runner's job
    stops at its next write (JobGone) and deletes its own directories; what it leaves is swept by the next
    ``clean_runs``. Returns what ``forget`` returned and what a deletion failed on. ``forget.forget_everything`` —
    the one forget the CLI and the UI share — calls it, once it has refused if a runner is alive."""
    cleaning = clean_runs(db, stale=stale)
    try:
        forgotten = forget()
    finally:
        undeleted = cleaning.close(db, wait=True)
    return forgotten, undeleted


def salvage(db: sqlite3.Connection, job_id: str, *, process: lock.Process | None = None,
            stale: Callable[[int, str], bool] = lock.stale) -> tuple[list[ComparisonStatus], tuple[str, ...]]:
    """``engine.salvage`` — close the job, keeping what completed — then stop and delete what its runs left in
    ``runs/<job>/`` (a salvaged job is never resumed, so no resume would). Returns each comparison's status and what a
    deletion failed on."""
    statuses = engine.salvage(db, job_id, process=process, stale=stale)
    cleaning = clean_runs(db, only=frozenset({job_id}), process=process, stale=stale)
    return statuses, cleaning.close(db, wait=True)


class VariantExecutor:
    """The engine's Executor for real runs: a fresh variant home and working directory per run, a launcher to run
    the situation in them under the job's model and pinned harness version, the directories deleted in the
    background."""

    def __init__(self, adapter: VariantHomes, launcher: Launcher, *, project: Path, job_id: str,
                 targets_by_id: dict[str, AblationTarget], root: Path | None = None) -> None:
        self.adapter, self.launcher, self.project = adapter, launcher, Path(project)
        self.job_dir = (root or runs_root()) / job_id
        self.disposal = Disposal(self.job_dir / TRASH)
        self.targets = targets_by_id
        self.baseline: VariantReading | None = None
        self.checked: BaselineChecked | None = None
        self.swept = 0
        self.stopped = 0
        self.cleaned = False  # whether this runner stopped and swept what earlier runners of the job left

    def clean(self) -> None:
        """Holding the job's lock: stop any harness a killed runner left running, and sweep what it left."""
        if self.job_dir.is_dir():
            self.stopped += stop_left_running(self.job_dir)
        self.swept += self.disposal.sweep(self.job_dir)
        self.cleaned = True

    def prepare(self) -> None:
        """Before the job's first launch in this runner, holding its lock: stop any harness a killed runner left
        running, sweep what it left, then the baseline check (D4)."""
        self.clean()
        place = self.job_dir / CHECK
        variant = build_variant(self.adapter, place / "home", None, project=self.project, workdir=place / "work")
        try:
            reading = read_variant(self.adapter, place / "home", place / "work", self.project, variant.path_pairs())
            unplaced = self.adapter.unplaced(self.project, place / "work")
        finally:
            self.disposal.discard(place)
        self.checked = check_baseline(read_real(self.adapter, self.project), reading, unplaced)
        self.baseline = reading

    def run(self, request: RunRequest) -> RunResult:
        planned = request.run
        place = self.job_dir / run_dir_name(planned.key)
        home, work = place / HOME, place / WORK
        target = None
        if planned.variant != "baseline":
            target = self.targets.get(planned.comparison.target or "")
            if target is None:
                raise LookupError(f"{planned.key}: its target {planned.comparison.target} is not in the configuration")
        if os.path.lexists(place):  # an attempt of this key this runner made before: never reused
            self.disposal.discard(place)
        variant = build_variant(self.adapter, home, target, project=self.project, workdir=work)
        try:
            if target is not None:
                if self.baseline is None:
                    raise RuntimeError("the baseline check must run before a variant is verified")
                verify(target, self.baseline, read_variant(self.adapter, home, work, self.project,
                                                           variant.path_pairs()))
            launched = self.launcher.launch(Launch(planned.comparison.text, home, work, request.model,
                                                   request.timeout_s, place / RUNNING, request.harness_version,
                                                   request.removed_tools))
        finally:
            self.disposal.discard(place)
        trace = launched.trace
        return RunResult(launched.outcome, None if trace is None else trace.to_dict(), launched.session_id,
                         None if trace is None else trace.harness_version, None if trace is None else trace.model,
                         launched.cost_usd)

    def interrupt(self) -> None:
        """A resume ending in an exception with runs still out (``runs_at_once``): stop every harness this job's runs
        are running, so their threads return. The runs are left in flight, as interrupted."""
        if self.job_dir.is_dir():
            stop_left_running(self.job_dir)

    def finish(self, db: sqlite3.Connection, job_id: str, wait: bool = True) -> None:
        """However the resume ended, still holding the job's lock: a resume that closed the job before its first launch
        — at its ceiling, say, after a runner killed mid-run had used the last launch — never ran ``prepare``, so what
        the earlier runners left (a harness still running, its directories) is stopped and swept now, since no resume
        of a closed job ever will; then the deletions are waited for (unless ``wait`` is false)."""
        job = jobs.load(db, job_id)
        if not self.cleaned and (job is None or job.status is not JobStatus.OPEN):
            self.clean()
        self.close(wait)

    def close(self, wait: bool = True) -> None:
        self.disposal.close(wait)


def pinned_fingerprint(snapshot: ConfigSnapshot, version: str) -> str:
    """The configuration's fingerprint with the harness version a job pins in place of the one on disk: every run of
    the job runs the pinned version (D5), so a launcher update between resumes changes neither the runs nor this, and
    a resume refused with ConfigChanged means the configuration itself changed."""
    return fingerprint(snapshot.config_hash, version, snapshot.model)


@dataclass(frozen=True)
class Tested:
    job_id: str
    report: Report
    baseline: BaselineChecked | None  # None when this resume launched nothing (already done, or at its ceiling)
    swept: int  # leftovers found and deleted: this job's killed runs' (before its first launch, or as a resume that
    # launched nothing closes it), and every other job's no live runner holds
    undeleted: tuple[str, ...] = ()  # run directories a deletion failed on: the next run_test, forget or salvage
    # sweeps them again (clean_runs), whatever their job's state
    stopped: int = 0  # harness runs a killed runner left running (any job's), found and stopped: before this resume's
    # first launch, or as a resume that launched nothing closes the job


def acting_tools(db: sqlite3.Connection, adapter: VariantHomes, project: Path, *, sessions: int | None = None
                 ) -> tuple[str, ...]:
    """The tools a new job would remove from every run (D6): those the configuration in ``project`` is known to offer
    — what the last ``sessions`` sessions (default: your sessions setting) recorded of its tool listings — that act
    outside the machine, as the adapter tells them (``VariantHomes.acting_tools``). For ``run_test`` when no list is
    given; ``removed_tools`` adds your Settings override."""
    return adapter.acting_tools(_reading(db, adapter, project, sessions))


def _reading(db: sqlite3.Connection, adapter: VariantHomes, project: Path, sessions: int | None) -> Reading:
    count = settings.load(db).sessions if sessions is None else sessions
    return read_config(adapter, Path(os.path.realpath(project)), sessions=count, forgotten_before=usage.cutoff(db))


def removed_tools(verdicts: Sequence[ToolVerdict], *, keep: Sequence[str] = (), remove: Sequence[str] = ()
                  ) -> tuple[str, ...]:
    """The tools a new job removes from every run (D6): those the adapter removes (``tool_verdicts``) less ``keep``,
    then ``remove`` — your Settings override (``keep_tools``, ``remove_tools``) — each once, in that order."""
    kept = set(keep)
    return tuple(dict.fromkeys([*(v.name for v in verdicts if v.removed and v.name not in kept), *remove]))


def default_model(reading: Reading) -> str | None:
    """The model your own sessions use, as the adapter reads it: the model your harness's settings set (the snapshot's
    ``model``), else the model your latest interactive session recorded answering first; None when neither says. What
    a new job asks for when the ``model`` setting is empty."""
    snapshot = reading.snapshot
    if snapshot.model:
        return snapshot.model
    return next((header.model for header in snapshot.sessions if header.interactive and header.model), None)


def create(db: sqlite3.Connection, adapter: VariantHomes, *, project: Path, model: str, plan: Plan, run_ceiling: int,
           timeout_s: int, removed_tools: Sequence[str] | None = None, sessions: int | None = None,
           harness: str | None = None) -> str:
    """Freeze a new job, launching nothing: its plan, its run ceiling and time limit per run, the model every run asks
    for, the harness version the adapter reports now (pinned, D5), and the tools removed from every run (D6) —
    ``removed_tools`` when given, else the adapter's choice (``acting_tools``). ``harness``: the adapter's name, for the
    worker that runs it. Its id; ``run_test(job_id=…)`` or the worker runs it."""
    if run_ceiling is None or timeout_s is None:
        raise ValueError("a new job needs a run ceiling and a time limit per run")
    project = Path(os.path.realpath(project))
    version = adapter.version()
    if version is None:
        raise ValueError("the harness's version can't be read from disk, so a job can't pin it")
    snapshot = read_config(adapter.at_home(adapter.home_dir()), project, sessions=0).snapshot
    environment = Environment(pinned_fingerprint(snapshot, version), version, model)
    tools = acting_tools(db, adapter, project, sessions=sessions) if removed_tools is None else removed_tools
    return engine.create_job(db, project=str(project), environment=environment,
                             plan=replace(plan, removed_tools=tuple(dict.fromkeys(tools))), run_ceiling=run_ceiling,
                             timeout_s=timeout_s, harness=harness)


def job_removed_tools(db: sqlite3.Connection, job_id: str) -> tuple[str, ...]:
    """The tools a job removes from every run, frozen when it was created: every resume uses the same list."""
    job = jobs.load(db, job_id)
    if job is None:
        raise JobNotFound(f"no job {job_id}")
    return Plan.from_json(job.plan).removed_tools


def run_test(db: sqlite3.Connection, adapter: VariantHomes, launcher: Launcher, *, project: Path, model: str,
             plan: Plan | None = None, job_id: str | None = None, run_ceiling: int | None = None,
             timeout_s: int | None = None, removed_tools: Sequence[str] | None = None, sessions: int | None = None,
             wait_for_deletion: bool = True, **engine_options: object) -> Tested:
    """Start a job for ``plan`` (with its run ceiling and per-run time limit) or resume ``job_id``, and run it with
    real runs: the baseline check first, then every run of the frozen plan not yet recorded. ``model`` is the job's
    model, given to every run explicitly. A new job pins the harness version the adapter reports now
    (``version()``), and every launch of the job, resumes included, runs that version (D5). A new job also freezes
    the tools removed from every run (D6): ``removed_tools`` when given (your Settings override), else the adapter's
    choice (``acting_tools``, reading the last ``sessions`` sessions); a resume uses the job's own list and ignores
    both. Raises what the engine raises — BaselineMismatch, EnvironmentChanged (another model), ConfigChanged,
    RetriesExhausted and the other Paused — with the job kept."""
    if (plan is None) == (job_id is None):
        raise ValueError("give a plan to start a job, or a job id to resume one")
    project = Path(os.path.realpath(project))
    if plan is not None:
        if run_ceiling is None or timeout_s is None:
            raise ValueError("a new job needs a run ceiling and a time limit per run")
        job_id = create(db, adapter, project=project, model=model, plan=plan, run_ceiling=run_ceiling,
                        timeout_s=timeout_s, removed_tools=removed_tools, sessions=sessions)
    assert job_id is not None
    snapshot = read_config(adapter.at_home(adapter.home_dir()), project, sessions=0).snapshot
    pinned = jobs.load(db, job_id)
    if pinned is None:
        raise JobNotFound(f"no job {job_id}")
    version = pinned.harness_version  # the job's pin, whatever the launcher has installed since
    environment = Environment(pinned_fingerprint(snapshot, version), version, model)
    # Every other job's leftovers first: a closed or forgotten job is never resumed, so nothing else would sweep them.
    # This job's own, when it is open, are its prepare's (holding its lock, before its first launch).
    job = jobs.load(db, job_id)
    cleaning = clean_runs(db, skip=frozenset({job_id}) if job is not None and job.status is JobStatus.OPEN
                          else frozenset())
    executor = VariantExecutor(adapter, launcher, project=project, job_id=job_id,
                               targets_by_id={t.id: t for t in targets(snapshot)})
    undeleted: tuple[str, ...] = ()
    try:
        with stopping_on_signals():
            report = engine.run_job(db, job_id, executor, environment=environment, prepare=executor.prepare,
                                    finish=lambda: executor.finish(db, job_id, wait=wait_for_deletion),
                                    terms=getattr(adapter, "action_terms", None),  # the gate's exercise check
                                    **engine_options)  # type: ignore[arg-type]
    finally:
        executor.close(wait=wait_for_deletion)  # a resume refused before its lock was taken never ran finish
        undeleted = cleaning.close(db, wait=wait_for_deletion)
        if wait_for_deletion:
            closed = jobs.load(db, job_id)
            if closed is None or closed.status is not JobStatus.OPEN:
                _remove_empty(executor.job_dir)
    return Tested(job_id, report, executor.checked, executor.swept + cleaning.swept,
                  tuple(executor.disposal.failures) + undeleted, executor.stopped + cleaning.stopped)

