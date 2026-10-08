"""Claude Code transcripts read back as conversations, for the live UI's transcript reader."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
from agentlint.adapters.claude_code.discovery import Paths
from agentlint.config.conversation import (
    OUTPUT_LIMIT,
    Briefing,
    BriefingReason,
    Clipped,
    Injected,
    Note,
    NoteKind,
    Prompt,
    Reply,
    Thinking,
    ToolCall,
)


def line(**record) -> str:
    return json.dumps({"sessionId": "s1", "cwd": "/w", "version": "2.0", "entrypoint": "cli",
                       "timestamp": "2026-10-01T09:00:00Z", **record})


def transcript(home: Path, session_id: str = "s1") -> Path:
    path = home / ".claude" / "projects" / "-w" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        line(type="ai-title", aiTitle="Fix the flaky test"),
        line(type="user", message={"content": "Why does it flake?"}),
        line(type="assistant", message={"model": "claude-x", "content": [
            {"type": "thinking", "thinking": "Probably a race."},
            {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -x", "description": "run"}},
            {"type": "tool_use", "id": "t2", "name": "Skill", "input": {"skill": "pkg:helper"}}]}),
        line(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x" * (OUTPUT_LIMIT + 50),
                                                "is_error": True}]}),
        line(type="user", isMeta=True, message={"content": "<command-name>pkg:helper</command-name>\nThe skill's body."}),
        line(type="attachment", attachment={"type": "hook_additional_context", "hookName": "SessionStart:startup",
                                            "content": ["Use search_files."]}),
        line(type="attachment", attachment={"type": "hook_non_blocking_error", "hookName": "Stop", "stderr": "no such file"}),
        line(type="attachment", attachment={"type": "hook_success", "content": "bookkeeping"}),  # not the conversation's
        line(type="user", isSidechain=True, message={"content": "a subagent's prompt"}),
        line(type="system", subtype="compact_boundary"),
        line(type="system", subtype="away_summary", content="Goal: fix the flake."),
        line(type="user", message={"content": "<command-name>/model</command-name><command-args>opus</command-args>"}),
        line(type="user", message={"content": "Thanks"}, timestamp="2026-10-01T09:30:00Z"),
    ]
    path.write_text("\n".join(records) + "\n")
    return path


def entries(conversation):
    """A conversation's entries but its briefings (tests/adapters/test_claude_code_briefings.py reads those)."""
    return tuple(item for item in conversation.items if not isinstance(item, Briefing))


def kind(item):
    """A note by its kind, anything else by its class."""
    return item.kind if isinstance(item, Note) else type(item)


def words(item) -> str:
    """What an entry says, whatever its kind."""
    if isinstance(item, ToolCall):
        return item.summary
    return item.text.text if isinstance(item, (Injected, Note)) else item.text


def test_a_transcript_reads_back_as_a_conversation(tmp_path: Path) -> None:
    transcript(tmp_path)
    adapter = ClaudeCodeAdapter(Paths(tmp_path))
    [listing] = adapter.list_conversations()
    header = listing.header
    assert (listing.title, listing.prompts, header.model, header.interactive) == ("Fix the flaky test", 2, "claude-x", True)
    assert (header.session_id, header.project, header.harness_version) == ("s1", Path("/w"), "2.0")
    assert (listing.ended_at - header.started_at).seconds == 1800
    whole = adapter.read_conversation("s1")
    assert [item.reason for item in whole.items if isinstance(item, Briefing)] == [BriefingReason.START,
                                                                                   BriefingReason.COMPACTED]
    assert isinstance(whole.items[0], Briefing) and isinstance(whole.items[10], Briefing)  # the top; after compacting
    conversation = replace(whole, items=entries(whole))
    assert [kind(item) for item in conversation.items] == [
        Prompt, Thinking, Reply, ToolCall, ToolCall, Injected, Injected, NoteKind.HOOK_ERROR, NoteKind.COMPACTED,
        NoteKind.RECAP, NoteKind.COMMAND, Prompt]
    bash, skill = conversation.items[3], conversation.items[4]
    assert bash.name == "Bash" and bash.summary == "pytest -x" and bash.error
    assert bash.result.omitted == 50 and len(bash.result.text) == OUTPUT_LIMIT  # the cut travels with its text
    assert skill.result is None and "skills/pkg:helper" in skill.entities  # no result recorded; the skill linked
    assert conversation.items[6].by == "hook context · SessionStart:startup"
    assert "no such file" in conversation.items[7].text.text
    assert conversation.items[10].text == Clipped("/model opus")
    assert all("subagent" not in words(item) and "bookkeeping" not in words(item) for item in conversation.items)


def test_sessions_are_found_by_id_only_among_those_kept(tmp_path: Path) -> None:
    transcript(tmp_path)
    (tmp_path / "elsewhere.jsonl").write_text(line(type="user", message={"content": "private"}))
    adapter = ClaudeCodeAdapter(Paths(tmp_path))
    for wanted in ("../../elsewhere", "../elsewhere", "*", "s?", "", "/etc/passwd", "nope"):
        assert adapter.read_conversation(wanted) is None
    assert adapter.read_conversation("s1") is not None


def test_agent_and_workflow_calls_link_to_the_transcripts_they_started(tmp_path: Path) -> None:
    from agentlint.adapters.claude_code.conversation import belongs

    session = tmp_path / ".claude" / "projects" / "-w" / "s1"
    records = [
        line(type="user", message={"content": "go"}),
        line(type="assistant", message={"content": [
            {"type": "tool_use", "id": "a", "name": "Agent", "input": {"subagent_type": "code-search", "prompt": "find it"}},
            {"type": "tool_use", "id": "w", "name": "Workflow", "input": {"script": "..."}}]}),
        line(type="user", toolUseResult={"agentId": "a1b2", "status": "completed"},
             message={"content": [{"type": "tool_result", "tool_use_id": "a", "content": "found"}]}),
        line(type="user", toolUseResult={"runId": "wf_9x", "status": "async_launched"},
             message={"content": [{"type": "tool_result", "tool_use_id": "w", "content": "launched"}]}),
    ]
    path = session.parent / "s1.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(records) + "\n")
    agent = session / "subagents" / "agent-a1b2.jsonl"
    worker = session / "subagents" / "workflows" / "wf_9x" / "agent-c3.jsonl"
    for file, said in ((agent, "find it"), (worker, "check a lens")):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(line(type="user", isSidechain=True, message={"content": said}) + "\n"
                        + line(type="assistant", isSidechain=True, message={"content": [{"type": "text", "text": "done"}]}) + "\n")
    adapter = ClaudeCodeAdapter(Paths(tmp_path))
    items = adapter.read_conversation("s1").items
    assert [item.subagent_ref for item in items if isinstance(item, ToolCall)] == ["agent:a1b2", "run:wf_9x"]
    def unread() -> bytes:
        raise AssertionError("a run called once is settled by the path alone")

    assert belongs("agent:a1b2", str(agent), unread) and belongs("run:wf_9x", str(worker), unread)
    assert not belongs("agent:a1b2", str(worker), unread) and not belongs("run:wf_9x", str(agent), unread)
    assert not belongs("agent:../x", str(agent), unread)
    kept = adapter.conversation_from(str(agent), agent.read_bytes())  # a subagent's lines are all sidechain: kept
    assert [type(item) for item in kept.items] == [Briefing, Prompt, Reply]
    files = {f.handle: f for f in adapter.transcripts()}
    assert files[str(worker)].subagent and files[str(worker)].session_id == "s1"


def test_only_a_subagent_reference_the_adapter_writes_is_one() -> None:
    """The check on a reference a request hands back (core never reads its grammar): a resumed run's window too,
    and nothing looser."""
    adapter = ClaudeCodeAdapter(Paths(Path("/nonexistent")))
    for ref in ("run:wf_9x", "run:wf_9x@1759398058180-", "run:wf_9x@1-2", "agent:a1"):
        assert adapter.is_subagent_ref(ref)
    for ref in ("run:wf_9x@x", "run:wf_9x@1", "run:../x", "run:wf@1-2-3", "agent:", "other:a1", ""):
        assert not adapter.is_subagent_ref(ref)
    assert adapter.subagent_id("/p/-w/s1/subagents/workflows/wf_9x/agent-c3.jsonl") == "agent-c3"


def test_a_workflow_runs_journal_is_not_a_subagent(tmp_path: Path) -> None:
    """Beside a run's agents the harness writes journal.jsonl, a launch log: not one of the agents it started."""
    from agentlint.adapters.claude_code.conversation import belongs

    run = tmp_path / ".claude" / "projects" / "-w" / "s1" / "subagents" / "workflows" / "wf_9x"
    run.mkdir(parents=True)
    worker, journal = run / "agent-c3.jsonl", run / "journal.jsonl"
    worker.write_text(line(type="user", isSidechain=True, message={"content": "check a lens"}) + "\n")
    journal.write_text(json.dumps({"type": "started", "agentId": "c3", "label": "review"}) + "\n")
    handles = {f.handle for f in ClaudeCodeAdapter(Paths(tmp_path)).transcripts()}
    assert str(worker) in handles and str(journal) not in handles  # never archived
    assert belongs("run:wf_9x", str(worker), worker.read_bytes) and not belongs("run:wf_9x", str(journal), journal.read_bytes)  # nor listed, if archived before


def write_session(home: Path, records: list[str], session_id: str = "s1") -> Path:
    path = home / ".claude" / "projects" / "-w" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(records) + "\n")
    return path


def test_a_prompt_typed_while_the_agent_worked_is_a_prompt(tmp_path: Path) -> None:
    # Delivered mid-turn, the harness writes it only as a queued_command attachment: no user line.
    write_session(tmp_path, [
        line(type="user", message={"content": "fix the test"}),
        line(type="assistant", message={"content": [{"type": "text", "text": "Looking."}]}),
        line(type="attachment", attachment={"type": "queued_command", "prompt": "also update the changelog",
                                            "commandMode": "prompt", "origin": {"kind": "human"}, "humanTurn": True}),
        line(type="attachment", attachment={"type": "queued_command", "prompt": "<task-notification>x</task-notification>",
                                            "commandMode": "task-notification",
                                            "origin": {"kind": "task-notification"}}),
        line(type="assistant", message={"content": [{"type": "text", "text": "Will do."}]}),
    ])
    adapter = ClaudeCodeAdapter(Paths(tmp_path))
    items = entries(adapter.read_conversation("s1"))
    assert [(kind(item), words(item)) for item in items] == [
        (Prompt, "fix the test"), (Reply, "Looking."), (Prompt, "also update the changelog"),
        (NoteKind.NOTIFICATION, "a background task finished"), (Reply, "Will do.")]
    [listing] = adapter.list_conversations()
    assert listing.prompts == 2


def test_a_subagents_interrupt_marker_is_not_said_to_be_yours(tmp_path: Path) -> None:
    marker = line(type="user", isSidechain=True, message={"content": "[Request interrupted by user]"})
    tool = line(type="user", isSidechain=True, message={"content": "[Request interrupted by user for tool use]"})
    handle = str(tmp_path / "s1" / "subagents" / "workflows" / "wf_1" / "agent-a1.jsonl")
    adapter = ClaudeCodeAdapter(Paths(tmp_path))
    notes = [words(item) for item in entries(adapter.conversation_from(handle, (marker + "\n" + tool + "\n").encode()))]
    assert notes and not any("You" in note for note in notes)
    main = adapter.conversation_from(str(tmp_path / "s1.jsonl"), (marker.replace("true", "false") + "\n").encode())
    assert [(item.kind, item.text) for item in entries(main)] == [(NoteKind.INTERRUPTED, Clipped("You interrupted the agent"))]


def test_session_start_hook_stdout_that_reached_the_model_is_shown_once_in_the_briefing(tmp_path: Path) -> None:
    write_session(tmp_path, [
        line(type="attachment", attachment={"type": "hook_success", "hookName": "SessionStart:clear",
                                            "hookEvent": "SessionStart", "content": "Use the internal mirror.",
                                            "stdout": "Use the internal mirror."}),
        line(type="attachment", attachment={"type": "hook_success", "hookName": "PostToolUse:Write",
                                            "hookEvent": "PostToolUse", "content": "not delivered"}),
        line(type="attachment", attachment={"type": "hook_success", "hookName": "UserPromptSubmit",
                                            "hookEvent": "UserPromptSubmit", "content": ""}),
        line(type="user", message={"content": "hi"}),
        line(type="attachment", attachment={"type": "async_hook_response", "hookName": "Stop", "hookEvent": "Stop",
                                            "response": {"systemMessage": "Tests still fail."}, "stdout": "x"}),
        line(type="attachment", attachment={"type": "async_hook_response", "hookName": "Stop", "response": {}}),
    ])
    conversation = ClaudeCodeAdapter(Paths(tmp_path)).read_conversation("s1")
    briefing, *items = conversation.items
    assert items == [Prompt(items[0].at, "hi"), Injected(items[1].at, "hook output · Stop", Clipped("Tests still fail."))]
    [hook] = [part for part in briefing.parts if part.label == "SessionStart hook output"]
    assert conversation.texts[hook.text_ref] == "Use the internal mirror." and hook.detail == "SessionStart:clear"


def test_one_clear_is_one_note(tmp_path: Path) -> None:
    write_session(tmp_path, [
        line(type="user", message={"content": "<command-name>/clear</command-name>\n<command-message>clear</command-message>\n"
                                              "<command-args></command-args>"}),
        line(type="system", subtype="local_command", content="<local-command-stdout></local-command-stdout>",
             commandRun={"command": "clear", "args": ""}),
        line(type="system", subtype="local_command", commandRun={"command": "resume", "args": ""}),  # nothing typed
    ])
    items = entries(ClaudeCodeAdapter(Paths(tmp_path)).read_conversation("s1"))
    assert [(item.kind, item.text.text, item.entities) for item in items] == [
        (NoteKind.COMMAND, "/clear", ("skills/clear",)), (NoteKind.COMMAND, "/resume", ("skills/resume",))]


def test_a_command_older_versions_wrote_only_as_a_system_line_is_one_note(tmp_path: Path) -> None:
    """2.1.266-2.1.280 sometimes wrote a typed command only as a system ``local_command`` line holding the markup,
    with no user line; its output line follows, with or without ``commandRun`` (VERIFIED 2026-10-04, read-only)."""
    markup = "<command-name>/{0}</command-name>\n            <command-message>{0}</command-message>\n            <command-args>{1}</command-args>"
    write_session(tmp_path, [
        line(type="user", message={"content": "hi"}),
        line(type="system", subtype="local_command", content=markup.format("effort", "high"), timestamp="2026-10-01T10:00:00Z"),
        line(type="system", subtype="local_command", content="<local-command-stdout>Set effort level</local-command-stdout>",
             timestamp="2026-10-01T10:00:00Z"),
        line(type="system", subtype="local_command", content=markup.format("resume", ""), timestamp="2026-10-01T11:00:00Z"),
        line(type="system", subtype="local_command", content="<local-command-stdout>Resume cancelled</local-command-stdout>",
             commandRun={"command": "resume", "args": ""}, timestamp="2026-10-01T11:00:00Z"),
        line(type="system", subtype="local_command", content="<command-name>my password is hunter2</command-name>"),
    ])
    items = entries(ClaudeCodeAdapter(Paths(tmp_path)).read_conversation("s1"))
    assert [(kind(item), words(item), getattr(item, "entities", ()), item.at.hour) for item in items] == [
        (Prompt, "hi", (), 9), (NoteKind.COMMAND, "/effort high", ("skills/effort",), 10),
        (NoteKind.COMMAND, "/resume", ("skills/resume",), 11)]


def test_workflow_runs_can_be_told_apart_and_a_resume_lists_only_its_own_agents(tmp_path: Path) -> None:
    session = tmp_path / ".claude" / "projects" / "-w" / "s1"
    result = {"status": "async_launched", "workflowName": "design-review", "runId": "wf_9x",
              "summary": "Review the design"}
    write_session(tmp_path, [
        line(type="user", message={"content": "go"}),
        line(type="assistant", timestamp="2026-10-01T09:00:00Z", message={"content": [
            {"type": "tool_use", "id": "w1", "name": "Workflow",
             "input": {"script": "export const meta = {\n  name: 'design-review',\n};"}}]}),
        line(type="user", toolUseResult=result,
             message={"content": [{"type": "tool_result", "tool_use_id": "w1", "content": "launched"}]}),
        line(type="assistant", timestamp="2026-10-01T11:00:00Z", message={"content": [
            {"type": "tool_use", "id": "w2", "name": "Workflow",
             "input": {"scriptPath": "/x/design-review-wf_9x.js", "resumeFromRunId": "wf_9x"}}]}),
        line(type="user", toolUseResult=result,
             message={"content": [{"type": "tool_result", "tool_use_id": "w2", "content": "launched"}]}),
        line(type="assistant", timestamp="2026-10-01T12:00:00Z", message={"content": [
            {"type": "tool_use", "id": "w3", "name": "Workflow", "input": {"script": "export const meta = {name: \"other\"}"}}]}),
    ])
    run = session / "subagents" / "workflows" / "wf_9x"
    run.mkdir(parents=True)
    for agent, at, described in (("a1", "09:00:02", "review:lens"), ("a2", "09:30:00", None), ("a3", "11:00:01", "merge")):
        (run / f"agent-{agent}.jsonl").write_text(
            line(type="user", isSidechain=True, timestamp=f"2026-10-01T{at}Z",
                 message={"content": "[Workflow harness — user request] the same preamble\nthe task"}) + "\n")
        if described:
            (run / f"agent-{agent}.meta.json").write_text(json.dumps({"agentType": "workflow-subagent", "description": described}))
    adapter = ClaudeCodeAdapter(Paths(tmp_path))
    calls = [item for item in adapter.read_conversation("s1").items if isinstance(item, ToolCall)]
    assert [call.summary for call in calls] == ["design-review · Review the design",
                                                 "resumes wf_9x · design-review · Review the design", "other"]
    first, resumed = calls[0].subagent_ref, calls[1].subagent_ref
    assert first != resumed and first.startswith("run:wf_9x") and resumed.startswith("run:wf_9x")
    assert adapter.is_subagent_ref(first) and adapter.is_subagent_ref(resumed)
    files = {adapter.subagent_id(f.handle): f.handle for f in adapter.transcripts()}

    def listed(ref: str) -> list[str]:
        return sorted(ident for ident, handle in files.items()
                      if adapter.belongs(ref, handle, lambda handle=handle: adapter.read_transcript(handle)))
    assert listed(first) == ["agent-a1", "agent-a2"] and listed(resumed) == ["agent-a3"]
    assert listed("run:wf_9x") == ["agent-a1", "agent-a2", "agent-a3"]  # a run called once: all of it
    titles = {ident: adapter.conversation_from(handle, adapter.read_transcript(handle)).title
              for ident, handle in files.items() if ident.startswith("agent-")}
    assert titles == {"agent-a1": "review:lens", "agent-a2": None, "agent-a3": "merge"}  # the harness's own name for it


def test_a_transcript_with_nothing_dated_is_not_listed(tmp_path: Path) -> None:
    """Nothing says when it ran, and it holds no prompt or reply: not listed, rather than given a start it didn't
    say."""
    path = write_session(tmp_path, [line(type="summary", summary="x", timestamp=None)])
    adapter = ClaudeCodeAdapter(Paths(tmp_path))
    assert adapter.list_conversations() == [] and adapter.listing_from(str(path), path.read_bytes()) is None


def test_a_hook_error_that_cuts_stderr_says_how_much_it_cut(tmp_path: Path) -> None:
    adapter = ClaudeCodeAdapter(Paths(tmp_path))
    record = line(type="attachment", attachment={"type": "hook_non_blocking_error", "hookName": "PostToolUse:Bash",
                                                 "stderr": "e" * 1990 + "the end"})
    [note] = entries(adapter.conversation_from(str(tmp_path / "s1.jsonl"), (record + "\n").encode()))
    assert isinstance(note, Note) and note.kind is NoteKind.HOOK_ERROR
    assert note.text == Clipped("PostToolUse:Bash failed: " + "e" * 500, 1497)  # the end of a traceback is the part cut
    short = line(type="attachment", attachment={"type": "hook_non_blocking_error", "hookName": "Stop", "stderr": "oops"})
    [whole] = entries(adapter.conversation_from(str(tmp_path / "s1.jsonl"), (short + "\n").encode()))
    assert whole.text == Clipped("Stop failed: oops")
