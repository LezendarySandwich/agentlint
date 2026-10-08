"""What has been measured about each target of the plan — a rule, or a whole skill or hook — per project, across
jobs: the ranker's staleness record (docs/tech_impl/03-ranker.md). The runner (v2) writes it; the ranker reads it."""

from __future__ import annotations

import json
import sqlite3

from agentlint.rank.plan import TargetStatus
from agentlint.store._time import decode


def statuses(db: sqlite3.Connection, project: str) -> dict[str, TargetStatus]:
    return {
        row["target_id"]: TargetStatus(
            row["target_id"], decode(row["last_measured"]) if row["last_measured"] else None, row["last_fingerprint"],
            row["last_job_id"], json.loads(row["verdict"]) if row["verdict"] else None, row["situations_tried"])
        for row in db.execute(
            "SELECT target_id, last_measured, last_fingerprint, last_job_id, verdict, situations_tried "
            "FROM verdicts WHERE project = ?", (project,))
    }
