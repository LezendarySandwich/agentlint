"""Every transcript agentlint has read — sessions and their subagents — kept compressed in the store
(docs/tech_impl/08-recorder.md, "Archive").

So the transcript reader can show sessions the harness has deleted, and usage can be read again whenever the
reading rules change. Transcripts only grow, so a changed one usually costs just its new bytes; one that no
longer ends the way it did was rewritten, and is archived afresh. Each transcript is its own transaction, so an
interrupted import keeps what it finished, and two agentlints importing at once never keep the same bytes twice.
Forgetting deletes the bytes kept so far and remembers how far each transcript on disk had got, so what you forgot
is never read back in; keep_days does the same for a transcript that outlived it, so it isn't read again either.
"""

from __future__ import annotations

import hashlib
import sqlite3
import zlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple

from agentlint.config.conversation import TranscriptFile
from agentlint.store import transaction

LEVEL = 3  # ~145 MB/s at a third of the size, measured on this project's transcripts
_TAIL = 64  # the last bytes read, hashed — never kept — to tell a file that grew from one rewritten
_COMPACT_AT = 64  # chunks before a transcript's are joined into one

Read = Callable[[str, int, "int | None"], bytes]  # (handle, offset, length or None for the rest) -> bytes


@dataclass(frozen=True, kw_only=True)
class Archived:
    """A transcript the archive keeps bytes of, as last read. TranscriptFile's fields in its order, but not
    TranscriptFile: ``bytes_read`` is how far it was read, where TranscriptFile's ``size`` is the file's size now."""

    handle: str  # the adapter's handle for it: opaque to core (the table's ``key`` column)
    bytes_read: int
    mtime: float  # the file's modification time when last read, Unix seconds
    session_id: str  # its own session's id, or for a subagent the session it ran in
    subagent: bool


class _Kept(NamedTuple):
    """A transcript's row as last read: how far, when, how it ended, and how many leading bytes are not kept."""

    bytes_read: int
    mtime_epoch_s: float
    tail_sha256: bytes
    forgotten_bytes: int


_KEPT = "SELECT key, bytes_read, mtime_epoch_s, tail_sha256, forgotten_bytes FROM archive_files"


def _kept(row: sqlite3.Row | None) -> _Kept | None:
    return (_Kept(row["bytes_read"], row["mtime_epoch_s"], row["tail_sha256"], row["forgotten_bytes"])
            if row is not None else None)


def _digest(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _append(db: sqlite3.Connection, key: str, data: bytes) -> None:
    chunks = db.execute("SELECT count(*) AS count, coalesce(max(seq), -1) AS last FROM archive_chunks WHERE key = ?",
                        (key,)).fetchone()
    if chunks["count"] >= _COMPACT_AT:  # many small appends from a long session: joined, so reading stays one pass
        whole = b"".join(zlib.decompress(row["data"]) for row in db.execute(
            "SELECT data FROM archive_chunks WHERE key = ? ORDER BY seq", (key,))) + data
        db.execute("DELETE FROM archive_chunks WHERE key = ?", (key,))
        db.execute("INSERT INTO archive_chunks (key, seq, data) VALUES (?, 0, ?)", (key, zlib.compress(whole, LEVEL)))
        return
    db.execute("INSERT INTO archive_chunks (key, seq, data) VALUES (?, ?, ?)",
               (key, chunks["last"] + 1, zlib.compress(data, LEVEL)))


def update(db: sqlite3.Connection, harness: str, files: Iterable[TranscriptFile], read: Read, *,
           forgotten_before: datetime | None = None) -> list[str]:
    """Archive what changed since it was last read; the handles whose kept bytes changed."""
    known = {row["key"]: _kept(row) for row in db.execute(f"{_KEPT} WHERE harness = ?", (harness,))}
    changed, listed = [], set()
    for file in files:
        listed.add(file.handle)
        before = known.get(file.handle)
        if before is not None and (before.bytes_read, before.mtime_epoch_s) == (file.size, file.mtime):
            continue
        if before is None and forgotten_before is not None and file.mtime <= forgotten_before.timestamp():
            continue  # untouched since you forgot: it stays forgotten
        try:
            seen = min(_TAIL, before.bytes_read) if before is not None else 0
            grew = (before is not None and before.bytes_read > 0 and file.size >= before.bytes_read
                    and _digest(read(file.handle, before.bytes_read - seen, seen)) == before.tail_sha256)
            # rewritten after its bytes were forgotten or outlived keep_days: none of it is kept, only where it ends
            dropped = before is not None and before.forgotten_bytes > 0 and not grew
            offset = before.bytes_read if grew else 0  # grown: only the new bytes; rewritten: the whole file afresh
            data = b"" if dropped else read(file.handle, offset, None)
            bytes_read = file.size if dropped else offset + len(data)
            tail = _digest(read(file.handle, bytes_read - min(_TAIL, bytes_read), min(_TAIL, bytes_read)))
        except OSError:
            continue  # gone or unreadable since it was listed: tried again next time
        with transaction(db):
            if _kept(db.execute(f"{_KEPT} WHERE key = ?", (file.handle,)).fetchone()) != before:
                continue  # another agentlint archived it meanwhile: what it kept stands
            if not grew:
                db.execute("DELETE FROM archive_chunks WHERE key = ?", (file.handle,))
            if data:
                _append(db, file.handle, data)
            db.execute(
                "INSERT INTO archive_files (key, harness, session_id, subagent, bytes_read, mtime_epoch_s, "
                "forgotten_bytes, tail_sha256, derived) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0) "
                "ON CONFLICT (key) DO UPDATE SET "
                "bytes_read = excluded.bytes_read, mtime_epoch_s = excluded.mtime_epoch_s, "
                "tail_sha256 = excluded.tail_sha256, forgotten_bytes = excluded.forgotten_bytes, derived = 0",
                (file.handle, harness, file.session_id, int(file.subagent), bytes_read, file.mtime,
                 before.forgotten_bytes if grew else bytes_read if dropped else 0, tail))
        changed.append(file.handle)
    if listed:  # an empty listing is more likely a harness not found than every transcript gone: nothing dropped
        with transaction(db):  # nothing kept and gone from the harness: nothing left to remember about it
            db.executemany("DELETE FROM archive_files WHERE key = ? AND forgotten_bytes >= bytes_read",
                           [(key,) for key in known if key not in listed])
    return changed


def read(db: sqlite3.Connection, handle: str) -> bytes | None:
    """A transcript as kept: every chunk, in order. None if it was never archived."""
    if db.execute("SELECT 1 FROM archive_files WHERE key = ?", (handle,)).fetchone() is None:
        return None
    return b"".join(zlib.decompress(row["data"]) for row in db.execute(
        "SELECT data FROM archive_chunks WHERE key = ? ORDER BY seq", (handle,)))


def sessions(db: sqlite3.Connection, harness: str) -> list[Archived]:
    """The sessions kept (not their subagents), most recently changed first. One forgotten, with nothing kept since,
    isn't listed: nothing of it is kept."""
    return [Archived(handle=row["key"], bytes_read=row["bytes_read"], mtime=row["mtime_epoch_s"],
                     session_id=row["session_id"], subagent=False) for row in db.execute(
        "SELECT key, session_id, bytes_read, mtime_epoch_s FROM archive_files "
        "WHERE harness = ? AND subagent = 0 AND bytes_read > forgotten_bytes ORDER BY mtime_epoch_s DESC",
        (harness,))]


def find(db: sqlite3.Connection, harness: str, session_id: str) -> str | None:
    """The handle of a session's kept transcript, if any of it is kept."""
    row = db.execute("SELECT key FROM archive_files WHERE harness = ? AND session_id = ? AND subagent = 0 "
                     "AND bytes_read > forgotten_bytes", (harness, session_id)).fetchone()
    return row["key"] if row else None


def subagents(db: sqlite3.Connection, harness: str, session_id: str) -> list[Archived]:
    return [Archived(handle=row["key"], bytes_read=row["bytes_read"], mtime=row["mtime_epoch_s"],
                     session_id=session_id, subagent=True) for row in db.execute(
        "SELECT key, bytes_read, mtime_epoch_s FROM archive_files "
        "WHERE harness = ? AND session_id = ? AND subagent = 1 AND bytes_read > forgotten_bytes ORDER BY key",
        (harness, session_id))]


def totals(db: sqlite3.Connection) -> tuple[int, int, int]:
    """Transcripts kept, their bytes as read, and what they take in the store."""
    kept = db.execute("SELECT count(*) AS files, coalesce(sum(bytes_read - forgotten_bytes), 0) AS size "
                      "FROM archive_files WHERE bytes_read > forgotten_bytes").fetchone()
    stored = db.execute("SELECT coalesce(sum(length(data)), 0) AS stored FROM archive_chunks").fetchone()
    return kept["files"], kept["size"], stored["stored"]


def forget(db: sqlite3.Connection, harness: str | None = None, files: Iterable[TranscriptFile] = (),
           read: Read | None = None) -> int:
    """Delete every byte kept, and remember how far each transcript had got — as it is on disk now, for those the
    harness lists — so none of it is read back in. How many transcripts had bytes kept."""
    marks = []
    for file in files:  # the files as they are now, not as last read: what was written since is forgotten too
        try:
            seen = min(_TAIL, file.size)
            marks.append((file, _digest(read(file.handle, file.size - seen, seen))))
        except OSError:
            continue  # gone since it was listed: if it comes back untouched, the cutoff keeps it out
    with transaction(db):
        count = db.execute("SELECT count(*) AS count FROM archive_files WHERE bytes_read > forgotten_bytes"
                           ).fetchone()["count"]
        db.execute("DELETE FROM archive_chunks")
        db.execute("UPDATE archive_files SET forgotten_bytes = bytes_read")
        db.executemany(
            "INSERT INTO archive_files (key, harness, session_id, subagent, bytes_read, mtime_epoch_s, "
            "forgotten_bytes, tail_sha256, derived) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT (key) DO UPDATE SET "
            "bytes_read = excluded.bytes_read, mtime_epoch_s = excluded.mtime_epoch_s, "
            "tail_sha256 = excluded.tail_sha256, forgotten_bytes = excluded.forgotten_bytes",
            [(file.handle, harness, file.session_id, int(file.subagent), file.size, file.mtime, file.size, tail)
             for file, tail in marks])
    return count
