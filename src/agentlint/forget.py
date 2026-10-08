"""Forget everything: the one function `agentlint forget` and the UI's Privacy › Forget everything both call
(docs/tech_impl/00-overview.md, "State on disk"; 11-ui.md, "Settings").

It deletes everything agentlint kept from your transcripts (``store/usage.py``'s ``forget`` says what goes and what
stays — settings, suppressions and agentlint's own bookkeeping stay) and records the cutoff, so nothing from before
now is read back in. It refuses while a test job's runner is alive: deleting a job under a live runner would stop it
mid-run, and the person should stop it on purpose first. The check reads each job's lock (``store/jobs.py``: the
runner's pid and that process's start time), so a lock left by a runner that died doesn't block it. A runner that
claims a job between the check and the delete still stops at its next write (``JobGone``): the check is for the
person; the store's rule is the backstop.

A test run's directories under ``AGENTLINT_HOME/runs`` (whole variant homes, each holding its run's transcript) go
too: once the refusal check passes, ``runner/execute.py``'s ``forget_runs`` stops and sweeps what every job left there
while the jobs' locks still exist, then the database is forgotten, then the deletions are waited for. What a deletion
failed on is returned (``Forgotten.undeleted``) for the CLI and the UI to show; the next forget or test run sweeps it
again. The workers' logs (``AGENTLINT_HOME/jobs/<job>.log``) go with their jobs. Anything else a test run keeps that
``forget`` must delete joins here, in ``forget_everything``, and nowhere else.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from agentlint.config.conversation import TranscriptFile
from agentlint.runner import lock
from agentlint.store import archive, jobs
from agentlint.store import usage as usage_store


class Transcripts(Protocol):
    """A harness's transcripts as forgetting needs them: what an adapter is, seen from here."""

    name: str

    def transcripts(self) -> Iterable[TranscriptFile]: ...

    def read_transcript(self, handle: str, offset: int = 0, length: int | None = None) -> bytes: ...


class JobsRunning(Exception):
    """A test job's runner is alive: nothing was forgotten. The message names each job and its process."""

    def __init__(self, running: Sequence[jobs.Job]) -> None:
        listed = ", ".join(f"{job.job_id} (process {job.runner_pid})" for job in running)
        super().__init__(f"a test job is running — {listed}; stop it first, then forget")
        self.running = list(running)


@dataclass(frozen=True)
class Forgotten:
    turns: int  # turns of usage deleted
    transcripts: int  # archived transcripts whose bytes were deleted
    undeleted: tuple[str, ...] = ()  # test runs' directories a deletion failed on (each with why): swept again later


def running(db: sqlite3.Connection, stale: Callable[[int, str], bool] | None = None) -> list[jobs.Job]:
    """The jobs a live runner holds now. ``stale``: whether a lock's holder is gone (default ``runner/lock.py``'s)."""
    return jobs.held(db, stale or lock.stale)


def forget_everything(db: sqlite3.Connection, now: datetime, sources: Sequence[Transcripts], *,
                      stale: Callable[[int, str], bool] | None = None) -> Forgotten:
    """Delete everything kept from transcripts, as of ``now``; JobsRunning, with nothing deleted, while a runner is
    alive. ``sources``: the harnesses whose transcripts are on disk now, so what they gained since they were last
    read is forgotten too. The test runs' directories are stopped and swept with it (``execute.forget_runs``); what
    a deletion failed on comes back in ``undeleted``."""
    from agentlint.runner import execute  # the executor's module reads harnesses and plans: loaded only to forget

    busy = running(db, stale)
    if busy:
        raise JobsRunning(busy)
    transcripts = archive.totals(db)[0]

    def forget() -> int:
        turns = usage_store.forget(db, now)  # every harness's rows, whatever is installed now
        for source in sources:  # each one's transcripts as they are now: deletes nothing more, marks how far they got
            usage_store.forget(db, now, source.name, source.transcripts(), source.read_transcript)
        return turns

    turns, undeleted = execute.forget_runs(db, forget, stale=stale or lock.stale)
    from agentlint.runner.worker import remove_logs

    remove_logs(db)  # every job is gone, so every worker's log goes too
    return Forgotten(turns, transcripts, undeleted)
