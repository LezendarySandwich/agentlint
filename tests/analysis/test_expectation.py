"""How strongly a target makes the agent do what its situations expect (analysis/expectation.py): the runs with it are
the consistency runs, counted against the expectation; none met it is "not followed"; else the runs without it are
counted the same, and anything short of every run with and none without is a finding."""

from __future__ import annotations

from fractions import Fraction

import pytest

from agentlint.analysis import expectation as ex
from agentlint.analysis.stability import Environment
from agentlint.probes.matters import Matters, WhatMatters
from agentlint.trace.model import Action, Trace
from agentlint.trace.terms import ActionKind, ActionTerms

SKILL = WhatMatters(Matters.TOOL_PRESENT, "skill:*")


def terms(action: Action) -> ActionTerms:
    if action.tool == "skill":
        return ActionTerms(ActionKind.SKILL, "skill", ("tdd",))
    if action.tool == "sh":
        return ActionTerms(ActionKind.SHELL, "sh", tuple(action.input["programs"]))
    return ActionTerms(ActionKind.OTHER, action.tool)


def run(*tools: str, version: str = "2.0") -> ex.Run:
    actions = tuple(Action(i, t, {"programs": ["ls"]} if t == "sh" else {}, None, None, (), True, False, False)
                    for i, t in enumerate(tools))
    trace = Trace("s", version, ("m",), True, actions, ())
    return ex.Run(trace, Environment(version, ("m",), "fp"))


def test_none_of_the_runs_with_the_target_met_it_is_not_followed_with_what_they_did_instead() -> None:
    found = ex.situation(SKILL, [run(), run("sh"), run("sh")], None, terms)
    assert (found.verdict, found.with_met, found.with_runs, found.without_runs) == ("not followed", 0, 3, None)
    assert found.instead == (("shell:ls", 2), ("no action", 1))  # why: two ran ls, one answered in words


@pytest.mark.parametrize(("with_runs", "without_runs", "verdict", "strength"), [
    ([run("skill")] * 3, [run("sh")] * 3, "works", Fraction(1)),  # 3 of 3 with, 0 of 3 without: the only perfect result
    ([run("skill")] * 3, [run("skill"), run(), run()], "partial", Fraction(2, 3)),  # the agent does it once anyway
    ([run("skill"), run(), run()], [run()] * 3, "partial", Fraction(1, 3)),  # followed in 1 of 3 runs only
    ([run("skill")] * 2 + [run()], [run("skill")] * 2 + [run()], "adds nothing", Fraction(0)),  # as often without it
    ([run("skill"), run(), run()], [run("skill")] * 3, "backfires", Fraction(-2, 3)),  # less often with it
])
def test_the_strength_is_the_share_with_minus_the_share_without(with_runs, without_runs, verdict, strength) -> None:
    found = ex.situation(SKILL, with_runs, without_runs, terms)
    assert (found.verdict, found.strength) == (verdict, strength)
    assert found.to_dict()["with"]["runs"] == 3 and found.to_dict()["without"]["runs"] == 3


def test_too_few_runs_mixed_environments_and_no_expectation_say_nothing() -> None:
    assert ex.situation(SKILL, [run("skill")] * 2, None, terms).verdict == "incomplete"
    assert ex.situation(SKILL, [run("skill")] * 3, [run()] * 2, terms).verdict == "incomplete"  # without still coming
    assert ex.situation(SKILL, [run("skill")] * 3, [run(version="2.1")] * 3, terms).verdict == "mixed environments"
    assert ex.situation(WhatMatters(Matters.ORDER), [run()] * 3, None, terms).verdict == "no expectation"


def test_a_target_works_only_when_every_situation_did_and_its_weakest_result_leads() -> None:
    works = ex.situation(SKILL, [run("skill")] * 3, [run()] * 3, terms)
    partial = ex.situation(SKILL, [run("skill")] * 3, [run("skill"), run(), run()], terms)
    ignored = ex.situation(SKILL, [run()] * 3, None, terms)
    assert ex.target([works, works]).verdict == "works" and ex.target([works, works]).strength == 1
    both = ex.target([works, partial, ignored])
    assert (both.verdict, both.strength) == ("not followed", Fraction(1 + Fraction(2, 3), 3))  # not followed counts 0
    assert both.situations == {"works": 1, "partial": 1, "not followed": 1}
    assert ex.target([works, ex.situation(SKILL, [run("skill")] * 2, None, terms)]).verdict == "incomplete"
