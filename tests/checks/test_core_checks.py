from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agentlint.checks.delivery import LARGE_DEFINITION, definition_size, untraced_injection
from agentlint.checks.duplicate_name import run as judge_duplicate_names
from agentlint.checks.files import file_facts
from agentlint.config.assemble import assemble
from agentlint.config.entity import Entity, Visibility
from agentlint.config.model import Declared
from agentlint.config.taxonomy import Authority, EntityKind, EntityRole, Load

from ..config.helpers import HARNESS, PROJECT, delivery, file_source, ref, session

SKILL = EntityKind(HARNESS, "skill", "skill", EntityRole.SKILL, "skills", ":")
AGENT = EntityKind(HARNESS, "agent", "agent", EntityRole.AGENT, "agents", ":")


def duplicate_name(snapshot):
    return judge_duplicate_names(snapshot, file_facts(snapshot))


def broken_links(snapshot):
    from agentlint.checks.references import broken_links as judge

    return judge(snapshot, file_facts(snapshot))


def build(sources, entities, visibility=(), deliveries=()):
    declared = Declared(HARNESS, PROJECT, "2.0", "m", tuple(sources), tuple(entities))
    s = replace(session(ref(1), *deliveries), visibility=tuple(visibility))
    return assemble(declared, [s])


def skill_file(source_id: str, description: str, body: str = "body") -> tuple:
    text = f"---\ndescription: {description}\n---\n{body}\n"
    return file_source(source_id, text, load=Load.ON_INVOKE, authority=Authority.PACKAGE)


def test_duplicate_name_classifies_and_scopes_by_namespace() -> None:
    sources = [skill_file("f1", "runs sql"), skill_file("f2", "runs sql"), skill_file("f3", "runs sql", "other body"),
               skill_file("f4", "unrelated"), skill_file("f5", "runs sql")]
    entities = [
        Entity("skill:a:sql", "a:sql", SKILL, "f1", "runs sql"),
        Entity("skill:b:sql", "b:sql", SKILL, "f2", "runs sql"),  # identical file to f1
        Entity("skill:c:query", "c:query", SKILL, "f3", "runs sql"),
        Entity("skill:d:query", "d:query", SKILL, "f4", "unrelated"),
        Entity("agent:x:sql", "x:sql", AGENT, "f5", "runs sql"),  # another namespace: no collision
    ]
    visible = [("skill:a:sql", Visibility.NAME_ONLY), ("skill:b:sql", Visibility.DESCRIBED),
               ("skill:c:query", Visibility.NAME_ONLY), ("skill:d:query", Visibility.NOT_SHOWN)]
    findings = {f.discriminator: f for f in duplicate_name(build(sources, entities, visible))}
    assert set(findings) == {"skills:sql", "skills:query"}
    sql, query = findings["skills:sql"], findings["skills:query"]
    assert "identical copies" in sql.title and sql.severity.value == "warn"
    assert "different descriptions" in query.title and query.title.endswith("(latent)") and query.severity.value == "info"
    removed = [e for e in entities if e.id != "skill:a:sql"] + [Entity("skill:z:sql", "z:sql", SKILL, "f1")]
    again = {f.discriminator: f for f in duplicate_name(build(sources, removed, visible))}
    assert again["skills:sql"].id == sql.id  # membership changed, identity did not


def test_untraced_injection_reports_frequency_and_identity() -> None:
    nudge = delivery(None, "IMPORTANT: remember the thing", label="hook output — not traced", load=Load.PER_MESSAGE)
    snap = build([file_source("notes", "x")], [], deliveries=[nudge])
    [finding] = untraced_injection(snap)
    assert "with every message" in finding.title and "IMPORTANT: remember the thing" in finding.detail
    reworded = build([file_source("notes", "x")], [], deliveries=[replace(nudge, text="IMPORTANT:  remember the thing ")])
    assert untraced_injection(reworded)[0].id == finding.id  # whitespace doesn't change identity


def test_definition_size_lists_only_large_invocables() -> None:
    big = file_source("big", "word " * (LARGE_DEFINITION * 2), load=Load.ON_INVOKE)
    small = file_source("small", "short", load=Load.ON_INVOKE)
    always = file_source("always", "word " * (LARGE_DEFINITION * 2))
    snap = build([big, small, always], [Entity("skill:big", "big", SKILL, "big")])
    [finding] = definition_size(snap)
    assert finding.title.startswith("1 invocable definitions") and "instructions file big" in finding.detail
    assert definition_size(build([small], [])) == []


def test_broken_reference_links(tmp_path: Path) -> None:
    from agentlint.config.content import file_text
    from agentlint.config.formats import ContentFormat
    from agentlint.config.model import AblationPolicy, Activation, Provenance, Source
    from ..config.helpers import INSTRUCTIONS

    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "present.md").write_text("x")
    text = ("See [ok](present.md), [missing](absent.md), [template](url), [web](https://x.y),\n"
            "[escape](../outside.md), [dir](sub/notes.md).\n"
            "```\n[in code](nowhere.md)\n```\n")
    path = skill_dir / "SKILL.md"
    path.write_text(text)
    content = file_text(ContentFormat.MARKDOWN, text)
    source = Source("doc", Provenance(INSTRUCTIONS, path, Authority.USER), Activation(Load.ON_INVOKE),
                    content.content_hash, content, AblationPolicy.blocked("x"))
    links = {f.discriminator.rsplit(":", 1)[1]: f for f in broken_links(build([source], []))}
    assert set(links) == {"absent.md", "sub/notes.md"} and links["absent.md"].locations[0].line == 1
    odd = "See [the notes](notes%00.md).\n"  # no filesystem can look this up: no claim either way, and no crash
    odd_content = file_text(ContentFormat.MARKDOWN, odd)
    odd_source = Source("odd", Provenance(INSTRUCTIONS, path, Authority.USER), Activation(Load.ON_INVOKE),
                        odd_content.content_hash, odd_content, AblationPolicy.blocked("x"))
    assert broken_links(build([odd_source], [])) == []
    part = Source("part", Provenance(INSTRUCTIONS, path, Authority.USER, anchor="claudeMd"), Activation(Load.ALWAYS),
                  content.content_hash, content, AblationPolicy.blocked("x"))  # one part of a file, not the file's text
    assert broken_links(build([part], [])) == []  # its links are not the file's: no claim about where they resolve

def test_untraced_injection_needs_a_session_from_this_project() -> None:
    import pytest
    from agentlint.checks import NotEvaluated

    nudge = delivery(None, "IMPORTANT: remember the thing", label="hook output — not traced", load=Load.PER_MESSAGE)
    declared = Declared(HARNESS, PROJECT, "2.0", "m", (file_source("notes", "x"),), ())
    elsewhere = assemble(declared, [session(ref(1, project=Path("/elsewhere")), nudge)])
    with pytest.raises(NotEvaluated, match="in this project"):
        untraced_injection(elsewhere)  # other projects' sessions say nothing about this one


def test_untraced_injection_merges_one_text_seen_under_two_labels() -> None:
    pre = delivery(None, "REMINDER: keep it short", label="PreToolUse hook — not traced", load=Load.ALWAYS)
    post = delivery(None, "REMINDER:  keep it short", label="PostToolUse hook — not traced", load=Load.PER_MESSAGE)
    [finding] = untraced_injection(build([file_source("notes", "x")], [], deliveries=[pre, post]))
    assert "with every message" in finding.title  # the more frequent of its two loads
    assert "PreToolUse" in finding.detail and "PostToolUse" in finding.detail


COMMAND = EntityKind(HARNESS, "command", "slash command", EntityRole.SKILL, "skills", ":")


def test_duplicate_name_describes_copies_within_a_group() -> None:
    same = "---\ndescription: runs sql\n---\nthe same body\n"
    sources = [file_source(f"f{i}", same, load=Load.ON_INVOKE, authority=Authority.PACKAGE) for i in range(3)]
    sources.append(file_source("f3", "---\ndescription: runs sql\n---\na body that drifted\n", load=Load.ON_INVOKE))
    entities = [Entity(f"skill:{p}:sql", f"{p}:sql", SKILL, f"f{i}", "runs sql") for i, p in enumerate("abcd")]
    visible = [(e.id, Visibility.NAME_ONLY) for e in entities]
    [finding] = duplicate_name(build(sources, entities, visible))
    assert "3 of 4 are identical copies" in finding.title and "do different things" not in finding.detail
    assert {m.name: m.identical_to for m in finding.members} == {  # each copy names a member it matches
        "a:sql": "skill:b:sql", "b:sql": "skill:a:sql", "c:sql": "skill:a:sql", "d:sql": None}
    assert finding.suggested_action.startswith("Remove the extra copies")


def test_duplicate_name_never_calls_session_only_entities_copies() -> None:
    from agentlint.config.taxonomy import InjectionPoint, Mechanism

    listing = InjectionPoint(HARNESS, "listing", "skill listing", Mechanism.HARNESS_GENERATED)
    sources = [file_source("listing", "- a:feedback: one\n- b:feedback: two", point=listing)]
    entities = [Entity("skill:a:feedback", "a:feedback", SKILL, "listing", "one"),
                Entity("skill:b:feedback", "b:feedback", SKILL, "listing", "two")]
    [finding] = duplicate_name(build(sources, entities, [(e.id, Visibility.DESCRIBED) for e in entities]))
    assert "different descriptions" in finding.title  # one listing, two skills: not one file installed twice


def test_duplicate_name_counts_names_and_labels_mixed_kinds() -> None:
    sources = [file_source(f"f{i}", f"body {i}", load=Load.ON_INVOKE) for i in range(3)]
    entities = [Entity("skill:x:feedback", "x:feedback", SKILL, "f0", "give feedback"),
                Entity("command:x:feedback", "x:feedback", COMMAND, "f1", "give feedback"),  # same name, twice
                Entity("command:y:feedback", "y:feedback", COMMAND, "f2", "other feedback")]
    visible = [("skill:x:feedback", Visibility.DESCRIBED), ("command:x:feedback", Visibility.NOT_SHOWN),
               ("command:y:feedback", Visibility.NAME_ONLY)]
    [finding] = duplicate_name(build(sources, entities, visible))
    assert finding.title.startswith('2 entries named "feedback"')  # names counted once; mixed kinds aren't pluralised
    assert [(m.name, m.kind, m.seen) for m in finding.members] == [  # every declaration of a name, twins included
        ("x:feedback", "skill", Visibility.DESCRIBED), ("x:feedback", "slash command", Visibility.NOT_SHOWN),
        ("y:feedback", "slash command", Visibility.NAME_ONLY)]
    assert [m.source for m in finding.members] == ["f0", "f1", "f2"] and finding.detail.startswith("The agent was shown")


def test_duplicate_name_exposure_is_unknown_without_a_session() -> None:
    sources = [file_source("f0", "one", load=Load.ON_INVOKE), file_source("f1", "two", load=Load.ON_INVOKE)]
    entities = [Entity("skill:a:q", "a:q", SKILL, "f0", "one"), Entity("skill:b:q", "b:q", SKILL, "f1", "two")]
    declared = Declared(HARNESS, PROJECT, "2.0", "m", tuple(sources), tuple(entities))
    [finding] = duplicate_name(assemble(declared, []))
    assert finding.title.endswith("(exposure unknown)") and finding.severity.value == "warn"
    assert "wasn't shown" not in finding.detail  # nobody observed that


def test_code_fenced_inside_a_list_item_is_still_code(tmp_path: Path) -> None:
    from agentlint.config.content import file_text
    from agentlint.config.formats import ContentFormat
    from agentlint.config.markdown import blank_code
    from agentlint.config.model import AblationPolicy, Activation, Provenance, Source
    from ..config.helpers import INSTRUCTIONS

    text = "1. Run it:\n\n    ```bash\n    cat [notes](missing.md) and `pkg:gone`\n    ```\n2. Done.\n"
    assert "missing.md" not in blank_code(text) and "pkg:gone" not in blank_code(text, spans=False)
    assert len(blank_code(text)) == len(text)  # offsets preserved
    path = tmp_path / "SKILL.md"
    path.write_text(text)
    content = file_text(ContentFormat.MARKDOWN, text)
    source = Source("doc", Provenance(INSTRUCTIONS, path, Authority.USER), Activation(Load.ON_INVOKE),
                    content.content_hash, content, AblationPolicy.blocked("x"))
    assert broken_links(build([source], [])) == []  # an example in code is not a broken link


def test_an_indented_fence_inside_an_outer_one_does_not_close_it() -> None:
    from agentlint.config.markdown import blank_code

    text = ("```markdown\n1. A step:\n\n    ```bash\n    echo hi\n    ```\n```\n"
            "## Later\nSee [notes](missing.md).\n")
    blanked = blank_code(text)
    assert "echo hi" not in blanked and "## Later" in blanked and "missing.md" in blanked  # prose stays prose


def test_duplicate_name_sees_a_copy_behind_a_hidden_twin() -> None:
    from agentlint.config.taxonomy import InjectionPoint, Mechanism

    same = "Run the divine loop."
    sources = [file_source("f0", same, load=Load.ON_INVOKE), file_source("f1", "a workflow script", load=Load.ON_INVOKE),
               file_source("f2", same, load=Load.ON_INVOKE)]
    workflow = EntityKind(HARNESS, "workflow", "workflow", EntityRole.SKILL, "skills", ":")
    entities = [Entity("command:x:divine", "x:divine", COMMAND, "f0", ""),
                Entity("workflow:x:divine", "x:divine", workflow, "f1", ""),  # the one the agent was shown
                Entity("command:user:divine", "user:divine", COMMAND, "f2", "")]
    visible = [("command:x:divine", Visibility.NOT_SHOWN), ("workflow:x:divine", Visibility.DESCRIBED),
               ("command:user:divine", Visibility.NAME_ONLY)]
    snap = build(sources, entities, visible)
    [finding] = duplicate_name(snap)
    assert finding.title == '2 entries named "divine" — identical copies'
    assert [(m.entity, m.identical_to) for m in finding.members] == [  # the mark sits on the matching file's entity
        ("command:user:divine", "command:x:divine"), ("command:x:divine", "command:user:divine"),
        ("workflow:x:divine", None)]
    from agentlint.checks.duplicate_name import CHECK
    lines = [line.strip() for line in _terminal(snap, CHECK).splitlines()]
    assert "x:divine  slash command (user)  [not shown]  (identical to user:divine)" in lines
    assert "x:divine  workflow (user)  [described]" in lines
    assert [l.source_id for l in finding.locations] == ["f0", "f1", "f2"]  # every file behind it

    listing = InjectionPoint(HARNESS, "listing", "skill listing", Mechanism.HARNESS_GENERATED)
    sources = [file_source("listing", "- a:q: same\n- b:q: same", point=listing), file_source("f3", "x")]
    entities = [Entity("skill:a:q", "a:q", SKILL, "listing", "same"), Entity("skill:b:q", "b:q", SKILL, "f3", "same")]
    [finding] = duplicate_name(build(sources, entities, [(e.id, Visibility.NAME_ONLY) for e in entities]))
    assert finding.title.endswith("— same description")  # one body was never read: no claim about bodies
    assert set(finding.sources) == {"listing", "f3"}  # the listing too: unobserved, it keeps it from closing falsely


def test_filesystem_facts_are_taken_when_read_and_the_checks_judge_only_those(tmp_path: Path) -> None:
    from agentlint.checks.references import broken_links as judge_links
    from agentlint.config.content import file_text
    from agentlint.config.formats import ContentFormat
    from agentlint.config.model import AblationPolicy, Activation, Provenance, Source
    from ..config.helpers import INSTRUCTIONS

    real = tmp_path / "store" / "deploy"
    real.mkdir(parents=True)
    text = "---\ndescription: ship it\n---\nSee [the steps](steps.md).\n"
    (real / "SKILL.md").write_text(text)
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "deploy").symlink_to(real)
    content = file_text(ContentFormat.MARKDOWN, text)
    sources = [Source(f"f{i}", Provenance(INSTRUCTIONS, where / "SKILL.md", Authority.USER), Activation(Load.ON_INVOKE),
                      content.content_hash, content, AblationPolicy.blocked("x"))
               for i, where in enumerate((tmp_path / "skills" / "deploy", real))]
    entities = [Entity("skill:a:deploy", "a:deploy", SKILL, "f0", "ship it"), Entity("skill:b:deploy", "b:deploy", SKILL, "f1", "ship it")]
    snap = build(sources, entities)
    facts = file_facts(snap)
    assert facts.symlinks == {tmp_path / "skills/deploy/SKILL.md": (str(tmp_path / "skills/deploy"), str(real))}
    [finding] = judge_duplicate_names(snap, facts)
    assert [(m.name, m.symlink) for m in finding.members] == [("a:deploy", str(real)), ("b:deploy", None)]
    assert {f.discriminator.rsplit(":", 1)[1] for f in judge_links(snap, facts)} == {"steps.md"}
    (real / "steps.md").write_text("now here")  # the disk changed after the reading: the reading's verdict stands
    assert {f.discriminator.rsplit(":", 1)[1] for f in judge_links(snap, facts)} == {"steps.md"}
    assert judge_links(snap, file_facts(snap)) == []  # a new reading sees it


def test_duplicate_name_judges_copies_per_declaration_not_per_name() -> None:
    copy, drifted = "---\ndescription: ship it\n---\nthe same steps\n", "---\ndescription: ship it\n---\nother steps\n"
    sources = [file_source("pk", copy, load=Load.ON_INVOKE, authority=Authority.PACKAGE),
               file_source("user", copy, load=Load.ON_INVOKE, authority=Authority.USER),
               file_source("proj", drifted, load=Load.ON_INVOKE, authority=Authority.PROJECT)]
    entities = [Entity("skill:package:pk:deploy", "pk:deploy", SKILL, "pk", "ship it"),
                Entity("skill:user:deploy", "deploy", SKILL, "user", "ship it"),
                Entity("skill:project:deploy", "deploy", SKILL, "proj", "ship it")]
    visible = [(e.id, Visibility.NOT_SHOWN) for e in entities]
    [finding] = duplicate_name(build(sources, entities, visible))
    # the user's is the package's copy, the project's drifted: one name, but not one file
    assert finding.title == '2 skills named "deploy" — 2 of 3 declarations are identical copies (latent)'
    assert finding.suggested_action.startswith("Remove the extra copies, then compare what remains")
    assert [(m.entity, m.scope, m.identical_to) for m in finding.members] == [
        ("skill:project:deploy", Authority.PROJECT, None), ("skill:user:deploy", Authority.USER, "skill:package:pk:deploy"),
        ("skill:package:pk:deploy", Authority.PACKAGE, "skill:user:deploy")]
    from agentlint.checks.duplicate_name import CHECK
    lines = [line.strip() for line in _terminal(build(sources, entities, visible), CHECK).splitlines()]
    assert "deploy  skill (user)  [not shown]  (identical to pk:deploy)  \"ship it\"" in lines
    assert 'pk:deploy  skill (package)  [not shown]  (identical to deploy (user))  "ship it"' in lines  # which "deploy"

    sources[2] = file_source("proj", copy, load=Load.ON_INVOKE, authority=Authority.PROJECT)
    [finding] = duplicate_name(build(sources, entities, visible))
    assert "— identical copies" in finding.title and finding.suggested_action == "Remove all but one copy."

    sources[0] = file_source("pk", drifted, load=Load.ON_INVOKE, authority=Authority.PACKAGE)
    [finding] = duplicate_name(build(sources, entities, visible))  # the user's and the project's are the copies
    assert "— 2 of 3 declarations are identical copies" in finding.title
    assert {m.entity: m.identical_to for m in finding.members} == {
        "skill:project:deploy": "skill:user:deploy", "skill:user:deploy": "skill:project:deploy",
        "skill:package:pk:deploy": None}


def _terminal(snapshot, check) -> str:
    from agentlint.checks import run_checks
    from agentlint.config.reading import Reading
    from agentlint.report.terminal import render

    return render(snapshot, run_checks(Reading(snapshot, file_facts(snapshot)), [check]), detail=True)


def test_the_terminal_prints_every_namesake_and_the_id_never_moves() -> None:
    """Pinned before members became data: each member's name, kind, where it sits, what the agent saw of it, its copy
    mark and its description still print, and the finding keeps its id."""
    from agentlint.checks.duplicate_name import CHECK

    copy, drifted = "---\ndescription: ship it\n---\nthe same steps\n", "---\ndescription: ship it\n---\nother steps\n"
    sources = [file_source("pk", copy, load=Load.ON_INVOKE, authority=Authority.PACKAGE),
               file_source("user", copy, load=Load.ON_INVOKE, authority=Authority.USER),
               file_source("proj", drifted, load=Load.ON_INVOKE, authority=Authority.PROJECT)]
    entities = [Entity("skill:package:pk:deploy", "pk:deploy", SKILL, "pk", "ship it to production"),
                Entity("skill:user:deploy", "deploy", SKILL, "user", "ship it"),
                Entity("skill:project:deploy", "deploy", SKILL, "proj", "ship it, the project's way")]
    visible = [("skill:package:pk:deploy", Visibility.DESCRIBED), ("skill:user:deploy", Visibility.NAME_ONLY)]
    snap = build(sources, entities, visible)
    [finding] = duplicate_name(snap)
    assert finding.id == "5411b09f226f497b"  # check and discriminator only: "skills:deploy"
    out = _terminal(snap, CHECK)
    assert "pk:deploy" in out and "described" in out and '"ship it to production"' in out
    assert "(user)" in out and "name only" in out and "(identical to pk:deploy)" in out
    assert "(project)" in out and "[not known]" in out
    assert "The agent was shown 2 of them" in out
    assert '"ship it, the project\'s way"' in out  # from the members: each declaration's own description, now
    assert [m.entity for m in finding.members] == ["skill:project:deploy", "skill:user:deploy", "skill:package:pk:deploy"]
    from agentlint.config.serialize import as_model, to_data
    from agentlint.report.finding import Finding
    assert as_model(Finding, to_data(finding)) == finding  # the store reads its findings back, members and all
