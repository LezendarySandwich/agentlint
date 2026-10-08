"""A Claude Code run's trace, from the transcript the harness wrote for it (core model: trace/model.py).

The transcript, not the JSON result, carries the tool calls (VERIFIED 2026-10-01). It is found by session id among
those the harness keeps under the run's home (``conversation.find``), never by a path computed from the working
directory: the recorded directory can differ from the one given (``/tmp`` is recorded as ``/private/tmp``).

Every record is read by the helpers the rest of the adapter uses — ``records``, ``tool_uses``, ``tool_results``,
``uses``, ``hook_record``, ``HeaderReader``, ``subagent_ref_of`` — so a run's trace and its reading as usage, a
conversation or hook health never disagree about what a line says. Only main-thread lines are read: a subagent's
work is in its own file (``<session>/subagents/agent-<id>.jsonl``), and the call that started it carries where
(``subagent_ref``).

What becomes what:

- each ``tool_use`` block of an assistant line → an Action, with its whole input, the line's ``message.id`` and the
  block's id; its ``tool_result`` (matched by id) sets ``answered``, ``error`` (``is_error``) and ``denied``
- a hook execution (``hook_success``, ``hook_non_blocking_error``, ``hook_error_during_execution``,
  ``hook_cancelled``) → a FiredHook, with the call it ran for: its ``toolUseID`` when that names one of the run's
  calls. SessionStart and Stop records carry a ``toolUseID`` too, naming no call (VERIFIED 2026-10-04: 21 of the 60
  hook records of the headless run here), so theirs is dropped. Only these outcome records are read, so ``fired`` is
  not every hook that ran: a UserPromptSubmit hook leaves no ``hook_success`` (0 in the 60 most recent transcripts
  here, against 397 ``hook_additional_context`` records; only a cancelled one has an outcome record), and an async
  hook leaves only an ``async_hook_response`` (3,286 here: SubagentStart 1,925, PostToolUseFailure 738, Stop 524, ...)
  — both absent, as is a SessionStart hook whose only record is its ``hook_additional_context`` (19 such here). The
  output records aren't read as firings: one can sit beside its own hook's ``hook_success`` (same event, toolUseID and
  hook name: 26 times here, SessionStart and PreToolUse), so one execution would count twice; and whether a
  prompt-submit hook that printed nothing leaves any record is UNVERIFIED
- a synthetic assistant line (``isApiErrorMessage``) → an ``ApiError`` in ``api_errors``: its ``error`` code, its
  ``apiError`` reason, its ``apiErrorIsTransient`` mark and the text shown in place of the reply — a model call that
  failed, not the agent acting. The 2.1.285 binary writes those four fields (read 2026-10-04 in its error classifier);
  the 46 such lines in local transcripts carry only ``error`` (``server_error``, ``invalid_request``), so whether the
  reason and mark reach a transcript is UNVERIFIED
- a transcript with no readable record, or none of the run's own lines naming a version or entry point (a zero-byte
  file, one whose only line is torn) → ``TraceEmpty``, never an empty trace
- a transcript that can't be opened, or whose reading fails partway (an ``OSError``) → ``TraceMissing``, its cause
  chained; never the lines read before the failure, which would pass for a run that stopped early
- the last ``cost-state`` record → the run's cost (VERIFIED 2026-10-04 as the last record of both headless transcripts
  here); none recorded → ``cost`` None. What a killed run leaves is UNVERIFIED.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from agentlint.adapters.claude_code.conversation import find, subagent_ref_of
from agentlint.adapters.claude_code.discovery import Paths
from agentlint.adapters.claude_code.transcripts import (
    UNREADABLE,
    HeaderReader,
    _lines,
    headless,
    hook_record,
    tool_results,
    tool_uses,
    uses,
)
from agentlint.trace.model import Action, ApiError, FiredHook, RunCost, Trace, TraceEmpty, TraceMissing

# How a tool result begins when the harness refused the call rather than ran it. Seen in transcripts here
# (VERIFIED 2026-10-04, read-only): a call the user rejected, and one auto mode couldn't judge. Others are added
# when seen; an unrecognised refusal stays an error, never a denial by guess.
DENIALS = (
    "The user doesn't want to proceed with this tool use",
    "The server-side auto mode classifier gave no verdict",
)
_ERROR_CODE = re.compile(r"[A-Za-z0-9_.-]{1,60}")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _number(value: object, kind: type) -> Any:
    return value if isinstance(value, kind) and not isinstance(value, bool) else None


def _cost(record: dict[str, Any]) -> RunCost:
    cost = record.get("totalCostUSD")
    return RunCost(float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
                   _number(record.get("totalDuration"), int), _number(record.get("totalAPIDuration"), int))


def _api_error(record: dict[str, Any], message: dict[str, Any]) -> ApiError:
    code, reason, transient = record.get("error"), record.get("apiError"), record.get("apiErrorIsTransient")
    return ApiError(code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else "",
                    reason if isinstance(reason, str) and _ERROR_CODE.fullmatch(reason) else None,
                    transient if isinstance(transient, bool) else None,
                    _text(message.get("content"))[:ApiError.TEXT_LIMIT])


def trace_from(session_id: str, lines: Iterable[dict[str, Any]]) -> Trace:
    """The trace of the run whose transcript records are ``lines`` (already read by ``records``). A record of a shape
    no harness version writes is skipped and the rest read. Raises ``TraceEmpty`` when no record was read, or none of
    the run's own says which version or entry point wrote it: a transcript that recorded nothing is no trace, not an
    empty one."""
    header = HeaderReader()
    actions: list[dict[str, Any]] = []
    by_call: dict[str, dict[str, Any]] = {}
    fired: list[FiredHook] = []
    models: dict[str, None] = {}
    api_errors: list[ApiError] = []
    cost: RunCost | None = None
    read = 0
    for record in lines:
        read += 1
        header.feed(record)
        if record.get("isSidechain"):
            continue
        try:
            kind = record.get("type")
            if kind == "cost-state":
                cost = _cost(record)
            elif kind == "assistant":
                message = record.get("message") or {}
                if record.get("isApiErrorMessage"):
                    api_errors.append(_api_error(record, message))
                model = message.get("model")
                if model and model != "<synthetic>":
                    models[model] = None
                ident = message.get("id")
                for call in tool_uses(record):
                    action = {"tool": call.name, "input": call.given, "message_id": ident if isinstance(ident, str) else None,
                              "call_id": call.id, "uses": uses(call.name, call.given), "answered": False,
                              "error": False, "denied": False, "subagent_ref": None}
                    actions.append(action)
                    if call.id:
                        by_call.setdefault(call.id, action)
            elif kind == "user":
                for block in tool_results(record):
                    action = by_call.get(block.get("tool_use_id"))  # type: ignore[arg-type]
                    if action is None or action["answered"]:
                        continue
                    error = bool(block.get("is_error"))
                    action.update(answered=True, error=error,
                                  denied=error and _text(block.get("content")).startswith(DENIALS),
                                  subagent_ref=subagent_ref_of(record.get("toolUseResult")))
            else:
                hook = hook_record(record, session_id)
                if hook is not None:
                    fired.append(FiredHook(hook.event, hook.hook_name, hook.command, hook.outcome, hook.tool_use_id))
        except UNREADABLE:
            continue
    if not read or (header.version is None and header.entrypoint is None):
        raise TraceEmpty(f"the transcript of session {session_id!r} holds "
                         + ("no readable record" if not read else "no line saying which version or entry point wrote it"))
    return Trace(
        session_id=session_id,
        harness_version=header.version,
        models=tuple(models),
        headless=headless(header.entrypoint),
        actions=tuple(Action(seq=n, **action) for n, action in enumerate(actions)),
        fired=tuple(replace(h, call_id=h.call_id if h.call_id in by_call else None) for h in fired),
        api_errors=tuple(api_errors),
        cost=cost,
    )


def locate_trace(home: Path, session_id: str) -> Path:
    """The transcript of the run with this id, among those the harness keeps under ``home`` (the run's HOME).
    Raises ``TraceMissing`` when there is none."""
    path = find(Paths(home).projects, session_id)
    if path is None:
        raise TraceMissing(f"no transcript for session {session_id!r} under {Paths(home).projects}")
    return path


def read_trace(home: Path, session_id: str) -> Trace:
    """The trace of the run with this id, its transcript found under ``home``. Raises ``TraceMissing`` when there is
    no transcript, or one there that can't be opened or fails partway through reading (the ``OSError`` chained: a
    partial reading would pass for a run that stopped early), and ``TraceEmpty`` (a ``TraceMissing``) when the one
    there recorded nothing readable."""
    path = locate_trace(home, session_id)
    try:
        return trace_from(session_id, _lines(path))
    except OSError as error:
        raise TraceMissing(f"the transcript of session {session_id!r} can't be read: {error.strerror or error}") from error
