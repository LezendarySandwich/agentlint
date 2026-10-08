"""Each section of instruction text's directives, as the extraction found them (12-directives.md): keyed by the
section's fingerprint — its text's content hash — and the reader's version (``prompt.READER_VERSION``, kept in the
``prompt_version`` column), so a section is read once and again only when its text, or the reader, changes. A row
under an older version is stale: it is never loaded for a check, and reading the section again replaces it. What a
section of configuration says, not anything from a transcript: forget keeps it, as it keeps snapshots.

The table is created on first use (``CREATE TABLE IF NOT EXISTS``), not by a schema version: it is additive, an older
agentlint never reads it, and a version bump would make an older agentlint still running — the UI you have open —
refuse the whole store the moment a newer process opened it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from agentlint.directives.model import Directive, read, stored
from agentlint.store import transaction
from agentlint.store._time import decode, encode

_TABLE = """
CREATE TABLE IF NOT EXISTS directives (  -- what a section of instruction text tells the agent (12-directives.md)
    text_hash TEXT NOT NULL,        -- the section's fingerprint: its text's content hash
    prompt_version INTEGER NOT NULL,
    directives TEXT NOT NULL,       -- JSON: the checked directives, possibly none
    model TEXT,                     -- the model that answered, as the call reported it
    created TEXT NOT NULL,
    PRIMARY KEY (text_hash, prompt_version)
) STRICT
"""


@dataclass(frozen=True)
class Read:
    text_hash: str
    directives: tuple[Directive, ...]
    model: str | None
    created: datetime


def _ensure(db: sqlite3.Connection) -> None:
    db.execute(_TABLE)


def load(db: sqlite3.Connection, hashes: Iterable[str], prompt_version: int) -> dict[str, Read]:
    """What was read of these sections under this prompt version, by fingerprint; a section not read is absent."""
    _ensure(db)
    wanted = list(dict.fromkeys(hashes))
    found: dict[str, Read] = {}
    for start in range(0, len(wanted), 500):
        chunk = wanted[start:start + 500]
        rows = db.execute(f"SELECT text_hash, directives, model, created FROM directives WHERE prompt_version = ? "
                          f"AND text_hash IN ({','.join('?' * len(chunk))})", (prompt_version, *chunk))
        for row in rows:
            found[row["text_hash"]] = Read(row["text_hash"], read(row["directives"]), row["model"], decode(row["created"]))
    return found


def versions(db: sqlite3.Connection, hashes: Iterable[str]) -> dict[str, int]:
    """The newest reader version each of these sections was read under, by fingerprint; one never read is absent."""
    _ensure(db)
    wanted = list(dict.fromkeys(hashes))
    found: dict[str, int] = {}
    for start in range(0, len(wanted), 500):
        chunk = wanted[start:start + 500]
        for row in db.execute(f"SELECT text_hash, max(prompt_version) AS v FROM directives WHERE text_hash IN "
                              f"({','.join('?' * len(chunk))}) GROUP BY text_hash", chunk):
            found[row["text_hash"]] = row["v"]
    return found


def save(db: sqlite3.Connection, text_hash: str, prompt_version: int, directives: tuple[Directive, ...],
         model: str | None, now: datetime) -> None:
    """The section's directives under this reader version, in place of what an older version found."""
    _ensure(db)
    with transaction(db):
        db.execute("DELETE FROM directives WHERE text_hash = ? AND prompt_version < ?", (text_hash, prompt_version))
        db.execute("INSERT OR REPLACE INTO directives (text_hash, prompt_version, directives, model, created) "
                   "VALUES (?, ?, ?, ?, ?)", (text_hash, prompt_version, stored(directives), model, encode(now)))
