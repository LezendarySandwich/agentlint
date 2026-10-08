"""How strongly a target makes the agent do what its situations expect (decided with the user 2026-10-07; 07-analysis.md,
"The expectation's strength"). The situations and their expectations are trusted: each says what the target should
make the agent do (its what matters, naming a thing).

For one situation: the runs **with** the target are its consistency runs; count those that met the expectation.
- None met it: **not followed** — the target didn't make the agent do it, and it is never run without (there is
  nothing to take away); a finding, with what the runs did instead.
- Some met it: the runs **without** the target count the same, and the target's **strength** is the share with minus
  the share without — 1 is the only perfect result, every run with and none without (the user: "for the skill to have
  a place in my context, the skill has to demonstrate 3/3 with and 0/3 without"). Anything less is a finding: **partial**
  (it helps, not fully: not always followed, or the agent does it anyway in some runs without it), **adds nothing** (as
  often without it), **backfires** (less often with it).

Pure: what a run did is matched against the expectation through the adapter's action terms (``analysis/exercise.py``);
no model reads a run. Counts are said as they are — at 3 runs a side one run's difference can be chance, and the
result never claims more than the counts.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from agentlint.analysis.exercise import ran
from agentlint.analysis.projections import effect
from agentlint.analysis.stability import MIN_RUNS, Environment, differing
from agentlint.probes.matters import WhatMatters
from agentlint.trace.model import Trace
from agentlint.trace.terms import Terms

NOT_FOLLOWED, WORKS, PARTIAL, ADDS_NOTHING, BACKFIRES = "not followed", "works", "partial", "adds nothing", "backfires"
INCOMPLETE, MIXED, NO_EXPECTATION = "incomplete", "mixed environments", "no expectation"
FINDINGS = (NOT_FOLLOWED, PARTIAL, ADDS_NOTHING, BACKFIRES)  # every result but the perfect one, as the user decided


@dataclass(frozen=True)
class Run:
    """One run as the comparison takes it: its trace and the environment it ran under."""

    trace: Trace
    environment: Environment


@dataclass(frozen=True)
class Strength:
    """What one situation showed of its target."""

    verdict: str
    with_met: int  # runs with the target that met the expectation
    with_runs: int  # readable runs with the target
    without_met: int | None = None  # the same without it; None when it wasn't run (not followed) or isn't in yet
    without_runs: int | None = None
    strength: Fraction | None = None  # with share minus without share: 1 perfect, 0 adds nothing, below 0 backfires
    instead: tuple[tuple[str, int], ...] = field(default=())  # not followed: what the runs with it did, by term, most first
    differing: tuple[str, ...] = ()  # mixed environments: what differs

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "with": {"met": self.with_met, "runs": self.with_runs},
                "without": None if self.without_runs is None else {"met": self.without_met, "runs": self.without_runs},
                "strength": None if self.strength is None else float(self.strength),
                "instead": [{"term": term, "runs": n} for term, n in self.instead], "differing": list(self.differing)}


def _instead(runs: Sequence[Run], terms: Terms) -> tuple[tuple[str, int], ...]:
    """What the runs did, by term (``projections.effect``), the most runs first; a run that did nothing is "no action"."""
    counted: Counter[str] = Counter()
    for run in runs:
        used = effect(run.trace, terms)
        counted.update(used or ("no action",))
    return tuple(sorted(counted.items(), key=lambda item: (-item[1], item[0])))


def situation(what: WhatMatters | None, with_runs: Sequence[Run], without_runs: Sequence[Run] | None,
              terms: Terms) -> Strength:
    """One situation's result, from its runs with the target (its consistency runs) and, once run, without it."""
    met_with = sum(ran(what, run.trace, terms) for run in with_runs) if what is not None and what.uses else 0
    if what is None or what.uses is None:
        return Strength(NO_EXPECTATION, 0, len(with_runs))
    if len(with_runs) < MIN_RUNS:
        return Strength(INCOMPLETE, met_with, len(with_runs))
    split = differing(run.environment for run in [*with_runs, *(without_runs or ())])
    if split:
        return Strength(MIXED, met_with, len(with_runs), differing=split)
    if met_with == 0:
        return Strength(NOT_FOLLOWED, 0, len(with_runs), instead=_instead(with_runs, terms))
    if without_runs is None or len(without_runs) < MIN_RUNS:
        return Strength(INCOMPLETE, met_with, len(with_runs), None if without_runs is None else
                        sum(ran(what, run.trace, terms) for run in without_runs),
                        None if without_runs is None else len(without_runs))
    met_without = sum(ran(what, run.trace, terms) for run in without_runs)
    strength = Fraction(met_with, len(with_runs)) - Fraction(met_without, len(without_runs))
    verdict = WORKS if strength == 1 else PARTIAL if strength > 0 else ADDS_NOTHING if strength == 0 else BACKFIRES
    return Strength(verdict, met_with, len(with_runs), met_without, len(without_runs), strength)


@dataclass(frozen=True)
class TargetStrength:
    """What a target showed across its situations: ``works`` only when every situation worked; otherwise its weakest
    result leads, with how many situations ended each way and the mean strength (a situation not followed counts 0)."""

    verdict: str
    strength: Fraction | None
    situations: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "strength": None if self.strength is None else float(self.strength),
                "situations": dict(self.situations)}


_WEAKEST = (BACKFIRES, NOT_FOLLOWED, ADDS_NOTHING, PARTIAL, WORKS)  # which result leads a target's, the worst first


def target(results: Sequence[Strength]) -> TargetStrength:
    tally = Counter(r.verdict for r in results)
    measured = [r for r in results if r.verdict in (*FINDINGS, WORKS)]
    if not measured or len(measured) < len(results):
        undecided = next((v for v in (INCOMPLETE, MIXED, NO_EXPECTATION) if tally[v]), INCOMPLETE)
        return TargetStrength(undecided, None, dict(tally))
    mean = sum((r.strength if r.strength is not None else Fraction(0)) for r in measured) / len(measured)
    lead = next(v for v in _WEAKEST if tally[v])
    return TargetStrength(lead, mean, dict(tally))
