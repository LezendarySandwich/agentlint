"""Claude Code checks on hand-built snapshots."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agentlint.adapters.claude_code.adapter import AGENT_LISTING, SKILL_LISTING, Evidence
from agentlint.adapters.claude_code.checks import listing_budget, package_footprint
from agentlint.adapters.claude_code.points import Point
from agentlint.config.assemble import assemble
from agentlint.config.content import NotObserved, content_hash
from agentlint.config.model import AblationPolicy, Activation, Declared, Provenance, Source
from agentlint.config.taxonomy import Authority, Load
from agentlint.config.formats import ContentFormat
from agentlint.config.sessions import Delivery, SessionDeliveries

from ..config.helpers import PROJECT, ref


def part(source_id: str, point: Point, package: str | None) -> Source:
    return Source(source_id, Provenance(point.value, None, Authority.PACKAGE if package else Authority.RUNTIME, package),
                  Activation(Load.ALWAYS), content_hash(source_id), NotObserved(0, "only a session"), AblationPolicy.blocked("x"))


def settings(fraction: float) -> Source:
    import json
    from agentlint.config.content import file_text

    content = file_text(ContentFormat.JSON, json.dumps({"skillListingBudgetFraction": fraction}))
    return Source("settings:user", Provenance(Point.SETTINGS.value, Path("/u/settings.json"), Authority.USER),
                  Activation(Load.NOT_IN_CONTEXT), content.content_hash, content, AblationPolicy.blocked("x"))


def snapshot(skill_lines: dict[str | None, str], agent_text: str = "", model: str = "model-x[1m]",
             described: tuple[str, ...] = (), bare: tuple[str, ...] = (), fraction: float | None = None):
    """A snapshot whose session delivered these listing parts and recorded these names as listed."""
    from agentlint.adapters.claude_code.points import Kind
    from agentlint.config.entity import Entity, Visibility

    sources = [part(SKILL_LISTING, Point.SKILL_LISTING, None), part(AGENT_LISTING, Point.AGENT_LISTING, None)]
    if fraction is not None:
        sources.append(settings(fraction))
    deliveries = []
    for owner, text in skill_lines.items():
        sid = f"{SKILL_LISTING}:{owner}" if owner else SKILL_LISTING
        if owner:
            sources.append(part(sid, Point.SKILL_LISTING, owner))
        deliveries.append(Delivery(Point.SKILL_LISTING.value, sid, ContentFormat.PLAIN, text, Load.ALWAYS, "listing"))
    if agent_text:
        sources.append(part(f"{AGENT_LISTING}:pkg@mk", Point.AGENT_LISTING, "pkg@mk"))
        deliveries.append(Delivery(Point.AGENT_LISTING.value, f"{AGENT_LISTING}:pkg@mk", ContentFormat.PLAIN, agent_text, Load.ALWAYS, "agents"))
    entities = tuple(Entity(f"skill:{n}", n, Kind.SKILL.value, SKILL_LISTING) for n in (*described, *bare))
    visibility = tuple((f"skill:{n}", Visibility.DESCRIBED) for n in described) + tuple(
        (f"skill:{n}", Visibility.NAME_ONLY) for n in bare)
    declared = Declared("claude-code", PROJECT, "2.0", model, tuple(sources), ())
    return assemble(declared, [SessionDeliveries(ref(1), tuple(deliveries), None, entities, visibility)])


PKG_LISTING = "- pkg:a\n- pkg:b\n- pkg:c: described here\n  continued description\n  - with a bullet of its own"


def test_listing_budget_counts_bare_names_per_package() -> None:
    snap = snapshot({None: "The following skills are available:\n- builtin: has a description", "pkg@mk": PKG_LISTING,
                     "other@mk": "- other:x"}, described=("builtin", "pkg:c"), bare=("pkg:a", "pkg:b", "other:x"),
                    fraction=0.00005)  # a budget of 150 characters, which this listing fills
    finding = listing_budget(snap)[0]
    assert finding.title == "3 of 5 skills reach the model as a bare name"  # the bullet in pkg:c is not a skill
    assert "2 name-only from pkg@mk (e.g. pkg:a, pkg:b)" in finding.detail
    assert {f.label: f.value for f in finding.figures}["budget"] == 150  # 1M context × 3 × 0.00005
    assert "/name" in finding.detail and finding.severity.value == "warn"
    assert "Built-ins keep their descriptions" not in finding.detail


def test_listing_budget_is_silent_unless_the_listing_is_being_cut() -> None:
    assert listing_budget(snapshot({None: "- a: one\n- b: two"}, described=("a", "b"))) == []
    under_cap = snapshot({"pkg@mk": PKG_LISTING}, described=("pkg:c",), bare=("pkg:a", "pkg:b"))  # 30,000-char budget
    assert listing_budget(under_cap) == []  # bare names here have no description to give, not a cut one


def test_package_footprint_counts_the_listing_share_as_removable_unless_capped() -> None:
    text = "- pkg:a: a description long enough to cost tokens\n- pkg:b"
    cases = ((None, ("pkg:b",), False),  # far under its budget
             (0.00001, (), False),  # near its budget, but nothing was cut: every entry has its description
             (0.00001, ("pkg:b",), True))  # near its budget and cutting
    for fraction, bare, capped in cases:
        snap = snapshot({"pkg@mk": text}, agent_text="- pkg:agent: does agent things", described=("pkg:a",),
                        bare=bare, fraction=fraction)
        finding = package_footprint(snap)[0]
        assert finding.title.startswith("1 packages add ≈")
        removable = [line for line in finding.detail.splitlines() if "pkg@mk" in line][0]
        agent = snap.source(f"{AGENT_LISTING}:pkg@mk").tokens.value
        skill = snap.source(f"{SKILL_LISTING}:pkg@mk").tokens.value
        expected = agent if capped else agent + skill
        assert f"≈{expected:,} would leave context if disabled" in removable
        assert ("at its cap" in finding.detail) is capped


def test_interrupted_start_and_hook_health_from_transcripts(tmp_path) -> None:
    import json
    from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
    from agentlint.adapters.claude_code.checks import hook_health, interrupted_start
    from agentlint.adapters.claude_code.discovery import Paths
    from agentlint.harness import read_config

    home, cwd = tmp_path / "home", tmp_path / "work"
    (home / ".claude" / "projects" / "-work").mkdir(parents=True)
    cwd.mkdir()

    def line(kind, ts, **f):
        return json.dumps({"type": kind, "sessionId": f.pop("sid", "s1"), "cwd": str(cwd), "version": "2.0",
                           "timestamp": ts, "isSidechain": False, **f})

    def att(payload, ts, sid="s1"):
        return line("attachment", ts, sid=sid, attachment=payload)

    listing = {"type": "skill_listing", "names": ["a"], "skillCount": 1, "content": "- a"}
    fail = {"type": "hook_non_blocking_error", "hookEvent": "PostToolUse", "hookName": "PostToolUse:Edit",
            "command": "bash fix.sh", "stderr": "bash: fix.sh: No such file or directory", "exitCode": "1"}
    timeout = {"type": "hook_cancelled", "hookEvent": "PreToolUse", "hookName": "PreToolUse:Bash",
               "command": "guard.py", "timedOut": "True", "durationMs": "6000", "timeoutMs": "5000"}
    interrupted = [line("user", "2026-10-01T09:00:00Z", message={"content": "first"}), att(listing, "2026-10-01T09:00:01Z"),
                   line("user", "2026-10-01T09:01:00Z", message={"content": "again"}),
                   *[att(fail, f"2026-10-01T09:0{i}:30Z") for i in range(1, 5)],
                   *[att(timeout, f"2026-10-01T0{i}:00:00Z") for i in range(5, 9)],
                   line("assistant", "2026-10-01T09:09:00Z", message={"usage": {"input_tokens": 9}, "content": []})]
    (home / ".claude" / "projects" / "-work" / "s1.jsonl").write_text("\n".join(interrupted) + "\n")
    configured = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "quiet.sh"}]}]}}
    (home / ".claude" / "settings.json").write_text(json.dumps(configured))
    adapter = ClaudeCodeAdapter(Paths(home, tmp_path / "none.json", tmp_path / "install"))
    reading = read_config(adapter, cwd, sessions=10)

    [dropped] = interrupted_start(reading.evidence)
    assert dropped.title == "In 1 of your last 1 sessions the skill listing never reached the model"
    import os
    import time
    zone = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Kolkata"  # UTC+5:30: the session's start reads in local time, as the UI shows it
    time.tzset()
    try:
        [local] = interrupted_start(reading.evidence)
    finally:
        if zone is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = zone
        time.tzset()
    assert local.detail.startswith("2026-10-01 14:30  s1")

    result = hook_health(reading.evidence)
    findings = {f.discriminator: f for f in result.findings}
    error, killed = findings["error:bash fix.sh"], findings["timeout:guard.py"]
    assert result.judged("error:bash fix.sh")  # it ran in this window: its absence later would mean fixed
    assert result.judged("timeout:gone.sh")  # neither running nor configured any more: gone
    assert not result.judged("timeout:quiet.sh")  # configured but didn't run here: this window can't tell
    assert "failed 4 times in 1 session" in error.title and "No such file" in error.detail
    assert "of 4" not in error.title and "isn't recorded" in error.detail  # no rate: successes aren't all recorded
    assert error.severity.value == "warn" and "something enables it at launch" in error.detail  # newest session, undeclared
    assert "killed at its timeout 4 times in 1 session" in killed.title and "busy machine" not in killed.detail
    assert error.suggested_action.startswith("Fix or remove the hook")  # live: newest session, enabled somewhere

    from agentlint.checks import run_checks
    later = run_checks(read_config(adapter, cwd, sessions=0), adapter.checks())  # the same adapter, no sessions asked
    assert {"interrupted-start", "hook-health"} <= set(later.not_evaluated)  # the earlier read's sessions aren't used


def test_unavailable_reference_grades_denied_absent_and_optional() -> None:
    import json
    from types import SimpleNamespace
    from agentlint.adapters.claude_code.checks import unavailable_reference
    from agentlint.config.content import file_text
    from agentlint.config.entity import Entity
    from agentlint.adapters.claude_code.points import Kind

    settings_text = json.dumps({"permissions": {"deny": ["mcp__plugin_p_srv__forbidden"]}})
    settings = Source("settings", Provenance(Point.SETTINGS.value, Path("/s.json"), Authority.USER), Activation(Load.NOT_IN_CONTEXT),
                      content_hash(settings_text), file_text(ContentFormat.JSON, settings_text), AblationPolicy.blocked("x"))
    words = ("Use the Grep and Read tools.\nCall mcp__plugin_p_srv__forbidden for search.\n"
             "Try mcp__plugin_p_srv__renamed, or mcp__other__thing if you have it.\n")
    notes = Source("notes", Provenance(Point.CLAUDE_MD.value, Path("/n.md"), Authority.USER), Activation(Load.ALWAYS),
                   content_hash(words), file_text(ContentFormat.MARKDOWN, words), AblationPolicy.blocked("x"))
    tools = Source("tools", Provenance(Point.TOOL_DEFINITIONS.value, None, Authority.HARNESS), Activation(Load.ALWAYS),
                   content_hash("tools"), NotObserved(0, "x"), AblationPolicy.blocked("x"))
    entities = (Entity("tool:Read", "Read", Kind.TOOL.value, "tools"),
                Entity("tool:mcp__plugin_p_srv__real", "mcp__plugin_p_srv__real", Kind.MCP_TOOL.value, "tools"))
    snap = assemble(Declared("claude-code", PROJECT, "2.0", "m", (settings, notes, tools), entities), [])
    package = SimpleNamespace(id="p@mk", name="p")
    evidence = Evidence(packages=(package,))
    found = {f.discriminator: f for f in unavailable_reference(snap, evidence)}
    assert set(found) == {"denied:mcp__plugin_p_srv__forbidden", "absent:Grep", "stale:mcp__plugin_p_srv__renamed",
                          "unavailable-server:other"}
    assert found["denied:mcp__plugin_p_srv__forbidden"].severity.value == "warn"
    assert found["absent:Grep"].severity.value == "warn" and found["absent:Grep"].locations[0].line == 1
    assert found["unavailable-server:other"].severity.value == "info"  # optional elsewhere-tools are information
    no_session = assemble(Declared("claude-code", PROJECT, "2.0", "m", (settings, notes, tools), ()), [])
    from agentlint.checks import NotEvaluated
    with pytest.raises(NotEvaluated):  # nothing to compare against: say nothing, and don't mark anything fixed
        unavailable_reference(no_session, evidence)


def test_broken_path_only_flags_what_would_run(tmp_path) -> None:
    from types import SimpleNamespace
    from agentlint.adapters.claude_code.checks import broken_path
    from agentlint.config.content import file_text

    root = tmp_path / "pkg"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "here.py").write_text("")
    package = SimpleNamespace(id="pkg@mk", root=root)
    hooks = [SimpleNamespace(package=package, command="bash ${CLAUDE_PLUGIN_ROOT}/hooks/gone.sh", source_id="hook:gone"),
             SimpleNamespace(package=package, command="bash -c '[ -f \"$0\" ] && exec bash \"$0\"' \"${CLAUDE_PLUGIN_ROOT}/hooks/optional.sh\"",
                             source_id="hook:optional")]
    text = ("python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/missing.py\" --go\n"
            "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/here.py\n"
            "Read ${CLAUDE_PLUGIN_ROOT}/CLAUDE.md if it is present\n"
            "Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/lib/:*)\n")
    content = file_text(ContentFormat.MARKDOWN, text)
    source = Source("cmd", Provenance(Point.COMMAND.value, root / "cmd.md", Authority.PACKAGE, "pkg@mk"),
                    Activation(Load.ON_INVOKE, "x"), content.content_hash, content, AblationPolicy.blocked("x"))
    snap = assemble(Declared("claude-code", PROJECT, "2.0", "m", (source,), ()), [])
    from agentlint.adapters.claude_code.package_paths import missing_paths

    missing = missing_paths({"pkg@mk": root}, [("pkg@mk", h.command) for h in hooks], [("pkg@mk", text)])  # at read time
    assert ("pkg@mk", "scripts/here.py") not in missing and ("pkg@mk", "scripts/missing.py") in missing
    evidence = Evidence(hooks=tuple(hooks), packages=(package,), missing_paths=missing)
    found = {f.discriminator: f for f in broken_path(snap, evidence)}
    assert set(found) == {"path:pkg@mk:hooks/gone.sh", "path:pkg@mk:hooks/optional.sh", "path:pkg@mk:scripts/missing.py"}
    assert found["path:pkg@mk:hooks/gone.sh"].severity.value == "error"
    assert found["path:pkg@mk:hooks/optional.sh"].severity.value == "info"  # guarded: skips quietly
    assert found["path:pkg@mk:scripts/missing.py"].locations[0].line == 1
    assert [l.source_id for l in found["path:pkg@mk:hooks/gone.sh"].locations] == ["hook:gone"]  # the hook, by id


def test_listing_descriptions_stop_at_the_end_of_each_entry() -> None:
    from agentlint.adapters.claude_code.listings import listing_descriptions

    text = ("<system-reminder>\nAvailable agent types:\n- Explore: Read-only search\n  agent for fan-out. (Tools: Read, Bash(git:*))\n"
            "- bare\n- other: Not delivered\n- Plan: Plans things\n\nWhen you launch agents, batch them.\n</system-reminder>")
    found = listing_descriptions(text, ["Explore", "bare", "Plan"])
    assert found == {"Explore": "Read-only search agent for fan-out.", "Plan": "Plans things"}


def test_unavailable_reference_parsing_and_wording() -> None:
    import json
    from types import SimpleNamespace
    from agentlint.adapters.claude_code.checks import unavailable_reference
    from agentlint.adapters.claude_code.points import Kind
    from agentlint.config.content import file_text
    from agentlint.config.entity import Entity

    deny = {"permissions": {"deny": ["mcp__plugin_p_srv__forbidden", "WebFetch"]}}
    settings_text = json.dumps(deny)
    settings_source = Source("settings", Provenance(Point.SETTINGS.value, Path("/s.json"), Authority.USER),
                             Activation(Load.NOT_IN_CONTEXT), content_hash(settings_text),
                             file_text(ContentFormat.JSON, settings_text), AblationPolicy.blocked("x"))
    words = ("File bugs with mcp__plugin_p_srv__real.\n"           # trailing full stop: still the real tool
             "Never use the WebFetch tool; fetching is blocked.\n"  # agrees with the deny rule: not a contradiction
             "For search, use mcp__plugin_p_srv__forbidden first.\n"  # asks for a denied tool: reported, quoted
             "Use the SlashCommand tool, or open it with the Editor tool.\n"
             "Try mcp__excal__draw or mcp__other__thing.\n")
    notes = Source("notes", Provenance(Point.CLAUDE_MD.value, Path("/n.md"), Authority.USER), Activation(Load.ALWAYS),
                   content_hash(words), file_text(ContentFormat.MARKDOWN, words), AblationPolicy.blocked("x"))
    entities = (Entity("tool:Read", "Read", Kind.TOOL.value, "tools"),
                Entity("tool:mcp__plugin_p_srv__real", "mcp__plugin_p_srv__real", Kind.MCP_TOOL.value, "tools"))
    tools = Source("tools", Provenance(Point.TOOL_DEFINITIONS.value, None, Authority.HARNESS),
                   Activation(Load.ALWAYS), content_hash("tools"), NotObserved(0, "x"), AblationPolicy.blocked("x"))
    snap = assemble(Declared("claude-code", PROJECT, "2.0", "m", (settings_source, notes, tools), entities), [])
    recent = [SimpleNamespace(failed_mcp_servers=("excal", "plugin:acme:other"), tool_names=())]
    package = SimpleNamespace(id="p@mk", name="p")
    evidence = Evidence(packages=(package,), sessions=tuple(recent))
    found = {f.discriminator: f for f in unavailable_reference(snap, evidence)}
    assert "stale:mcp__plugin_p_srv__real." not in found and "denied:WebFetch" not in found
    denied = found["denied:mcp__plugin_p_srv__forbidden"]
    assert "as a tool to use" in denied.title and "For search, use mcp__plugin_p_srv__forbidden first." in denied.detail
    assert "absent:SlashCommand" in found and "absent:Edit" not in found  # whole words only
    assert "failed to connect" in found["unavailable-server:excal"].detail
    assert "no server of that name" in found["unavailable-server:other"].detail  # plugin:acme:other is another server


def test_broken_entity_references_the_claude_code_way(tmp_path) -> None:
    from agentlint.adapters.claude_code.checks import broken_entity_references
    from agentlint.adapters.claude_code.points import Kind
    from agentlint.config.content import file_text
    from agentlint.config.entity import Entity

    text = ("Use `pkg:helpr` or Skill('pkg:gone'), subagent_type: \"pkg:help-me\".\n"
            "Plain prose pkg:ignored and `other:thing` and `pkg:helper` are fine.\n"
            "```\n`pkg:also-ignored`\nTask(subagent_type=\"pkg:in-fence\")\n```\n")
    path = tmp_path / "SKILL.md"
    path.write_text(text)
    content = file_text(ContentFormat.MARKDOWN, text)
    source = Source("doc", Provenance(Point.SKILL.value, path, Authority.USER), Activation(Load.ON_INVOKE),
                    content.content_hash, content, AblationPolicy.blocked("x"))
    entities = (Entity("skill:pkg:helper", "pkg:helper", Kind.SKILL.value, "doc"),
                Entity("skill:pkg:help_me", "pkg:help_me", Kind.SKILL.value, "doc"))
    snap = assemble(Declared("claude-code", PROJECT, "2.0", "m", (source,), entities), [])
    refs = {f.discriminator: f for f in broken_entity_references(snap)}
    assert set(refs) == {"entity:pkg:helpr", "entity:pkg:gone", "entity:pkg:help-me", "entity:pkg:in-fence"}
    assert refs["entity:pkg:in-fence"].locations[0].line == 5  # a call in code counts; a code span in code doesn't
    assert refs["entity:pkg:help-me"].suggested_action == "Change it to pkg:help_me."  # near-certain: recommended
    assert refs["entity:pkg:gone"].suggested_action == "Fix or remove the reference."


def test_hook_health_advice_for_a_hook_that_is_gone(tmp_path) -> None:
    import json
    from types import SimpleNamespace
    from agentlint.adapters.claude_code.checks import hook_health

    def transcript(name: str, failures: int):
        lines = [json.dumps({"type": "attachment", "sessionId": name, "timestamp": f"2026-09-2{i}T10:00:00Z",
                             "isSidechain": False, "attachment": {
                                 "type": "hook_non_blocking_error", "hookEvent": "PostToolUse",
                                 "hookName": "PostToolUse:Edit", "command": "bash gone.sh",
                                 "stderr": "bash: gone.sh: No such file or directory", "exitCode": "1"}})
                 for i in range(failures)]
        path = tmp_path / f"{name}.jsonl"
        path.write_text("\n".join(lines) + "\n" if lines else "")
        return path

    paths = [transcript("newest", 0), transcript("middle", 0), transcript("oldest", 3)]  # most recent first
    from agentlint.adapters.claude_code.transcripts import hook_records

    evidence = Evidence(sessions=tuple(SimpleNamespace(path=path) for path in paths),
                        hook_runs=tuple(record for path in paths for record in hook_records(path)))
    [finding] = hook_health(evidence).findings
    assert finding.severity.value == "info" and "probably already resolved" in finding.detail
    assert finding.suggested_action.startswith("Nothing configured runs it now")  # not "fix or remove" what isn't there


def test_hook_health_last_seen_is_a_local_date(tmp_path, local_zone) -> None:
    """"Last seen" is the day the UI shows that session on: local time, like every other time shown (round 12)."""
    import json
    from types import SimpleNamespace
    from agentlint.adapters.claude_code.checks import hook_health
    from agentlint.adapters.claude_code.transcripts import hook_records

    def transcript(name: str, stamps: list[str]):
        path = tmp_path / f"{name}.jsonl"
        path.write_text("".join(json.dumps({"type": "attachment", "sessionId": name, "timestamp": at, "isSidechain": False,
                                            "attachment": {"type": "hook_non_blocking_error", "hookEvent": "PostToolUse",
                                                           "command": "bash gone.sh", "stderr": "no such file",
                                                           "exitCode": "1"}}) + "\n" for at in stamps))
        return path

    paths = [transcript("newest", []), transcript("middle", []),
             transcript("oldest", ["2026-10-01T23:00:00Z", "2026-10-01T23:10:00Z", "2026-10-01T23:30:00Z"])]
    evidence = Evidence(sessions=tuple(SimpleNamespace(path=path) for path in paths),
                        hook_runs=tuple(record for path in paths for record in hook_records(path)))
    local_zone("Europe/London")  # BST, UTC+1: 23:30Z on 1 Oct is 00:30 on 2 Oct
    [finding] = hook_health(evidence).findings
    assert "Last seen 2026-10-02;" in finding.detail


@pytest.mark.parametrize("sentence, reported", [
    ("Never use the WebFetch tool; fetching is blocked here.", False),
    ("The WebFetch tool is blocked here.", False),
    ("The WebFetch tool is denied in this repo, so fetch with curl instead.", False),
    ("Avoid the Bash tool; use the WebFetch tool for URLs.", True),
    ("Fetching is blocked by proxies, so use the WebFetch tool.", True),
    ("Use the WebFetch tool instead of curl.", True),
    ("Never use the Bash tool, the WebFetch tool or the WebSearch tool.", False),  # one prohibition over a list
    ("Do not call WebSearch tool, WebFetch tool, or Bash tool.", False),
    ("Never use curl, wget — or the WebFetch tool.", False),
    ("Never use Bash, but use the WebFetch tool.", True),
])
def test_a_denied_tool_is_reported_only_where_it_is_asked_for(sentence: str, reported: bool) -> None:
    from agentlint.adapters.claude_code.checks import _prohibited

    offset = sentence.index("WebFetch")
    assert _prohibited(sentence, offset) is not reported


def test_a_stale_name_on_a_connected_user_server_is_reported() -> None:
    import json
    from types import SimpleNamespace
    from agentlint.adapters.claude_code.checks import unavailable_reference
    from agentlint.adapters.claude_code.points import Kind
    from agentlint.config.content import file_text
    from agentlint.config.entity import Entity

    words = "File it with mcp__github__old_tool.\n"
    notes = Source("notes", Provenance(Point.CLAUDE_MD.value, Path("/n.md"), Authority.USER), Activation(Load.ALWAYS),
                   content_hash(words), file_text(ContentFormat.MARKDOWN, words), AblationPolicy.blocked("x"))
    tools = Source("tools", Provenance(Point.TOOL_DEFINITIONS.value, None, Authority.HARNESS),
                   Activation(Load.ALWAYS), content_hash("tools"), NotObserved(0, "x"), AblationPolicy.blocked("x"))
    entities = (Entity("tool:mcp__github__create_issue", "mcp__github__create_issue", Kind.MCP_TOOL.value, "tools"),)
    snap = assemble(Declared("claude-code", PROJECT, "2.0", "m", (notes, tools), entities), [])
    found = {f.discriminator for f in unavailable_reference(snap, Evidence())}
    assert "stale:mcp__github__old_tool" in found  # the server connected: the name is wrong, not optional


def _hook_evidence(entries, runs):
    """Evidence of these configured hooks and these recorded runs, in sessions s1 (most recent) and s2."""
    from types import SimpleNamespace

    return Evidence(sessions=(SimpleNamespace(path=Path("s1.jsonl")), SimpleNamespace(path=Path("s2.jsonl"))),
                    hooks=tuple(entries), hook_runs=tuple(runs))


def _entry(owner: str, event: str, command: str, index: int = 0, module: bool = False):
    from agentlint.adapters.claude_code.hooks import HookEntry
    from agentlint.config.taxonomy import Authority

    return HookEntry(owner, event, None, command, Path(f"/plugins/{owner}/.claude-plugin/plugin.json"),
                     f"/hooks/{event}/{index}/hooks/0", Authority.PACKAGE, module=module)


def _timeouts(event: str, command: str, count: int = 5):
    from agentlint.adapters.claude_code.transcripts import HookRecord

    return [HookRecord(f"s{1 + i % 2}", event, event, command, "timeout", 6000, "") for i in range(count)]


def test_a_hook_health_finding_rests_on_and_points_at_the_hook_that_ran_its_command() -> None:
    from agentlint.adapters.claude_code.checks import hook_health
    from agentlint.report.finding import Location

    ran = _entry("brain@acme-market", "UserPromptSubmit", "node brain.js")
    elsewhere = _entry("brain@acme-market", "SessionStart", "node brain.js")  # the same command, another hook
    other = _entry("other@mk", "UserPromptSubmit", "node other.js", index=1)
    [finding] = hook_health(_hook_evidence([ran, elsewhere, other], _timeouts("UserPromptSubmit", "node brain.js"))).findings
    assert finding.title == "A UserPromptSubmit hook was killed at its timeout 5 times in 2 sessions (brain@acme-market)"
    assert finding.sources == (ran.source_id,)
    assert finding.locations == (Location(ran.source_id),)  # no line: a hook's text is what it printed, not its JSON
    assert finding.severity.value == "warn" and "configuration files" not in finding.detail


def test_a_command_two_packages_share_rests_on_both_hooks() -> None:
    from agentlint.adapters.claude_code.checks import hook_health

    a = _entry("a@mk", "SessionStart", "${CLAUDE_PLUGIN_ROOT}/hooks/start.sh")
    b = _entry("b@mk", "SessionStart", "${CLAUDE_PLUGIN_ROOT}/hooks/start.sh")
    [finding] = hook_health(_hook_evidence([a, b], _timeouts("SessionStart", a.command))).findings
    assert finding.title.endswith("(one of a@mk, b@mk)")
    assert finding.sources == (a.source_id, b.source_id)
    assert [l.source_id for l in finding.locations] == list(finding.sources) and all(l.line is None for l in finding.locations)


def test_a_command_configured_only_for_another_event_is_not_what_ran() -> None:
    """Precision over recall: the hook declared for SessionStart never fired a UserPromptSubmit run, so the finding
    rests on nothing — and the title says the command is declared, but only for that other event."""
    from agentlint.adapters.claude_code.checks import hook_health

    moved = _entry("brain@acme-market", "SessionStart", "node brain.js")
    [finding] = hook_health(_hook_evidence([moved], _timeouts("UserPromptSubmit", "node brain.js"))).findings
    assert finding.sources == () and finding.locations == ()
    assert finding.title.endswith("(declared now only for SessionStart)")
    assert "Not in your configuration files for UserPromptSubmit: something enables it at launch." in finding.detail


def test_a_command_not_configured_now_rests_on_nothing() -> None:
    from agentlint.adapters.claude_code.checks import hook_health
    from agentlint.adapters.claude_code.transcripts import HookRecord

    module = _entry("mod@mk", "*", "", module=True)  # a module hook names no command: never what ran a run with none
    nameless = [HookRecord(f"s{1 + i % 2}", "Stop", "Stop", "", "timeout", 6000, "") for i in range(3)]
    findings = hook_health(_hook_evidence([module], _timeouts("PreToolUse", "bash gone.sh") + nameless)).findings
    assert len(findings) == 2 and all(f.sources == () and f.locations == () for f in findings)
    assert all(f.title.endswith("(not declared in your configuration now)") for f in findings)


def test_a_same_event_hook_whose_matcher_rejects_every_failing_run_is_not_what_ran() -> None:
    """Two entries of one package run one command for PostToolUse, one for Write|Edit|Read and one for Bash; every
    timeout was recorded as PostToolUse:Bash, so only the Bash entry can have fired them — the matcher tells them apart,
    as it does for the tracer."""
    from dataclasses import replace

    from agentlint.adapters.claude_code.checks import hook_health
    from agentlint.adapters.claude_code.transcripts import HookRecord

    command = "${CLAUDE_PLUGIN_ROOT}/rules-hooks post"
    files = replace(_entry("llm-rules@mk", "PostToolUse", command), matcher="Write|Edit|Read")
    bash = replace(_entry("llm-rules@mk", "PostToolUse", command, index=1), matcher="Bash")
    runs = [HookRecord(f"s{1 + i % 2}", "PostToolUse", "PostToolUse:Bash", command, "timeout", 6000, "") for i in range(5)]
    [finding] = hook_health(_hook_evidence([files, bash], runs)).findings
    assert finding.sources == (bash.source_id,) and [l.source_id for l in finding.locations] == [bash.source_id]
    assert finding.title.endswith("(llm-rules@mk)")

    # Only the Write|Edit|Read entry configured now: the hook that ran is gone, and the title says what is declared.
    [gone] = hook_health(_hook_evidence([files], runs)).findings
    assert gone.sources == () and gone.locations == ()
    assert gone.title.endswith("(declared now only for PostToolUse on Write|Edit|Read)")
    assert "Not in your configuration files for PostToolUse on Bash: something enables it at launch." in gone.detail

    # A run recorded with no target is not told apart: either entry may have fired it.
    untargeted = [replace(r, hook_name="PostToolUse") for r in runs]
    [both] = hook_health(_hook_evidence([files, bash], untargeted)).findings
    assert set(both.sources) == {files.source_id, bash.source_id}
