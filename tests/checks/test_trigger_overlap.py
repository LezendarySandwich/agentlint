from __future__ import annotations

from agentlint.checks.trigger_overlap import THRESHOLD, choices, overlaps, run
from agentlint.config.entity import Entity, Visibility
from agentlint.config.taxonomy import Authority, Load

from ..config.helpers import file_source
from .test_core_checks import AGENT, SKILL, build

METRICS_SKILL = ("Official Metrics skill. Load Metrics UI URLs, discover datasets, fetch table and column metadata, "
         "and craft queries with structured queries or SQL.")


def entity(name: str, description: str, source: str, kind=SKILL) -> Entity:
    return Entity(f"{kind.name}:{name}", name, kind, source, description)


def sources(*packages: str | None):
    made = []
    for i, package in enumerate(packages):
        source = file_source(f"s{i}", "body", load=Load.ON_INVOKE, authority=Authority.PACKAGE)
        if package:
            from dataclasses import replace
            source = replace(source, provenance=replace(source.provenance, package=package))
        made.append(source)
    return made


def test_near_copies_are_reported_and_related_descriptions_are_not() -> None:
    entities = [
        entity("data:metrics", METRICS_SKILL, "s0"),
        entity("tools:metrics_cli", METRICS_SKILL, "s1"),  # the same description under another name
        entity("exp:find-experiments", "Find and discover experiments and wiki posts by semantic search.", "s2"),
        entity("ds:search-experiment", "Find experiments and wiki posts about experiments by semantic search "
                                       "across concepts, topics, teams and metrics.", "s3"),
    ]
    snap = build(sources("data@m", "tools@m", "exp@m", "ds@m"), entities,
                 visibility=[(e.id, Visibility.NAME_ONLY) for e in entities])
    [finding] = run(snap)
    assert finding.title.startswith("data:metrics and tools:metrics_cli have nearly identical descriptions")
    assert finding.title.endswith("(latent)") and finding.severity.value == "info"
    assert finding.confidence.value == "suggestive" and finding.detail.startswith("Shared wording: metrics")
    assert [(m.entity, m.package, m.seen) for m in finding.members] == [
        ("skill:data:metrics", "data@m", Visibility.NAME_ONLY), ("skill:tools:metrics_cli", "tools@m", Visibility.NAME_ONLY)]
    related = [o for o in overlaps(snap, floor=0.0) if "exp:find-experiments" in o.names]
    assert related and related[0].similarity < THRESHOLD  # shared wording, distinguishable jobs: labelled so


def test_scope_rules() -> None:
    entities = [
        entity("fam:alpha", METRICS_SKILL, "s0"), entity("fam:beta", METRICS_SKILL, "s1"),  # one package: a family, skipped
        entity("x:metrics", METRICS_SKILL, "s2"), entity("y:metrics", METRICS_SKILL, "s3"),  # one short name: duplicate-name's
        entity("z:agent-copy", METRICS_SKILL, "s4", kind=AGENT),  # another list: never compared with skills
    ]
    snap = build(sources("fam@m", "fam@m", "x@m", "y@m", "z@m"), entities,
                 visibility=[(e.id, Visibility.DESCRIBED) for e in entities])
    pairs = {o.names for o in overlaps(snap)}
    assert ("fam:alpha", "fam:beta") not in pairs and ("x:metrics", "y:metrics") not in pairs
    assert not any("z:agent-copy" in names for names in pairs)
    assert ("fam:alpha", "fam:beta") in {o.names for o in overlaps(snap, same_package=True)}
    active = [f for f in run(snap) if "fam:alpha" in f.title and "x:metrics" in f.title]
    assert active and active[0].severity.value == "warn" and not active[0].title.endswith("(latent)")


def test_one_entity_per_name_prefers_the_one_the_agent_saw() -> None:
    command = Entity("command:p:go", "p:go", SKILL, "s0", "Go somewhere")
    skill = Entity("skill:p:go", "p:go", SKILL, "s1", "Go somewhere, described")
    snap = build(sources("p@m", "p@m"), [command, skill],
                 visibility=[("command:p:go", Visibility.NOT_SHOWN), ("skill:p:go", Visibility.DESCRIBED)])
    assert [e.id for e in choices(snap)] == ["skill:p:go"]


def test_exposure_is_unknown_without_a_session() -> None:
    from agentlint.config.assemble import assemble
    from agentlint.config.model import Declared
    from ..config.helpers import HARNESS, PROJECT

    entities = [entity("data:metrics", METRICS_SKILL, "s0"), entity("tools:metrics_cli", METRICS_SKILL, "s1")]
    snap = assemble(Declared(HARNESS, PROJECT, "2.0", "m", tuple(sources("data@m", "tools@m")), tuple(entities)), [])
    [finding] = run(snap)
    assert finding.title.endswith("(exposure unknown)") and finding.severity.value == "warn"
    assert [(m.name, m.seen) for m in finding.members] == [("data:metrics", None), ("tools:metrics_cli", None)]  # no claim
    assert [m.description for m in finding.members] == [METRICS_SKILL, METRICS_SKILL] and "[not shown]" not in finding.detail


def test_the_terminal_prints_the_pair_from_its_members() -> None:
    from agentlint.checks import run_checks
    from agentlint.checks.trigger_overlap import CHECK
    from agentlint.config.reading import Reading
    from agentlint.report.terminal import render

    entities = [entity("data:metrics", METRICS_SKILL, "s0"), entity("tools:metrics_cli", METRICS_SKILL, "s1")]
    snap = build(sources("data@m", "tools@m"), entities, visibility=[(entities[0].id, Visibility.DESCRIBED)])
    [finding] = run(snap)
    assert finding.id == "1847c596c2bea3b0"  # check and discriminator only: never the members
    lines = [line.strip() for line in render(snap, run_checks(Reading(snap), [CHECK])).splitlines()]
    assert f'data:metrics  skill (data@m)  [described]  "{METRICS_SKILL}"' in lines
    assert f'tools:metrics_cli  skill (tools@m)  [not known]  "{METRICS_SKILL}"' in lines
    assert lines.index("Shared wording: metrics, queries, column, craft, datasets, discover") > lines.index(
        f'tools:metrics_cli  skill (tools@m)  [not known]  "{METRICS_SKILL}"')  # members first, then why it matters
