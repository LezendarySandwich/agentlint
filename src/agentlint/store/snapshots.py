"""Snapshots: each configuration agentlint has read, per project and fingerprint — what `show --diff`
compares against.

A snapshot is the serialized ``ConfigSnapshot`` with every text moved out into ``blobs``, keyed by
its content hash (``serialize.texts_out``) and stored once however many snapshots share it. ``load`` reads one
back as a ``ConfigSnapshot`` (``serialize.from_data``), its texts taken back in from ``blobs``. Most text doesn't
change between snapshots, so a new one costs only the files that changed plus ~1 MB of metadata
before compression. Re-reading an unchanged configuration replaces that fingerprint's row with the
latest reading.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from dataclasses import dataclass
from datetime import datetime

from agentlint.config.model import ConfigSnapshot
from agentlint.config.serialize import FormatError, from_data, snapshot_data, texts_out
from agentlint.store import transaction
from agentlint.store._time import decode, encode
from agentlint.store.usage import refuse_forgotten

@dataclass(frozen=True)
class Stored:
    fingerprint: str
    taken_at: datetime


class UnknownSnapshot(LookupError):
    pass


class UnreadableSnapshot(UnknownSnapshot):
    """Stored by an agentlint whose snapshot format this one can't read back."""


def _bytes(text: str) -> bytes:
    """Text as stored: UTF-8, letting through the lone surrogates a JSON escape can put in a string."""
    return text.encode("utf-8", "surrogatepass")


def save(db: sqlite3.Connection, snapshot: ConfigSnapshot, at: datetime) -> None:
    texts: dict[str, str] = {}
    data = texts_out(snapshot_data(snapshot), texts)
    with transaction(db):
        refuse_forgotten(db, snapshot.sessions)
        have = {row["hash"] for row in db.execute(
            f"SELECT hash FROM blobs WHERE hash IN ({','.join('?' * len(texts))})", list(texts))} if texts else set()
        db.executemany("INSERT INTO blobs (hash, text) VALUES (?, ?)",
                       [(key, zlib.compress(_bytes(text), 6)) for key, text in texts.items() if key not in have])
        db.execute("INSERT OR REPLACE INTO snapshots (project, fingerprint, taken_at, data) VALUES (?, ?, ?, ?)",
                   (str(snapshot.cwd), snapshot.fingerprint, encode(at),
                    zlib.compress(json.dumps(data).encode("ascii"), 6)))


def stored(db: sqlite3.Connection, project: str) -> list[Stored]:
    """This project's snapshots, most recent first."""
    return [Stored(row["fingerprint"], decode(row["taken_at"])) for row in db.execute(
        "SELECT fingerprint, taken_at FROM snapshots WHERE project = ? ORDER BY taken_at DESC", (project,))]


def previous(db: sqlite3.Connection, project: str, fingerprint: str) -> Stored | None:
    """The most recent snapshot of this project with a different fingerprint."""
    return next((s for s in stored(db, project) if s.fingerprint != fingerprint), None)


def resolve(db: sqlite3.Connection, project: str, prefix: str) -> Stored:
    """The one snapshot of this project whose fingerprint starts with ``prefix`` (``sha256:`` optional)."""
    wanted = prefix if prefix.startswith("sha256:") else f"sha256:{prefix}"
    matches = [s for s in stored(db, project) if s.fingerprint.startswith(wanted)]
    if len(matches) != 1:
        raise UnknownSnapshot(f"{'no' if not matches else len(matches)} snapshots of this project match {prefix}; "
                              "`agentlint show --snapshots` lists them")
    return matches[0]


def load(db: sqlite3.Connection, project: str, fingerprint: str) -> ConfigSnapshot:
    """A stored snapshot, texts and all. UnreadableSnapshot if an agentlint wrote it in a form this one can't read."""
    row = db.execute("SELECT data FROM snapshots WHERE project = ? AND fingerprint = ?", (project, fingerprint)).fetchone()
    if row is None:
        raise UnknownSnapshot(f"no snapshot {fingerprint} of {project}")
    try:
        return from_data(json.loads(zlib.decompress(row["data"])), text_of=lambda ref: text(db, ref))
    except FormatError as error:
        raise UnreadableSnapshot(f"the snapshot {fingerprint.removeprefix('sha256:')[:12]} was stored by an agentlint "
                                 f"whose format this one can't read back ({error})") from error


def harness_of(db: sqlite3.Connection, project: str, fingerprint: str) -> str | None:
    """The harness a stored snapshot was read through, without reading it back — so one stored in a format this
    agentlint can't read back can still be listed under its harness. None when it doesn't say."""
    row = db.execute("SELECT data FROM snapshots WHERE project = ? AND fingerprint = ?", (project, fingerprint)).fetchone()
    if row is None:
        raise UnknownSnapshot(f"no snapshot {fingerprint} of {project}")
    try:
        harness = json.loads(zlib.decompress(row["data"])).get("harness")
    except (zlib.error, ValueError, AttributeError):
        return None
    return harness if isinstance(harness, str) else None


def text(db: sqlite3.Connection, ref: str) -> str | None:
    row = db.execute("SELECT text FROM blobs WHERE hash = ?", (ref,)).fetchone()
    return zlib.decompress(row["text"]).decode("utf-8", "surrogatepass") if row else None


def declared_hashes(db: sqlite3.Connection, project: str) -> dict[str, str] | None:
    """The declared hash of every source in the project's latest stored configuration; None when none is stored, or
    the latest was stored in a form this agentlint can't read back."""
    latest = stored(db, project)
    if not latest:
        return None
    try:
        snapshot = load(db, project, latest[0].fingerprint)
    except UnreadableSnapshot:
        return None
    return {source.id: source.declared_hash for source in snapshot.sources}
