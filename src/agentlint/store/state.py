"""agentlint's own bookkeeping, never a setting (the ``state`` table, schema 14).

Each key is named here, with what its JSON value holds. ``forget`` never deletes them and ``keep_days`` never prunes
them: they say nothing about your transcripts.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from agentlint.store import transaction
from agentlint.store._time import encode

WELCOMED = "welcomed"  # {"by": "cli" | "ui" | "config.toml"}: the first-run notice was shown — or panel closed — once
ONBOARDED = "onboarded"  # {"accepted": true, "by": "ui"}: the one-time test onboarding was accepted (D18), set once
TICKED = "ticked"  # {"<harness> <project>": [target id, ...]}: the targets ticked to test in Tests › Plan, per harness and
# project, the most recently ticked last (ui/testing.py keeps the last 50); a project never ticked has no entry
LAST_SYNC = "last_sync"  # {"ok": bool, "changed": int, "turns": int} or {"ok": false, "error": str}: `agentlint sync`'s last


def get(db: sqlite3.Connection, key: str) -> tuple[Any, str] | None:
    """The value and when it was written (ISO text), or None when it never was. A value that isn't JSON reads as None:
    bookkeeping that can't be read is bookkeeping not yet written."""
    row = db.execute("SELECT value, updated FROM state WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value"]), row["updated"]
    except ValueError:
        return None


def put(db: sqlite3.Connection, key: str, value: Any, now: datetime) -> None:
    """Write it, replacing what was there. Joins an open transaction."""
    with transaction(db):
        db.execute("INSERT INTO state (key, value, updated) VALUES (?, ?, ?) "
                   "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated = excluded.updated",
                   (key, json.dumps(value), encode(now)))


def claim(db: sqlite3.Connection, key: str, value: Any, now: datetime) -> bool:
    """Write it only if it was never written; whether this call wrote it. Two processes asking at once: one wins."""
    with transaction(db):
        return db.execute("INSERT OR IGNORE INTO state (key, value, updated) VALUES (?, ?, ?)",
                          (key, json.dumps(value), encode(now))).rowcount == 1
