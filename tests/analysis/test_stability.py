"""The stability screen on synthetic runs: no agent, no transcript."""

from __future__ import annotations

import pytest

from agentlint.analysis.branches import every_run_of_one, symmetric_difference
from agentlint.analysis.render import first_divergence, render
from agentlint.analysis.stability import (
    MIN_RUNS,
    RUNS_STABILITY,
    STABILITY_FLOOR,
    Branch,
    Run,
    ScreenState,
    distribution,
    group,
    modal_share,
    screen,
)
from agentlint.trace.model import Action, Trace


def action(seq: int, tool: str, uses: tuple[str, ...] = (), **given: object) -> Action:
    return Action(seq, tool, dict(given), f"m{seq}", f"c{seq}", (f"tools/{tool}", *uses), True, False, False)


def trace(*steps: Action, version: str = "1.0", models: tuple[str, ...] = ("model-a",)) -> Trace:
    return Trace("s", version, models, True, steps, ())


def skill_run(skill: str, **env: object) -> Trace:
    return trace(action(0, "Read", file_path="AGENTS.md"), action(1, "Skill", (f"skills/{skill}",), skill=skill),
                 action(2, "Bash", command="sql"), **env)


def names(t: Trace) -> list[str]:
    """A test's projection: each action's last usage reference. Core builds none in (Open Question 2 is open)."""
    return [a.uses[-1] for a in t.actions]


def runs(*traces: Trace, fingerprint: str = "fp") -> list[Run]:
    return [Run(n, t, fingerprint) for n, t in enumerate(traces)]


def test_decided_constants() -> None:
    assert (RUNS_STABILITY, STABILITY_FLOOR, MIN_RUNS) == (3, 0.9, 3)


def test_a_two_one_skill_split_disagrees_and_names_both_branches() -> None:
    result = screen(runs(skill_run("data:sql"), skill_run("analytics:sql"), skill_run("data:sql")), names)
    assert result.state is ScreenState.DISAGREED
    assert [(b.count, b.runs) for b in result.branches] == [(2, (0, 2)), (1, (1,))]
    assert result.branches[0].steps == ("tools/Read", "skills/data:sql", "tools/Bash")
    assert result.modal_share == pytest.approx(2 / 3)
    assert render(result.branches).splitlines() == [
        "runs 0, 2  →  tools/Read → skills/data:sql → tools/Bash",
        "run 1      →  tools/Read → skills/analytics:sql → tools/Bash",
        "                           " + "^" * len("skills/analytics:sql") + " differs",
    ]


def test_three_of_three_agree_and_nothing_says_stable() -> None:
    result = screen(runs(*(skill_run("data:sql") for _ in range(3))), names)
    assert result.state is ScreenState.AGREED and result.modal_share == 1.0
    assert [b.runs for b in result.branches] == [(0, 1, 2)]
    shown = " ".join([render(result.branches), str(result), result.state.value, *(s.value for s in ScreenState)])
    assert "stable" not in shown.lower()
    assert "^" not in render(result.branches)  # one branch: nothing differs


def test_runs_that_all_took_no_action_are_no_action_never_agreed() -> None:
    """Decided with the user 2026-10-05: runs that all did nothing are no evidence about the target."""
    result = screen(runs(trace(), trace(), trace()), names)
    assert result.state is ScreenState.NO_ACTION and result.state.value == "no action"
    assert [(b.steps, b.runs) for b in result.branches] == [((), (0, 1, 2))] and result.modal_share == 1.0
    assert screen(runs(*(trace() for _ in range(10))), names).state is ScreenState.NO_ACTION  # at any count
    assert screen(runs(trace(), trace(), trace()), names, hasher=lambda steps: "one").state is ScreenState.NO_ACTION


def test_runs_that_disagree_stay_disagreed_even_if_some_took_no_action() -> None:
    result = screen(runs(trace(), skill_run("x"), trace()), names)
    assert result.state is ScreenState.DISAGREED
    assert [(b.steps, b.runs) for b in result.branches][0] == ((), (0, 2))
    # a hasher that puts an empty run and one that acted in one class is still not "no action"
    assert screen(runs(trace(), skill_run("x"), trace()), names, hasher=lambda steps: "one").state is ScreenState.AGREED


def test_too_few_runs_that_took_no_action_are_incomplete_and_mixed_environments_are_refused() -> None:
    assert screen(runs(trace(), trace()), names).state is ScreenState.INCOMPLETE
    mixed = runs(trace(), trace(version="2.0"), trace())
    assert screen(mixed, names).state is ScreenState.MIXED_ENVIRONMENT


def test_two_runs_are_incomplete_and_compare_nothing() -> None:
    result = screen(runs(skill_run("data:sql"), skill_run("analytics:sql")), names)
    assert result.state is ScreenState.INCOMPLETE and result.runs == 2
    assert result.branches == () and result.modal_share is None


def test_mixed_versions_are_refused() -> None:
    mixed = runs(skill_run("data:sql"), skill_run("data:sql", version="1.1"), skill_run("data:sql"))
    result = screen(mixed, names)
    assert result.state is ScreenState.MIXED_ENVIRONMENT
    assert result.branches == () and result.modal_share is None
    assert [e.harness_version for e in result.environments] == ["1.0", "1.1"]
    assert result.differing == ("harness_version",)


@pytest.mark.parametrize("change", ["model", "fingerprint"])
def test_mixed_models_or_fingerprints_are_refused(change: str) -> None:
    made = runs(*(skill_run("data:sql") for _ in range(3)))
    if change == "model":
        made[1] = Run(1, skill_run("data:sql", models=("model-b",)), "fp")
    else:
        made[2] = Run(2, made[2].trace, "fp-other")
    assert screen(made, names).state is ScreenState.MIXED_ENVIRONMENT


def test_a_run_no_model_answered_is_no_different_environment() -> None:
    """Run 1's only model call failed: it recorded no model, which differs from nobody's. Whether such a run is an
    outcome is the runner's to classify (D7), so it is compared, and named as unrecorded."""
    made = runs(skill_run("data:sql"), skill_run("data:sql", models=()), skill_run("data:sql"))
    result = screen(made, names)
    assert result.state is ScreenState.AGREED
    assert result.differing == () and result.unrecorded == (1,)


def test_an_unrecorded_version_or_fingerprint_is_no_different_environment() -> None:
    made = runs(skill_run("a"), skill_run("a", version=None), skill_run("a"))  # type: ignore[arg-type]
    made[2] = Run(2, made[2].trace, None)
    result = screen(made, names)
    assert result.state is ScreenState.AGREED and result.unrecorded == (1, 2)


def test_an_unrecorded_run_still_counts_toward_the_share_until_the_runner_classifies_it() -> None:
    """Listing a run as unrecorded doesn't take it out of the comparison (D7 is the runner's): a run that recorded
    none of the three fields and did something else makes a 2/1 split. A transcript that recorded nothing at all is
    no such run: reading it raises (TraceEmpty)."""
    made = runs(skill_run("a"), skill_run("a"), trace(version=None, models=()))  # type: ignore[arg-type]
    made[2] = Run(2, made[2].trace, None)
    result = screen(made, names)
    assert result.state is ScreenState.DISAGREED and result.unrecorded == (2,)
    assert [b.runs for b in result.branches] == [(0, 1), (2,)]


def test_models_compare_as_a_set_not_in_the_order_they_first_answered() -> None:
    made = runs(*(skill_run("a", models=m) for m in [("opus", "haiku"), ("haiku", "opus"), ("opus", "haiku")]))
    result = screen(made, names)
    assert result.state is ScreenState.AGREED and result.unrecorded == ()
    assert [e.models for e in result.environments] == [("haiku", "opus")]


def test_a_refusal_names_the_fields_recorded_differently() -> None:
    made = runs(skill_run("a"), skill_run("a", version="1.1", models=()), skill_run("a"))
    result = screen(made, names)
    assert result.state is ScreenState.MIXED_ENVIRONMENT
    assert result.differing == ("harness_version",) and result.unrecorded == (1,)


def test_refused_whatever_the_count() -> None:
    two = runs(skill_run("a"), skill_run("a", version="1.1"))
    assert screen(two, names).state is ScreenState.MIXED_ENVIRONMENT  # more runs can't make these comparable


def test_order_of_runs_and_a_resume_change_nothing() -> None:
    made = runs(skill_run("x"), skill_run("y"), skill_run("x"))
    assert screen(made, names) == screen(list(reversed(made)), names)
    before_kill = screen(made[:2], names)  # killed after two runs: incomplete, nothing compared
    after_resume = screen([*made[:2], made[2]], names)
    assert before_kill.state is ScreenState.INCOMPLETE
    assert after_resume == screen(made, names)


def test_duplicate_run_indices_are_an_error() -> None:
    with pytest.raises(ValueError):
        screen([Run(0, skill_run("x"), "fp"), Run(0, skill_run("x"), "fp"), Run(1, skill_run("x"), "fp")], names)


def test_the_floor_needs_nine_of_ten_at_ten_runs() -> None:
    nine = runs(*(skill_run("x") for _ in range(9)), skill_run("y"))
    eight = runs(*(skill_run("x") for _ in range(8)), skill_run("y"), skill_run("y"))
    assert screen(nine, names).state is ScreenState.AGREED
    assert screen(eight, names).state is ScreenState.DISAGREED


def test_the_projection_and_hash_are_parameters() -> None:
    made = runs(skill_run("x"), skill_run("y"), skill_run("x"))
    assert screen(made, lambda t: [a.tool for a in t.actions]).state is ScreenState.AGREED  # tools alone agree
    assert screen(made, names, hasher=lambda steps: "one").state is ScreenState.AGREED


def test_grouping_and_modal_share() -> None:
    branches = group(runs(skill_run("y"), skill_run("x"), skill_run("x"), skill_run("y")), names)
    assert [b.runs for b in branches] == [(0, 3), (1, 2)]  # a tie: the branch whose first run came first
    assert sorted(distribution(branches).values()) == [2, 2]
    assert modal_share(distribution(branches)) == 0.5
    with pytest.raises(ValueError):
        modal_share(distribution(()))


def test_first_divergence() -> None:
    assert first_divergence([["a", "b"], ["a", "c"]]) == 1
    assert first_divergence([["a", "b"], ["a", "b", "c"]]) == 2  # one stopped where the other went on
    assert first_divergence([["a"], ["a"]]) is None
    assert first_divergence([["a"]]) is None
    assert first_divergence([[], ["a"]]) == 0


def test_a_branch_that_stops_is_shown_ending() -> None:
    lines = render((Branch("h1", ("tools/Read", "tools/Edit"), (0, 1)), Branch("h2", ("tools/Read",), (2,)))).splitlines()
    assert lines[1] == "run 2      →  tools/Read → (ends)"
    assert lines[2] == "                           ^^^^^^^^^^ differs"
    assert render(()) == ""
    assert render((Branch("h", (), (0,)), Branch("g", ("tools/Read",), (1,)))).splitlines()[0] == "run 0  →  (ends)"


def test_the_default_rule_is_the_symmetric_difference_of_what_each_branch_invoked() -> None:
    a = [{"skills/x"}, {"skills/x", "skills/y"}]
    b = [{"skills/z"}]
    assert symmetric_difference(a, b) == (frozenset({"skills/x", "skills/y"}), frozenset({"skills/z"}))
    assert symmetric_difference([{"tools/Bash"}], [{"tools/Bash"}]) == (frozenset(), frozenset())
    with pytest.raises(ValueError):
        symmetric_difference([], b)


def test_the_strict_rule_takes_what_every_run_of_one_branch_used_and_none_of_the_other() -> None:
    a = [{"skills/x"}, {"skills/x", "skills/y"}]
    assert every_run_of_one(a, [{"skills/z"}]) == (frozenset({"skills/x"}), frozenset({"skills/z"}))  # y: one run only
    assert every_run_of_one([{"skills/z"}], [{"skills/z", "skills/y"}, {"skills/y"}]) == (frozenset(), frozenset({"skills/y"}))
    with pytest.raises(ValueError):
        every_run_of_one(a, [])
