"""Whether a situation's runs without its target happen (D9, decided with the user 2026-10-05 and 2026-10-07;
07-analysis.md, "The expectation's strength"): only when some of its runs with the target — its stability comparison,
earlier in the same job — met the situation's expectation (what its what matters names). None did: the target wasn't
followed, which is the finding, and there is nothing to take away, so the runs without it never happen. Whether those
runs agreed doesn't decide it (an inconsistency is a finding of its own); too few readable runs, or runs in mixed
environments, keep it shut, as they decide nothing. A plan from before expectations (its comparisons name no thing) keeps
the earlier rule: its runs must have screened ``agreed``. Pure: it reads only the job's recorded runs, so every resume of a job, and every
reader of it (the engine, its progress, its results), decides the same.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from agentlint.analysis.exercise import ran
from agentlint.analysis.stability import MIN_RUNS
from agentlint.analysis.projections import used
from agentlint.analysis.stability import Run as ScreenRun
from agentlint.analysis.stability import Screen, ScreenState, screen
from agentlint.runner.plan import Comparison, Plan
from agentlint.store import jobs
from agentlint.probes.matters import parsed
from agentlint.trace.model import Trace
from agentlint.trace.terms import Terms

NOT_FOLLOWED = "not followed"  # why a gate is closed when none of the runs with the target met the expectation


@dataclass(frozen=True)
class Screened:
    """One stability comparison's runs, screened: its screen, the runs whose trace was read (index, row, trace) and
    those whose trace couldn't be (index, row), each in run order. One whose trace couldn't be read (a timeout before
    the harness wrote anything) is screened with nothing — never as a run that did nothing."""

    screen: Screen
    read: tuple[tuple[int, jobs.Run, Trace], ...]
    unread: tuple[tuple[int, jobs.Run], ...]


def trace_of(row: jobs.Run) -> Trace | None:
    """A recorded run's trace, or None when what was stored can't be read as one."""
    try:
        data = json.loads(row.trace)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return Trace.from_dict(data)
    except (KeyError, TypeError, ValueError):
        return None


def index_of(row: jobs.Run) -> int:
    return int(row.run_key.rsplit(":", 1)[1])


def screened(rows: Iterable[jobs.Run], fingerprint: str) -> Screened:
    """The stability screen of these runs by D10's rule for "inconsistent" (``projections.used``)."""
    read, unread = [], []
    for row in sorted(rows, key=index_of):
        trace = trace_of(row)
        if trace is None:
            unread.append((index_of(row), row))
        else:
            read.append((index_of(row), row, trace))
    found = screen([ScreenRun(index, trace, fingerprint) for index, _, trace in read], used)
    return Screened(found, tuple(read), tuple(unread))


def by_comparison(rows: Iterable[jobs.Run]) -> dict[str, list[jobs.Run]]:
    """The runs recorded for each comparison, by its id, in run order (run 10 after run 9, not after run 1)."""
    found: dict[str, list[jobs.Run]] = {}
    for row in rows:
        found.setdefault(row.run_key.split(":", 1)[0], []).append(row)
    return {comparison: sorted(listed, key=index_of) for comparison, listed in found.items()}


def closed_why(gate: Comparison, rows: Sequence[jobs.Run], fingerprint: str, terms: Terms | None = None) -> str | None:
    """Why the comparison ``gate`` gates may not run, or None when it may — and None too while some of the gate's
    planned runs aren't recorded (``gate_open`` says undecided). With an expectation to check (the situation names a
    thing, and the adapter's action terms are given): ``NOT_FOLLOWED`` when none of its readable runs met it, else
    "incomplete" (fewer than MIN_RUNS readable) or "mixed environment". Without one: its screen state when not
    ``agreed``."""
    found = screened(rows, fingerprint)
    state = found.screen.state
    if terms is not None and gate.matters and parsed(gate.matters).uses:
        if state in (ScreenState.MIXED_ENVIRONMENT,) or len(found.read) < MIN_RUNS:
            return state.value if state is ScreenState.MIXED_ENVIRONMENT else ScreenState.INCOMPLETE.value
        what = parsed(gate.matters)
        return None if any(ran(what, trace, terms) for _, _, trace in found.read) else NOT_FOLLOWED
    return None if state is ScreenState.AGREED else state.value


def gate_open(gate: Comparison, rows: Sequence[jobs.Run], fingerprint: str, terms: Terms | None = None) -> bool | None:
    """Whether the comparison ``gate`` gates may run: None while some of the gate's planned runs aren't recorded,
    else whether ``closed_why`` found nothing against it."""
    recorded = {row.run_key for row in rows}
    if any(run.key not in recorded for run in gate.planned()):
        return None
    return closed_why(gate, rows, fingerprint, terms) is None


def closed(plan: Plan, rows: Mapping[str, Sequence[jobs.Run]], fingerprint: str,
           terms: Terms | None = None) -> frozenset[str]:
    """The comparisons of ``plan`` whose gate is closed: decided, and not open. Those spend no runs."""
    return frozenset(c.id for c in plan.comparisons
                     if c.gate is not None
                     and gate_open(plan.comparison(c.gate), rows.get(c.gate, ()), fingerprint, terms) is False)
