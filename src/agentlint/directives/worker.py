"""The worker a reading of instructions runs in (12-directives.md, "Reading jobs"): ``python -m agentlint.directives
<read id>``, launched by the UI in a process group of its own — as a test job's worker is — so it outlives the server
and the terminal, and Stop is Ctrl-C to its group. It takes the reading's lock, reads its sections still pending or
failed (``extract.run_read``), and marks the reading complete, or stopped when it was stopped; what it was reading
when stopped is pending again, for a resume. It logs to ``<AGENTLINT_HOME>/jobs/<read id>.log``.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import threading
import traceback
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, TextIO

from agentlint import store
from agentlint.directives.extract import run_read
from agentlint.runner import lock
from agentlint.runner.execute import Terminated, stopping_on_signals
from agentlint.runner.worker import EXIT_DONE, EXIT_ERROR, EXIT_REFUSED, EXIT_STOPPED, Log, log_path
from agentlint.store import reads

Parts = Callable[[str], Any]  # harness -> what runs its generator (``.adapter``, ``.generator``), or None


def work(read_id: str, *, connect: Callable[[], sqlite3.Connection], parts: Parts, environ: Mapping[str, str],
         out: TextIO | None = None, stale: Callable[[int, str], bool] = lock.stale) -> int:
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, signal.default_int_handler)  # Ctrl-C raises, even if started with it ignored
    owned = out is None
    if out is None:
        path = log_path(read_id)
        store.private_dirs(path.parent)
        out = open(path, "a", encoding="utf-8")  # noqa: SIM115 - closed below
    log = Log(out)
    try:
        return _work(read_id, connect, parts, environ, log, stale)
    finally:
        if owned:
            out.close()


def _work(read_id: str, connect: Callable[[], sqlite3.Connection], parts: Parts, environ: Mapping[str, str], log: Log,
          stale: Callable[[int, str], bool]) -> int:
    log.say(f"worker {os.getpid()} for reading {read_id}")
    db = connect()
    try:
        read = reads.load(db, read_id)
        if read is None:
            log.say(f"refused: no reading {read_id}")
            return EXIT_REFUSED
        made = parts(read.harness)
        if made is None:
            log.say(f"refused: agentlint can't read with {read.harness}")
            return EXIT_REFUSED
        nested = made.adapter.nested(environ)
        if nested is not None:
            log.say(f"refused: {nested}")
            return EXIT_REFUSED
        me = lock.current()
        if not reads.claim(db, read_id, me.pid, me.started, stale):
            log.say("refused: another process is reading it")
            return EXIT_REFUSED
        status, code = reads.Status.COMPLETE, EXIT_DONE
        try:
            with stopping_on_signals():
                run_read(connect, read_id, made.generator)
        except (KeyboardInterrupt, Terminated):
            status, code = reads.Status.STOPPED, EXIT_STOPPED
            log.say("stopped: the sections under way finished; the rest are read when it resumes")
        except Exception as error:  # noqa: BLE001 - logged whole: the page shows the log's last lines
            status, code = reads.Status.STOPPED, EXIT_ERROR
            log.say(f"failed: {type(error).__name__}: {error}\n{traceback.format_exc()}")
        finally:
            reads.settle(db, read_id)
            reads.finish(db, read_id, status, datetime.now(UTC))
        found = reads.counts(db, read_id)
        if code == EXIT_DONE:
            log.say(f"complete: {found['read']} sections read, {found['failed']} failed")
        return code
    finally:
        db.close()
