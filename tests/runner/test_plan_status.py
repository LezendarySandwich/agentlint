"""A job's frozen plan, its run order, and the status worked out from the runs recorded."""

from __future__ import annotations

import subprocess
import sys

import pytest

from agentlint.rank.plan import Phase
from agentlint.runner import lock
from agentlint.runner.plan import Comparison, Plan, PlanError, Planned, stability_plan, two_step_plan
from agentlint.runner.status import (MIN_RUNS_FOR_VERDICT, ComparisonState, comparison_status, progress)


def ablation(cid: str = "cmp-0002", runs: int = 10) -> Comparison:
    return Comparison(cid, Phase.ABLATION, "s2", "Fix the failing test", "hook:SessionStart", runs)


def test_order_is_depth_first_across_comparisons_and_interleaved_within_one() -> None:
    plan = Plan((Comparison("a", Phase.ABLATION, "s1", "t", "x", 2), Comparison("b", Phase.STABILITY, "s2", "t", None, 2)))
    assert [run.key for run in plan.runs()] == [
        "a:baseline:0", "a:without:0", "a:baseline:1", "a:without:1", "b:baseline:0", "b:baseline:1"]
    assert plan.size == 6


def test_the_plan_reads_back_exactly_as_frozen() -> None:
    plan = Plan((*stability_plan([("s1", "Add a test"), ("s2", "Ünïcode: and colons")]).comparisons, ablation("cmp-0003")))
    assert Plan.from_json(plan.to_json()) == plan
    assert [c.runs for c in stability_plan([("s1", "x")]).comparisons] == [3]


@pytest.mark.parametrize("comparisons", [
    (Comparison("a:b", Phase.STABILITY, "s", "t", None, 3),),  # ':' would make run keys ambiguous
    (Comparison("a", Phase.STABILITY, "s", "t", None, 3), Comparison("a", Phase.STABILITY, "s", "t", None, 3)),
    (Comparison("a", Phase.STABILITY, "s", "t", None, 0),),
    (Comparison("a", Phase.STABILITY, "s", "t", "target", 3),),  # stability measures the configuration as it is
    (Comparison("a", Phase.ABLATION, "s", "t", None, 3),),  # ablation removes something
    (Comparison("a", Phase.ABLATION, "s", "t", "x", 3, gate="b"), Comparison("b", Phase.STABILITY, "s", "t", None, 3)),  # a gate comes first
    (Comparison("a", Phase.STABILITY, "s", "t", None, 3), Comparison("b", Phase.ABLATION, "s", "t", "x", 3, gate="c")),  # a gate is in the plan
    (Comparison("a", Phase.ABLATION, "s", "t", "x", 3), Comparison("b", Phase.ABLATION, "s", "t", "x", 3, gate="a")),  # a gate is a stability one
])
def test_a_plan_that_cant_run_as_written_is_refused(comparisons) -> None:
    with pytest.raises(PlanError):
        Plan(comparisons)


def test_a_plan_from_a_newer_format_is_refused_not_misread() -> None:
    with pytest.raises(PlanError):
        Plan.from_json('{"format": 7, "comparisons": []}')


def test_a_comparison_keeps_the_target_its_situation_was_written_for_and_an_older_plan_reads_without() -> None:
    plan = stability_plan([("p1", "Fix the test", "rule-a"), ("p2", "Add logging")], runs=3)
    assert [c.about for c in plan.comparisons] == ["rule-a", None]
    assert Plan.from_json(plan.to_json()) == plan and '"format":6' in plan.to_json()
    older = Plan.from_json('{"format": 2, "comparisons": [{"id": "c1", "phase": "stability", "situation": "p1", '
                           '"text": "t", "target": null, "runs": 3}], "removed_tools": ["mcp__a__send"]}')
    assert older.comparisons[0].about is None and older.removed_tools == ("mcp__a__send",)


def test_fewer_than_the_minimum_on_any_variant_is_incomplete_never_a_verdict() -> None:
    comparison = ablation(runs=10)
    keys = [run.key for run in comparison.planned()]
    assert MIN_RUNS_FOR_VERDICT == 3
    assert comparison_status(comparison, set()).state is ComparisonState.NOT_ATTEMPTED
    five = comparison_status(comparison, set(keys[:5]))  # baseline 3, without 2
    assert (five.state, five.recorded, five.verdict_allowed) == (ComparisonState.INCOMPLETE,
                                                                 (("baseline", 3), ("without", 2)), False)
    six = comparison_status(comparison, set(keys[:6]))
    assert (six.state, six.verdict_allowed) == (ComparisonState.PARTIAL, True)
    full = comparison_status(comparison, set(keys) | {"other:baseline:0"})
    assert (full.state, full.verdict_allowed) == (ComparisonState.COMPLETE, True)


def test_a_stability_comparison_of_three_is_complete_or_incomplete() -> None:
    (comparison,) = stability_plan([("s1", "x")]).comparisons
    assert comparison_status(comparison, {"cmp-0001:baseline:0", "cmp-0001:baseline:1"}).state is ComparisonState.INCOMPLETE
    assert comparison_status(comparison, {f"cmp-0001:baseline:{n}" for n in range(3)}).state is ComparisonState.COMPLETE


def test_a_plan_of_fewer_than_the_minimum_fully_recorded_is_incomplete_not_complete() -> None:
    two = ablation(runs=2)
    status = comparison_status(two, {run.key for run in two.planned()})
    assert (status.state, status.recorded, status.verdict_allowed) == (ComparisonState.INCOMPLETE,
                                                                       (("baseline", 2), ("without", 2)), False)
    (one,) = stability_plan([("s1", "x")], runs=1).comparisons
    status = comparison_status(one, {"cmp-0001:baseline:0"})
    assert (status.state, status.recorded, status.verdict_allowed) == (ComparisonState.INCOMPLETE,
                                                                       (("baseline", 1),), False)


def test_progress_counts_only_this_plans_runs() -> None:
    plan = stability_plan([("s1", "x"), ("s2", "y")])
    seen = progress(plan, {"cmp-0001:baseline:0", "cmp-0001:baseline:1", "cmp-0001:baseline:2", "zzz:baseline:0"}, 5)
    assert (seen.runs_recorded, seen.runs_planned, seen.launched_without_a_run) == (3, 6, 2)
    assert dict(seen.comparisons)[ComparisonState.COMPLETE] == 1
    assert dict(seen.comparisons)[ComparisonState.NOT_ATTEMPTED] == 1


def test_this_process_holds_a_live_lock_and_a_dead_or_reused_pid_is_stale() -> None:
    me = lock.current()
    assert not lock.stale(me.pid, me.started)
    assert lock.stale(me.pid, me.started + "-another")  # the pid reused by a process that started at another time
    assert lock.stale(2 ** 22 + 12345, me.started)  # no such process


def test_a_processs_start_time_reads_the_same_from_any_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A runner started by a scheduler in UTC and a resume from a terminal in Tokyo must agree the runner is alive."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        seen = []
        for zone in ("UTC", "Asia/Tokyo", "America/Los_Angeles"):
            monkeypatch.setenv("TZ", zone)
            seen.append(lock.process_start(child.pid))
        assert seen[0] is not None and seen == [seen[0]] * 3
        monkeypatch.setenv("TZ", "UTC")
        held = lock.process_start(child.pid)
        monkeypatch.setenv("TZ", "Asia/Tokyo")
        assert held is not None and not lock.stale(child.pid, held)
    finally:
        child.kill()
        child.wait()


def test_the_removed_tools_are_frozen_with_the_plan_and_a_format_1_plan_has_none() -> None:
    plan = Plan((), removed_tools=("mcp__s__send", "mcp__s__delete"))
    assert Plan.from_json(plan.to_json()) == plan
    assert Plan.from_json('{"format": 1, "comparisons": []}').removed_tools == ()


def test_a_two_step_plan_runs_each_targets_situations_then_its_runs_without_the_target_gated_on_them() -> None:
    plan = two_step_plan([Planned("hook-a", (("p1", "Run the report", {"kind": "targets", "uses": "program:acme"}),
                                             ("p2", "Find the table")), True),
                          Planned("skill-b", (("p3", "Write notes"),), False)], runs=3, runs_per_side=3)
    assert [(c.id, c.phase.value, c.situation, c.target, c.runs, c.about, c.gate, c.variants) for c in plan.comparisons] == [
        ("cmp-0001", "stability", "p1", None, 3, "hook-a", None, ("baseline",)),
        ("cmp-0002", "stability", "p2", None, 3, "hook-a", None, ("baseline",)),
        ("cmp-0003", "ablation", "p1", "hook-a", 3, "hook-a", "cmp-0001", ("without",)),  # the runs with it: cmp-0001's
        ("cmp-0004", "ablation", "p2", "hook-a", 3, "hook-a", "cmp-0002", ("baseline", "without")),  # expects nothing
        ("cmp-0005", "stability", "p3", None, 3, "skill-b", None, ("baseline",)),  # no variant can remove it
    ]
    assert plan.comparisons[0].matters == {"kind": "targets", "uses": "program:acme"} and plan.comparisons[1].matters is None
    assert plan.size == 3 * 3 + 3 + 2 * 3  # the most it can run: every gate open; p1 runs only without, p2 both sides
    assert plan.gated_by("cmp-0001") == plan.comparison("cmp-0003") and plan.gated_by("cmp-0005") is None
    assert Plan.from_json(plan.to_json()) == plan
    older = Plan.from_json('{"format": 3, "comparisons": [{"id": "c1", "phase": "stability", "situation": "p1", '
                           '"text": "t", "target": null, "runs": 3, "about": "a"}]}')
    assert older.comparisons[0].gate is None and older.comparisons[0].sides is None


def test_sides_must_be_some_of_the_phases_variants() -> None:
    with pytest.raises(PlanError):
        Plan((Comparison("a", Phase.STABILITY, "s", "t", None, 3, sides=("without",)),))
    with pytest.raises(PlanError):
        Plan((Comparison("a", Phase.ABLATION, "s", "t", "x", 3, sides=("without", "without")),))
