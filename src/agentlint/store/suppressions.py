"""Suppressions: findings the user has dismissed, until what they rest on changes.

A suppression is global — dismissing a finding id dismisses it in every project — but what a
finding rests on can differ by project (the always-on budget includes each project's own
instruction files). So each project keeps its own **baseline**: the declared hashes of the sources
the finding rested on there — armed, for every project where the finding is open, when it is
dismissed, and in any other project the first time a check sees it there — together with every source
the configuration declared at that moment. It lapses in a project when something in that baseline is
declared differently now or is gone from the configuration, or when the finding now rests on a source the
configuration didn't have when it was armed — a new instruction file or hook in the budget, a third
namesake. A source that was already configured never lapses it by joining or leaving the finding, as
sources do when the sessions read show different things.
Where it has lapsed, the finding is shown again, marked as previously dismissed rather than new.

Per project, a suppression is in one ``SuppressionState``: ACTIVE (hiding the finding), LAPSED (shown again) or
MOOT (no check finds the finding there now, so there is nothing to hide).
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from agentlint.config.serialize import as_model
from agentlint.report.finding import Finding
from agentlint.store import FindingStatus, transaction
from agentlint.store._time import decode, encode

MIN_PREFIX = 4


class SuppressionState(Enum):
    """What a suppression does in one project."""

    ACTIVE = "active"  # it hides the finding
    LAPSED = "lapsed"  # what the finding rests on there changed since it was armed: shown again
    MOOT = "moot"  # the last check that ran the finding's check there didn't produce it: nothing to hide


class UnknownFinding(LookupError):
    pass


class NoMatch(UnknownFinding):
    """No finding recorded or suppressed has an id starting with the prefix given."""


@dataclass(frozen=True)
class Suppression:
    id: str
    reason: str
    at: datetime
    baselines: Mapping[str, Mapping[str, str | None]]  # project -> {source id: declared hash} there
    known: Mapping[str, frozenset[str] | None] = field(default_factory=dict)  # project -> sources configured when armed

    def lapsed(self, project: str, declared: Mapping[str, str | None], inputs: Collection[str] = ()) -> bool:
        """Whether this project's baseline no longer describes what the finding rests on there.

        ``declared`` is the declared hash of every source in the project's current configuration, and
        ``inputs`` the sources the finding rests on now. A project without a baseline has not lapsed: it gets
        armed on first sight. A baseline armed before agentlint recorded what was configured (``known`` None)
        lapses only on change."""
        baseline = self.baselines.get(project)
        if baseline is None:
            return False
        known = self.known.get(project)
        return (any(declared.get(source) != h for source, h in baseline.items())
                or (known is not None and any(source not in baseline and source not in known for source in inputs)))


def resolve(db: sqlite3.Connection, prefix: str) -> str:
    """The one finding id that starts with ``prefix``, among findings ever seen or suppressed."""
    prefix = prefix.strip().lower()
    if len(prefix) < MIN_PREFIX or not all(ch in "0123456789abcdef" for ch in prefix):
        raise UnknownFinding(f"{prefix!r} is not a finding id: use at least {MIN_PREFIX} of its hex characters")
    matches = sorted({row["id"] for row in db.execute(
        "SELECT id FROM findings WHERE id LIKE ? || '%' UNION SELECT id FROM suppressions WHERE id LIKE ? || '%'",
        (prefix, prefix))})
    if not matches:
        raise NoMatch(f"no finding id starts with {prefix}; run `agentlint check` to see current ids")
    if len(matches) > 1:
        raise UnknownFinding(f"{prefix} matches {len(matches)} findings ({', '.join(m[:12] for m in matches[:5])}); "
                             "use more characters")
    return matches[0]


def latest(db: sqlite3.Connection, finding_id: str, project: str | None = None) -> tuple[str, str, dict] | None:
    """The latest (check, title, input hashes) of a finding: in one project, or wherever it is open."""
    if project is not None:
        row = db.execute("SELECT check_id, data, input_hashes FROM findings WHERE id = ? AND project = ?",
                         (finding_id, project)).fetchone()
    else:
        row = db.execute("SELECT check_id, data, input_hashes FROM findings WHERE id = ? "
                         "ORDER BY status = ? DESC, last_seen DESC LIMIT 1",
                         (finding_id, FindingStatus.OPEN.value)).fetchone()
    if row is None:
        return None
    return row["check_id"], as_model(Finding, json.loads(row["data"])).title, json.loads(row["input_hashes"])


def open_in(db: sqlite3.Connection, finding_id: str) -> list[str]:
    """The projects where a finding is open."""
    return [row["project"] for row in db.execute("SELECT project FROM findings WHERE id = ? AND status = ?",
                                                  (finding_id, FindingStatus.OPEN.value))]


def load(db: sqlite3.Connection) -> dict[str, Suppression]:
    baselines: dict[str, dict[str, dict]] = defaultdict(dict)
    known: dict[str, dict[str, frozenset[str] | None]] = defaultdict(dict)
    for row in db.execute("SELECT id, project, input_hashes, known FROM suppression_baselines"):
        baselines[row["id"]][row["project"]] = json.loads(row["input_hashes"])
        known[row["id"]][row["project"]] = frozenset(json.loads(row["known"])) if row["known"] is not None else None
    return {
        row["id"]: Suppression(row["id"], row["reason"], decode(row["at"]), baselines.get(row["id"], {}),
                               known.get(row["id"], {}))
        for row in db.execute("SELECT id, reason, at FROM suppressions")
    }


def arm(db: sqlite3.Connection, finding_id: str, project: str, hashes: Mapping[str, str | None],
        configured: Collection[str] | None, *, replace: bool = False) -> None:
    """Record a project's baseline — what the finding rests on there, and every source configured there now."""
    db.execute(f"INSERT OR {'REPLACE' if replace else 'IGNORE'} INTO suppression_baselines "
               "(id, project, input_hashes, known) VALUES (?, ?, ?, ?)",
               (finding_id, project, json.dumps(dict(hashes), sort_keys=True),
                json.dumps(sorted(configured)) if configured is not None else None))


def suppress(db: sqlite3.Connection, finding_id: str, reason: str, at: datetime) -> Suppression:
    """Dismiss a finding everywhere, (re-)arming it in every project where it is open against what it
    rests on there now: the sources its row names, at the hashes the project's latest stored configuration declares."""
    stamp = encode(at)
    with transaction(db):
        if latest(db, finding_id) is None:
            raise UnknownFinding(f"{finding_id} has never been seen by `agentlint check`")
        db.execute("INSERT INTO suppressions (id, reason, at) VALUES (?, ?, ?) "
                   "ON CONFLICT (id) DO UPDATE SET reason = excluded.reason, at = excluded.at", (finding_id, reason, stamp))
        from agentlint.store.snapshots import declared_hashes

        for row in db.execute("SELECT project, input_hashes FROM findings WHERE id = ? AND status = ?",
                              (finding_id, FindingStatus.OPEN.value)).fetchall():
            rests_on = json.loads(row["input_hashes"])
            configured = declared_hashes(db, row["project"])
            if configured is not None:
                # The row is as fresh as the last check that produced the finding; a later one (`--only`, or its check
                # unable to tell) may have stored newer configuration. Arm against that configuration, never the
                # row's older hashes beside it, or the suppression would lapse the moment it is made.
                rests_on = {source: configured.get(source) for source in rests_on}
            arm(db, finding_id, row["project"], rests_on, configured, replace=True)
    return load(db)[finding_id]


def unsuppress(db: sqlite3.Connection, finding_id: str) -> bool:
    with transaction(db):
        db.execute("DELETE FROM suppression_baselines WHERE id = ?", (finding_id,))
        return db.execute("DELETE FROM suppressions WHERE id = ?", (finding_id,)).rowcount > 0


def _produced_by_last_check(db: sqlite3.Connection, project: str, check_id: str, last_seen: str) -> bool:
    """Whether the latest recorded analysis in the project that evaluated the finding's check produced it — True
    where no analysis kept says otherwise."""
    for row in db.execute("SELECT at, checks FROM analyses WHERE project = ? ORDER BY at DESC", (project,)):
        if check_id in json.loads(row["checks"]):
            return decode(last_seen) >= decode(row["at"])
    return True


def status(db: sqlite3.Connection, item: Suppression) -> dict[str, SuppressionState]:
    """Per project with a baseline: MOOT where the last check that ran the finding's check didn't produce it —
    closed, kept open only because that check couldn't tell (a hook's output no session showed), or closed and
    since deleted by retention — LAPSED where it is shown again — against that project's latest stored
    configuration and what the finding last rested on there, the comparison `check` makes — and otherwise
    ACTIVE."""
    from agentlint.store.snapshots import declared_hashes

    states = {}
    for project in item.baselines:
        row = db.execute("SELECT status, input_hashes, check_id, last_seen FROM findings WHERE id = ? AND project = ?",
                         (item.id, project)).fetchone()
        if (row is None or row["status"] == FindingStatus.CLOSED.value
                or not _produced_by_last_check(db, project, row["check_id"], row["last_seen"])):
            states[project] = SuppressionState.MOOT  # the finding isn't produced there now: nothing a check would show
            continue
        declared = declared_hashes(db, project)
        if declared is None:
            states[project] = SuppressionState.ACTIVE  # no stored configuration to judge against
            continue
        inputs = json.loads(row["input_hashes"])
        states[project] = SuppressionState.LAPSED if item.lapsed(project, declared, inputs) else SuppressionState.ACTIVE
    return states
