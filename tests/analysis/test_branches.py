"""The configured entities two branches differ in, as Members."""

from __future__ import annotations

from agentlint.analysis.branches import branch_members, every_run_of_one
from agentlint.config.entity import Entity
from agentlint.config.taxonomy import Authority, Load

from ..checks.test_core_checks import AGENT, SKILL, build
from ..config.helpers import file_source
from .test_stability import action, trace


def snapshot():
    sources = [file_source("pkg-a", "a", load=Load.ON_INVOKE, authority=Authority.PACKAGE),
               file_source("pkg-b", "b", load=Load.ON_INVOKE, authority=Authority.PACKAGE),
               file_source("user-b", "b", load=Load.ON_INVOKE, authority=Authority.USER),
               file_source("builtin", "agents", load=Load.ALWAYS, authority=Authority.HARNESS)]
    entities = [Entity("skill:data:sql", "data:sql", SKILL, "pkg-a", "runs sql"),
                Entity("skill:analytics:sql", "analytics:sql", SKILL, "pkg-b", "runs sql too"),
                Entity("skill:analytics:sql@user", "analytics:sql", SKILL, "user-b"),  # a namesake
                Entity("agent:Explore", "Explore", AGENT, "builtin")]  # the harness's own: not configured
    return build(sources, entities)


def run(skill: str, *more: str):
    return trace(action(0, "Skill", (f"skills/{skill}",)), *(action(n, "Agent", (f"agents/{m}",)) for n, m in enumerate(more, 1)))


def test_the_skills_that_separate_two_branches_are_members() -> None:
    members = branch_members(snapshot(), [run("data:sql"), run("data:sql")], [run("analytics:sql", "Explore")])
    assert [(m.entity, m.name, m.kind, m.source) for m in members] == [
        ("skill:data:sql", "data:sql", "skill", "pkg-a"),
        ("skill:analytics:sql", "analytics:sql", "skill", "pkg-b"),  # both namesakes: the trace can't say which
        ("skill:analytics:sql@user", "analytics:sql", "skill", "user-b"),
    ]  # agents/Explore separates them too, but the harness provides it
    assert members[0].scope is Authority.PACKAGE and members[0].description == "runs sql"


def test_branches_that_invoked_the_same_entities_have_no_members() -> None:
    assert branch_members(snapshot(), [run("data:sql")], [run("data:sql", "Explore")]) == ()
    assert branch_members(snapshot(), [run("unknown:skill")], [run("other")]) == ()  # nothing configured differs


def test_the_rule_is_a_parameter_and_defaults_to_the_symmetric_difference() -> None:
    """data:sql was invoked by one of b's runs and none of a's: a member by default, not by the stricter rule
    (which a finding uses is D11, open)."""
    a, b = [run("unknown:skill")], [run("data:sql"), run("other")]
    assert [m.entity for m in branch_members(snapshot(), a, b)] == ["skill:data:sql"]
    assert branch_members(snapshot(), a, b, rule=every_run_of_one) == ()
