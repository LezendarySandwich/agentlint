"""How far each comparison of a job got, worked out from the run keys recorded — never stored (one rule per fact).

Pure: a comparison and the set of recorded run keys in, its status out. Salvage, ``agentlint status`` and analysis
all read it, so they can't disagree about what counts as measured.

A comparison with fewer than MIN_RUNS_FOR_VERDICT recorded runs on any variant is **incomplete** — never
``no-change``, and never complete either, even when its plan asked for fewer runs and all of them are recorded: "no measurable effect" off two runs would be confidently wrong (05-runner.md, "Partial results").

A with/without comparison whose gate is closed (``runner/gate.py``: its situation's stability runs didn't agree) is
**skipped**: it spends no runs, and the job's planned runs no longer count it. Which gates are closed is the caller's to
say (``closed``), since only it holds the runs' traces.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from enum import Enum

from agentlint.runner.plan import Comparison, Plan

MIN_RUNS_FOR_VERDICT = 3


class ComparisonState(Enum):
    COMPLETE = "complete"  # every planned run of every variant recorded, and at least MIN_RUNS_FOR_VERDICT each
    PARTIAL = "partial"  # stopped short of the plan, but every variant has at least MIN_RUNS_FOR_VERDICT
    INCOMPLETE = "incomplete"  # started, and some variant has fewer than MIN_RUNS_FOR_VERDICT: no verdict
    NOT_ATTEMPTED = "not-attempted"  # no run recorded
    SKIPPED = "skipped"  # its gate is closed: its situation's stability runs didn't agree, so it never runs (D9)


@dataclass(frozen=True)
class ComparisonStatus:
    comparison: str
    state: ComparisonState
    recorded: tuple[tuple[str, int], ...]  # (variant, runs recorded), in the plan's variant order
    planned: int  # runs per variant

    @property
    def verdict_allowed(self) -> bool:
        """Enough runs on every variant for a verdict. Whether a partial one gets one is analysis's to say."""
        return self.state in (ComparisonState.COMPLETE, ComparisonState.PARTIAL)


def comparison_status(comparison: Comparison, recorded: Collection[str], *, gated_off: bool = False) -> ComparisonStatus:
    counts = tuple((variant, sum(run.key in recorded for run in comparison.planned() if run.variant == variant))
                   for variant in comparison.variants)
    fewest = min(n for _, n in counts)
    # The floor first, whatever the plan asked for: a plan of 2 runs a variant, fully recorded, is still too few.
    if gated_off:
        state = ComparisonState.SKIPPED
    elif not any(n for _, n in counts):
        state = ComparisonState.NOT_ATTEMPTED
    elif fewest < MIN_RUNS_FOR_VERDICT:
        state = ComparisonState.INCOMPLETE
    elif all(n == comparison.runs for _, n in counts):
        state = ComparisonState.COMPLETE
    else:
        state = ComparisonState.PARTIAL
    return ComparisonStatus(comparison.id, state, counts, comparison.runs)


def statuses(plan: Plan, recorded: Collection[str], closed: Collection[str] = ()) -> list[ComparisonStatus]:
    return [comparison_status(comparison, recorded, gated_off=comparison.id in closed) for comparison in plan.comparisons]


@dataclass(frozen=True)
class Progress:
    runs_recorded: int
    runs_planned: int
    launches: int  # every invocation, retries and interrupted ones included
    comparisons: tuple[tuple[ComparisonState, int], ...]  # how many comparisons are in each state

    @property
    def launched_without_a_run(self) -> int:
        """Launches that recorded nothing: retries, and runs that were interrupted."""
        return self.launches - self.runs_recorded


def progress(plan: Plan, recorded: Collection[str], launches: int, closed: Collection[str] = ()) -> Progress:
    """Runs recorded of planned — the planned leaving out comparisons whose gate is closed — and the comparisons by
    state."""
    found: Sequence[ComparisonStatus] = statuses(plan, recorded, closed)
    known = {run.key for run in plan.runs()}
    skipped = sum(c.runs * len(c.variants) for c in plan.comparisons if c.id in closed)
    return Progress(len(known & set(recorded)), plan.size - skipped, launches,
                    tuple((state, sum(s.state is state for s in found)) for state in ComparisonState))
