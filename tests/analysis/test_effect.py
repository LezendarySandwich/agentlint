"""The with/without comparison (analysis/effect.py, analysis/projections.py): the projections, the exact permutation
test against brute force, each verdict rule and a target's verdict. Action terms come from a fake, so core is tested
without an adapter."""

from __future__ import annotations

import itertools
import random
import time
from collections import Counter
from typing import Any

import pytest

from agentlint.analysis import effect as fx
from agentlint.analysis.effect import EffectRun, p_value, situation, target, total_variation
from agentlint.analysis.projections import RunPlaces, effect, relative, strict
from agentlint.trace.model import Action, Trace
from agentlint.trace.terms import ActionKind, ActionTerms


def terms(action: Action) -> ActionTerms:
    """A harness of the tests' own: `sh` runs the programs it lists, `read` reads a path, `skill` and `agent` name one."""
    given = action.input
    if action.tool == "sh":
        return ActionTerms(ActionKind.SHELL, "sh", tuple(given.get("programs", ())))
    if action.tool == "read":
        return ActionTerms(ActionKind.FILE, "read", (given["path"],))
    if action.tool == "find":
        return ActionTerms(ActionKind.SEARCH, "find", (given.get("path", "."),))
    if action.tool in ("skill", "agent"):
        return ActionTerms(ActionKind(action.tool), action.tool, (given["name"],))
    return ActionTerms(ActionKind.OTHER, action.tool)


def trace(*calls: tuple[str, dict[str, Any]], version: str = "2.0", models: tuple[str, ...] = ("big",)) -> Trace:
    actions = tuple(Action(i, tool, given, None, None, (f"tools/{tool}",), True, False, False) for i, (tool, given) in enumerate(calls))
    return Trace("s", version, models, True, actions, ())


def runs(*traces: Trace, places: RunPlaces | None = None) -> list[EffectRun]:
    return [EffectRun(i, t, "fp", places) for i, t in enumerate(traces)]


SH = lambda *programs: ("sh", {"programs": list(programs)})  # noqa: E731


# --- projections ---------------------------------------------------------------------------------------------------

def test_effect_is_the_set_of_programs_skills_agents_and_tools_with_files_by_kind() -> None:
    t = trace(SH("ls", "grep", "head"), SH("acme", "head"), ("read", {"path": "/a"}), ("read", {"path": "/b"}),
              ("find", {}), ("skill", {"name": "tdd"}), ("agent", {"name": "reviewer"}), ("WebFetch", {}), SH())
    assert effect(t, terms) == ("agent:reviewer", "file", "search", "shell", "shell:acme", "shell:grep", "shell:head",
                                "shell:ls", "skill:tdd", "tool:WebFetch")
    assert effect(trace(), terms) == ()


def test_strict_is_every_action_in_order_with_paths_relative_to_the_run() -> None:
    places = RunPlaces(work="/private/r/job/cmp.baseline.0/work", home="/private/r/job/cmp.baseline.0/home")
    t = trace(SH("ls"), ("read", {"path": "/r/job/cmp.baseline.0/work/AGENTS.md"}),
              ("read", {"path": "/private/r/job/cmp.baseline.0/home/.claude/x.md"}), ("find", {"path": "/elsewhere"}))
    assert strict(t, terms, places) == (("shell", "sh", ("ls",)), ("file", "read", ("./AGENTS.md",)),
                                        ("file", "read", ("~/.claude/x.md",)), ("search", "find", ("/elsewhere",)))
    other = RunPlaces(work="/private/r/job/cmp.without.3/work", home="/private/r/job/cmp.without.3/home")
    same = trace(SH("ls"), ("read", {"path": "/private/r/job/cmp.without.3/work/AGENTS.md"}),
                 ("read", {"path": "/r/job/cmp.without.3/home/.claude/x.md"}), ("find", {"path": "/elsewhere"}))
    assert strict(same, terms, other) == strict(t, terms, places)  # two runs' own directories compare equal
    assert relative("/private/r/w", RunPlaces("/private/r/w", "/private/r/h")) == "."


# --- the exact test --------------------------------------------------------------------------------------------------

def brute(a: Counter, b: Counter) -> float:
    pool = [c for c in a for _ in range(a[c])] + [c for c in b for _ in range(b[c])]
    na = sum(a.values())
    observed = total_variation(a, b)
    hits = total = 0
    for chosen in itertools.combinations(range(len(pool)), na):
        x = Counter(pool[i] for i in chosen)
        y = Counter(pool[i] for i in range(len(pool)) if i not in set(chosen))
        total += 1
        hits += total_variation(x, y) >= observed - 1e-12
    return hits / total


@pytest.mark.parametrize("seed", range(40))
def test_the_exact_test_agrees_with_shuffling_every_way(seed: int) -> None:
    draw = random.Random(seed)
    classes = "abcd"[:draw.randint(1, 4)]
    a = Counter(draw.choice(classes) for _ in range(draw.randint(1, 6)))
    b = Counter(draw.choice(classes) for _ in range(draw.randint(1, 6)))
    assert p_value(a, b, seed="c") == pytest.approx(brute(a, b), abs=1e-12)


def test_ten_against_ten_is_exact_deterministic_and_quick() -> None:
    a = Counter({"x": 10})
    b = Counter({"y": 10})
    started = time.perf_counter()
    p = p_value(a, b, seed="cmp-0001")
    assert time.perf_counter() - started < 1.0
    assert p == pytest.approx(2 / 184756)  # all of one side's runs one way: two splits of C(20,10) gap that much
    mixed_a, mixed_b = Counter({"x": 7, "y": 2, "z": 1}), Counter({"y": 6, "w": 4})
    assert p_value(mixed_a, mixed_b, seed="s") == p_value(mixed_a, mixed_b, seed="other")  # exact: no seed in it


def test_past_the_limit_the_test_is_seeded_monte_carlo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fx, "STATES_LIMIT", 1)
    monkeypatch.setattr(fx, "MONTE_CARLO_DRAWS", 2000)
    a, b = Counter({"x": 6, "y": 1}), Counter({"y": 6, "x": 1})
    first = p_value(a, b, seed="cmp-0007")
    assert first == p_value(a, b, seed="cmp-0007")  # seeded from the comparison: the same runs, the same p
    assert first == pytest.approx(brute(a, b), abs=0.03)


def test_the_floor_is_compared_exactly_so_a_gap_of_exactly_three_tenths_is_at_it() -> None:
    # The user's real with/without runs (2026-10-06) gapped by exactly 0.3, which floats make 0.30000000000000004; a
    # gap that floats would make 0.29999999999999993 must count the same.
    a = Counter({"x": 10})
    b = Counter({"x": 7, "y": 3})
    assert fx.gap(a, b) == fx.EFFECT_FLOOR and fx.gap(a, b) >= fx.EFFECT_FLOOR
    assert total_variation(a, b) == pytest.approx(0.3)


# --- one situation ---------------------------------------------------------------------------------------------------

def test_too_few_runs_on_a_side_is_incomplete() -> None:
    verdict = situation(runs(trace(SH("ls")), trace(SH("ls")), trace(SH("ls"))), runs(trace(SH("ls")), trace(SH("ls"))), terms, comparison="c")
    assert (verdict.measured, verdict.reason, verdict.runs) == ("incomplete", "too few runs on a side to compare", (3, 2))


def test_runs_in_different_environments_are_not_compared() -> None:
    verdict = situation(runs(*[trace(SH("ls"))] * 3), runs(*[trace(SH("ls"), version="2.1")] * 3), terms, comparison="c")
    assert (verdict.measured, verdict.reason, verdict.differing) == ("unmeasurable", "mixed environments", ("harness_version",))


def test_a_baseline_that_took_no_action_never_says_no_change() -> None:
    verdict = situation(runs(*[trace()] * 10), runs(*[trace()] * 10), terms, comparison="c")
    assert (verdict.measured, verdict.reason) == ("unmeasurable", "no action")


def test_a_baseline_whose_runs_disagree_is_unstable() -> None:
    baseline = runs(*[trace(SH("ls"))] * 8, trace(("WebFetch", {})), trace(("WebFetch", {})))
    verdict = situation(baseline, runs(*[trace(SH("ls"))] * 10), terms, comparison="c")
    assert (verdict.measured, verdict.reason, verdict.modal_share) == ("unmeasurable", "baseline unstable", 0.8)


def test_without_the_hook_the_agent_stops_running_meta_changes_with_evidence() -> None:
    baseline = runs(*[trace(SH("ls", "grep"), SH("acme", "head"))] * 7, *[trace(SH("ls"), SH("acme"))] * 3)
    without = runs(*[trace(SH("ls", "grep"))] * 10)
    verdict = situation(baseline, without, terms, comparison="cmp-0005")
    assert verdict.measured == "changes" and verdict.reason is None
    assert verdict.tv == 1.0 and verdict.p is not None and verdict.p < 0.05
    assert verdict.evidence[0].to_dict() == {"term": "shell:acme", "baseline": 10, "without": 0}
    assert [t.term for t in verdict.evidence] == ["shell:acme", "shell:head", "shell:grep"]  # what changed, most first
    assert "shell:ls" not in [t.term for t in verdict.evidence]  # 10 of 10 both sides: not a difference


def test_a_gap_below_the_floor_or_not_significant_is_no_change_claim() -> None:
    # 3 against 3 can never reach p < 0.05: a total gap, and still not a change (02-design: why ablation runs at 10)
    verdict = situation(runs(*[trace(SH("acme"))] * 3), runs(*[trace(SH("ls"))] * 3), terms, comparison="c")
    assert verdict.measured == "unmeasurable" and verdict.reason == "neither a clear change nor the same actions"
    assert verdict.tv == 1.0 and verdict.p == pytest.approx(2 / 20)


def test_no_change_needs_every_run_on_both_sides_to_take_the_same_actions() -> None:
    places = lambda side, i: RunPlaces(f"/r/j/c.{side}.{i}/work", f"/r/j/c.{side}.{i}/home")  # noqa: E731
    same = lambda side, i: trace(SH("ls"), ("read", {"path": f"/r/j/c.{side}.{i}/work/AGENTS.md"}))  # noqa: E731
    baseline = [EffectRun(i, same("baseline", i), "fp", places("baseline", i)) for i in range(10)]
    without = [EffectRun(i, same("without", i), "fp", places("without", i)) for i in range(10)]
    verdict = situation(baseline, without, terms, comparison="c")
    assert (verdict.measured, verdict.reason) == ("no-change", None)
    # one run reading a second file: not the same actions, and not a clear change either
    without[4] = EffectRun(4, trace(SH("ls"), ("read", {"path": "/r/j/c.without.4/work/AGENTS.md"}), ("read", {"path": "/x"})),
                           "fp", places("without", 4))
    assert situation(baseline, without, terms, comparison="c").measured == "unmeasurable"


def test_a_verdict_is_json_shaped() -> None:
    verdict = situation(runs(*[trace(SH("acme"))] * 10), runs(*[trace(SH("ls"))] * 10), terms, comparison="c")
    shaped = verdict.to_dict()
    assert shaped["measured"] == "changes" and shaped["runs"] == {"baseline": 10, "without": 10}
    assert shaped["evidence"][:2] == [{"term": "shell:acme", "baseline": 10, "without": 0}, {"term": "shell:ls", "baseline": 0, "without": 10}]


# --- a target --------------------------------------------------------------------------------------------------------

def v(measured: str) -> fx.SituationVerdict:
    return fx.SituationVerdict(measured, None, (10, 10))


@pytest.mark.parametrize(("situations", "expected"), [
    (["changes", "unmeasurable", "incomplete"], "changes"),  # any change
    (["no-change", "incomplete"], "incomplete"),  # can't say no-change while a situation is short of runs
    (["no-change", "no-change"], "no-change"),  # every situation showed the same actions
    (["no-change", "unmeasurable"], "unmeasurable"),  # never "does nothing" from a situation that couldn't say
    ([], "unmeasurable"),
])
def test_a_targets_verdict(situations: list[str], expected: str) -> None:
    verdict = target([v(s) for s in situations])
    assert verdict.measured == expected
    assert sum(verdict.situations.values()) == len(situations)
