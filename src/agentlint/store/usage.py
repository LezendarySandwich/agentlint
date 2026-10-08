"""Usage history in the database: turns derived from the archived transcripts (docs/tech_impl/08-recorder.md).

Derivation follows the archive: a transcript whose kept bytes changed since usage was last derived from it is read
again — an interrupted import is finished by the next — and when the adapter's usage reader version changes every
transcript kept is, so a parsing fix reaches the whole history. A turn's identity is its session and position, so
reading again never double-counts. `forget` deletes every turn — and everything else kept that came from
transcripts — and sets a cutoff, so nothing from before it is ever read back in: not turns (each is cut by its own
time, so a session that carried on past the cutoff keeps its later turns), and not sessions in a reading of the
configuration (``harness.read_config``). Each transcript's bookkeeping (``archive.forget``) stays.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import NamedTuple

from agentlint.config.conversation import TranscriptFile
from agentlint.config.sessions import SessionHeader
from agentlint.config.usage import Turn, UsageIndex, build_index
from agentlint.store import archive, diagnostics, jobs, probes, transaction
from agentlint.store._time import decode, encode

_COLUMNS = "session_id, turn, at, project, harness_version, model, interactive, sidechain, entities"
_UNSAID = {"project": "", "harness_version": "unknown", "model": "unknown"}  # stored for None: the columns are NOT
# NULL, and these are what they always held for it; nothing reads them back (``_Kept``)


def _text(value: str) -> str:
    """A string SQLite can store: a lone surrogate from a JSON escape becomes "?"."""
    return value.encode("utf-8", "replace").decode("utf-8")


def cutoff(db: sqlite3.Connection) -> datetime | None:
    """When you last forgot: nothing from before it is ever read back in."""
    row = db.execute("SELECT value FROM usage_meta WHERE key = 'forgotten_before'").fetchone()
    return decode(row["value"]) if row else None


class Forgotten(sqlite3.Error):
    """A reading holds a session from before the forget cutoff: you forgot while it was being read."""


def refuse_forgotten(db: sqlite3.Connection, sessions: Iterable[SessionHeader]) -> None:
    """Raise Forgotten unless every session started at or after the cutoff — called inside the writing transaction,
    so a forget that lands while a configuration is read never has its sessions stored after it."""
    forgotten = cutoff(db)
    if forgotten is not None and any(ref.started_at < forgotten for ref in sessions):
        raise Forgotten("you forgot while the configuration was being read, so nothing of it was stored; run it again")


def derive(db: sqlite3.Connection, harness: str, reader_version: str,
           turns_from: Callable[[str, bytes], list[Turn]]) -> int:
    """Usage from the archived transcripts: those not derived since their kept bytes changed — or every one kept,
    when the adapter's ``usage_reader_version`` changed since usage was last derived — each in its own transaction.
    Turns stored."""
    from agentlint.store import archive

    meta = f"rules:{harness}"  # the key's old name stays: it is stored
    row = db.execute("SELECT value FROM usage_meta WHERE key = ?", (meta,)).fetchone()
    every = row is None or row["value"] != reader_version
    todo = db.execute(
        "SELECT key, session_id, bytes_read, mtime_epoch_s FROM archive_files WHERE harness = ? AND subagent = 0"
        + ("" if every else " AND derived = 0"), (harness,)).fetchall()
    stored = 0
    for item in todo:
        key, session_id = item["key"], item["session_id"]
        forgotten = cutoff(db)
        data = archive.read(db, key)
        if data is None:
            continue
        turns = [t for t in turns_from(key, data) if forgotten is None or t.at > forgotten]
        with transaction(db):
            now = db.execute("SELECT bytes_read, mtime_epoch_s FROM archive_files WHERE key = ?", (key,)).fetchone()
            if (now is None or (now["bytes_read"], now["mtime_epoch_s"]) != (item["bytes_read"], item["mtime_epoch_s"])
                    or cutoff(db) != forgotten):
                continue  # archived again, or forgotten, by another agentlint meanwhile: derived next time
            db.execute("DELETE FROM observations WHERE session_id = ?", (session_id,))
            db.executemany(
                f"INSERT OR REPLACE INTO observations ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(_text(t.header.session_id), t.turn, encode(t.at),
                  _text(str(t.header.project) if t.header.project else _UNSAID["project"]),
                  _text(t.header.harness_version or _UNSAID["harness_version"]), _text(t.model or _UNSAID["model"]),
                  int(t.header.interactive), int(t.sidechain), json.dumps(list(t.entities)))
                 for t in turns])
            db.execute("UPDATE archive_files SET derived = 1 WHERE key = ?", (key,))
        stored += len(turns)
    with transaction(db):
        db.execute("INSERT OR REPLACE INTO usage_meta (key, value) VALUES (?, ?)", (meta, reader_version))
    return stored


class _Kept(NamedTuple):
    """A turn as the store keeps it for counting (``config.usage.Counted``): when, whether it is your usage, what it
    used. Its session's header isn't kept whole — not its start — so it is not read back as a ``Turn``."""

    at: datetime
    counts: bool
    entities: tuple[str, ...]


def _kept(row: sqlite3.Row) -> _Kept:
    return _Kept(decode(row["at"]), bool(row["interactive"]) and not row["sidechain"], tuple(json.loads(row["entities"])))


def index(db: sqlite3.Connection, *, since: datetime | None = None, until: datetime | None = None) -> UsageIndex:
    """Usage counted over a window (all kept history when ``since`` is None)."""
    oldest = db.execute("SELECT min(at) AS oldest FROM observations").fetchone()["oldest"]
    query, args = "SELECT at, interactive, sidechain, entities FROM observations", []
    if since is not None:
        query, args = query + " WHERE at >= ?", [encode(since)]
    turns = [_kept(row) for row in db.execute(query, args)]
    return build_index(turns, window_start=since, window_end=until,
                       history_start=decode(oldest) if oldest else None)


def forget(db: sqlite3.Connection, now: datetime, harness: str | None = None, files: Iterable[TranscriptFile] = (),
           read: archive.Read | None = None) -> int:
    """Delete everything kept that came from transcripts, and never read back anything from before now: every turn,
    every archived transcript's bytes — including what the harness's transcripts (``files``, as listed now) gained
    since they were last read — every stored reading with its texts (what sessions showed: hook output, session
    context, the sessions' directories), every finding (which can quote them), every test job with its runs (a
run's trace is read from its transcript; a job still running stops at its next write), every generated situation
and every diagnostic. Kept: suppressions and their
    baselines (your decisions; declared hashes and source ids), analyses (check ids), verdicts and each transcript's
    bookkeeping (where it is, its session, how far it had got, when, a hash of its end). A reading takes
    the cutoff and uses no session that started before it. How many turns went."""
    with transaction(db):
        removed = db.execute("DELETE FROM observations").rowcount
        for table in ("snapshots", "blobs", "findings"):
            db.execute(f"DELETE FROM {table}")
        jobs.delete_all(db)  # a run's trace is read from its transcript; results live under agentlint's home
        probes.delete_all(db)  # generated situations and diagnostics live under agentlint's home too
        diagnostics.delete_all(db)
        db.execute("INSERT OR REPLACE INTO usage_meta (key, value) VALUES ('forgotten_before', ?)", (encode(now),))
        archive.forget(db, harness, files, read)
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # secure_delete zeroed the rows; this empties the WAL of them
    return removed
