"""What tests said of a configuration's targets, read from the store with the configuration (``Reading.tests``): each
target's latest decided test of what its situations expect (``analysis/expectation.py``), for the behaviour checks
(``checks/tested.py``, decided with the user 2026-10-07: "any delta short of perfect is a finding").

A test counts for a target only while the target's text is the text its situations were written for — a situation's
id is a hash of that text's hash and the situation (``probes.generate.probe_id``), so an edited target leaves its
tests behind, and they are not read. The newest test that decided the target is taken: one still running, or whose
runs are too few, is passed over for an older decided one. A test from before expectations (its situations expect
nothing) or of a target no variant could remove (no runs without it) says nothing here.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Sequence

from agentlint.analysis import expectation
from agentlint.analysis.stability import Environment, ScreenState
from agentlint.config.model import ConfigSnapshot
from agentlint.config.reading import TestedSituation, TestedTarget, TestEvidence
from agentlint.probes.generate import probe_id, target_hash
from agentlint.probes.matters import WhatMatters
from agentlint.probes.matters import parsed as parse_matters
from agentlint.rank.plan import targets
from agentlint.runner.gate import by_comparison, screened, trace_of
from agentlint.runner.plan import WITHOUT, Comparison, Phase, Plan, PlanError
from agentlint.store import JobStatus, jobs, probes
from agentlint.trace.model import Trace
from agentlint.trace.terms import Terms

MOST_JOBS = 30  # a project's newest jobs looked through: a target's latest decided test is almost always among them


def _expects(comparison: Comparison) -> WhatMatters | None:
    if not comparison.matters:
        return None
    what = parse_matters(comparison.matters)
    return what if what.uses else None


def _written_for(db: sqlite3.Connection, comparison: Comparison, current: str) -> bool:
    """Whether the situation was written for the target's text as it is now."""
    probe = probes.load(db, comparison.situation)
    if probe is not None:
        return probe.target_hash == current
    return probe_id(current, comparison.text) == comparison.situation  # regenerated away, still the same text


def _runs(rows: Sequence[jobs.Run], fingerprint: str) -> list[expectation.Run]:
    found = []
    for row in rows:
        trace: Trace | None = trace_of(row)
        if trace is not None:
            found.append(expectation.Run(trace, Environment(trace.harness_version, tuple(sorted(set(trace.models))),
                                                            fingerprint)))
    return found


def _all_in(comparison: Comparison, rows: Sequence[jobs.Run]) -> bool:
    recorded = {row.run_key for row in rows}
    return all(run.key in recorded for run in comparison.planned())


def _target(plan: Plan, job: jobs.Job, about: str, rows: dict[str, list[jobs.Run]],
            terms: Terms) -> TestedTarget | None:
    """A target's result in one job, or None when the job didn't decide it."""
    situations: list[TestedSituation] = []
    strengths: list[expectation.Strength] = []
    still = job.status is JobStatus.OPEN
    for comparison in plan.comparisons:
        if comparison.phase is not Phase.STABILITY or comparison.about != about:
            continue
        what, paired = _expects(comparison), plan.gated_by(comparison.id)
        if what is None or paired is None:
            return None
        mine = rows.get(comparison.id, [])
        if still and not _all_in(comparison, mine):
            return None
        with_runs = _runs(mine, job.fingerprint)
        found = expectation.situation(what, with_runs, None, terms)
        if found.verdict != expectation.NOT_FOLLOWED:
            without = rows.get(paired.id, [])
            if still and not _all_in(paired, without):
                return None
            without = [row for row in without if row.run_key.split(":")[1] == WITHOUT]  # a plan from before: both sides
            found = expectation.situation(what, with_runs, _runs(without, job.fingerprint), terms)
        if found.verdict not in (*expectation.FINDINGS, expectation.WORKS):
            return None
        strengths.append(found)
        situations.append(TestedSituation(
            comparison.situation, comparison.text, what.words(), found.verdict, found.with_met, found.with_runs,
            found.without_met, found.without_runs, None if found.strength is None else float(found.strength),
            found.instead, screened(mine, job.fingerprint).screen.state is not ScreenState.DISAGREED))
    if not strengths:
        return None
    result = expectation.target(strengths)
    return TestedTarget(about, job.job_id, job.created, result.verdict,
                        None if result.strength is None else float(result.strength), tuple(situations))


def load(db: sqlite3.Connection, snapshot: ConfigSnapshot, harness: str, terms: Terms | None) -> TestEvidence | None:
    """Each of the snapshot's targets' latest decided test, by the project's newest jobs of this harness; None when
    the harness's runs can't be read here (no action terms)."""
    if terms is None:
        return None
    current = {target.id: target_hash(target) for target in targets(snapshot)}
    found: dict[str, TestedTarget] = {}
    settled: set[str] = set()  # decided, or whose newest test was written for other text: older ones are too
    for job in jobs.listed(db, os.path.realpath(str(snapshot.cwd)))[:MOST_JOBS]:
        if job.harness != harness:
            continue
        try:
            plan = Plan.from_json(job.plan)
        except (PlanError, ValueError, KeyError, TypeError):
            continue
        about = [a for a in dict.fromkeys(c.about for c in plan.comparisons if c.phase is Phase.STABILITY)
                 if a is not None and a in current and a not in settled]
        if not about:
            continue
        rows = by_comparison(jobs.runs(db, job.job_id))
        for target in about:
            mine = [c for c in plan.comparisons if c.phase is Phase.STABILITY and c.about == target]
            if not all(_written_for(db, c, current[target]) for c in mine):
                settled.add(target)
                continue
            result = _target(plan, job, target, rows, terms)
            if result is not None:
                found[target] = result
                settled.add(target)
        if len(settled) == len(current):
            break
    return TestEvidence(tuple(found[t] for t in current if t in found))
