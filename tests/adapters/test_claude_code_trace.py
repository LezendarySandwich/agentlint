from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
from agentlint.adapters.claude_code.discovery import Paths
from agentlint.adapters.claude_code.trace import locate_trace, read_trace, trace_from
from agentlint.adapters.claude_code.transcripts import records
from agentlint.trace.model import ApiError, Trace, TraceEmpty, TraceMissing

SID = "0b1c2d3e-0000-4000-8000-000000000001"
TS = "2026-10-04T09:00:00.000Z"


def line(kind: str, **fields: object) -> dict:
    return {"type": kind, "sessionId": SID, "cwd": "/private/tmp/w", "version": "2.1.285", "timestamp": TS,
            "isSidechain": False, "entrypoint": "sdk-cli", **fields}


def call(message_id: str, name: str, ident: str, given: dict, model: str = "model-a") -> dict:
    return line("assistant", message={"id": message_id, "role": "assistant", "model": model,
                                      "content": [{"type": "tool_use", "id": ident, "name": name, "input": given}]})


def result(ident: str, text: str = "fine", error: bool = False, **fields: object) -> dict:
    return line("user", message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": ident, "content": text, "is_error": error}]}, **fields)


def hook(event: str, ident: str | None, command: str = "${CLAUDE_PLUGIN_ROOT}/h.sh", kind: str = "hook_success") -> dict:
    payload = {"type": kind, "hookEvent": event, "hookName": f"{event}:Bash", "command": command,
               "stdout": "", "content": "", "durationMs": 5}
    if ident:
        payload["toolUseID"] = ident
    return line("attachment", attachment=payload)


RUN = [
    {k: v for k, v in line("queue-operation").items() if k != "entrypoint"},  # early lines name no entry point
    hook("SessionStart", "a0f1e2d3"),  # the harness gives session hooks an id that names no call
    line("user", message={"role": "user", "content": "do the thing"}),
    call("msg_1", "Skill", "tu_1", {"skill": "data:sql-query"}),
    result("tu_1"),
    call("msg_2", "Bash", "tu_2", {"command": "cd /private/tmp/w && ls", "description": "list"}),
    call("msg_2", "Read", "tu_3", {"file_path": "/private/tmp/w/a.py"}),  # the same message: made together
    hook("PreToolUse", "tu_2"),
    hook("PostToolUse", "tu_2", kind="hook_non_blocking_error"),
    result("tu_2"),
    result("tu_3", "boom", error=True),
    {**call("msg_s", "Write", "tu_s", {"file_path": "x"}), "isSidechain": True},  # a subagent's line: not this run's
    line("assistant", isApiErrorMessage=True, error="server_error",
         message={"id": "msg_e", "role": "assistant", "model": "<synthetic>", "content": [{"type": "text", "text": "API Error"}]}),
    call("msg_3", "Agent", "tu_4", {"subagent_type": "Explore", "prompt": "look"}, model="model-b"),
    result("tu_4", "done", toolUseResult={"agentId": "a1b2c3", "status": "completed"}),
    call("msg_4", "Bash", "tu_5", {"command": "git push"}),
    result("tu_5", "The user doesn't want to proceed with this tool use. The tool use was rejected.", error=True),
    call("msg_5", "Bash", "tu_6", {"command": "sleep 999"}),  # never answered: the run ended first
    {"type": "cost-state", "sessionId": SID, "totalCostUSD": 0.25, "totalDuration": 38077, "totalAPIDuration": 19660},
]


def write(home: Path, records_: list, slug: str = "-private-tmp-w", torn: bool = False) -> Path:
    path = home / ".claude" / "projects" / slug / f"{SID}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r) for r in records_) + "\n"
    path.write_text(text + ('{"type": "assistant", "mess' if torn else ""))
    return path


def test_actions_in_order_with_raw_input_and_ids(tmp_path: Path) -> None:
    write(tmp_path, RUN, torn=True)
    trace = read_trace(tmp_path, SID)
    assert [a.tool for a in trace.actions] == ["Skill", "Bash", "Read", "Agent", "Bash", "Bash"]
    assert [a.seq for a in trace.actions] == list(range(6))
    bash = trace.actions[1]
    assert bash.input == {"command": "cd /private/tmp/w && ls", "description": "list"}  # raw: nothing normalised
    assert (bash.message_id, bash.call_id) == ("msg_2", "tu_2")
    assert trace.actions[2].message_id == "msg_2"
    assert trace.actions[0].uses == ("tools/Skill", "skills/data:sql-query")
    assert trace.actions[3].uses == ("tools/Agent", "agents/Explore")
    assert trace.invoked() >= {"skills/data:sql-query", "agents/Explore", "tools/Bash"}


def test_results_set_answered_error_denied_and_subagent(tmp_path: Path) -> None:
    write(tmp_path, RUN)
    actions = read_trace(tmp_path, SID).actions
    assert [(a.answered, a.error, a.denied) for a in actions] == [
        (True, False, False), (True, False, False), (True, True, False),
        (True, False, False), (True, True, True), (False, False, False)]
    assert actions[3].subagent_ref == "agent:a1b2c3"
    assert actions[0].subagent_ref is None


def test_run_facts_hooks_errors_and_cost(tmp_path: Path) -> None:
    write(tmp_path, RUN)
    trace = read_trace(tmp_path, SID)
    assert trace.harness_version == "2.1.285"
    assert trace.models == ("model-a", "model-b") and trace.model == "model-a"
    assert trace.headless
    assert [(h.event, h.outcome, h.call_id) for h in trace.fired] == [
        ("SessionStart", "ok", None), ("PreToolUse", "ok", "tu_2"), ("PostToolUse", "error", "tu_2")]
    assert trace.fired[0].command == "${CLAUDE_PLUGIN_ROOT}/h.sh"  # as configured, unexpanded
    assert trace.api_errors == (ApiError("server_error", text="API Error"),)
    assert trace.cost is not None
    assert (trace.cost.cost_usd, trace.cost.duration_ms, trace.cost.api_duration_ms) == (0.25, 38077, 19660)


def test_an_api_error_keeps_its_reason_transience_and_text_and_survives_storage() -> None:
    gateway = line("assistant", isApiErrorMessage=True, error="unknown", apiError="gateway_session_expired",
                   apiErrorIsTransient=False, message={"id": "m", "role": "assistant", "model": "<synthetic>",
                                                       "content": [{"type": "text", "text": "x" * 900}]})
    odd = line("assistant", isApiErrorMessage=True, error="cloud_credential_error", apiError="no spaces allowed",
               apiErrorIsTransient="yes", message={"id": "n", "role": "assistant", "content": "Could not load"})
    trace = trace_from(SID, [RUN[0], gateway, odd])
    assert trace.api_errors == (ApiError("unknown", "gateway_session_expired", False, "x" * ApiError.TEXT_LIMIT),
                                ApiError("cloud_credential_error", None, None, "Could not load"))
    assert Trace.from_dict(trace.to_dict()) == trace
    old = {**trace.to_dict(), "api_errors": ["server_error"]}  # stored before reasons were kept
    assert Trace.from_dict(old).api_errors == (ApiError("server_error"),)


def test_no_cost_record_means_no_cost(tmp_path: Path) -> None:
    write(tmp_path, RUN[:-1])
    assert read_trace(tmp_path, SID).cost is None


def test_an_interactive_session_is_not_headless() -> None:
    lines = [{**r, "entrypoint": "cli"} if "entrypoint" in r else r for r in RUN]
    assert not trace_from(SID, lines).headless


def test_an_unreadable_record_is_skipped_and_the_rest_read() -> None:
    odd = line("assistant", message={"id": "m", "model": "model-a", "content": [
        {"type": "tool_use", "id": "tu_x", "name": "Bash", "input": {"command": "ls"}}]})
    odd["toolUseResult"] = None
    weird = result("tu_x")
    weird["message"]["content"][0]["tool_use_id"] = ["not", "hashable"]  # a shape no version writes
    trace = trace_from(SID, records(json.dumps(r).encode() for r in [odd, weird, *RUN]))
    assert [a.tool for a in trace.actions][:2] == ["Bash", "Skill"]
    assert not trace.actions[0].answered


def test_a_tool_call_named_by_a_non_string_is_skipped_by_every_reader_alike() -> None:
    from agentlint.adapters.claude_code.conversation import conversation_from
    from agentlint.adapters.claude_code.transcripts import turns_from
    from agentlint.config.conversation import ToolCall

    odd = line("assistant", message={"id": "m", "model": "model-a", "content": [
        {"type": "tool_use", "id": "tu_x", "name": 7, "input": {}},
        {"type": "tool_use", "id": "tu_y", "name": "Bash", "input": {"command": "ls"}}]})
    prompt = line("user", message={"role": "user", "content": "go"})
    good = call("msg_g", "Read", "tu_g", {"file_path": "a"})
    lines = [prompt, odd, good]
    assert [a.tool for a in trace_from(SID, lines).actions] == ["Read"]
    assert [i.name for i in conversation_from(lines).items if isinstance(i, ToolCall)] == ["Read"]
    assert [sorted(t.entities) for t in turns_from(SID, lines)] == [["tools/Read"]]


def test_a_missing_transcript_raises_never_an_empty_trace(tmp_path: Path) -> None:
    with pytest.raises(TraceMissing):
        read_trace(tmp_path, SID)
    write(tmp_path, RUN)
    with pytest.raises(TraceMissing):
        locate_trace(tmp_path, "0b1c2d3e-0000-4000-8000-000000000002")
    with pytest.raises(TraceMissing):
        locate_trace(tmp_path, "../" + SID)  # an id is never a path


def test_a_transcript_that_recorded_nothing_raises_never_an_empty_trace(tmp_path: Path) -> None:
    """A zero-byte file, or one whose only line is torn (a run killed as it began), would otherwise read as a run
    that did nothing: an outcome class of its own, and a false 'disagreement' beside two runs that acted."""
    path = write(tmp_path, [])
    path.write_bytes(b"")
    with pytest.raises(TraceEmpty):
        read_trace(tmp_path, SID)
    path.write_text('{"type": "assistant", "mess')
    with pytest.raises(TraceEmpty):
        read_trace(tmp_path, SID)
    with pytest.raises(TraceMissing):  # a TraceMissing: whoever handles a missing trace handles this one
        read_trace(tmp_path, SID)


def test_a_transcript_that_cant_be_read_raises_trace_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A runner that marks a run with no trace on TraceMissing must not crash on an OSError instead."""
    path = write(tmp_path, RUN)
    path.chmod(0)
    try:
        with pytest.raises(TraceMissing) as raised:
            read_trace(tmp_path, SID)
        assert not isinstance(raised.value, TraceEmpty)  # it may well hold a run: it's unread, not empty
        assert isinstance(raised.value.__cause__, PermissionError)
    finally:
        path.chmod(0o600)

    def failing_partway(_: Path):
        yield from records(json.dumps(r).encode() for r in RUN[:5])
        raise OSError(5, "Input/output error")

    monkeypatch.setattr("agentlint.adapters.claude_code.trace._lines", failing_partway)
    with pytest.raises(TraceMissing) as raised:  # never the calls read before the failure, as if the run stopped there
        read_trace(tmp_path, SID)
    assert isinstance(raised.value.__cause__, OSError)


def test_records_that_never_say_which_harness_wrote_them_are_no_trace() -> None:
    cost_only = RUN[-1]
    with pytest.raises(TraceEmpty):
        trace_from(SID, [cost_only])
    with pytest.raises(TraceEmpty):  # a subagent's lines say nothing about this run
        trace_from(SID, [{**call("msg_s", "Write", "tu_s", {"file_path": "x"}), "isSidechain": True}])
    with pytest.raises(TraceEmpty):
        trace_from(SID, [])
    no_version = [{k: v for k, v in r.items() if k != "version"} for r in RUN]
    assert trace_from(SID, no_version).harness_version is None  # an entry point is enough to say a run was recorded
    started = trace_from(SID, [line("user", message={"role": "user", "content": "go"})])
    assert started.actions == ()  # a run that began and made no call did nothing: that is an outcome


def test_found_by_id_whatever_the_slug(tmp_path: Path) -> None:
    path = write(tmp_path, RUN, slug="-somewhere-else")
    assert locate_trace(tmp_path, SID) == path


def test_the_adapter_reads_a_trace_under_its_home(tmp_path: Path) -> None:
    write(tmp_path, RUN)
    adapter = ClaudeCodeAdapter(Paths(home=tmp_path))
    assert adapter.trace(SID) == read_trace(tmp_path, SID)
    with pytest.raises(TraceMissing):
        ClaudeCodeAdapter(Paths(home=tmp_path / "other")).trace(SID)


def test_a_trace_round_trips_through_json(tmp_path: Path) -> None:
    write(tmp_path, RUN)
    trace = read_trace(tmp_path, SID)
    assert Trace.from_dict(json.loads(json.dumps(trace.to_dict()))) == trace
