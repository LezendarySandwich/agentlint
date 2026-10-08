"""The stability screen: did repeated runs of one situation, unchanged, do the same thing? (07 "Instability")

Each run is reduced to steps by a projection passed in (Open Question 2 decides which; none is built in), the steps
to an outcome class by a hash, and the runs grouped into branches — runs of one outcome class. A screen then says one
of five things:

- **incomplete** — fewer than ``MIN_RUNS`` runs: not a measurement
- **mixed environment** — two runs recorded a different harness version, set of models or configuration fingerprint:
  refused, never compared, because a split between two versions is not the agent disagreeing with itself. Only what
  was recorded is compared: a run that recorded none of a field (no model answered it, say) differs from nobody in it,
  and is listed in ``unrecorded`` for the runner to classify (whether a failed run is an outcome is readiness D7, open).
  Being listed doesn't take a run out of the comparison: until the runner classifies it, an unrecorded run — even one
  that recorded none of the three — is grouped and counts toward the modal share like any other
- **no action** — every compared run's projection is empty: no run used a tool, skill or agent (decided with the user
  2026-10-05). Never agreed: runs that all did nothing say nothing about what the situation was written for, and two
  sides that both do nothing are no evidence that a rule does nothing
- **disagreed** — the modal branch's share is below the floor, empty runs among them or not
- **agreed** — at or above it. **Agreement is not evidence of stability**: at three runs a true 50/50 split looks
  unanimous a quarter of the time, so this state is never shown as "stable" (02-design "Run counts")

At ``RUNS_STABILITY`` = 3 the floor of 0.9 means all three agree; a share like 9 of 10 can only pass it at ten runs.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import Enum

from agentlint.trace.model import Trace

RUNS_STABILITY = 3  # runs per situation in the screen (02-design "Run counts", decided 2026-10-01)
STABILITY_FLOOR = 0.9  # the modal share a screen needs to pass (stability_floor)
MIN_RUNS = 3  # fewer completed runs than this is not a measurement (MIN_RUNS_FOR_VERDICT, 05-runner)

Projection = Callable[[Trace], Sequence[str]]  # a run's trace -> its steps, in order
Hasher = Callable[[Sequence[str]], str]  # steps -> outcome class
Distribution = Counter  # outcome class -> runs


def sha256_steps(steps: Sequence[str]) -> str:
    """The default outcome class: a digest of the steps exactly, in order."""
    return hashlib.sha256(json.dumps(list(steps), ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class Environment:
    """What a run executed under, beyond the situation: runs are compared only within one. A field the run recorded
    nothing for is None, or no models."""

    harness_version: str | None
    models: tuple[str, ...]  # sorted: the set of models that answered, not the order they first did
    fingerprint: str | None  # the configuration's, as the runner recorded it for the run

    def unrecorded(self) -> tuple[str, ...]:
        """The fields this run recorded nothing for."""
        return tuple(name for name, value in self._fields() if not value)

    def _fields(self) -> tuple[tuple[str, object], ...]:
        return (("harness_version", self.harness_version), ("models", self.models), ("fingerprint", self.fingerprint))


def differing(environments: Iterable[Environment]) -> tuple[str, ...]:
    """The fields in which two of these environments recorded different values. A field one of them recorded nothing
    for differs from nothing: an infrastructure failure is not environment drift."""
    seen: dict[str, set[object]] = {}
    for environment in environments:
        for name, value in environment._fields():
            if value:
                seen.setdefault(name, set()).add(value)
    return tuple(name for name, values in seen.items() if len(values) > 1)


@dataclass(frozen=True)
class Run:
    """One completed run as analysis takes it: its position in its comparison, its trace, the fingerprint it ran
    under."""

    index: int
    trace: Trace
    fingerprint: str | None

    @property
    def environment(self) -> Environment:
        return Environment(self.trace.harness_version, tuple(sorted(set(self.trace.models))), self.fingerprint)


@dataclass(frozen=True)
class Branch:
    """Runs that did the same thing, by the projection used."""

    outcome_class: str
    steps: tuple[str, ...]
    runs: tuple[int, ...]  # their indices, ascending

    @property
    def count(self) -> int:
        return len(self.runs)


def _ordered(runs: Iterable[Run]) -> list[Run]:
    ordered = sorted(runs, key=lambda run: run.index)
    indices = [run.index for run in ordered]
    if len(set(indices)) != len(indices):
        raise ValueError(f"two runs share an index: {indices}")
    return ordered


def group(runs: Iterable[Run], projection: Projection, hasher: Hasher = sha256_steps) -> tuple[Branch, ...]:
    """The runs' branches, the largest first; a tie goes to the branch whose first run came first. The same runs in
    any order give the same branches."""
    steps: dict[str, tuple[str, ...]] = {}
    members: dict[str, list[int]] = {}
    for run in _ordered(runs):
        projected = tuple(projection(run.trace))
        outcome = hasher(projected)
        steps.setdefault(outcome, projected)
        members.setdefault(outcome, []).append(run.index)
    branches = [Branch(outcome, steps[outcome], tuple(indices)) for outcome, indices in members.items()]
    return tuple(sorted(branches, key=lambda branch: (-branch.count, branch.runs[0])))


def distribution(branches: Iterable[Branch]) -> Distribution:
    return Counter({branch.outcome_class: branch.count for branch in branches})


def modal_share(d: Distribution) -> float:
    """The largest outcome class's share of the runs."""
    total = sum(d.values())
    if total == 0:
        raise ValueError("no runs: modal share is undefined")
    return max(d.values()) / total


class ScreenState(Enum):
    INCOMPLETE = "incomplete"
    MIXED_ENVIRONMENT = "mixed environment"
    NO_ACTION = "no action"  # every compared run's projection empty: never agreed, whatever the share
    DISAGREED = "disagreed"
    AGREED = "agreed"  # at or above the floor: not evidence of stability, never shown as "stable"


@dataclass(frozen=True)
class Screen:
    """One situation's screen. Branches and the modal share are given only where runs were compared: not for an
    incomplete screen, nor a refused one."""

    state: ScreenState
    runs: int  # completed runs screened
    branches: tuple[Branch, ...] = ()
    modal_share: float | None = None
    environments: tuple[Environment, ...] = ()  # each distinct one, in run order
    differing: tuple[str, ...] = ()  # the fields two runs recorded differently in: some when refused, else none
    unrecorded: tuple[int, ...] = ()  # runs that recorded nothing for some field: still in the branches and the share


def screen(runs: Iterable[Run], projection: Projection, *, hasher: Hasher = sha256_steps,
           floor: float = STABILITY_FLOOR, min_runs: int = MIN_RUNS) -> Screen:
    """Screen one situation's completed runs. Runs that recorded different environments are refused whatever their
    number: more runs can't make them comparable. Otherwise fewer than ``min_runs`` is incomplete; runs whose
    projections are all empty took no action, which is never agreement; and the rest is decided by the modal share
    against ``floor``."""
    ordered = _ordered(runs)
    environments = tuple(dict.fromkeys(run.environment for run in ordered))
    unrecorded = tuple(run.index for run in ordered if run.environment.unrecorded())
    differs = differing(environments)
    if differs:
        return Screen(ScreenState.MIXED_ENVIRONMENT, len(ordered), environments=environments, differing=differs,
                      unrecorded=unrecorded)
    if len(ordered) < min_runs:
        return Screen(ScreenState.INCOMPLETE, len(ordered), environments=environments, unrecorded=unrecorded)
    branches = group(ordered, projection, hasher)
    share = modal_share(distribution(branches))
    if not any(projection(run.trace) for run in ordered):  # every run's projection empty, whatever the hasher
        state = ScreenState.NO_ACTION
    else:
        state = ScreenState.AGREED if share >= floor else ScreenState.DISAGREED
    return Screen(state, len(ordered), branches, share, environments, unrecorded=unrecorded)
