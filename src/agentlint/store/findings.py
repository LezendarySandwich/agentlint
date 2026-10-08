"""Which findings are open in each project, and which of them a suppression hides — scoped to what ran.

Each recorded analysis is diffed by finding id against the project's open findings: a finding found is opened, or
kept open. A finding absent now is closed only where this analysis could have seen it and didn't:

- its check judged it — ``CheckResults.judges``: evaluated (not deselected by ``--only``, not ``NotEvaluated``) and,
  for a check whose evidence covers only some of its earlier findings (``Partial``), within that scope;
- nothing it rested on is still configured but went unobserved this time (a hook whose output no recent session
  showed) — a source that is gone doesn't keep it open: removing it is how such findings get closed. A check that
  judges by something other than its sources' text (``not_by_text``: hook-health, from recorded runs) is exempt;
- for a check whose findings can vanish with nothing they rest on changed (``needs_change``: a ranking, or members
  only sessions show), one of its own inputs changed.

Everything these rules need is derived here from the check results and the configuration reading, so a caller can't
leave a guard out. Open and closed are bookkeeping, never shown: nothing reports a finding as new or fixed
(2026-10-03). What reads them is suppressions — armed where a finding is open, moot where it is closed — and
retention, which deletes closed findings after ``keep_days``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from agentlint.checks import CheckResults
from agentlint.config.content import NotObserved
from agentlint.config.model import ConfigSnapshot
from agentlint.config.serialize import as_model, to_data
from agentlint.config.taxonomy import Mechanism
from agentlint.report.finding import Finding
from agentlint.store import FindingStatus, transaction
from agentlint.store._time import encode
from agentlint.store.suppressions import Suppression, arm, load
from agentlint.store.usage import refuse_forgotten


@dataclass(frozen=True)
class Tracked:
    finding: Finding
    suppression: Suppression | None = None
    suppression_lapsed: bool = False  # dismissed before, but what it rests on has changed since

    @property
    def suppressed(self) -> bool:
        return self.suppression is not None and not self.suppression_lapsed


@dataclass(frozen=True)
class Lifecycle:
    tracked: tuple[Tracked, ...]  # in the order the findings were given
    note: str | None = None  # set when this reading was not recorded, saying why

    @property
    def recorded(self) -> bool:
        return self.note is None

    @property
    def shown(self) -> tuple[Tracked, ...]:
        return tuple(t for t in self.tracked if not t.suppressed)

    @property
    def suppressed_count(self) -> int:
        return sum(1 for t in self.tracked if t.suppressed)


_NO_CONFIGURATION = (Mechanism.HARNESS_GENERATED, Mechanism.HARNESS_BUILTIN)


def inputs_of(finding: Finding, snapshot: ConfigSnapshot) -> dict[str, str | None]:
    """The declared hash of every source of configuration a finding rests on (None for one that is gone).

    Sources the harness makes — its listings, its own prompt — carry no configuration: their declared
    hash never changes, and whether a finding involves them depends on what recent sessions showed.
    They are not inputs — except a package's share of a listing, which is declared from disk, one per
    installed package: it is how a newly installed package joins what a finding rests on (and, being
    configured already, can't lapse a suppression by coming and going with the sessions read)."""
    hashes: dict[str, str | None] = {}
    for source_id in finding.sources:
        try:
            source = snapshot.source(source_id)
        except KeyError:
            hashes[source_id] = None
            continue
        if source.provenance.point.mechanism not in _NO_CONFIGURATION or (
                source.provenance.point.mechanism is Mechanism.HARNESS_GENERATED and source.provenance.package):
            hashes[source_id] = source.declared_hash
    return hashes


def record(
    db: sqlite3.Connection,
    *,
    project: str,
    results: CheckResults,
    snapshot: ConfigSnapshot,
    at: datetime,
    write: bool,
    note: str | None,
) -> Lifecycle:
    """Diff this analysis against the project's open findings and, with ``write``, record it.

    With ``write=False`` nothing is written and nothing is closed: suppressions still hide what they hide, and
    ``note`` says why the reading wasn't recorded. A recorded reading has no note; one not recorded must say why."""
    if write != (note is None):
        raise ValueError("a recorded reading has no note; one not recorded says why")
    stamp = encode(at)
    declared = {source.id: source.declared_hash for source in snapshot.sources}
    unobserved = {source.id for source in snapshot.sources if isinstance(source.content, NotObserved)}
    with transaction(db):
        if write:
            refuse_forgotten(db, snapshot.sessions)  # what it found may quote them
        suppressions = load(db)
        open_before = {
            row["id"]: (row["check_id"], as_model(Finding, json.loads(row["data"])).discriminator,
                        json.loads(row["input_hashes"]))
            for row in db.execute(
                "SELECT id, check_id, data, input_hashes FROM findings WHERE project = ? AND status = ?",
                (project, FindingStatus.OPEN.value),
            )
        }
        tracked = []
        for finding in results.findings:
            inputs = inputs_of(finding, snapshot)
            dismissed = suppressions.get(finding.id)
            lapsed = bool(dismissed and dismissed.lapsed(project, declared, inputs))
            if write and dismissed is not None and project not in dismissed.baselines:
                arm(db, finding.id, project, inputs, declared)  # first sight here since it was dismissed
            elif write and dismissed is not None and inputs and not dismissed.baselines[project] and not lapsed:
                # Armed when the finding rested on nothing (hook-health before 2026-10-04), it now rests on sources that
                # were all configured then: they join without lapsing it, and from now on editing them does.
                arm(db, finding.id, project, inputs, dismissed.known.get(project), replace=True)
            tracked.append(Tracked(finding, dismissed, lapsed))
            if not write:
                continue
            values = {
                "project": project, "id": finding.id, "check_id": finding.check, "at": stamp,
                "status": FindingStatus.OPEN.value, "fingerprint": snapshot.fingerprint,
                "data": json.dumps(to_data(finding)),  # ASCII: a lone surrogate can't fail it
                "input_hashes": json.dumps(inputs, sort_keys=True),
            }
            if finding.id in open_before:
                db.execute(
                    "UPDATE findings SET check_id = :check_id, last_seen = :at, fingerprint = :fingerprint, "
                    "input_hashes = :input_hashes, data = :data WHERE project = :project AND id = :id", values)
            else:
                db.execute(
                    "INSERT INTO findings (project, id, check_id, status, first_seen, last_seen, fingerprint, "
                    "input_hashes, data) VALUES (:project, :id, :check_id, :status, :at, :at, :fingerprint, "
                    ":input_hashes, :data) ON CONFLICT (project, id) DO UPDATE SET check_id = excluded.check_id, "
                    "status = excluded.status, first_seen = excluded.first_seen, last_seen = excluded.last_seen, "
                    "fingerprint = excluded.fingerprint, input_hashes = excluded.input_hashes, data = excluded.data",
                    values)
        if write:
            present = {finding.id for finding in results.findings}
            for finding_id, (check_id, discriminator, inputs) in open_before.items():
                if finding_id in present or not results.judges(check_id, discriminator):
                    continue  # found again, or these results couldn't tell
                if check_id not in results.not_by_text and any(source in unobserved for source in inputs):
                    continue  # still configured, just not seen this time
                if check_id in results.needs_change and all(declared.get(i) == h for i, h in inputs.items()):
                    continue  # nothing it rests on changed: outranked, or out of the session window
                db.execute("UPDATE findings SET status = ?, last_seen = ? WHERE project = ? AND id = ?",
                           (FindingStatus.CLOSED.value, stamp, project, finding_id))
            checks = json.dumps(sorted(results.evaluated))
            last = db.execute("SELECT at, fingerprint, checks FROM analyses WHERE project = ? ORDER BY at DESC LIMIT 1",
                              (project,)).fetchone()
            if last is not None and (last["fingerprint"], last["checks"]) == (snapshot.fingerprint, checks):
                # The same configuration judged by the same checks — a page reloaded, `check` run again: the latest
                # analysis moves to now rather than piling up beside it. Only the latest analysis that ran a check
                # is ever read (suppressions.py), and this one ran the same checks, so nothing is lost.
                db.execute("UPDATE analyses SET at = ? WHERE project = ? AND at = ?", (stamp, project, last["at"]))
            else:
                db.execute("INSERT INTO analyses (project, at, fingerprint, checks) VALUES (?, ?, ?, ?)",
                           (project, stamp, snapshot.fingerprint, checks))
    return Lifecycle(tuple(tracked), note)
