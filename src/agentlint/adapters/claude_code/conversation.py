"""Claude Code transcripts as conversations you can read back (core model: config/conversation.py).

Main-session transcripts only — ``~/.claude/projects/<slug>/<session>.jsonl``, looked up by session id among
those, never by a path from outside. What becomes what:

- a genuine prompt → Prompt, and so is one typed while the agent worked (a ``queued_command`` attachment,
  ``commandMode: "prompt"``: it has no user line); a slash command you typed → a COMMAND Note (`/name args`); what
  the harness writes in your role — an interrupt, a `!` shell command and its output, a task notification — → an
  INTERRUPTED, SHELL or NOTIFICATION Note (in a subagent's transcript an interrupt names no one: the calling agent or
  its run stops it there); the summary carried over at compaction → Injected
- assistant ``text`` → Reply (consecutive blocks joined), ``thinking`` → Thinking, ``tool_use`` → ToolCall, given
  its ``tool_result`` (matched by id) as result, and ``is_error`` as error; an ``Agent`` call whose result names its
  ``agentId``, or a ``Workflow`` call whose result names its ``runId``, refers to the subagent transcripts it started
  (its ``subagent_ref``: ``agent:<id>``, ``run:<id>``). A resumed run keeps its run id and directory, so where one
  run was called more than once each call's reference also carries when it was called until the next
  (``run:<id>@<ms>-<ms>``), and lists only the agents that started in between. A Workflow call's line is the
  workflow's name and what it does, from its result
- ``isMeta`` user text (a skill's body, an expanded command), ``hook_additional_context``, and hook output the model
  got on plain stdout (``hook_success`` content for SessionStart / UserPromptSubmit / UserPromptExpansion, as
  transcripts.py counts it delivered) or from an async hook → Injected
- ``hook_non_blocking_error`` → a HOOK_ERROR Note; a local command (``/clear``) → a COMMAND Note, once: its output
  line right after the typed command is not a second — the typed command a user line, or (older versions) a
  ``local_command`` system line holding the markup; a background task's notification → a NOTIFICATION Note
- ``away_summary`` → a RECAP Note; ``compact_boundary`` → a COMPACTED Note; the last ``ai-title`` names the session;
  a subagent's name is the ``description`` in the ``.meta.json`` beside its transcript

- what the harness gave the model → a Briefing (``briefings``): one at the top, read as the configuration reading reads
  a first turn (``transcripts._Builder``: everything before the first reply, the first ``prompt_snapshot``'s system
  prompt and the first one's tool definitions); one right after every ``compact_boundary``'s COMPACTED Note, read the
  same way from the boundary on, with what the compaction re-attached (``file``, ``compact_file_reference``,
  ``invoked_skills``); and one wherever a later ``prompt_snapshot`` records a system prompt or tool definitions unlike
  the last, outside those — an identical re-send says nothing. Each part's text is its ``rendered`` text where
  recorded — a SessionStart hook's output its share of its record's (``rendered_shares``) — but an instruction file's,
  which is its own ``content`` (``rendered`` frames all the files as one), held once per conversation by its hash. SessionStart hook output inside a briefing's first turn is shown
  there and nowhere else. A version that wrote no snapshot (2.1.266, 2.1.267) says so; nothing is reconstructed.

A record of a shape no harness version writes is skipped, and the rest read (``records``, ``UNREADABLE``).

Everything else — other hooks' bookkeeping, per-turn reminders, cost state, file-history snapshots — is the
harness's, not the conversation's, and is left out. (Until 2026-10-04 the listings, instruction files and session
context were left out too: the reader showed no briefing.) In a session's transcript, subagent lines (``isSidechain``) are skipped: subagents
have their own files, `<session>/subagents/agent-<id>.jsonl` (a workflow's under `workflows/<run>/`), read the same
way with their lines kept.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from agentlint.adapters.claude_code.hooks import DELIVERING_STDOUT_EVENTS
from agentlint.adapters.claude_code.transcripts import (
    UNREADABLE,
    HeaderReader,
    _Builder,
    _json_object,
    _is_prompt,
    _lines,
    _message_text,
    _text_of,
    _timestamp,
    command_run,
    queued_prompt,
    records,
    skill_use,
    snapshot_texts,
    tool_results,
    tool_uses,
    typed_command,
    uses,
)
from agentlint.config.content import content_hash
from agentlint.config.conversation import (
    Briefing,
    BriefingReason,
    Clipped,
    Conversation,
    Injected,
    Item,
    Note,
    NoteKind,
    Prompt,
    Reply,
    SessionListing,
    Thinking,
    ToolCall,
    clip,
)
from agentlint.config.tokens import TokenCount, estimated

HOOK_ERROR_LIMIT = 500  # characters of a failed hook's stderr kept in its note; the rest is counted, not kept
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_REF_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


def subagent_ref_of(result: Any) -> str | None:
    """Where an Agent or Workflow call's own record of its result says its subagents went."""
    if not isinstance(result, dict):
        return None
    if isinstance(result.get("agentId"), str) and _REF_ID.fullmatch(result["agentId"]):
        return f"agent:{result['agentId']}"
    if isinstance(result.get("runId"), str) and _REF_ID.fullmatch(result["runId"]):
        return f"run:{result['runId']}"
    return None


def subagent_title(path: Path) -> str | None:
    """The harness's own name for a subagent: the ``description`` in the ``agent-<id>.meta.json`` it writes beside the
    transcript (a workflow agent's is its phase and lens, such as ``review:invariants``) — VERIFIED 2026-10-03 on
    2.1.285. Every workflow agent's first prompt is the same harness preamble, so the prompt names none of them.
    None where the file is gone (an archived copy whose live files were deleted) or says nothing usable."""
    try:
        meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    said = meta.get("description") if isinstance(meta, dict) else None
    line = said.strip().splitlines()[0][:160] if isinstance(said, str) and said.strip() else None
    return line or None


_WINDOW = re.compile(r"([0-9]{1,15})-([0-9]{0,15})")
_REF = re.compile(r"(agent|run):[A-Za-z0-9_-]{1,80}(@[0-9]{1,15}-[0-9]{0,15})?")


def is_subagent_ref(ref: str) -> bool:
    """Whether ``ref`` has the shape ``subagent_ref_of`` and ``_resumed_runs`` write: ``agent:<id>``, ``run:<id>``, or
    ``run:<id>@<ms>-<ms>``."""
    return bool(_REF.fullmatch(ref))


def belongs(ref: str, handle: str, read: Callable[[], bytes]) -> bool:
    """Whether a subagent transcript is one a subagent reference points to: by its path (the handle), and — for a run
    called more than once (resumed: the same run id, the same directory) — by when it started, read only then."""
    if not is_subagent_ref(ref):
        return False
    kind, _, ident = ref.partition(":")
    ident, _, window = ident.partition("@")
    path = Path(handle)
    if "subagents" not in path.parts:
        return False
    if kind == "agent":
        return path.name == f"agent-{ident}.jsonl"
    if kind != "run" or path.parent.name != ident or path.parent.parent.name != "workflows":
        return False
    if not path.name.startswith("agent-"):
        return False  # not the run's journal.jsonl, archived before it was skipped
    if not window:
        return True
    bounds = _WINDOW.fullmatch(window)
    started = next((at for at in (_timestamp(r.get("timestamp")) for r in records(read().splitlines()[:20])) if at), None)
    if not bounds or started is None:
        return False  # not known to be this call's: listed under neither rather than under the wrong one
    since, until = int(bounds.group(1)), int(bounds.group(2)) if bounds.group(2) else None
    return since <= _ms(started) and (until is None or _ms(started) < until)


def _ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


def _resumed_runs(items: list[Item]) -> None:
    """A resumed Workflow call gets back the run id it resumed, and its agents go in the same directory. Where a
    run was called more than once, each call's reference keeps only the agents started from it until the next call —
    VERIFIED 2026-10-03: in run wf_f50e405a-fe5 here, 24 agents started after the first call and 6 after the
    resume, none in between."""
    calls: dict[str, list[int]] = {}
    for index, item in enumerate(items):
        if isinstance(item, ToolCall) and item.subagent_ref and item.subagent_ref.startswith("run:"):
            calls.setdefault(item.subagent_ref, []).append(index)
    for ref, indexes in calls.items():
        if len(indexes) < 2 or any(items[i].at is None for i in indexes):
            continue
        indexes.sort(key=lambda i: items[i].at)
        for n, index in enumerate(indexes):
            until = str(_ms(items[indexes[n + 1]].at)) if n + 1 < len(indexes) else ""
            items[index] = replace(items[index], subagent_ref=f"{ref}@{_ms(items[index].at)}-{until}")
_COMMAND_ARGS = re.compile(r"<command-args>([^<]*)</command-args>")
_SUMMARY_KEYS = {  # which input says, in one line, what a tool call did
    "Bash": ("command",), "Read": ("file_path",), "Write": ("file_path",), "Edit": ("file_path",),
    "MultiEdit": ("file_path",), "NotebookEdit": ("notebook_path",), "Grep": ("pattern", "path"), "Glob": ("pattern",),
    "WebFetch": ("url",), "WebSearch": ("query",), "Skill": ("skill",), "Agent": ("subagent_type", "description"),
    "Task": ("subagent_type", "description"), "ToolSearch": ("query",), "Workflow": ("resumes", "name", "description"),
}
_SCRIPT_NAME = re.compile(r"""\bname\s*:\s*['"]([^'"\n]{1,120})['"]""")


def _workflow(given: dict[str, Any], result: Any = None) -> dict[str, Any]:
    """What a Workflow call ran, for its one line. Its input is a ``script`` or a ``scriptPath`` (with
    ``resumeFromRunId`` to resume a run), never a name; the result names the workflow and says what it does."""
    result = result if isinstance(result, dict) else {}
    named = _SCRIPT_NAME.search(str(given.get("script") or "")[:2000])
    name = result.get("workflowName") or (named.group(1) if named else Path(str(given.get("scriptPath") or "")).stem)
    resumes = given.get("resumeFromRunId")
    return {"resumes": f"resumes {resumes}" if isinstance(resumes, str) and resumes else None, "name": name,
            "description": result.get("summary")}


def _summary(name: str, given: dict[str, Any], result: Any = None) -> str:
    if name == "Workflow":
        given = _workflow(given, result)
    keys = _SUMMARY_KEYS.get(name) or [k for k, v in given.items() if isinstance(v, str)][:1]
    parts = [str(given[k]) for k in keys if isinstance(given.get(k), (str, int, float)) and str(given[k]).strip()]
    line = " · ".join(parts).replace("\n", " ⏎ ")
    return line if len(line) <= 200 else line[:199] + "…"


_SHELL = re.compile(r"<bash-(input|stdout|stderr)>(.*?)</bash-\1>", re.S)


def _harness_line(text: str, subagent: bool = False) -> tuple[NoteKind, str] | None:
    """A user-role line the harness wrote, as (its kind, words); None if it is not one. In a subagent's transcript the
    harness writes the same interrupt marker when the calling agent stops it (TaskStop) or its run times out —
    VERIFIED 2026-10-03: 635 of this machine's 1,478 workflow-agent transcripts end with it, timed to the main
    agent's TaskStop calls or a fixed cadence, with no interrupt of yours in the session — so there it names no one."""
    stripped = text.lstrip()
    if stripped.startswith("[Request interrupted by user"):
        tool = "tool use" in stripped
        if subagent:
            return NoteKind.INTERRUPTED, "A tool call was stopped" if tool else "Stopped before it finished"
        return NoteKind.INTERRUPTED, "You interrupted" + (" a tool call" if tool else " the agent")
    if stripped.startswith("<task-notification>"):
        return NoteKind.NOTIFICATION, "a background task finished"
    shell = dict((kind, body.strip()) for kind, body in _SHELL.findall(stripped))
    if "input" in shell:
        return NoteKind.SHELL, f"! {shell['input']}"
    if "stdout" in shell or "stderr" in shell:
        return NoteKind.SHELL, (shell.get("stdout") or "") + ("\n" + shell["stderr"] if shell.get("stderr") else "")
    return None


def _command_line(command: str, text: str) -> str:
    """A typed command as its note shows it: ``/name args``."""
    args = " ".join(a.strip() for a in _COMMAND_ARGS.findall(text) if a.strip())
    return f"/{command}" + (f" {args}" if args else "")


def _result_text(content: Any) -> str:
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(_text_of(block))
            elif isinstance(block, dict) and block.get("type") == "image":
                parts.append("[image]")
        return "\n".join(parts)
    return content if isinstance(content, str) else ""


def listing(path: Path) -> SessionListing | None:
    return listing_from(path.stem, list(_lines(path)), path.stat().st_size)


def listing_from(session_id: str, lines: list[dict[str, Any]], size: int) -> SessionListing | None:
    """What a session was, without its contents: for the list. None when no line of it is dated — then it holds no
    prompt and no reply, and nothing says when it ran."""
    fields = HeaderReader()
    ended = None
    prompts = 0
    for record in lines:
        if record.get("isSidechain"):
            continue
        try:
            prompted = (record.get("type") == "user" and _is_prompt(record)) or queued_prompt(record) is not None
        except UNREADABLE:
            continue  # a record of a shape no harness version writes: skipped, the rest read
        fields.feed(record)
        ended = _timestamp(record.get("timestamp")) or ended
        prompts += prompted
    header = fields.header(session_id)
    return SessionListing(header, _title(lines), ended or header.started_at, prompts, size) if header else None


def _title(lines: list[dict[str, Any]]) -> str | None:
    """The harness's name for a session: its last ``ai-title``."""
    titles = [r["aiTitle"] for r in lines if r.get("type") == "ai-title" and isinstance(r.get("aiTitle"), str)]
    return titles[-1] if titles else None


def read_conversation(path: Path, scan: Any = None) -> Conversation:
    return conversation_from(list(_lines(path)), scan=scan)


def conversation_from(lines: list[dict[str, Any]], *, subagent: bool = False, scan: Any = None,
                      parts: bool = True) -> Conversation:
    """A transcript's records as a conversation; ``scan`` (the adapter's ``Scan``, as configured now) traces each
    briefing part to its source — without it, parts say only what the transcript does. ``parts=False``: the same
    items, each briefing without its parts (and no texts), for a listing that only counts them."""
    shown, moved, texts = briefings(lines, subagent=subagent, scan=scan, parts=parts)
    items: list[Item] = [shown[-1]] if -1 in shown else []
    tools: dict[str, tuple[int, dict[str, Any]]] = {}  # tool_use id -> its item's index and input, for the result

    def injected(at, by: str, text: str) -> None:
        items.append(Injected(at, by, clip(text)))

    def command(at, line: str, name: str) -> None:
        items.append(Note(at, NoteKind.COMMAND, Clipped(line), (skill_use(name),)))

    for index, record in enumerate(lines):
        if (record.get("isSidechain") and not subagent) or index in moved:
            continue  # a subagent's line in its session's transcript; SessionStart output its briefing shows
        before, last = len(items), items[-1] if items else None  # a reply's next block is joined onto the last item
        try:
            kind, at = record.get("type"), _timestamp(record.get("timestamp"))
            message = record.get("message") or {}
            if kind == "user":
                content = message.get("content")
                answers = tool_results(record)
                for block in answers:
                    if block.get("tool_use_id") in tools:
                        index, given = tools.pop(block["tool_use_id"])
                        result = record.get("toolUseResult")
                        call = items[index]
                        items[index] = replace(call, result=clip(_result_text(block.get("content"))),
                                               error=bool(block.get("is_error")), subagent_ref=subagent_ref_of(result),
                                               summary=_summary(call.name, given, result))
                text = "" if answers else _message_text(content)
                if not text.strip():
                    continue
                typed = typed_command(record)
                harness = None if record.get("isMeta") else _harness_line(text, subagent)
                if typed:
                    command(at, _command_line(typed, text), typed)
                elif _is_prompt(record):
                    items.append(Prompt(at, text))
                elif harness:
                    items.append(Note(at, harness[0], clip(harness[1])))
                elif record.get("isCompactSummary"):
                    injected(at, "the summary carried over at compaction", text)
                elif record.get("isMeta"):
                    injected(at, "expanded by the harness", text)
            elif kind == "assistant":
                calls = iter(tool_uses(record))  # the tool_use blocks below, read in the one place they are read
                for block in message.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and _text_of(block).strip():
                        if items and isinstance(items[-1], Reply) and items[-1].at == at:
                            items[-1] = replace(items[-1], text=items[-1].text + "\n\n" + block["text"])
                        else:
                            items.append(Reply(at, block["text"]))
                    elif block.get("type") == "thinking" and _text_of(block, "thinking").strip():
                        items.append(Thinking(at, block["thinking"]))
                    elif block.get("type") == "tool_use" and block.get("name"):
                        call = next(calls)
                        if call.id:
                            tools[call.id] = (len(items), call.given)
                        items.append(ToolCall(at, call.name, _summary(call.name, call.given),
                                              json.dumps(call.given, indent=1, ensure_ascii=False),
                                              entities=uses(call.name, call.given)))
            elif kind == "attachment":
                attachment = record.get("attachment") or {}
                kind_of = attachment.get("type")
                hook = attachment.get("hookName") or "a hook"
                if kind_of == "hook_additional_context":
                    content = attachment.get("content")
                    text = "\n".join(content) if isinstance(content, list) else str(content or "")
                    if text.strip():
                        injected(at, f"hook context · {hook}", text)
                elif kind_of == "hook_non_blocking_error":
                    stderr = clip(str(attachment.get("stderr") or "").strip(), HOOK_ERROR_LIMIT)
                    items.append(Note(at, NoteKind.HOOK_ERROR, Clipped(f"{hook} failed: {stderr.text}", stderr.omitted)))
                elif kind_of == "queued_command" and attachment.get("commandMode") == "task-notification":
                    items.append(Note(at, NoteKind.NOTIFICATION, Clipped("a background task finished")))
                elif queued_prompt(record):
                    items.append(Prompt(at, queued_prompt(record)))
                elif kind_of == "hook_success" and attachment.get("hookEvent") in DELIVERING_STDOUT_EVENTS:
                    text = str(attachment.get("content") or "")  # plain stdout: the model got it (transcripts.py)
                    if text.strip():
                        injected(at, f"hook output · {hook}", text)
                elif kind_of == "prompt_snapshot" and index in shown:
                    items.append(shown[index])  # the harness's own prompt changed here
                elif kind_of == "async_hook_response":
                    response = _json_object(attachment.get("response"))
                    said = (response.get("systemMessage"), _json_object(response.get("hookSpecificOutput")).get("additionalContext"))
                    for text in said:
                        if isinstance(text, str) and text.strip():
                            injected(at, f"hook output · {hook}", text)
            elif kind == "system":
                subtype = record.get("subtype")
                if subtype == "away_summary" and record.get("content"):
                    items.append(Note(at, NoteKind.RECAP, Clipped(str(record["content"]))))
                elif subtype == "compact_boundary":
                    items.append(Note(at, NoteKind.COMPACTED, Clipped("The conversation was summarised here to free context.")))
                    if index in shown:
                        items.append(shown[index])  # what the context was rebuilt with
                elif subtype == "local_command" and typed_command(record):  # older versions: the typed command itself
                    typed = typed_command(record)
                    command(at, _command_line(typed, str(record["content"])), typed)
                elif subtype == "local_command" and command_run(record):
                    ran, args = command_run(record), record["commandRun"].get("args")
                    text = f"/{ran} {args if isinstance(args, str) else ''}".rstrip()
                    previous = items[-1] if items else None
                    if (isinstance(previous, Note) and previous.kind is NoteKind.COMMAND
                            and previous.text.text.split(" ")[0] == text.split(" ")[0]):
                        continue  # the output of the command you just typed, already shown: one /clear, one note
                    command(at, text, ran)
        except UNREADABLE:  # a record of a shape no harness version writes: skipped, the rest read
            del items[before:]
            if last is not None:
                items[-1] = last
    _resumed_runs(items)
    return Conversation(_title(lines), tuple(items), texts)


def find(projects: Path, session_id: str) -> Path | None:
    """The main transcript with this id — only among those the harness keeps, never a path from outside."""
    if not _SESSION_ID.fullmatch(session_id) or not projects.is_dir():
        return None
    return next((path for path in projects.glob(f"*/{session_id}.jsonl") if path.is_file()), None)


# --- what the harness gave the model: briefings --------------------------------------------------------------------

_STARTING_OUTPUT = ("hook_success", "hook_additional_context", "async_hook_response")


def _starting_output(record: dict[str, Any]) -> bool:
    """A SessionStart hook's output record: shown in the briefing it came with, never also on its own."""
    attachment = record.get("attachment")
    return (record.get("type") == "attachment" and isinstance(attachment, dict)
            and attachment.get("type") in _STARTING_OUTPUT and attachment.get("hookEvent") == "SessionStart")


@dataclass
class _Window:
    """The records one briefing is read from: from the start, or from a compaction's boundary, to the first reply
    after it — and on, for its snapshots only, until one has recorded the tool definitions."""

    reason: BriefingReason
    anchor: int  # the index of the record it follows: -1 for the start, else the compaction's boundary
    builder: _Builder
    at: datetime | None = None
    first_turn: list[int] = field(default_factory=list)  # the indexes of the records read as its first turn


LAST_UNRECORDING_VERSION = (2, 1, 267)  # the newest harness version seen never to write a prompt_snapshot; 2.1.280 is
# the oldest seen to write one, and those between are unknown — VERIFIED 2026-10-04 (docs/02-design.md, Verified Facts)


def _records_no_snapshots(version: object) -> bool:
    """Whether a harness version is one known never to record its prompt: no newer than the last seen not to."""
    try:
        parts = tuple(int(piece) for piece in str(version).split("-")[0].split("."))
    except ValueError:
        return False
    return bool(parts) and parts <= LAST_UNRECORDING_VERSION


def briefings(lines: list[dict[str, Any]], *, subagent: bool = False, scan: Any = None, parts: bool = True
              ) -> tuple[dict[int, Briefing], set[int], dict[str, str]]:
    """Where a transcript shows what the harness gave the model, by the index of the record each follows (-1: the
    top), the indexes of the SessionStart hook output records moved into one, and every part's text by its hash.

    A briefing at the start, read as the configuration reading reads a first turn (``_Builder``: what came before
    the first reply, and the first snapshot with tool definitions after it); one after every compaction
    (``compact_boundary``), read the same way from the boundary on; and one wherever a later ``prompt_snapshot``
    records a system prompt or tool definitions that differ from the last recorded, outside those — an identical
    re-send says nothing. ``scan`` is the configuration as discovered now, to trace each part to its source; with
    ``parts=False`` each briefing is placed, and noted, without its parts."""
    from agentlint.adapters.claude_code.adapter import briefing_parts, prompt_parts  # the adapter imports this module

    texts: dict[str, str] = {}
    counted: dict[str, TokenCount] = {}

    def keep(text: str) -> tuple[str, TokenCount]:
        ref = content_hash(text)
        if ref not in texts:
            texts[ref], counted[ref] = text, estimated(text)
        return ref, counted[ref]

    def window(reason: BriefingReason, anchor: int) -> _Window:
        return _Window(reason, anchor, _Builder(Path("transcript"), subagent))

    closed: list[_Window] = []
    changed: dict[int, tuple[datetime | None, str | None, str | None, int, str | None]] = {}
    snapshots, version, answered = 0, None, False
    last_prompt = last_tools = None  # the system prompt and tool definitions recorded last
    command = None  # the command typed since the last snapshot
    open_window: _Window | None = window(BriefingReason.START, -1)
    for index, record in enumerate(lines):
        if record.get("isSidechain") and not subagent:
            continue
        version = version or record.get("version")
        answered = answered or record.get("type") == "assistant"
        try:
            if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
                if open_window:
                    closed.append(open_window)
                open_window = window(BriefingReason.COMPACTED, index)
                open_window.at = _timestamp(record.get("timestamp"))
                continue
            attachment = record.get("attachment")
            if isinstance(attachment, dict) and attachment.get("type") == "prompt_snapshot":
                snapshots += 1
                prompt, tools = snapshot_texts(attachment)
                new_prompt = prompt if prompt is not None and last_prompt is not None and prompt != last_prompt else None
                new_tools = tools if tools is not None and last_tools is not None and tools != last_tools else None
                settled = open_window is None or open_window.builder.first_turn_done  # past what a window reads
                if (settled and new_prompt) or (open_window is None and new_tools):
                    count = sum(isinstance(tool, dict) for tool in attachment.get("tools") or ())
                    changed[index] = (_timestamp(record.get("timestamp")), new_prompt,
                                      new_tools if open_window is None else None, count, command)
                last_prompt, last_tools = prompt or last_prompt, tools or last_tools
                command = None
            elif typed_command(record) or command_run(record):
                command = typed_command(record) or command_run(record)
            if open_window:
                open_window.at = open_window.at or _timestamp(record.get("timestamp"))
                if not open_window.builder.first_turn_done:
                    open_window.first_turn.append(index)
                open_window.builder.feed(record)
                if open_window.builder.done():
                    closed.append(open_window)
                    open_window = None
        except UNREADABLE:
            continue  # a record of a shape no harness version writes: skipped, the rest read
    if open_window:
        closed.append(open_window)

    memo: dict = {}
    found: dict[int, Briefing] = {}
    moved: set[int] = set()
    for shown in closed:
        builder = shown.builder
        if not builder.first_turn_done:
            builder._close_first_turn()  # no reply yet: everything recorded so far
        raw = builder.build()
        delivered = {id(r) for segment in builder.delivered for r in segment}
        moved.update(i for i in shown.first_turn if _starting_output(lines[i]) and id(lines[i]) in delivered)
        notes = []
        if raw.system_prompt is None and snapshots:
            notes.append("The harness recorded no prompt at this point.")
        elif raw.system_prompt is None:  # a harness writes its prompt as it calls the model: none called, none written
            unrecorded = f"Not recorded by this harness version{f' ({version})' if version else ''}."
            if answered:
                notes.append(unrecorded)
            elif _records_no_snapshots(version):
                notes.append(f"{unrecorded} The model never answered here either.")
            else:
                notes.append("The model never answered here, so no prompt was recorded.")
        elif raw.tool_definitions is None:
            notes.append("The tool definitions were not recorded at this point.")
        if shown.reason is BriefingReason.START and raw.interrupted_start:
            notes.append("The first prompt was abandoned before the model answered: what was recorded only with it "
                         "may not have reached the model, and is left out.")
        found[shown.anchor] = Briefing(shown.at, shown.reason, raw.system_prompt is not None, " ".join(notes) or None,
                                       tuple(briefing_parts(raw, scan, memo, keep)) if parts else ())
    for index, (at, prompt, tools, count, after) in changed.items():
        found[index] = Briefing(at, BriefingReason.CHANGED, True, f"After /{after}." if after else None,
                                tuple(prompt_parts(prompt, tools, count, keep)) if parts else ())
    return found, moved, texts
