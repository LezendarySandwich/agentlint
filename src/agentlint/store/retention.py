"""One setting decides what is kept: `keep_days` (docs/tech_impl/00-overview.md, "Settings").

Everything time-based older than that is deleted — usage, archived transcripts, configuration readings, closed
findings, analyses, closed jobs (complete, salvaged, stopped at their ceiling) with their runs and their workers' logs,
agentlint's diagnostics about itself — except what is current:
the latest reading and analysis per project, open findings, suppressions, every open job whatever its age, generated
situations (they last until their target's text changes), and verdicts
for the configuration a
project has now. An archived transcript keeps only its
bookkeeping — how far it had got and a hash of its last bytes, as forgetting leaves it — so one the harness still
has isn't read in again; that goes too once the harness no longer lists the transcript.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from datetime import datetime, timedelta

from agentlint.store import FindingStatus, diagnostics, jobs, transaction
from agentlint.store._time import encode


def _referenced(db: sqlite3.Connection) -> set[str]:
    refs: set[str] = set()

    def collect(node) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("text_ref"), str):
                refs.add(node["text_ref"])
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    for row in db.execute("SELECT data FROM snapshots"):
        collect(json.loads(zlib.decompress(row["data"])))
    return refs


def prune(db: sqlite3.Connection, now: datetime, keep_days: int) -> int:
    """Delete what is older than keep_days and isn't current; how many rows went."""
    before = now - timedelta(days=keep_days)
    stamp = encode(before)
    removed = 0
    with transaction(db):
        for statement, args in (
            ("DELETE FROM observations WHERE at < ?", (stamp,)),
            ("DELETE FROM archive_chunks WHERE key IN (SELECT key FROM archive_files WHERE mtime_epoch_s < ?)",
             (before.timestamp(),)),
            # the row stays, as forget leaves it: the harness may still have the file, and it isn't read again
            ("UPDATE archive_files SET forgotten_bytes = bytes_read "
             "WHERE mtime_epoch_s < ? AND forgotten_bytes < bytes_read", (before.timestamp(),)),
            ("DELETE FROM findings WHERE status = ? AND last_seen < ?", (FindingStatus.CLOSED.value, stamp)),
            ("DELETE FROM analyses WHERE at < ? AND at < (SELECT max(at) FROM analyses AS a WHERE a.project = analyses.project)",
             (stamp,)),
            ("DELETE FROM verdicts WHERE last_measured < ? AND coalesce(last_fingerprint, '') NOT IN "
             "(SELECT fingerprint FROM snapshots AS s WHERE s.project = verdicts.project AND s.taken_at = "
             "(SELECT max(taken_at) FROM snapshots AS t WHERE t.project = verdicts.project))", (stamp,)),
        ):
            removed += db.execute(statement, args).rowcount
        removed += jobs.prune(db, before)  # closed jobs with their runs; an open job is kept whatever its age
        removed += diagnostics.prune(db, before)  # generated situations are kept: they last until their target changes
        removed += db.execute(
            "DELETE FROM snapshots WHERE taken_at < ? AND taken_at < "
            "(SELECT max(taken_at) FROM snapshots AS s WHERE s.project = snapshots.project)", (stamp,)).rowcount
        # every text no kept reading refers to — also one a reading under the same fingerprint replaced, with no row
        # deleted: what a session showed isn't in the fingerprint
        keep = _referenced(db)
        orphans = [row["hash"] for row in db.execute("SELECT hash FROM blobs") if row["hash"] not in keep]
        db.executemany("DELETE FROM blobs WHERE hash = ?", [(h,) for h in orphans])
        removed += len(orphans)
    from agentlint.runner.worker import remove_logs  # a pruned job's log goes with it (a file, not a row)

    remove_logs(db)
    return removed
