"""agentlint's findings about itself (decided 2026-10-04, D16): the generator wrote nothing for a target, a situation
named its own target and was dropped, a run was retried for an API error.

They are useful for debugging agentlint and for nothing else, so they are their own table and their own list, never
in ``findings``, its counts, ``--json`` findings or an exit code. A diagnostic's ``kind`` is its producer's name for
what happened (``probes/generate.py`` names the generator's); the store keeps it as text, so a new producer needs no
migration. ``keep_days`` prunes them by age; ``forget`` deletes them all.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agentlint.store import transaction
from agentlint.store._time import decode, encode


@dataclass(frozen=True)
class Diagnostic:
    kind: str
    subject: str  # what it is about: a target id, a job id
    message: str  # one sentence for a person
    data: Mapping[str, Any] = field(default_factory=dict)  # the details, as JSON
    created: datetime | None = None  # set by the store when read back
    id: int | None = None

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.subject.strip() or not self.message.strip():
            raise ValueError("a diagnostic needs a kind, a subject and a message")


def add(db: sqlite3.Connection, diagnostic: Diagnostic, now: datetime) -> int:
    """Store one; its id. Joins an open transaction."""
    with transaction(db):
        return db.execute("INSERT INTO diagnostics (kind, subject, message, data, created) VALUES (?, ?, ?, ?, ?)",
                          (diagnostic.kind, diagnostic.subject, diagnostic.message,
                           json.dumps(dict(diagnostic.data), sort_keys=True), encode(now))).lastrowid


def listed(db: sqlite3.Connection, *, subject: str | None = None, kind: str | None = None) -> list[Diagnostic]:
    """Every diagnostic, or a subject's or kind's, the most recent first."""
    where, args = [], []
    for column, value in (("subject", subject), ("kind", kind)):
        if value is not None:
            where.append(f"{column} = ?")
            args.append(value)
    query = "SELECT id, kind, subject, message, data, created FROM diagnostics"
    if where:
        query += " WHERE " + " AND ".join(where)
    return [Diagnostic(row["kind"], row["subject"], row["message"], json.loads(row["data"]), decode(row["created"]),
                       row["id"]) for row in db.execute(query + " ORDER BY created DESC, id DESC", args)]


def prune(db: sqlite3.Connection, before: datetime) -> int:
    """``keep_days``: every diagnostic created before ``before``. How many went."""
    with transaction(db):
        return db.execute("DELETE FROM diagnostics WHERE created < ?", (encode(before),)).rowcount


def delete_all(db: sqlite3.Connection) -> int:
    """``forget``: every diagnostic. How many went."""
    with transaction(db):
        return db.execute("DELETE FROM diagnostics").rowcount
