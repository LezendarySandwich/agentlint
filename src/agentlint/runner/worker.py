"""The worker: one test job run or resumed in a process of its own, which the UI launches and which outlives it
(docs/tech_impl/05-runner.md "The worker"; decided with the user 2026-10-04: Q4, Q7).

``python -m agentlint.runner <job_id>`` is its entry point — internal, never a command a person types. It runs or
resumes the job through ``runner/execute.run_test``, so everything the executor guarantees holds: the frozen plan, the
pinned harness version, the removed tools, the lock, the ceiling, a kill losing at most one run. What it adds:

- **It re-checks that it can run here.** The adapter decides from the environment whether this process sits inside
  one of the harness's own sessions, which can't start another (``VariantHomes.nested``); the worker refuses then,
  saying to start agentlint ui from a plain terminal, whatever the server checked before launching it.
- **It logs** to ``<AGENTLINT_HOME>/jobs/<job id>.log``: when it started and why it ended, and each run it launched
  with what that run came to. The server sends the worker's own output there too, so a crash before the log opens is
  kept. ``keep_days`` deletes a log with its job, ``forget`` every log (``remove_logs``).
- **It stops cleanly** on SIGINT (Ctrl-C: Stop sends it to the worker's process group) and on SIGTERM or SIGHUP: the
  run in progress is stopped by the launcher on the way out and its key left for the resume to report, as the engine
  does for any runner. SIGINT's handler is installed explicitly, since a process started with SIGINT ignored (a
  server started in the background, say) would otherwise ignore it too.

The process control the server needs lives here too, harness-agnostic: ``launch`` (its own session and process group,
so closing the terminal that ran ``agentlint ui`` never reaches it), ``stop`` (the job's runner verified by pid, start
time and process group, then SIGINT to the group, escalating to SIGKILL after ``STOP_GRACE_S``) and ``KeepAwake``
(macOS ``caffeinate -i -w <worker pid>``: idle sleep is held off while the worker lives, and released when it exits).
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from agentlint import settings, store
from agentlint.harness import VariantHomes
from agentlint.runner import engine, execute, lock
from agentlint.runner.execute import Launch, Launched, Launcher
from agentlint.runner.variant import VariantError
from agentlint.store import jobs

LOGS = "jobs"  # under AGENTLINT_HOME: one log per job
STOP_GRACE_S = 60  # Stop: SIGINT, then SIGKILL this long after. The launcher gives the harness 10 s after SIGTERM, and
# the worker then waits for its run directories' deletion (24-45 s a home, E1); a kill during that wait only leaves
# what the next runner sweeps, so the grace covers the harness's stop with room, not the deletion.
EXIT_DONE = 0  # the job is complete, or closed at its ceiling
EXIT_ERROR = 1  # something unexpected: its traceback is in the log
EXIT_PAUSED = 3  # paused (retries used up, an authentication failure): resume it later
EXIT_REFUSED = 4  # refused before anything ran: no such job, closed, held by another runner, nested, changed
EXIT_STOPPED = 130  # Ctrl-C (Stop): 128 + SIGINT, as a shell reports it; SIGTERM and SIGHUP end at 128 + theirs
_JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")

Parts = Callable[[str], "tuple[VariantHomes, Launcher] | None"]  # a harness's name -> its adapter and launcher


class WorkerRefused(Exception):
    """The job can't be run or stopped as asked: the message says why, in words a person can act on."""


def log_path(job_id: str) -> Path:
    """Where a job's worker writes: ``<AGENTLINT_HOME>/jobs/<job id>.log``."""
    if not _JOB_ID.fullmatch(job_id):
        raise ValueError(f"{job_id!r} isn't a job id")
    return store.home() / LOGS / f"{job_id}.log"


def remove_logs(db: sqlite3.Connection) -> int:
    """Delete the log of every job no longer in the database — forgotten, or pruned past keep_days — and of nothing
    else. How many went."""
    folder = store.home() / LOGS
    if not folder.is_dir():
        return 0
    from agentlint.store import reads  # a reading of instructions logs here too (directives/worker.py)

    known = {row["job_id"] for row in db.execute("SELECT job_id FROM jobs")} | {r.read_id for r in reads.listed(db)}
    removed = 0
    for log in folder.glob("*.log"):
        if log.stem not in known and not log.is_symlink():
            try:
                log.unlink()
                removed += 1
            except OSError:
                pass  # tried again the next time
    return removed


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Log:
    """A job's log: one timestamped line each, flushed at once (the page reads it while the worker runs)."""

    def __init__(self, out: TextIO) -> None:
        self.out = out

    def say(self, line: str) -> None:
        self.out.write(f"{_now()}  {line}\n")
        self.out.flush()


class LoggedLauncher:
    """A launcher that logs each run it launches, and what the run came to — an outcome, or why it wasn't one."""

    def __init__(self, launcher: Launcher, log: Log) -> None:
        self.launcher, self.log = launcher, log

    def launch(self, launch: Launch) -> Launched:
        run = launch.home.parent.name  # the run directory: the run key with '.' for ':'
        self.log.say(f"run {run}: started")
        try:
            launched = self.launcher.launch(launch)
        except engine.Retry as error:
            self.log.say(f"run {run}: not an outcome, tried again after a pause ({error})")
            raise
        except engine.Paused as error:
            self.log.say(f"run {run}: the job pauses ({error})")
            raise
        except BaseException as error:
            self.log.say(f"run {run}: stopped ({type(error).__name__})")
            raise
        self.log.say(f"run {run}: {launched.outcome}")
        return launched


def work(job_id: str, *, connect: Callable[[], sqlite3.Connection], parts: Parts, environ: Mapping[str, str],
         out: TextIO | None = None, **options: object) -> int:
    """Run or resume ``job_id`` through ``execute.run_test``, logging to ``out`` (default: the job's log, appended).
    Its exit status: EXIT_DONE, EXIT_PAUSED, EXIT_REFUSED, EXIT_STOPPED (or 128 + SIGTERM/SIGHUP) or EXIT_ERROR.
    ``parts`` builds the adapter and launcher of the harness the job names; ``options`` go to ``run_test``."""
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, signal.default_int_handler)  # Ctrl-C raises, even if started with it ignored
    owned = out is None
    if out is None:
        path = log_path(job_id)
        store.private_dirs(path.parent)
        out = open(path, "a", encoding="utf-8")  # noqa: SIM115 - closed below
    log = Log(out)
    try:
        return _work(job_id, connect, parts, environ, log, options)
    finally:
        if owned:
            out.close()


def _work(job_id: str, connect: Callable[[], sqlite3.Connection], parts: Parts, environ: Mapping[str, str], log: Log,
          options: dict[str, object]) -> int:
    log.say(f"worker {os.getpid()} for job {job_id}")
    try:
        db = connect()
    except (store.StoreError, sqlite3.Error, OSError) as error:
        log.say(f"refused: the agentlint database can't be opened ({error})")
        return EXIT_REFUSED
    try:
        job = jobs.load(db, job_id)
        if job is None:
            log.say(f"refused: no job {job_id} (forgotten, or pruned)")
            return EXIT_REFUSED
        if job.harness is None:
            log.say(f"refused: job {job_id} was made before jobs named their harness; start a new test")
            return EXIT_REFUSED
        made = parts(job.harness)
        if made is None:
            log.say(f"refused: agentlint can't run {job.harness} tests")
            return EXIT_REFUSED
        adapter, launcher = made
        nested = adapter.nested(environ)
        if nested is not None:
            log.say(f"refused: {nested}")
            return EXIT_REFUSED
        if "at_once" not in options:  # your setting as this resume starts: the machine's load is now's, not the job's
            try:
                options = {**options, "at_once": settings.load(db).runs_at_once}
            except settings.SettingsError:
                pass  # a stored value that can't be used: one at a time, as before the setting
        if options.get("at_once", 1) != 1:
            log.say(f"{options['at_once']} runs at once")
        try:
            tested = execute.run_test(db, adapter, LoggedLauncher(launcher, log), project=Path(job.project),
                                      model=job.model, job_id=job_id, **options)  # type: ignore[arg-type]
        except KeyboardInterrupt as error:
            log.say("stopped by Ctrl-C: the run in progress was stopped and is run again when the job resumes"
                    + _interrupted(error))
            return EXIT_STOPPED
        except execute.Terminated as error:
            log.say(f"{error}: the run in progress was stopped and is run again when the job resumes"
                    + _interrupted(error))
            return 128 + error.signum
        except engine.Paused as error:
            log.say(f"paused, kept to resume: {error}{_interrupted(error)}")
            return EXIT_PAUSED
        except (engine.JobClosed, engine.ConfigChanged, engine.EnvironmentChanged, jobs.JobLocked, jobs.JobGone,
                engine.JobNotFound, VariantError) as error:  # VariantError: the baseline check, a cut that failed
            log.say(f"refused: {error}{_interrupted(error)}")
            return EXIT_REFUSED
        except Exception as error:  # noqa: BLE001 - logged whole: the page shows the log's last lines
            log.say(f"failed: {type(error).__name__}: {error}{_interrupted(error)}\n{traceback.format_exc()}")
            return EXIT_ERROR
        report = tested.report
        said = "; ".join(report.messages())
        log.say(f"{'complete' if report.stopped is engine.Stopped.COMPLETE else 'stopped at its run ceiling'}: "
                f"{report.recorded} runs recorded this time, {report.retried} tried again"
                + (f"; {said}" if said else "")
                + (f"; {tested.stopped} harness runs a killed runner left were stopped" if tested.stopped else "")
                + (f"; not deleted: {', '.join(tested.undeleted)}" if tested.undeleted else ""))
        return EXIT_DONE
    finally:
        db.close()


def _interrupted(error: BaseException) -> str:
    found = engine.interrupted_before(error)
    return f" (before this resume, {len(found)} interrupted: {', '.join(found)})" if found else ""


# The server's side: launching a worker, stopping it, keeping the Mac awake while it runs.

def launch(job_id: str, command: Sequence[str], *, environ: Mapping[str, str]) -> subprocess.Popen[bytes]:
    """Start the worker ``command`` for ``job_id`` in a session and process group of its own (so it outlives the
    server and the terminal that ran it), its output appended to the job's log. The caller reaps it (``reap``)."""
    path = log_path(job_id)
    store.private_dirs(path.parent)
    with open(path, "ab") as out:
        return subprocess.Popen(list(command), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                env=dict(environ), start_new_session=True, close_fds=True)


def reap(process: subprocess.Popen[bytes]) -> None:
    """Wait for a launched worker off the request path, so it never lingers as a zombie under a long-lived server."""
    threading.Thread(target=process.wait, name=f"agentlint-reap-{process.pid}", daemon=True).start()


def runner_of(db: sqlite3.Connection, job_id: str, launched: lock.Process | None = None,
              stale: Callable[[int, str], bool] = lock.stale) -> lock.Process | None:
    """The live process running the job: the job's lock, when a live process holds it, else ``launched`` — a worker
    the server started that hasn't claimed the lock yet — when it is still that same process. None: nothing runs it."""
    job = jobs.load(db, job_id)
    if job is not None and job.runner_pid is not None and not stale(job.runner_pid, job.runner_started or ""):
        return lock.Process(job.runner_pid, job.runner_started or "")
    if launched is not None and not stale(launched.pid, launched.started):
        return launched
    return None


def stop(process: lock.Process, *, grace_s: float = STOP_GRACE_S, after_kill: Callable[[], object] = lambda: None,
         killpg: Callable[[int, int], None] = os.killpg, getpgid: Callable[[int], int] = os.getpgid,
         started: Callable[[int], str | None] = lock.process_start) -> threading.Thread:
    """Stop a job's worker: verified as the same live process (its pid and start time) leading its own process group
    (pgid == pid, as ``launch`` starts it), then SIGINT — Ctrl-C — to the group; a thread waits ``grace_s`` and, if
    that same process is still alive, sends SIGKILL to the group, then ``after_kill`` (stop the harness run the kill
    orphaned). WorkerRefused, with nothing sent, when the process isn't that, or doesn't lead its group. The thread."""
    if started(process.pid) != process.started:
        raise WorkerRefused(f"process {process.pid} isn't running this job any more")
    try:
        group = getpgid(process.pid)
    except ProcessLookupError as error:
        raise WorkerRefused(f"process {process.pid} isn't running this job any more") from error
    if group != process.pid:
        raise WorkerRefused(f"process {process.pid} doesn't lead its own process group, so agentlint won't signal the "
                            "group; stop it from the terminal that started it")
    killpg(process.pid, signal.SIGINT)

    def escalate() -> None:
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            if started(process.pid) != process.started:
                return
            time.sleep(0.2)
        if started(process.pid) == process.started:
            try:
                killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                return
            after_kill()

    thread = threading.Thread(target=escalate, name=f"agentlint-stop-{process.pid}", daemon=True)
    thread.start()
    return thread


class KeepAwake:
    """Holds off idle sleep while a worker runs: on macOS ``caffeinate -i -w <pid>``, which exits by itself when the
    worker does — so closing the server never releases it early, and a worker that ends releases it at once. It runs
    in a session of its own, so Ctrl-C on the terminal running ``agentlint ui`` doesn't end it. Elsewhere, nothing:
    the line the page shows says so. Whether a worker is held is read from the process table, so a server started
    again sees the hold an earlier one took."""

    COMMAND = "caffeinate"

    def __init__(self, *, platform: str = sys.platform, which: Callable[[str], str | None] = shutil.which,
                 spawn: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
                 table: Callable[[], str] | None = None) -> None:
        self.platform, self._which, self._spawn = platform, which, spawn
        self._table = table or _process_table

    @property
    def available(self) -> bool:
        return self.platform == "darwin" and self._which(self.COMMAND) is not None

    def line(self) -> str:
        """What onboarding and the job's page say about sleep."""
        if self.available:
            return ("While a test runs, agentlint keeps this Mac from idle-sleeping (caffeinate -i), and lets it sleep "
                    "again when the test ends or stops. Closing the lid still sleeps it, and the test waits.")
        return "agentlint can't keep this machine awake: while it sleeps, a running test waits."

    def holding(self, pid: int) -> bool:
        wanted = f"{self.COMMAND} -i -w {pid}"
        return any(line.split(None, 1)[1:] == [wanted] for line in self._table().splitlines() if line.strip())

    def hold(self, pid: int) -> bool:
        """Keep the machine awake while process ``pid`` lives; whether it is held now."""
        if not self.available:
            return False
        if self.holding(pid):
            return True
        process = self._spawn([self.COMMAND, "-i", "-w", str(pid)], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        reap(process)
        return True


def _process_table() -> str:
    """Every process's pid and command line (``ps -axo pid=,command=``); empty when it can't be read."""
    try:
        return subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10,
                              check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
