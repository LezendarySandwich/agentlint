from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from agentlint.adapters.claude_code.transcripts import main_transcripts, parse_session
from agentlint.config.conversation import Prompt, Reply

TS = "2026-10-01T09:00:00.000Z"


def line(kind: str, **fields: object) -> dict:
    return {"type": kind, "sessionId": "abc-123", "cwd": "/work/project", "version": "2.1.285",
            "timestamp": TS, "isSidechain": False, **fields}


def attachment(payload: dict, rendered: str | None = None, **fields: object) -> dict:
    record = line("attachment", attachment=payload, **fields)
    if rendered is not None:
        record["rendered"] = [{"content": rendered}]
    return record


def user(text: str) -> dict:
    return line("user", message={"role": "user", "content": text})


def assistant(total: tuple[int, int, int], model: str = "model-x") -> dict:
    usage = {"input_tokens": total[0], "cache_creation_input_tokens": total[1], "cache_read_input_tokens": total[2]}
    return line("assistant", message={"role": "assistant", "model": model, "usage": usage, "content": []})


def hook_success(event: str, content: str, command: str = "run.sh", stdout: str | None = None) -> dict:
    return attachment({"type": "hook_success", "hookEvent": event, "hookName": f"{event}:startup",
                       "command": command, "stdout": stdout if stdout is not None else content, "content": content})


def write(path: Path, records: list, torn_tail: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r) for r in records) + "\n"
    if torn_tail:
        text += '{"type": "user", "mess'
    path.write_text(text)
    return path


LISTINGS = [
    attachment({"type": "skill_listing", "names": ["a", "b"], "skillCount": 2, "content": "- a: does a\n- b"},
               rendered="<system-reminder>skills: - a: does a\n- b</system-reminder>"),
    attachment({"type": "agent_listing_delta", "addedTypes": ["x"], "addedLines": ["- x: agent x"]}),
    attachment({"type": "deferred_tools_delta", "addedNames": ["mcp__s__t"], "addedLines": ["mcp__s__t"],
                "failedMcpServers": [{"name": "plugin:p:broken", "errorCode": "E"}]}),
]


def test_clean_session(tmp_path: Path) -> None:
    records = [
        line("mode", mode="normal"),
        hook_success("SessionStart", "plain stdout reaches the model", command="${CLAUDE_PLUGIN_ROOT}/a.sh"),
        hook_success("SessionStart", "", stdout='{"hookSpecificOutput": {"additionalContext": "json ctx"}}'),
        hook_success("PostToolUse", "formatter output never reaches the model"),
        attachment({"type": "hook_system_message", "hookEvent": "SessionStart", "content": "not sent"}),
        attachment({"type": "hook_additional_context", "hookEvent": "SessionStart", "hookName": "SessionStart",
                    "content": ["json ctx", "second item"]}),
        {**attachment({"type": "hook_additional_context", "hookEvent": "SessionStart", "content": ["subagent"]}),
         "isSidechain": True},
        user("hello there"),
        attachment({"type": "date", "date": "2026-10-01"}, rendered="Today's date is 2026-10-01."),
        attachment({"type": "session_context", "context": {"gitStatus": "clean"}}),
        *LISTINGS,
        attachment({"type": "instructions", "files": [{"path": "<managed-settings>", "type": "Managed", "content": "policy"},
                                                     {"path": "/work/project/CLAUDE.md", "type": "Project", "content": "proj"}]}),
        attachment({"type": "async_hook_response", "hookEvent": "SessionStart", "hookName": "SessionStart",
                    "response": json.dumps({"systemMessage": "async note"})}),
        assistant((2, 30000, 10000)),
        attachment({"type": "prompt_snapshot", "systemPrompt": ["You are", "helpful"],
                    "tools": [{"name": "Bash", "description": "run", "schema": {}}]}),
    ]
    s = parse_session(write(tmp_path / "proj" / "abc-123.jsonl", records))
    assert s.header.session_id == "abc-123" and s.header.project == Path("/work/project") and s.header.harness_version == "2.1.285"
    assert s.header.model == "model-x" and s.first_turn_input == 40002 and not s.interrupted_start
    assert s.header.interactive and s.dated  # the fixture lines carry no entry point: a person at the keyboard
    assert s.header.started_at.tzinfo is not None
    assert s.system_prompt == "You are\nhelpful" and s.tool_names == ("Bash",)
    assert s.skill_listing == "<system-reminder>skills: - a: does a\n- b</system-reminder>"  # rendered wins
    assert s.skill_names == ("a", "b") and s.agent_types == ("x",) and s.deferred_tool_names == ("mcp__s__t",)
    assert s.failed_mcp_servers == ("plugin:p:broken",)
    assert [f.path for f in s.instruction_files] == ["<managed-settings>", "/work/project/CLAUDE.md"]
    assert s.session_context == ("Today's date is 2026-10-01.", "clean")
    delivered = [(h.event, h.channel, h.text) for h in s.hook_outputs]
    assert delivered == [
        ("SessionStart", "stdout", "plain stdout reaches the model"),
        ("SessionStart", "additional_context", "json ctx"),
        ("SessionStart", "additional_context", "second item"),
        ("SessionStart", "async", "async note"),
    ]
    assert s.hook_outputs[0].command == "${CLAUDE_PLUGIN_ROOT}/a.sh"
    assert len(s.hook_runs) == 3  # every recorded execution, delivered or not
    assert s.first_turn_user_text == "hello there"


def test_interrupted_start_keeps_session_start_and_the_final_submission(tmp_path: Path) -> None:
    records = [
        hook_success("SessionStart", "start text"),
        user("first try"),
        *LISTINGS,
        attachment({"type": "hook_additional_context", "hookEvent": "UserPromptSubmit", "content": ["dropped"]}),
        line("user", isMeta=True, message={"role": "user", "content": "<local-command-caveat>…</local-command-caveat>"}),
        user("<command-name>/model</command-name>"),
        user("second try"),
        LISTINGS[1],
        LISTINGS[2],
        attachment({"type": "hook_additional_context", "hookEvent": "UserPromptSubmit", "content": ["nudge"]}),
        line("user", isMeta=True, message={"role": "user", "content": "skill expansion"}),
        assistant((2, 20000, 10000)),
    ]
    s = parse_session(write(tmp_path / "proj" / "abc-123.jsonl", records))
    assert s.interrupted_start
    assert s.skill_listing is None  # recorded with the abandoned submission, never re-sent
    assert s.agent_types == ("x",)
    assert [h.text for h in s.hook_outputs] == ["start text", "nudge"]  # session start survives
    assert [r.content for r in s.hook_runs] == ["start text"]
    assert s.first_turn_user_text == "second try\nskill expansion"


def test_local_commands_alone_do_not_make_a_start_interrupted(tmp_path: Path) -> None:
    records = [
        user("<command-name>/effort</command-name>"),
        line("user", isMeta=True, message={"role": "user", "content": "<local-command-caveat>…</local-command-caveat>"}),
        user("the only real prompt"),
        *LISTINGS,
        assistant((2, 20000, 10000)),
    ]
    s = parse_session(write(tmp_path / "proj" / "abc-123.jsonl", records))
    assert not s.interrupted_start and s.skill_names == ("a", "b")


def test_session_without_a_response_and_torn_tail(tmp_path: Path) -> None:
    s = parse_session(write(tmp_path / "p" / "s.jsonl", [user("hi"), *LISTINGS], torn_tail=True))
    assert s.first_turn_input is None and s.skill_names == ("a", "b")


def test_main_transcripts_are_depth_one_and_most_recent_first(tmp_path: Path) -> None:
    older = write(tmp_path / "-proj-a" / "old.jsonl", [user("x")])
    newer = write(tmp_path / "-proj-b" / "new.jsonl", [user("y")])
    write(tmp_path / "-proj-b" / "new" / "subagents" / "agent-1.jsonl", [user("z")])
    now = time.time()
    os.utime(older, (now - 100, now - 100))
    os.utime(newer, (now, now))
    assert main_transcripts(tmp_path, 10) == [newer, older]
    assert main_transcripts(tmp_path, 1) == [newer]
    assert main_transcripts(tmp_path, 0) == [] and main_transcripts(tmp_path / "missing", 5) == []


ENTRYPOINTS = [None, "cli", "sdk-cli", "sdk-ts", "sdk-py", "claude-code-github-action", "claude-vscode"]


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
def test_one_rule_says_whether_a_session_is_interactive(tmp_path: Path, entrypoint: str | None) -> None:
    """A reading's sessions (assemble), the session list and usage turns carry one header, built in one place, for
    every entrypoint."""
    from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
    from agentlint.adapters.claude_code.discovery import Paths
    from agentlint.adapters.claude_code.transcripts import headless

    extra = {"entrypoint": entrypoint} if entrypoint else {}
    path = write(tmp_path / ".claude" / "projects" / "-work-project" / "abc-123.jsonl",
                 [{**user("hello"), **extra}, {**assistant((10, 0, 0)), **extra}])
    adapter = ClaudeCodeAdapter(Paths(tmp_path))
    [session] = adapter.read(Path("/work/project"), 1).sessions
    [listing] = adapter.list_conversations()
    [turn] = adapter.turns_from(str(path), path.read_bytes())
    expected = not headless(entrypoint)
    assert session.header == listing.header == turn.header and turn.header.interactive == expected
    assert expected == (entrypoint in (None, "cli", "claude-vscode"))


# One record of an unexpected shape: each is skipped by every reader, and the records around it are kept.
ODD_RECORDS = [
    user("x") | {"message": {"role": "user", "content": [{"type": "text", "text": None}]}},
    user("x") | {"timestamp": 123},
    user("x") | {"message": "hi"},
    user("x") | {"cwd": ["/a"], "version": {"v": 1}, "entrypoint": 7, "sessionId": 5},
    line("assistant", message={"model": ["m"], "usage": {"input_tokens": "abc"},
                               "content": [{"type": "text", "text": None}, {"type": "thinking", "thinking": 5},
                                           {"type": "tool_use", "name": 5, "id": ["t"], "input": {}}]}),
    line("assistant", message={"content": "words"}),
    line("assistant", message={"content": [{"type": "text", "text": "more"}, {"type": "text", "text": None}]}),  # joins the reply
    line("user", message={"content": [{"type": "tool_result", "tool_use_id": ["t"], "content": [{"type": "text", "text": 5}]}]}),
    line("attachment", attachment="str"),
    attachment({"type": "hook_additional_context", "hookName": "x", "content": [1, 2]}),
    attachment({"type": ["hook_success"], "hookEvent": ["SessionStart"]}),
    attachment({"type": "hook_success", "hookEvent": ["SessionStart"], "content": "x"}),
    attachment({"type": "skill_listing", "names": 5}, rendered=None) | {"rendered": 5},
    attachment({"type": "agent_listing_delta", "addedLines": [1]}),
    attachment({"type": "queued_command", "commandMode": "prompt", "prompt": [{"type": "text", "text": None}]}),
    line("system", subtype="local_command", commandRun="clear"),
    line("system", subtype="local_command", content=5),
    line("system", subtype=["local_command"]),
    line(["user"]),
    line("ai-title", aiTitle=5),
]


@pytest.mark.parametrize("odd", ODD_RECORDS, ids=range(len(ODD_RECORDS)))
def test_one_record_of_an_unexpected_shape_is_skipped_and_the_rest_kept(tmp_path: Path, odd: dict) -> None:
    from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
    from agentlint.adapters.claude_code.discovery import Paths
    from agentlint.adapters.claude_code.transcripts import hook_records

    reply = line("assistant", message={"role": "assistant", "model": "model-x", "content": [{"type": "text", "text": "ok"}]})
    later = user("second") | {"timestamp": "2026-10-01T10:00:00.000Z"}
    path = tmp_path / ".claude" / "projects" / "-work-project" / "abc-123.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(r) for r in [user("first"), reply, odd, later, reply]) + "\n")
    adapter, data = ClaudeCodeAdapter(Paths(tmp_path)), path.read_bytes()
    assert [t.at.hour for t in adapter.turns_from(str(path), data)] == [9, 10]
    items = adapter.conversation_from(str(path), data).items
    assert [i.text for i in items if isinstance(i, (Prompt, Reply))] == ["first", "ok", "second", "ok"]
    assert adapter.listing_from(str(path), data).prompts == 2
    assert parse_session(path).first_turn_user_text == "first"
    hook_records(path)
