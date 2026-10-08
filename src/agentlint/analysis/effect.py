"""Does removing a target change what the agent does? One situation's runs WITH the target (the baseline) against its
runs WITHOUT it, and a target's verdict from its situations (07-analysis.md, "Effect of a rule", "Verdicts per rule").
Pure: the runs come in, with the adapter's action terms; AI never judges, and nothing is reported from fewer runs than
it takes to say it.

Two claims, each on the evidence that makes it safe (D10, decided with the user 2026-10-04):

- **changes** — the runs used different things with and without: the ``effect`` projection's distributions differ by
  a total variation distance of at least ``EFFECT_FLOOR`` (0.3) and an exact permutation test puts the chance of a gap
  that big, were the target doing nothing, below ``SIGNIFICANCE`` (0.05). Both, never one. The evidence names what
  changed: how many runs used each term with and without ("shell:acme — 10 of 10 with, 0 of 10 without").
- **no-change** — no difference at all: every run, on both sides, took the same actions in the same order on the same
  targets (``strict``; a shell command compared by its programs, decided 2026-10-05). Never from runs that took no
  action: two sides that both do nothing are no evidence that a rule does nothing (decided 2026-10-05).

Anything else is **unmeasurable**, with the reason, or **incomplete** when either side has too few runs to say.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from math import comb
from typing import Any

from agentlint.analysis.projections import CANON_VERSION, RunPlaces, effect, strict, used
from agentlint.analysis.stability import MIN_RUNS, STABILITY_FLOOR, Environment, differing, modal_share
from agentlint.trace.model import Trace
from agentlint.trace.terms import Terms

EFFECT_FLOOR = Fraction(3, 10)  # the gap that counts as a real effect (effect_floor, 02-design.md "Run counts"): exact,
# so a gap of exactly 0.3 is at the floor whatever the floating point makes of it (one real run's came to 0.30000000000000004)
SIGNIFICANCE = 0.05  # the permutation test's p must be below this
STATES_LIMIT = 200_000  # partial sums the exact test may hold at once; past it, a Monte Carlo test (never reached at
# 10 vs 10, where the number of distinct sums stays in the hundreds)
MONTE_CARLO_DRAWS = 20_000

CHANGES, NO_CHANGE, UNMEASURABLE, INCOMPLETE = "changes", "no-change", "unmeasurable", "incomplete"
TOO_FEW = "too few runs on a side to compare"
MIXED = "mixed environments"
NO_ACTION = "no action"
UNSTABLE = "baseline unstable"
NEITHER = "neither a clear change nor the same actions"


@dataclass(frozen=True)
class EffectRun:
    """One completed run as the comparison takes it: its index on its side, its trace, the configuration fingerprint
    it ran under and its own directories (to show paths relative to them; None leaves them as recorded)."""

    index: int
    trace: Trace
    fingerprint: str | None = None
    places: RunPlaces | None = None

    @property
    def environment(self) -> Environment:
        return Environment(self.trace.harness_version, tuple(sorted(set(self.trace.models))), self.fingerprint)


@dataclass(frozen=True)
class TermCount:
    """How many runs on each side used one term."""

    term: str
    baseline: int
    without: int

    def to_dict(self) -> dict[str, Any]:
        return {"term": self.term, "baseline": self.baseline, "without": self.without}


@dataclass(frozen=True)
class SituationVerdict:
    """What removing the target did in one situation, with its reason and counts."""

    measured: str  # changes | no-change | unmeasurable | incomplete
    reason: str | None  # why it is unmeasurable or incomplete; None otherwise
    runs: tuple[int, int]  # readable runs: (baseline, without)
    tv: float | None = None  # the total variation distance, when the effect test ran
    p: float | None = None  # its p-value
    evidence: tuple[TermCount, ...] = ()  # the terms whose counts differ, the most different first (changes only)
    modal_share: float | None = None  # the baseline's share under D10's set rule, when it was checked
    differing: tuple[str, ...] = ()  # the environment fields that differ (mixed environments)
    terms: tuple[TermCount, ...] = field(default=(), repr=False)  # every term's counts, for a reader who wants them all

    def to_dict(self) -> dict[str, Any]:
        return {
            "measured": self.measured, "reason": self.reason,
            "runs": {"baseline": self.runs[0], "without": self.runs[1]},
            "tv": self.tv, "p": self.p, "evidence": [t.to_dict() for t in self.evidence],
            "modal_share": self.modal_share, "differing": list(self.differing),
            "terms": [t.to_dict() for t in self.terms], "canon": CANON_VERSION,
        }


@dataclass(frozen=True)
class TargetVerdict:
    """What removing a target did across its situations."""

    measured: str
    reason: str | None
    situations: dict[str, int]  # how many situations ended each way

    def to_dict(self) -> dict[str, Any]:
        return {"measured": self.measured, "reason": self.reason, "situations": dict(self.situations)}


def gap(a: Counter, b: Counter) -> Fraction:
    """The total variation distance, exactly: half the summed gap between the two distributions' shares — 0 identical,
    1 disjoint — as Σ|a_k·n_b − b_k·n_a| / (2·n_a·n_b)."""
    na, nb = sum(a.values()), sum(b.values())
    return Fraction(sum(abs(a[k] * nb - b[k] * na) for k in set(a) | set(b)), 2 * na * nb)


def total_variation(a: Counter, b: Counter) -> float:
    """The total variation distance, for reading: ``gap`` as a float."""
    return float(gap(a, b))


def p_value(a: Counter, b: Counter, *, seed: str) -> float:
    """The chance, were the two sides one population, that a split of these runs into sides of these sizes gaps at
    least as much as this one: exact, and so deterministic. Each class's runs are split between the sides in every way,
    weighted by C(n_i, k_i) — the multivariate hypergeometric — and the total variation of a split is
    Σ|k_i·N − n_i·n_a| / (2·n_a·n_b), a sum over classes, so the splits' sums are counted class by class, in integers,
    without listing a single split. Past ``STATES_LIMIT`` partial sums, a Monte Carlo test seeded from ``seed`` (the
    comparison's id), so the same runs give the same p."""
    classes = sorted(set(a) | set(b))
    na, nb = sum(a.values()), sum(b.values())
    if not na or not nb:
        raise ValueError("both sides need runs")
    total = na + nb
    sizes = [a[c] + b[c] for c in classes]
    observed = sum(abs(a[c] * total - (a[c] + b[c]) * na) for c in classes)
    states: dict[int, dict[int, int]] = {0: {0: 1}}  # runs given to side a so far -> {partial sum: ways}
    for n_i in sizes:
        following: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        for given, sums in states.items():
            for k in range(min(n_i, na - given) + 1):
                ways, part = comb(n_i, k), abs(k * total - n_i * na)
                row = following[given + k]
                for partial, count in sums.items():
                    row[partial + part] += count * ways
        states = following
        if sum(len(sums) for sums in states.values()) > STATES_LIMIT:
            return _monte_carlo(a, b, observed, seed)
    sums = states.get(na, {})
    return sum(count for partial, count in sums.items() if partial >= observed) / comb(total, na)


def _monte_carlo(a: Counter, b: Counter, observed: int, seed: str) -> float:
    pool = [c for c in sorted(a) for _ in range(a[c])] + [c for c in sorted(b) for _ in range(b[c])]
    na, total = sum(a.values()), len(pool)
    draw = random.Random(seed)
    hits = 0
    for _ in range(MONTE_CARLO_DRAWS):
        draw.shuffle(pool)
        x = Counter(pool[:na])
        y = Counter(pool[na:])
        if sum(abs(x[c] * total - (x[c] + y[c]) * na) for c in set(x) | set(y)) >= observed:
            hits += 1
    return (hits + 1) / (MONTE_CARLO_DRAWS + 1)


def situation(baseline: Sequence[EffectRun], without: Sequence[EffectRun], terms: Terms, *,
              comparison: str) -> SituationVerdict:
    """One situation's verdict from its runs with the target (``baseline``) and without it, in this order: too few on
    a side is incomplete; runs in different environments, a baseline that took no action, and a baseline whose runs
    disagree under D10's set rule are unmeasurable; then changes when the effect test fires, no-change when every run
    took the same actions; else unmeasurable. ``comparison`` seeds the Monte Carlo fallback."""
    runs = (len(baseline), len(without))
    if min(runs) < MIN_RUNS:
        return SituationVerdict(INCOMPLETE, TOO_FEW, runs)
    split = differing(run.environment for run in [*baseline, *without])
    if split:
        return SituationVerdict(UNMEASURABLE, MIXED, runs, differing=split)
    with_sets = [effect(run.trace, terms) for run in baseline]
    if not any(with_sets):
        return SituationVerdict(UNMEASURABLE, NO_ACTION, runs)
    share = modal_share(Counter(used(run.trace) for run in baseline))
    if share < STABILITY_FLOOR:
        return SituationVerdict(UNMEASURABLE, UNSTABLE, runs, modal_share=share)
    without_sets = [effect(run.trace, terms) for run in without]
    a, b = Counter(with_sets), Counter(without_sets)
    exact = gap(a, b)
    tv = float(exact)
    p = p_value(a, b, seed=comparison) if exact >= EFFECT_FLOOR else 1.0
    counts = _term_counts(with_sets, without_sets)
    if exact >= EFFECT_FLOOR and p < SIGNIFICANCE:
        shown = tuple(t for t in counts if t.baseline * runs[1] != t.without * runs[0])
        return SituationVerdict(CHANGES, None, runs, tv=tv, p=p, evidence=shown, modal_share=share, terms=counts)
    exact = {strict(run.trace, terms, run.places) for run in [*baseline, *without]}
    if len(exact) == 1 and next(iter(exact)):
        return SituationVerdict(NO_CHANGE, None, runs, tv=tv, p=p, modal_share=share, terms=counts)
    return SituationVerdict(UNMEASURABLE, NEITHER, runs, tv=tv, p=p, modal_share=share, terms=counts)


def _term_counts(with_sets: list[tuple[str, ...]], without_sets: list[tuple[str, ...]]) -> tuple[TermCount, ...]:
    """Each term with how many runs on each side used it, the biggest gap in share first, then by name."""
    a = Counter(term for terms in with_sets for term in terms)
    b = Counter(term for terms in without_sets for term in terms)
    na, nb = len(with_sets), len(without_sets)
    rows = [TermCount(term, a[term], b[term]) for term in set(a) | set(b)]
    return tuple(sorted(rows, key=lambda t: (-abs(t.baseline * nb - t.without * na), t.term)))


def target(verdicts: Sequence[SituationVerdict]) -> TargetVerdict:
    """A target's verdict: changes when any situation changes; else incomplete when any is; else no-change when every
    situation is (at least one); else unmeasurable — never "does nothing" from a situation that couldn't say."""
    tally = Counter(v.measured for v in verdicts)
    counts = {state: tally[state] for state in (CHANGES, NO_CHANGE, UNMEASURABLE, INCOMPLETE)}
    if tally[CHANGES]:
        return TargetVerdict(CHANGES, None, counts)
    if tally[INCOMPLETE]:
        return TargetVerdict(INCOMPLETE, TOO_FEW, counts)
    if verdicts and tally[NO_CHANGE] == len(verdicts):
        return TargetVerdict(NO_CHANGE, None, counts)
    return TargetVerdict(UNMEASURABLE, "no situation" if not verdicts else "not every situation showed the same actions",
                         counts)
