"""What the harness gave the model, in the transcript reader: a briefing before the first prompt, after every
compaction, and wherever the harness's own prompt changed (docs/tech_impl/11-ui.md, "Sessions")."""

from __future__ import annotations

import json
from pathlib import Path

from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
from agentlint.adapters.claude_code.discovery import Paths
from agentlint.config.conversation import (
    Briefing,
    BriefingReason,
    Conversation,
    Injected,
    Note,
    NoteKind,
    Prompt,
    Reply,
    ToolCall,
)
from agentlint.config.serialize import as_model, to_data
from agentlint.config.taxonomy import Authority
from agentlint.config.tokens import TokenMethod, estimate_tokens

PROMPT = ["You are the harness's agent.", "Be careful."]
TOOLS = [{"name": "Bash", "description": "run a command", "input_schema": {}},
         {"name": "Read", "description": "read a file", "input_schema": {}}]


def line(**record) -> str:
    return json.dumps({"sessionId": "s1", "cwd": "/w", "version": "2.1.285", "timestamp": "2026-10-01T09:00:00Z",
                       "isSidechain": False, **record})


def attachment(kind: str, rendered: str | None = None, **fields) -> str:
    extra = {"rendered": [{"content": rendered}], "renderedRole": "system"} if rendered is not None else {}
    return line(type="attachment", attachment={"type": kind, **fields}, **extra)


def snapshot(prompt=PROMPT, tools=None, **at) -> str:
    fields = {"systemPrompt": prompt} | ({"tools": tools, "cliPrefix": "You are a CLI."} if tools is not None else {})
    return line(type="attachment", attachment={"type": "prompt_snapshot", **fields}, **at)


def reply(text: str = "Done.", **at) -> str:
    return line(type="assistant", message={"model": "m", "content": [{"type": "text", "text": text}]}, **at)


def tool_turn() -> list[str]:
    """A reply that calls a tool and its result: the harness writes the snapshot with tool definitions after it."""
    return [line(type="assistant", message={"model": "m", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}),
            line(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "a b"}]})]


def session_start(text: str = "Use the mirror.", name: str = "SessionStart:startup") -> str:
    return attachment("hook_success", hookEvent="SessionStart", hookName=name, command="./start.sh", stdout=text,
                      content=text)


def first_turn() -> list[str]:
    return [
        session_start(),
        line(type="user", message={"content": "fix the test"}),
        attachment("deferred_tools_delta", "Deferred: mcp__x__y", addedNames=["mcp__x__y"], addedLines=["mcp__x__y"]),
        attachment("skill_listing", "<system-reminder>skills\n- helper: helps\n- other</system-reminder>",
                   names=["helper", "other"], content="- helper: helps\n- other"),
        attachment("agent_listing_delta", "Agents:\n- code-search: finds", addedTypes=["code-search"],
                   addedLines=["- code-search: finds"], builtInTypes=[]),
        attachment("instructions", "the files, framed", files=[
            {"path": "<managed-settings>", "type": "Managed", "content": "Org policy."},
            {"path": "/w/AGENTS.md", "type": "Project", "content": "# Project rules"},
            {"path": "/h/.claude/projects/-w/memory/MEMORY.md", "type": "AutoMem", "content": "- a memory"}]),
        attachment("session_context", "gitStatus: clean", context={"gitStatus": "clean"}),
        attachment("environment", "Platform: darwin", snapshot={"platform": "darwin"}),
        attachment("date", "Today's date is 2026-10-01.", date="2026-10-01"),
        snapshot(),
        *tool_turn(),
        snapshot(tools=TOOLS),
        reply(),
    ]


def read(tmp_path: Path, records: list[str], name: str = "s1.jsonl") -> Conversation:
    path = tmp_path / ".claude" / "projects" / "-w" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(records) + "\n")
    return ClaudeCodeAdapter(Paths(tmp_path)).conversation_from(str(path), path.read_bytes())


def briefings(conversation: Conversation) -> list[Briefing]:
    return [item for item in conversation.items if isinstance(item, Briefing)]


def text(conversation: Conversation, briefing: Briefing, label: str) -> str:
    [part] = [part for part in briefing.parts if part.label == label]
    return conversation.texts[part.text_ref]


def test_the_first_turn_is_one_briefing_at_the_top_with_each_part_in_its_recorded_text(tmp_path: Path) -> None:
    conversation = read(tmp_path, first_turn())
    start = conversation.items[0]
    assert isinstance(start, Briefing) and start.reason is BriefingReason.START
    assert (start.recorded, start.note) == (True, None)
    assert [(p.label, p.detail, p.count, p.authority) for p in start.parts] == [
        ("System prompt", "", None, Authority.HARNESS),
        ("Tool definitions", "", 2, Authority.HARNESS),
        ("AGENTS.md", "/w/AGENTS.md", None, Authority.PROJECT),
        ("MEMORY.md", "/h/.claude/projects/-w/memory/MEMORY.md", None, Authority.USER),
        ("Managed policy", "", None, Authority.MANAGED),
        ("Skill listing", "", 2, Authority.RUNTIME),  # nothing on disk says any entry is a package's
        ("Agent listing", "", 1, Authority.RUNTIME),
        ("Deferred tool listing", "", 1, Authority.RUNTIME),
        ("Environment · date · model · git status", "", None, Authority.RUNTIME),
        ("SessionStart hook output", "SessionStart:startup", None, None),  # no configured hook to trace it to
    ]
    assert text(conversation, start, "System prompt") == "You are the harness's agent.\nBe careful."
    assert json.loads(text(conversation, start, "Tool definitions")) == TOOLS
    assert text(conversation, start, "Skill listing").startswith("<system-reminder>skills")  # rendered, not content
    assert text(conversation, start, "AGENTS.md") == "# Project rules"
    assert text(conversation, start, "Environment · date · model · git status") == (
        "gitStatus: clean\nPlatform: darwin\nToday's date is 2026-10-01.")
    for part in start.parts:
        assert part.tokens.method is TokenMethod.ESTIMATED
        assert part.tokens.value == estimate_tokens(conversation.texts[part.text_ref])
    assert start.tokens.value == sum(part.tokens.value for part in start.parts)
    assert [p.source_id for p in start.parts][:3] == [
        "claude-code:builtin:system-prompt", "claude-code:builtin:tools", "claude-code:instructions:/w/AGENTS.md"]
    assert [type(item) for item in conversation.items[1:]] == [Prompt, ToolCall, Reply]
    assert len(briefings(conversation)) == 1  # the follow-up snapshot with tool definitions is the same briefing


def test_session_start_output_before_the_first_prompt_moves_into_the_briefing_never_shown_twice(tmp_path: Path) -> None:
    conversation = read(tmp_path, first_turn())
    assert not any(isinstance(item, Injected) and "mirror" in item.text.text for item in conversation.items)
    assert text(conversation, conversation.items[0], "SessionStart hook output") == "Use the mirror."
    later = read(tmp_path, [*first_turn(), session_start("A later start.", "SessionStart:resume")])
    [shown] = [item for item in later.items if isinstance(item, Injected)]  # outside a briefing: in its place
    assert shown.text.text == "A later start."


def compaction(files_text: str = "The file, re-read.") -> list[str]:
    return [
        line(type="system", subtype="compact_boundary", compactMetadata={"trigger": "auto"},
             timestamp="2026-10-01T10:00:00Z"),
        attachment("instructions", "framed", files=[{"path": "/w/AGENTS.md", "type": "Project", "content": "# Project rules"}]),
        attachment("session_context", "gitStatus: dirty", context={"gitStatus": "dirty"}),
        line(type="user", isCompactSummary=True, message={"content": "Summary: we fixed the test."}),
        attachment("file", files_text, filename="/w/a.py", displayPath="a.py",
                   content={"type": "text", "file": {"filePath": "/w/a.py", "content": "print(1)"}}),
        attachment("invoked_skills", "Skills invoked: helper", skills=[{"name": "helper", "path": "p", "content": "body"}]),
        session_start("Use the mirror.", "SessionStart:compact"),
        snapshot(),
        *tool_turn(),
        snapshot(tools=TOOLS),
        reply("Carrying on."),
    ]


def test_after_compacting_a_briefing_follows_the_divider_with_what_the_context_was_rebuilt_with(tmp_path: Path) -> None:
    conversation = read(tmp_path, [*first_turn(), *compaction()])
    start, after = briefings(conversation)
    at = conversation.items.index(after)
    divider, summary = conversation.items[at - 1], conversation.items[at + 1]
    assert isinstance(divider, Note) and divider.kind is NoteKind.COMPACTED
    assert isinstance(summary, Injected) and "Summary" in summary.text.text  # the summary carried over stays as it is
    assert after.reason is BriefingReason.COMPACTED and after.recorded and after.note is None
    assert [p.label for p in after.parts] == [
        "System prompt", "Tool definitions", "AGENTS.md", "Environment · date · model · git status", "Files re-attached",
        "Skills carried over", "SessionStart hook output"]
    files = next(p for p in after.parts if p.label == "Files re-attached")
    assert (files.count, files.detail, conversation.texts[files.text_ref]) == (1, "a.py", "The file, re-read.")
    assert text(conversation, after, "Skills carried over") == "Skills invoked: helper"
    assert not any(isinstance(item, Injected) and "mirror" in item.text.text for item in conversation.items)
    assert len(briefings(conversation)) == 2  # the identical snapshots after it say nothing more


def test_each_distinct_text_is_held_once_however_often_it_was_re_sent(tmp_path: Path) -> None:
    conversation = read(tmp_path, [*first_turn(), *compaction(), *compaction("Another file.")])
    start, *after = briefings(conversation)
    assert len(after) == 2
    refs = [part.text_ref for b in briefings(conversation) for part in b.parts if part.label == "System prompt"]
    assert len(refs) == 3 and len(set(refs)) == 1
    assert len(conversation.texts) == len({part.text_ref for b in briefings(conversation) for part in b.parts})
    wire = json.dumps(to_data(conversation))
    assert wire.count("You are the harness's agent.") == 1 and wire.count("# Project rules") == 1


def test_an_identical_re_send_with_no_rebuild_is_silent(tmp_path: Path) -> None:
    conversation = read(tmp_path, [*first_turn(), line(type="user", message={"content": "next"}), snapshot(),
                                   *tool_turn(), snapshot(tools=TOOLS), reply()])
    assert len(briefings(conversation)) == 1


def test_a_changed_prompt_is_a_briefing_where_it_changed(tmp_path: Path) -> None:
    typed = line(type="user", message={"content": "<command-name>/model</command-name><command-args>opus</command-args>"})
    conversation = read(tmp_path, [*first_turn(), typed, line(type="user", message={"content": "again"}),
                                   snapshot(["A new prompt."], timestamp="2026-10-01T11:00:00Z"), *tool_turn(),
                                   snapshot(["A new prompt."], TOOLS[:1]), reply()])
    start, prompt, tools = briefings(conversation)
    assert (prompt.reason, prompt.note, [p.label for p in prompt.parts]) == (BriefingReason.CHANGED, "After /model.",
                                                                              ["System prompt"])
    assert text(conversation, prompt, "System prompt") == "A new prompt." and prompt.at.hour == 11
    assert (tools.reason, [(p.label, p.count) for p in tools.parts], tools.note) == (
        BriefingReason.CHANGED, [("Tool definitions", 1)], None)
    after_command = conversation.items.index(prompt)
    assert isinstance(conversation.items[after_command - 1], Prompt)  # where the harness recorded it


def test_a_harness_version_that_recorded_no_prompt_says_so_and_shows_what_it_did_record(tmp_path: Path) -> None:
    records = [line(type="user", message={"content": "hi"}, version="2.1.266"),
               attachment("skill_listing", names=["helper"], content="- helper: helps"),
               reply()]
    conversation = read(tmp_path, records)
    [start] = briefings(conversation)
    assert (start.recorded, start.note) == (False, "Not recorded by this harness version (2.1.266).")
    assert [p.label for p in start.parts] == ["Skill listing"]
    assert text(conversation, start, "Skill listing") == "- helper: helps"  # no rendered text: the attachment's own


def test_an_interrupted_start_says_what_was_recorded_may_not_have_reached_the_model(tmp_path: Path) -> None:
    records = [line(type="user", message={"content": "first try"}),
               attachment("skill_listing", "the listing", names=["helper"], content="- helper"),
               line(type="user", message={"content": "second try"}),
               attachment("date", "Today's date is 2026-10-01.", date="2026-10-01"),
               snapshot(), *tool_turn(), snapshot(tools=TOOLS), reply()]
    conversation = read(tmp_path, records)
    [start] = briefings(conversation)
    assert start.recorded and "may not have reached the model" in start.note
    assert "Skill listing" not in [p.label for p in start.parts]  # recorded only with the abandoned prompt


def test_a_subagents_transcript_has_its_briefing_too(tmp_path: Path) -> None:
    records = [r.replace('"isSidechain": false', '"isSidechain": true') for r in [
        line(type="user", message={"content": "the harness's preamble"}),
        line(type="user", message={"content": "your task"}),
        attachment("skill_listing", "the listing", names=["helper"], content="- helper"),
        snapshot(), *tool_turn(), snapshot(tools=TOOLS), reply()]]
    conversation = read(tmp_path, records, "s1/subagents/agent-a1.jsonl")
    [start] = briefings(conversation)
    assert start.note is None and [p.label for p in start.parts] == ["System prompt", "Tool definitions", "Skill listing"]


def test_a_briefing_reads_back_from_its_json(tmp_path: Path) -> None:
    conversation = read(tmp_path, [*first_turn(), *compaction()])
    data = to_data(conversation)
    assert data["items"][0]["type"] == "briefing" and data["items"][0]["tokens"]["method"] == "estimated"
    assert as_model(Conversation, json.loads(json.dumps(data))) == conversation


def test_with_the_configuration_each_part_names_its_source_and_a_listing_its_split(tmp_path: Path) -> None:
    from agentlint.harness import read_config
    from tests.adapters.test_claude_code_adapter import build

    adapter, cwd = build(tmp_path)
    conversation = adapter.read_conversation("s-1")
    [start] = briefings(conversation)
    skills = next(p for p in start.parts if p.label == "Skill listing")
    assert skills.authority is None and {(s.label, s.authority) for s in skills.shares} == {
        ("pk", Authority.PACKAGE), ("the listing's own part", Authority.RUNTIME)}  # it spans groups: "mixed"
    hook = next(p for p in start.parts if p.label == "SessionStart hook output")
    assert (hook.authority, hook.detail) == (Authority.PACKAGE, "pk")
    assert not any(isinstance(item, Injected) and "Package context" in item.text.text for item in conversation.items)
    sources = {source.id for source in read_config(adapter, cwd, sessions=10).snapshot.sources}
    linked = [p.source_id for p in start.parts] + [s.source_id for p in start.parts for s in p.shares]
    assert all(source_id in sources for source_id in linked if source_id)  # each links to a source the report has
    assert {"claude-code:listing:skills:pk@mk", hook.source_id} <= set(linked)


def test_the_server_answers_a_session_with_its_briefings_and_each_text_once(tmp_path: Path) -> None:
    from agentlint.store import connect
    from agentlint.ui.catalog import Catalogs, SessionCatalog

    read(tmp_path, [*first_turn(), *compaction()])
    catalogs = Catalogs([SessionCatalog(ClaudeCodeAdapter(Paths(tmp_path)), connect)], Path("/w"))
    answered = json.loads(json.dumps(catalogs.session({}, "s1")))  # what GET /api/sessions/s1 sends
    shown = [item for item in answered["items"] if item["type"] == "briefing"]
    assert [b["reason"] for b in shown] == ["start", "compacted"]
    first = shown[0]["parts"][0]
    assert (first["label"], first["point"]["mechanism"], first["authority"]) == ("System prompt", "harness_builtin", "harness")
    assert answered["texts"][first["text_ref"]] == "You are the harness's agent.\nBe careful."
    assert set(answered["texts"]) == {part["text_ref"] for b in shown for part in b["parts"]}


def test_a_transcript_the_model_never_answered_does_not_blame_the_harness_version(tmp_path: Path) -> None:
    conversation = read(tmp_path, [line(type="user", message={"content": "hi"})])
    [start] = briefings(conversation)
    assert (start.recorded, start.note, start.parts) == (False, "The model never answered here, so no prompt was recorded.", ())


def test_a_listing_record_after_the_first_in_a_rebuild_is_shown_too_and_an_exact_re_send_is_not(tmp_path: Path) -> None:
    failed = attachment("deferred_tools_delta", "MCP servers that failed to connect: srv", addedNames=[], addedLines=[],
                        failedMcpServers=[{"name": "srv"}])
    rebuilt = compaction()
    rebuilt[1:1] = [attachment("deferred_tools_delta", "Deferred: mcp__x__y", addedNames=["mcp__x__y"],
                               addedLines=["mcp__x__y"]),
                    attachment("deferred_tools_delta", "Deferred: mcp__x__y", addedNames=["mcp__x__y"],
                               addedLines=["mcp__x__y"]),  # the same record again: said once
                    failed]
    conversation = read(tmp_path, [*first_turn(), *rebuilt])
    start, after = briefings(conversation)
    assert text(conversation, after, "Deferred tool listing") == (
        "Deferred: mcp__x__y\nMCP servers that failed to connect: srv")
    assert next(p for p in after.parts if p.label == "Deferred tool listing").count == 1
    assert text(conversation, start, "Deferred tool listing") == "Deferred: mcp__x__y"


def test_skills_carried_over_are_grouped_by_whose_each_skill_is(tmp_path: Path) -> None:
    from agentlint.harness import read_config
    from tests.adapters.test_claude_code_adapter import build

    adapter, cwd = build(tmp_path)
    path = next((tmp_path / "home" / ".claude" / "projects").glob("*/s-1.jsonl"))
    stamp = {"sessionId": "s-1", "cwd": str(cwd), "version": "9.9.9", "timestamp": "2026-10-01T10:00:00Z",
             "isSidechain": False}

    def carried(*skills: tuple[str, str]) -> list[str]:
        return [json.dumps(stamp | record) for record in (
            {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto"}},
            {"type": "user", "isCompactSummary": True, "message": {"content": "Summary."}},
            {"type": "attachment", "attachment": {"type": "invoked_skills", "skills": [
                {"name": name, "path": "p", "content": body} for name, body in skills]}},
            {"type": "assistant", "message": {"model": "model-x", "content": []}})]

    with path.open("a") as out:
        out.write("\n".join([*carried(("pk:helper", "the helper's body " * 40), ("builtin-skill", "built in " * 10)),
                             *carried(("pk:helper", "the helper's body"))]) + "\n")
    _, both, only = briefings(adapter.read_conversation("s-1"))
    mixed = next(p for p in both.parts if p.label == "Skills carried over")
    assert (mixed.authority, mixed.source_id, mixed.detail, mixed.count) == (None, None, "pk:helper, builtin-skill", 2)
    assert [(s.label, s.authority, s.source_id) for s in mixed.shares] == [
        ("pk", Authority.PACKAGE, "claude-code:listing:skills:pk@mk"),
        ("the listing's own part", Authority.RUNTIME, "claude-code:listing:skills")]
    assert [s.tokens.value for s in mixed.shares] == [
        estimate_tokens("the helper's body " * 40), estimate_tokens("built in " * 10)]
    package = next(p for p in only.parts if p.label == "Skills carried over")
    assert (package.authority, package.shares, package.source_id) == (
        Authority.PACKAGE, (), "claude-code:listing:skills:pk@mk")  # one group: no split, and its package's source
    sources = {source.id for source in read_config(adapter, cwd, sessions=10).snapshot.sources}
    assert {s.source_id for s in mixed.shares} <= sources


def test_a_version_that_never_records_its_prompt_blames_the_version_even_when_the_model_never_answered(
        tmp_path: Path) -> None:
    conversation = read(tmp_path, [line(type="user", message={"content": "hi"}, version="2.1.267")])
    [start] = briefings(conversation)
    assert (start.recorded, start.note) == (
        False, "Not recorded by this harness version (2.1.267). The model never answered here either.")
    newer = read(tmp_path, [line(type="user", message={"content": "hi"}, version="2.1.270")], "s2.jsonl")
    assert briefings(newer)[0].note == "The model never answered here, so no prompt was recorded."  # not known either way


def test_the_configuration_reading_counts_only_the_first_listing_record_a_briefing_joins_the_later_ones(
        tmp_path: Path) -> None:
    records = first_turn()
    records[3:3] = [attachment("deferred_tools_delta", "Deferred: mcp__b__z", addedNames=["mcp__b__z"],
                               addedLines=["mcp__b__z"])]  # a second, different record of the same kind
    conversation = read(tmp_path, records)
    assert text(conversation, conversation.items[0], "Deferred tool listing") == "Deferred: mcp__x__y\nDeferred: mcp__b__z"
    assert next(p for p in conversation.items[0].parts if p.label == "Deferred tool listing").count == 2
    [session] = ClaudeCodeAdapter(Paths(tmp_path)).read(Path("/w"), 10).sessions
    listed = [d for d in session.deliveries if d.label.startswith("deferred tool listing")]
    assert [d.text for d in listed] == ["Deferred: mcp__x__y"]  # the configuration reading's counts don't move


def test_session_start_output_shows_its_rendered_text_cut_one_share_per_output(tmp_path: Path) -> None:
    framed = "<system-reminder>\nSessionStart:startup hook success: Use the mirror.\n</system-reminder>"
    context = "<system-reminder>\nSessionStart hook additional context: First.\nSecond.\n</system-reminder>"
    records = first_turn()
    records[0] = attachment("hook_success", framed, hookEvent="SessionStart", hookName="SessionStart:startup",
                            command="./start.sh", stdout="Use the mirror.", content="Use the mirror.")
    records[1:1] = [attachment("hook_additional_context", context, hookEvent="SessionStart",
                               hookName="SessionStart", content=["First.", "Second."])]
    conversation = read(tmp_path, records)
    shown = [conversation.texts[p.text_ref] for p in conversation.items[0].parts if p.label == "SessionStart hook output"]
    assert shown == [framed, "<system-reminder>\nSessionStart hook additional context: First.",
                     "\nSecond.\n</system-reminder>"]  # what the model was shown, the record's shares joining back whole
    assert "".join(shown[1:]) == context
    [session] = ClaudeCodeAdapter(Paths(tmp_path)).read(Path("/w"), 10).sessions
    assert sorted(d.text for d in session.deliveries if "SessionStart" in d.label or "hook" in d.label) == [
        "First.", "Second.", "Use the mirror."]  # the configuration reading counts the hooks' own output, as before


def test_rendered_text_that_does_not_hold_each_output_in_order_is_not_cut() -> None:
    from agentlint.adapters.claude_code.transcripts import rendered_shares

    assert rendered_shares(["a", "b"], "[b then a]") == [None, None]
    assert rendered_shares(["a"], None) == [None]
    assert rendered_shares(["a", "a"], "<a|a>") == ["<a", "|a>"]


def test_reading_a_transcript_seldom_waits_for_discovery(tmp_path: Path, monkeypatch) -> None:
    """A project's configuration is discovered once and kept: past SCAN_SECONDS the kept one still serves while a
    fresh one is discovered in the background; ``prepare`` starts one before any read; one per project at a time."""
    import threading

    from agentlint.adapters.claude_code import adapter as module

    clock = [1000.0]
    release = threading.Event()
    discovered: list[str] = []

    def discovering(paths, cwd):
        discovered.append(str(cwd))
        assert release.wait(10)
        return f"scan {len(discovered)}"

    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module, "scan", discovering)
    adapter = ClaudeCodeAdapter(Paths(tmp_path))
    lines = [{"cwd": str(tmp_path)}]
    adapter.prepare(tmp_path)  # returns at once: the discovery runs in the background
    assert adapter._reader_scans == {}  # not done yet: nothing kept
    joined: list[object] = []
    reader = threading.Thread(target=lambda: joined.append(adapter._scan_for(lines)))
    reader.start()  # a read while it runs waits for it, never starting a second
    release.set()
    reader.join(10)
    assert joined == ["scan 1"] and discovered == [str(tmp_path)]
    release.clear()
    clock[0] += module.SCAN_SECONDS + 1
    assert adapter._scan_for(lines) == "scan 1"  # stale: served at once while a fresh one is discovered
    assert adapter._scan_for(lines) == "scan 1" and len(discovered) == 2  # still the one refresh under way
    release.set()
    for _ in range(100):
        if adapter._scan_for(lines) == "scan 2":
            break
        threading.Event().wait(0.05)
    assert adapter._scan_for(lines) == "scan 2"
    clock[0] += module.SCAN_KEPT_SECONDS + 1
    assert adapter._scan_for(lines) == "scan 3"  # too old to serve: the read waits for a fresh one


def test_a_configuration_reading_hands_its_discovery_to_the_reader_for_that_project_only(tmp_path: Path,
                                                                                         monkeypatch) -> None:
    """The UI reads a project's report before you open any of its sessions: the reader traces briefings to the
    discovery that reading just ran, never waiting for its own, and never to another project's."""
    from agentlint.adapters.claude_code import adapter as module

    here, there = tmp_path / "here", tmp_path / "there"
    here.mkdir()
    there.mkdir()
    adapter = ClaudeCodeAdapter(Paths(tmp_path / "home"))
    adapter.read(here, 0)
    [(kept_at, read_scan)] = adapter._reader_scans.values()
    discovered: list[Path] = []
    real_scan = module.scan
    monkeypatch.setattr(module, "scan", lambda paths, cwd: discovered.append(cwd) or real_scan(paths, cwd))
    assert adapter._scan_for([{"cwd": str(here)}]) is read_scan and discovered == []  # no discovery: the reading's
    other = adapter._scan_for([{"cwd": str(there)}])
    assert discovered == [there] and other is not read_scan  # another project is discovered for itself
    adapter._keep(here, kept_at - 1, None)  # a discovery that began before the reading's, done after it
    assert adapter._scan_for([{"cwd": str(here)}]) is read_scan  # doesn't replace the later one
