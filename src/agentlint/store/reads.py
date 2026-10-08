"""Readings of instructions as jobs (12-directives.md, "Reading jobs"): what you asked to be read — every section to
read, with its text, so the reading's worker needs nothing else — and where each section stands.

A reading is planned in one transaction (``create``): its sections are those of the sources you selected whose
fingerprint wasn't read yet, each once. Its worker (``directives/worker.py``) marks each section reading, read or
failed as it goes, holding the reading's lock (pid and start time, as a test job's). Stopped or killed, a reading is
resumed by its sections still pending or failed; a section another reading read meanwhile is read already.

Like ``store/directives.py``, the tables are created on first use, not by a schema version.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from agentlint.store import transaction
from agentlint.store._time import decode, encode

_TABLES = ("""
CREATE TABLE IF NOT EXISTS reads (      -- a reading of instructions you asked for (12-directives.md)
    read_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    harness TEXT NOT NULL,              -- whose generator reads it
    model TEXT NOT NULL,
    sources INTEGER NOT NULL,           -- how many sources you selected
    known TEXT NOT NULL,                -- JSON: the skill and agent names a directive may name (the check's)
    status TEXT NOT NULL CHECK (status IN ('open', 'complete', 'stopped')),
    runner_pid INTEGER,
    runner_started TEXT,
    created TEXT NOT NULL,
    finished TEXT
) STRICT""", """
CREATE TABLE IF NOT EXISTS read_sections (
    read_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,          -- the section's text hash: what the directives are stored by
    source_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    section TEXT NOT NULL,              -- the rule id
    line INTEGER NOT NULL,
    text TEXT NOT NULL,
    place TEXT NOT NULL,                -- where it comes from, in words, for the prompt
    loads TEXT NOT NULL,                -- when it reaches the agent, in words, for the prompt
    status TEXT NOT NULL CHECK (status IN ('pending', 'reading', 'read', 'failed')),
    directives INTEGER,                 -- read: how many directives it gave
    message TEXT,                       -- failed: why
    PRIMARY KEY (read_id, seq)
) STRICT""")


class Status(Enum):
    OPEN = "open"
    COMPLETE = "complete"
    STOPPED = "stopped"


class Section(Enum):
    PENDING = "pending"
    READING = "reading"
    READ = "read"
    FAILED = "failed"


@dataclass(frozen=True)
class Planned:
    fingerprint: str
    source_id: str
    source_name: str
    section: str
    line: int
    text: str
    place: str
    loads: str


@dataclass(frozen=True)
class Read:
    read_id: str
    project: str
    harness: str
    model: str
    sources: int
    known: tuple[str, ...]
    status: Status
    runner_pid: int | None
    runner_started: str | None
    created: datetime
    finished: datetime | None


@dataclass(frozen=True)
class SectionState:
    seq: int
    planned: Planned
    status: Section
    directives: int | None
    message: str | None


def _ensure(db: sqlite3.Connection) -> None:
    for table in _TABLES:
        db.execute(table)


def _read(row: sqlite3.Row) -> Read:
    return Read(row["read_id"], row["project"], row["harness"], row["model"], row["sources"],
                tuple(json.loads(row["known"])), Status(row["status"]), row["runner_pid"], row["runner_started"],
                decode(row["created"]), decode(row["finished"]) if row["finished"] else None)


def create(db: sqlite3.Connection, *, read_id: str, project: str, harness: str, model: str, sources: int,
           known: Iterable[str], sections: Sequence[Planned], now: datetime) -> None:
    _ensure(db)
    with transaction(db):
        db.execute("INSERT INTO reads (read_id, project, harness, model, sources, known, status, created) "
                   "VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
                   (read_id, project, harness, model, sources, json.dumps(sorted(set(known))), encode(now)))
        db.executemany("INSERT INTO read_sections (read_id, seq, fingerprint, source_id, source_name, section, line, "
                       "text, place, loads, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                       [(read_id, n, s.fingerprint, s.source_id, s.source_name, s.section, s.line, s.text, s.place,
                         s.loads) for n, s in enumerate(sections)])


def load(db: sqlite3.Connection, read_id: str) -> Read | None:
    _ensure(db)
    row = db.execute("SELECT * FROM reads WHERE read_id = ?", (read_id,)).fetchone()
    return _read(row) if row else None


def listed(db: sqlite3.Connection, project: str | None = None) -> list[Read]:
    """Every reading, or a project's, the most recent first."""
    _ensure(db)
    query, args = "SELECT * FROM reads", ()
    if project is not None:
        query, args = query + " WHERE project = ?", (project,)
    return [_read(row) for row in db.execute(query + " ORDER BY created DESC, read_id DESC", args)]


def counts(db: sqlite3.Connection, read_id: str) -> dict[str, int]:
    """How many of the reading's sections stand at each status, every status named."""
    _ensure(db)
    found = {s.value: 0 for s in Section}
    for row in db.execute("SELECT status, count(*) AS n FROM read_sections WHERE read_id = ? GROUP BY status", (read_id,)):
        found[row["status"]] = row["n"]
    return found


def sections(db: sqlite3.Connection, read_id: str, *, statuses: Iterable[Section] | None = None) -> list[SectionState]:
    _ensure(db)
    query, args = "SELECT * FROM read_sections WHERE read_id = ?", [read_id]
    if statuses is not None:
        wanted = [s.value for s in statuses]
        query += f" AND status IN ({','.join('?' * len(wanted))})"
        args += wanted
    return [SectionState(row["seq"], Planned(row["fingerprint"], row["source_id"], row["source_name"], row["section"],
                                             row["line"], row["text"], row["place"], row["loads"]),
                         Section(row["status"]), row["directives"], row["message"])
            for row in db.execute(query + " ORDER BY seq", args)]


def mark(db: sqlite3.Connection, read_id: str, seq: int, status: Section, *, directives: int | None = None,
         message: str | None = None) -> None:
    _ensure(db)
    with transaction(db):
        db.execute("UPDATE read_sections SET status = ?, directives = ?, message = ? WHERE read_id = ? AND seq = ?",
                   (status.value, directives, message, read_id, seq))


def settle(db: sqlite3.Connection, read_id: str) -> None:
    """What a runner left marked reading — it was killed, or stopped mid-call — is pending again."""
    _ensure(db)
    with transaction(db):
        db.execute("UPDATE read_sections SET status = 'pending' WHERE read_id = ? AND status = 'reading'", (read_id,))


def claim(db: sqlite3.Connection, read_id: str, pid: int, started: str, stale: Callable[[int, str], bool]) -> bool:
    """Take the reading's lock, or a dead runner's; False when a live process holds it, or the reading is gone."""
    _ensure(db)
    with transaction(db):
        row = db.execute("SELECT runner_pid, runner_started FROM reads WHERE read_id = ?", (read_id,)).fetchone()
        if row is None:
            return False
        if row["runner_pid"] is not None and (row["runner_pid"], row["runner_started"]) != (pid, started) \
                and not stale(row["runner_pid"], row["runner_started"] or ""):
            return False
        db.execute("UPDATE reads SET runner_pid = ?, runner_started = ?, status = 'open', finished = NULL "
                   "WHERE read_id = ?", (pid, started, read_id))
        return True


def finish(db: sqlite3.Connection, read_id: str, status: Status, now: datetime) -> None:
    """The runner ends: the reading complete or stopped, its lock released."""
    _ensure(db)
    with transaction(db):
        db.execute("UPDATE reads SET status = ?, finished = ?, runner_pid = NULL, runner_started = NULL "
                   "WHERE read_id = ?", (status.value, encode(now), read_id))
