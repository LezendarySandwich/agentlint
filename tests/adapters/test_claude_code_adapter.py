"""End to end: a synthetic home, install directory and transcript through read_config()."""

from __future__ import annotations

import json
from pathlib import Path

from agentlint.adapters.claude_code.adapter import SKILL_LISTING, SYSTEM_PROMPT, TOOLS, ClaudeCodeAdapter
from agentlint.adapters.claude_code.discovery import Paths
from agentlint.adapters.claude_code.instructions import project_slug
from agentlint.config.content import FileText, NotObserved, ObservedText
from agentlint.config.model import Ablation, Reconciled
from agentlint.config.taxonomy import Load
from agentlint.harness import read_config


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def build(tmp: Path) -> tuple[ClaudeCodeAdapter, Path]:
    home, market, cwd = tmp / "home", tmp / "home" / ".claude" / "market", tmp / "work"
    install = tmp / "install"
    write(install / "package.json", json.dumps({"dependencies": {"@anthropic-ai/claude-code": "9.9.9"}}))
    write(home / ".claude" / "plugins" / "known_marketplaces.json", json.dumps({"mk": {"installLocation": str(market)}}))
    write(market / ".claude-plugin" / "marketplace.json", json.dumps({"plugins": [{"name": "pk", "source": "./plugins/pk"}]}))
    write(market / "plugins/pk/.claude-plugin/plugin.json", json.dumps({"hooks": {
        "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/s.sh"}]}]}}))
    write(market / "plugins/pk/s.sh", 'echo "Package context: use the pk tools wisely"\n')
    write(market / "plugins/pk/skills/helper/SKILL.md", "---\nname: helper\ndescription: helps\n---\nbody\n")
    write(market / "plugins/pk/workflows/flow.workflow.js", "export const meta = { name: 'flow', description: 'd' }\nrun()\n")
    write(home / ".claude" / "settings.json", json.dumps({"enabledPlugins": {"pk@mk": True}, "model": "model-x"}))
    write(tmp / "managed.json", json.dumps({"claudeMd": "Org policy text."}))
    write(cwd / "AGENTS.md", "# Project\nRules here.\n")
    transcript = [
        {"type": "attachment", "attachment": {"type": "hook_success", "hookEvent": "SessionStart", "hookName": "SessionStart:startup",
                                              "command": "${CLAUDE_PLUGIN_ROOT}/s.sh", "stdout": "Package context: use the pk tools wisely",
                                              "content": "Package context: use the pk tools wisely"}},
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "attachment", "attachment": {"type": "skill_listing", "names": ["pk:helper", "builtin-skill"], "skillCount": 2,
                                              "content": "- pk:helper: helps\n- builtin-skill"}},
        {"type": "attachment", "attachment": {"type": "hook_additional_context", "hookEvent": "UserPromptSubmit",
                                              "content": ["A reminder injected from somewhere unknowable"]}},
        {"type": "attachment", "attachment": {"type": "instructions", "files": [
            {"path": "<managed-settings>", "type": "Managed", "content": "Org policy text."},
            {"path": str(cwd / "AGENTS.md"), "type": "Project", "content": "# Project\nRules here."}]}},
        {"type": "assistant", "message": {"model": "model-x", "usage": {"input_tokens": 3, "cache_creation_input_tokens": 4000,
                                                                       "cache_read_input_tokens": 0}, "content": []}},
        {"type": "attachment", "attachment": {"type": "prompt_snapshot", "systemPrompt": ["You are an agent."],
                                              "tools": [{"name": "Bash", "description": "run", "schema": {}}]}},
    ]
    stamped = [{"sessionId": "s-1", "cwd": str(cwd), "version": "9.9.9", "timestamp": "2026-10-01T09:00:00Z",
                "isSidechain": False, **r} for r in transcript]
    write(home / ".claude" / "projects" / project_slug(cwd) / "s-1.jsonl", "\n".join(json.dumps(r) for r in stamped) + "\n")
    return ClaudeCodeAdapter(Paths(home, tmp / "managed.json", install)), cwd


def test_read_config_end_to_end(tmp_path: Path) -> None:
    adapter, cwd = build(tmp_path)
    snap = read_config(adapter, cwd, sessions=10).snapshot
    assert snap.harness_version == "9.9.9" and snap.model == "model-x"
    by_id = {s.id: s for s in snap.sources}

    settings = [s for s in snap.sources if s.provenance.point.name == "settings"]
    assert {s.activation.load for s in settings} == {Load.NOT_IN_CONTEXT}
    managed = next(s for s in settings if "managed" in s.id)
    assert managed.ablation.mechanism is Ablation.NONE

    policy = next(s for s in snap.sources if s.provenance.point.name == "managed_claude_md")
    assert isinstance(policy.content, FileText) and policy.content.text == "Org policy text."
    agents_md = by_id[f"claude-code:instructions:{cwd / 'AGENTS.md'}"]
    assert agents_md.activation.load is Load.ALWAYS and [r.id.split("#")[1] for r in agents_md.rules()] == ["section:/Project"]

    skill = by_id["claude-code:skill:pk:helper"]
    assert skill.activation.load is Load.ON_INVOKE and skill.provenance.package == "pk@mk"

    hook = next(s for s in snap.sources if s.id.startswith("claude-code:hook:pk@mk"))
    assert isinstance(hook.content, ObservedText) and hook.content.text == "Package context: use the pk tools wisely"

    # where each is: a file and, for one part of a file, the place inside it; the harness's own text is in no file
    assert (policy.provenance.file, policy.provenance.anchor) == (tmp_path / "managed.json", "claudeMd")
    assert policy.id == f"claude-code:instructions:{tmp_path / 'managed.json'}#claudeMd"  # ids as they were: findings keep theirs
    assert (agents_md.provenance.file, agents_md.provenance.anchor) == (cwd / "AGENTS.md", None)
    plugin = tmp_path / "home/.claude/market/plugins/pk/.claude-plugin/plugin.json"
    assert (hook.provenance.file, hook.provenance.anchor) == (plugin, "/hooks/SessionStart/0/hooks/0")
    assert skill.provenance.file == tmp_path / "home/.claude/market/plugins/pk/skills/helper/SKILL.md"
    assert (by_id[SYSTEM_PROMPT].provenance.file, by_id[SYSTEM_PROMPT].provenance.anchor) == (None, None)
    part = by_id[f"{SKILL_LISTING}:pk@mk"]
    assert (part.provenance.file, part.provenance.anchor, part.provenance.package) == (None, None, "pk@mk")
    assert hook.activation.load is Load.ALWAYS

    assert isinstance(by_id[SYSTEM_PROMPT].content, ObservedText)
    assert by_id[SYSTEM_PROMPT].provenance.authority.value == "harness"  # not the org's "managed"
    workflow = by_id["claude-code:workflow:pk:flow"]
    assert workflow.activation.load is Load.NOT_IN_CONTEXT and [r.locator.__class__.__name__ for r in workflow.rules()] == ["Whole"]
    assert isinstance(by_id[TOOLS].content, ObservedText)
    assert isinstance(by_id[SKILL_LISTING].content, ObservedText)
    assert isinstance(by_id["claude-code:listing:agents"].content, NotObserved)  # this session had none

    entity_ids = {e.id for e in snap.entities}
    assert {"skill:package:pk:helper", "tool:session:Bash", "skill:session:builtin-skill"} <= entity_ids  # disk + observed
    assert next(e for e in snap.entities if e.id == "skill:session:builtin-skill").provided_by == SKILL_LISTING

    assert [u.content.text for u in snap.untraced] == ["A reminder injected from somewhere unknowable"]
    assert isinstance(snap.always_on, Reconciled)
    assert snap.always_on.observed_total.value == 4003 - 1  # minus the user's own "hi"


def test_fingerprint_ignores_session_text_and_tracks_disk(tmp_path: Path) -> None:
    adapter, cwd = build(tmp_path)
    first = read_config(adapter, cwd, sessions=10).snapshot
    assert read_config(adapter, cwd, sessions=0).snapshot.fingerprint == first.fingerprint
    write(cwd / "AGENTS.md", "# Project\nDifferent rules.\n")
    assert read_config(adapter, cwd, sessions=10).snapshot.config_hash != first.config_hash  # the same adapter


def test_one_adapter_reads_the_disk_as_it_is_at_each_reading(tmp_path: Path) -> None:
    """An open UI keeps one adapter: pressing r reads the disk again, so an edit since the last reading is seen."""
    adapter, cwd = build(tmp_path)
    first = read_config(adapter, cwd, sessions=10).snapshot
    assert not any(s.provenance.file == cwd / "CLAUDE.md" for s in first.sources)
    write(cwd / "CLAUDE.md", "# Added\nA new rule.\n")
    write(tmp_path / "home/.claude/skills/fresh/SKILL.md", "---\nname: fresh\ndescription: new\n---\nbody\n")
    again = read_config(adapter, cwd, sessions=10).snapshot
    added = next(s for s in again.sources if s.provenance.file == cwd / "CLAUDE.md")
    assert added.content.text == "# Added\nA new rule.\n"
    assert "skill:user:fresh" in {e.id for e in again.entities}
    assert again.config_hash != first.config_hash


def test_a_reading_carries_its_own_evidence_and_leaves_none_behind(tmp_path: Path) -> None:
    from agentlint.adapters.claude_code.adapter import Evidence

    adapter, cwd = build(tmp_path)
    with_history = read_config(adapter, cwd, sessions=10)
    assert isinstance(with_history.evidence, Evidence)
    assert [raw.header.session_id for raw in with_history.evidence.sessions] == ["s-1"]
    assert [p.id for p in with_history.evidence.packages] == ["pk@mk"] and with_history.evidence.hooks
    without = read_config(adapter, cwd, sessions=0)  # after a read with sessions, on the same adapter
    assert without.evidence.sessions == () and without.evidence.hook_runs == ()  # nothing carried over
    assert not hasattr(adapter, "recent") and not hasattr(adapter, "_scans")
    assert without.evidence.hooks[0] is not with_history.evidence.hooks[0]  # discovered afresh, not the first's
    [(_, kept)] = adapter._reader_scans.values()  # each hands its discovery to the transcript reader's briefings,
    assert tuple(kept.hooks) == without.evidence.hooks  # the latest one's: a reading never reads one back


def test_self_check_explains_every_difference(tmp_path: Path) -> None:
    from agentlint.adapters.claude_code.selfcheck import NOWHERE, self_check

    adapter, cwd = build(tmp_path)
    results = {r.name: r for r in self_check(adapter)}
    skills = results["skills and commands"]
    assert (skills.matched, skills.delivered) == (1, 2)  # pk:helper reproduced from disk
    assert skills.delivered_only == {"builtin-skill": NOWHERE}  # bare, defined nowhere on disk
    assert skills.unexplained == []
    write(adapter.paths.config / "skills" / "builtin-skill" / "SKILL.md", "---\ndescription: x\n---\n")
    stale = {r.name: r for r in self_check(adapter)}["skills and commands"]
    assert stale.unexplained == [] and stale.matched == 2  # now read from disk: it matches


def test_text_the_user_did_not_write_says_what_it_is() -> None:
    from agentlint.adapters.claude_code.points import Point
    from agentlint.config.taxonomy import InjectionPoint, Mechanism

    harness_made = [p.value for p in Point if p.value.mechanism in (Mechanism.HARNESS_GENERATED, Mechanism.HARNESS_BUILTIN)]
    assert harness_made and all(p.description for p in harness_made)
    plain = InjectionPoint(Point.SKILL_LISTING.value.harness, "skill_listing", "skill listing", Mechanism.HARNESS_GENERATED)
    assert plain == Point.SKILL_LISTING.value  # the words never change what a point is


def test_a_user_and_a_project_skill_of_one_name_are_two_entities(tmp_path: Path) -> None:
    from agentlint.checks.duplicate_name import run as duplicate_name

    adapter, cwd = build(tmp_path)
    copy, drifted = "---\ndescription: ship it\n---\nthe same steps\n", "---\ndescription: ship it\n---\nother steps\n"
    write(tmp_path / "home/.claude/market/plugins/pk/skills/deploy/SKILL.md", copy)
    write(tmp_path / "home/.claude/skills/deploy/SKILL.md", copy)  # the user's: a copy of the package's
    write(cwd / ".claude/skills/deploy/SKILL.md", drifted)  # the project's: drifted
    reading = read_config(adapter, cwd, sessions=10)
    snap = reading.snapshot
    deploys = sorted(e.id for e in snap.entities if e.short_name == "deploy")
    assert deploys == ["skill:package:pk:deploy", "skill:project:deploy", "skill:user:deploy"]
    assert {e.provided_by for e in snap.entities if e.name == "deploy"} == {
        "claude-code:skill:user:deploy", "claude-code:skill:project:deploy"}
    [finding] = duplicate_name(snap, reading.files)
    assert finding.title.startswith('2 skills named "deploy" — 2 of 3 declarations are identical copies')  # the user's
    # is the package's copy, the project's drifted: one name, two declarations, and only one of them a copy
    assert finding.suggested_action.startswith("Remove the extra copies, then compare what remains")
    marks = {m.entity: (m.scope.value, m.package, m.identical_to) for m in finding.members}
    assert marks == {"skill:user:deploy": ("user", None, "skill:package:pk:deploy"),
                     "skill:project:deploy": ("project", None, None),  # the drifted one carries no copy mark
                     "skill:package:pk:deploy": ("package", "pk@mk", "skill:user:deploy")}
    assert finding.discriminator == "skills:deploy"  # identity unchanged: the namespace and short name


def test_it_says_whether_the_harness_is_installed_and_its_version_from_disk(tmp_path: Path) -> None:
    adapter, _ = build(tmp_path)
    assert adapter.installed() and adapter.version() == "9.9.9"  # the install's package.json, never by running it
    bare = ClaudeCodeAdapter(Paths(tmp_path / "empty-home", tmp_path / "none.json", tmp_path / "no-install"))
    assert not bare.installed() and bare.version() is None


def test_a_package_is_known_by_its_name_before_the_marketplace(tmp_path: Path) -> None:
    adapter, cwd = build(tmp_path)
    assert adapter.package_label("brain@acme-market") == "brain"
    assert adapter.package_label("no-marketplace") == "no-marketplace"  # nothing to split: the id, never a guess
    assert adapter.package_label("@mk") == "@mk" and adapter.package_label("pk@") == "pk@"
    packages = {s.provenance.package for s in read_config(adapter, cwd, sessions=10).snapshot.sources} - {None}
    assert packages == {"pk@mk"} and {adapter.package_label(p) for p in packages} == {"pk"}


def test_a_reading_uses_no_session_that_started_before_you_forgot(tmp_path: Path) -> None:
    """The transcript was written just now, but its session started 2026-10-01T09:00Z: forgotten after that, it is
    read for nothing — not the sessions, not the evidence the adapter's checks judge."""
    from datetime import UTC, datetime

    adapter, cwd = build(tmp_path)
    after = read_config(adapter, cwd, sessions=10, forgotten_before=datetime(2026, 10, 1, 9, 0, 1, tzinfo=UTC))
    assert after.snapshot.sessions == () and after.evidence.sessions == () and after.evidence.hook_runs == ()
    hook = next(s for s in after.snapshot.sources if s.id.startswith("claude-code:hook:pk@mk"))
    assert isinstance(hook.content, NotObserved)
    assert after.forgotten_before == datetime(2026, 10, 1, 9, 0, 1, tzinfo=UTC)
    before = read_config(adapter, cwd, sessions=10, forgotten_before=datetime(2026, 10, 1, 9, 0, tzinfo=UTC))
    assert [r.session_id for r in before.snapshot.sessions] == ["s-1"]  # it started at the cutoff, not before


def test_the_adapter_answers_every_part_of_the_interface() -> None:
    """A Protocol isn't checked at runtime: name what each part declares and find it on the adapter, so a part that
    grows a member the adapter lacks fails here rather than in the live UI."""
    from agentlint.harness import Adapter, ConfigReader, ConversationReader, TranscriptArchive, VariantHomes

    adapter = ClaudeCodeAdapter(Paths(home=Path("/nonexistent")))
    for part in (ConfigReader, TranscriptArchive, ConversationReader, VariantHomes):
        declared = {name for name in vars(part) if not name.startswith("_")} | set(part.__annotations__)
        assert declared, part
        assert not [name for name in declared if not hasattr(adapter, name)], part.__name__
    assert set(Adapter.__mro__) >= {ConfigReader, TranscriptArchive, ConversationReader}
