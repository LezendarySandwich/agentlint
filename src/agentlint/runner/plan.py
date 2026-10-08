"""A job's plan: the comparisons it runs, in order, frozen as JSON when the job is created and replayed verbatim.

A **comparison** is one situation against one ablation target (or against the configuration as it is, for the
stability screen): ``runs`` runs on each of its variants. A **run key** is ``<comparison>:<variant>:<n>``. The order
is depth-first across comparisons — one finishes before the next starts — and interleaved within one: baseline 0,
without 0, baseline 1, without 1… so a job stopped part-way leaves whole comparisons and, in the one it was on,
matched counts on both variants (05-runner.md, "Execution order").

The situation's text is frozen with the plan, so a regenerated probe never changes a job already made, and so are the
tools removed from every run (``removed_tools``: those that act outside the machine, D6), so every resume runs with the
same list. ``format`` versions the JSON: a plan written by a newer agentlint is refused, not misread (format 2 added
``removed_tools``; a format-1 plan reads with none; format 3 added each comparison's ``about``, the target whose
situation it runs — for a stability comparison, which removes nothing, the only record of it; older plans read with
none; format 4 added each comparison's ``gate``; format 5 its situation's ``matters``, frozen with its text, so the gate
checks the job's own record of what each situation was written to make the agent do; format 6 its ``sides``, the
variants it runs when not its phase's).

**A test runs both steps, target by target** (D9, decided with the user 2026-10-05; ``two_step_plan``): a target's
situations each get a stability comparison (the configuration as it is, ``runs`` times — the runs with the target),
then — when a variant can remove the target — an ablation comparison of the same situation that runs only without it
(``sides`` ``("without",)``, ``runs_per_side`` times; decided with the user 2026-10-07: the runs with the target are the
stability runs), whose ``gate`` is that stability comparison: it runs only if some of those runs met the situation's
expectation (``runner/gate.py``), so a situation whose expectation was never met spends no runs without the target. The gate
reads only this job's recorded runs, so a resume decides the same; the plan is the most the test can run, and its
size the job's ceiling.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from agentlint.rank.plan import Phase, RunCounts

PLAN_FORMAT = 6
_READS = (1, 2, 3, 4, 5, 6)  # format 1: no removed_tools; 1-2: no comparison's about; 1-3: no gate; 1-4: no matters;
# 1-5: no sides (every comparison its phase's variants)
BASELINE = "baseline"  # the configuration as it is
WITHOUT = "without"  # the same, with the comparison's ablation target removed
_VARIANTS = {Phase.STABILITY: (BASELINE,), Phase.ABLATION: (BASELINE, WITHOUT)}


class PlanError(ValueError):
    """A plan that can't be run as written: ambiguous keys, a phase with the wrong variants, a newer format."""


@dataclass(frozen=True)
class Comparison:
    id: str  # unique in the plan; no ':' — it starts every run key
    phase: Phase
    situation: str  # the situation's id
    text: str  # the situation's text, as it was when the job was created
    target: str | None  # the ablation target's id; None for the stability screen
    runs: int  # per variant
    about: str | None = None  # the target the situation was written for (its generator's), whatever the phase
    gate: str | None = None  # the stability comparison whose recorded runs must agree for this one to run (D9)
    matters: dict[str, Any] | None = field(default=None, hash=False)  # its situation's what matters (matters.WhatMatters
    # as a dict), when it names a thing: its stability runs must have done it for the gate to open (2026-10-07)
    sides: tuple[str, ...] | None = None  # the variants it runs, when not its phase's: ("without",) for a situation's
    # second step, its runs with the target being its stability comparison's (2026-10-07)

    @property
    def variants(self) -> tuple[str, ...]:
        return self.sides or _VARIANTS[self.phase]

    def planned(self) -> Iterator[PlannedRun]:
        for n in range(self.runs):
            for variant in self.variants:
                yield PlannedRun(self, variant, n)


@dataclass(frozen=True)
class PlannedRun:
    comparison: Comparison
    variant: str
    n: int

    @property
    def key(self) -> str:
        return run_key(self.comparison.id, self.variant, self.n)


def run_key(comparison: str, variant: str, n: int) -> str:
    return f"{comparison}:{variant}:{n}"


@dataclass(frozen=True)
class Plan:
    comparisons: tuple[Comparison, ...]
    removed_tools: tuple[str, ...] = ()  # offered to no run of the job (D6), by the harness's names; set at creation

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for comparison in self.comparisons:
            if not comparison.id or ":" in comparison.id:
                raise PlanError(f"comparison id {comparison.id!r} must be non-empty and hold no ':'")
            if comparison.id in seen:
                raise PlanError(f"comparison id {comparison.id!r} appears twice")
            seen.add(comparison.id)
            if comparison.runs < 1:
                raise PlanError(f"comparison {comparison.id} plans {comparison.runs} runs per variant")
            if (comparison.target is None) != (comparison.phase is Phase.STABILITY):
                raise PlanError(f"comparison {comparison.id}: a stability comparison has no target, an ablation one "
                                "has one")
            if comparison.sides is not None and (not comparison.sides or len(set(comparison.sides)) != len(comparison.sides)
                                                 or not set(comparison.sides) <= set(_VARIANTS[comparison.phase])):
                raise PlanError(f"comparison {comparison.id}: sides {comparison.sides} must be some of its phase's "
                                f"variants {_VARIANTS[comparison.phase]}, each once")
            if comparison.gate is not None:
                gate = next((c for c in self.comparisons if c.id == comparison.gate), None)
                if gate is None or comparison.gate not in seen - {comparison.id} or gate.phase is not Phase.STABILITY:
                    raise PlanError(f"comparison {comparison.id}: its gate {comparison.gate!r} must be a stability "
                                    "comparison earlier in the plan")

    def comparison(self, comparison_id: str) -> Comparison:
        found = next((c for c in self.comparisons if c.id == comparison_id), None)
        if found is None:
            raise KeyError(comparison_id)
        return found

    def gated_by(self, comparison_id: str) -> Comparison | None:
        """The comparison this stability comparison gates, if one is: its situation's with/without comparison."""
        return next((c for c in self.comparisons if c.gate == comparison_id), None)

    def runs(self) -> Iterator[PlannedRun]:
        """Every run, in the order the job runs them."""
        for comparison in self.comparisons:
            yield from comparison.planned()

    @property
    def size(self) -> int:
        return sum(comparison.runs * len(comparison.variants) for comparison in self.comparisons)

    def to_json(self) -> str:
        return json.dumps({"format": PLAN_FORMAT, "comparisons": [
            {"id": c.id, "phase": c.phase.value, "situation": c.situation, "text": c.text, "target": c.target,
             "runs": c.runs, "about": c.about, "gate": c.gate, "matters": c.matters,
             "sides": list(c.sides) if c.sides else None} for c in self.comparisons],
            "removed_tools": list(self.removed_tools)},
            sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> Plan:
        data = json.loads(text)
        if data.get("format") not in _READS:
            raise PlanError(f"plan format {data.get('format')!r}; this agentlint reads formats "
                            f"{', '.join(map(str, _READS))}")
        return cls(tuple(Comparison(c["id"], Phase(c["phase"]), c["situation"], c["text"], c["target"], c["runs"],
                                    c.get("about"), c.get("gate"), c.get("matters"),
                                    tuple(c["sides"]) if c.get("sides") else None) for c in data["comparisons"]),
                   tuple(data.get("removed_tools") or ()))


def stability_plan(situations: Sequence[tuple[str, str] | tuple[str, str, str | None]],
                   runs: int = RunCounts().stability) -> Plan:
    """The stability screen over situations given as (id, text), or (id, text, the target it was written for): each
    run ``runs`` times on the baseline."""
    return Plan(tuple(Comparison(f"cmp-{i:04d}", Phase.STABILITY, given[0], given[1], None, runs,
                                 given[2] if len(given) > 2 else None)
                      for i, given in enumerate(situations, 1)))


@dataclass(frozen=True)
class Planned:
    """One target of a test: its id, its situations as (id, text), and whether a variant can remove it."""

    target: str
    situations: tuple[tuple[str, str], ...] | tuple[tuple[str, str, dict[str, Any] | None], ...]  # (id, text[, matters])
    removable: bool


def two_step_plan(targets: Sequence[Planned], *, runs: int = RunCounts().stability,
                  runs_per_side: int = RunCounts().ablation) -> Plan:
    """A test of these targets, target by target (D9): each situation's stability comparison of ``runs`` runs, then,
    for a target a variant can remove, each situation's ablation comparison of ``runs_per_side`` a side, gated on its
    stability comparison. A situation that expects something (its what matters names a thing, 2026-10-07) is run only
    without the target in its second step — its stability runs are its runs with it; one that expects nothing keeps
    both sides."""
    comparisons: list[Comparison] = []

    def next_id() -> str:
        return f"cmp-{len(comparisons) + 1:04d}"

    for planned in targets:
        screens = []
        for given in planned.situations:
            situation, text = given[0], given[1]
            matters = given[2] if len(given) > 2 else None  # type: ignore[misc]
            screens.append(Comparison(next_id(), Phase.STABILITY, situation, text, None, runs, planned.target,
                                      matters=matters))
            comparisons.append(screens[-1])
        if planned.removable:
            for screen in screens:
                comparisons.append(Comparison(next_id(), Phase.ABLATION, screen.situation, screen.text, planned.target,
                                              runs_per_side, planned.target, gate=screen.id,
                                              sides=(WITHOUT,) if (screen.matters or {}).get("uses") else None))
    return Plan(tuple(comparisons))
