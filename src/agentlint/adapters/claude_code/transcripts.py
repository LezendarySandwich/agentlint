"""Read Claude Code session transcripts: what the harness actually delivered at session start.

Main-session transcripts live at ``~/.claude/projects/<cwd-slug>/<session-id>.jsonl``; subagent
transcripts live in subdirectories and are not sessions. Only main-thread lines are read
(``isSidechain`` false).

The first turn is everything before the first model response: that is what was in context before
the user's first words were answered. Attachment lines from 2.1.280 on carry ``rendered`` — the
exact text sent to the model. Older lines don't, so text is taken from the attachment's own fields.

**A recorded attachment is not proof of delivery.** When the first submission is abandoned before
any model call (an interrupted prompt, or local commands before the first prompt), its attachments
are dropped and some are never re-sent — VERIFIED 2026-10-02: two sessions here repeat their
listing attachments before the first response, observe ~14k fewer first-turn tokens than their
recorded content implies, and in one of them the model never received the 506-entry skill listing.
Such a start is detected by more than one genuine prompt (not ``isMeta``, not local-command
markup) before the first response, or a listing attachment repeating; then the session-start
content plus the final submission onward count as delivered — session-start hook output survives,
as the model's own context in the reference session confirms — and the first-turn total is not
trusted.

Which hook records reach the model is harness-version data, VERIFIED 2026-10-02 against the
2.1.285 bundle: ``hook_success`` only for SessionStart / UserPromptSubmit / UserPromptExpansion and
only with non-empty ``content`` (plain stdout); ``hook_additional_context`` always;
``async_hook_response`` its ``systemMessage`` and ``additionalContext``; ``hook_cancelled``,
``hook_non_blocking_error`` and ``hook_system_message`` never.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentlint.adapters.claude_code.hooks import DELIVERING_STDOUT_EVENTS
from agentlint.adapters.claude_code.points import Kind
from agentlint.config.sessions import SessionHeader
from agentlint.config.usage import Turn, reference_to

SESSION_CONTEXT_TYPES = ("environment", "model", "date", "session_context", "auto_mode", "ultra_effort_enter")
LISTING_TYPES = ("skill_listing", "agent_listing_delta", "deferred_tools_delta", "instructions")
CARRIED_TYPES = ("file", "compact_file_reference", "invoked_skills")  # what a compaction re-attaches: files read
# before it, and the skills invoked before it — VERIFIED 2026-10-04 on this machine's 2.1.280/2.1.285 transcripts
_COMMAND_MARKUP = ("<local-command", "<command-name>", "<command-message>", "<command-args>")
# User-role lines the harness writes itself, recognisable by how they start (older versions record no origin):
# an Esc interrupt, a `!` shell command and its output, a background task's notification.
_HARNESS_WRITTEN = ("[Request interrupted by user", "<bash-input>", "<bash-stdout>", "<bash-stderr>", "<task-notification>")


@dataclass(frozen=True)
class HookRun:
    """One hook execution the transcript recorded, whether or not its output reached the model."""

    event: str
    hook_name: str  # "<Event>:<match target>", e.g. "SessionStart:startup"
    command: str  # as configured, unexpanded (e.g. "${CLAUDE_PLUGIN_ROOT}/hooks/x.sh")
    stdout: str
    content: str  # what the harness kept as plain-text output; empty when stdout was JSON


@dataclass(frozen=True)
class HookOutput:
    """Hook text that reached the model."""

    event: str
    hook_name: str
    channel: str  # "stdout" | "additional_context" | "async"
    text: str
    command: str | None  # known for plain stdout; None for additional context (trace_outputs finds its run's)
    rendered: str | None = None  # its share of the record's ``rendered`` text, framing included, where recorded: what
    # the model was shown (``rendered_shares``); ``text`` stays the hook's own output, which is what is traced


@dataclass(frozen=True)
class InstructionFile:
    path: str  # absolute path, or "<managed-settings>"
    kind: str  # harness label: "Managed", "Project", "User", "AutoMem", ...
    content: str


@dataclass(frozen=True)
class CarriedOver:
    """What a compaction re-attached: a file read before it (``file``; only its name for a
    ``compact_file_reference``), or the skills invoked before it (``skills``)."""

    kind: str  # "file" | "skills"
    names: tuple[str, ...]  # the file's display path, or the skills' names
    text: str  # what the model was shown (``rendered`` where recorded)
    pieces: tuple[str, ...] = ()  # for skills: each one's own recorded content, in the order of ``names``


@dataclass(frozen=True)
class LaterListing:
    """A listing record delivered in the turn after the first of its kind, saying something new: a delta that adds
    entries, or one that only names MCP servers that failed to connect (seen after a /compact,
    VERIFIED 2026-10-04 in a 2.1.285 transcript). The first of each kind is ``FirstTurn``'s own field."""

    kind: str  # "skill_listing" | "agent_listing_delta" | "deferred_tools_delta"
    text: str  # what the model was shown (``rendered`` where recorded)
    names: tuple[str, ...]  # the entries it adds


@dataclass(frozen=True)
class FirstTurn:
    """What a session's transcript shows of its first turn — everything before the first model response — and the
    tool list, for a reading of the configuration. Only the first turn: the rest of the session isn't read."""

    path: Path
    header: SessionHeader  # its start the file's modification time when no line is dated (``dated``)
    dated: bool  # whether a line said when the session started
    system_prompt: str | None
    tool_definitions: str | None
    tool_names: tuple[str, ...]
    skill_listing: str | None
    skill_names: tuple[str, ...]
    agent_listing: str | None
    agent_types: tuple[str, ...]
    agent_built_ins: tuple[str, ...]  # agent types the harness itself provides
    deferred_tools: str | None
    deferred_tool_names: tuple[str, ...]
    failed_mcp_servers: tuple[str, ...]
    instruction_files: tuple[InstructionFile, ...]
    session_context: tuple[str, ...]  # environment, model, date, git status, mode reminders
    hook_outputs: tuple[HookOutput, ...]
    hook_runs: tuple[HookRun, ...]
    first_turn_input: int | None  # input + cache_creation + cache_read of the first model call
    first_turn_user_text: str  # the user-side messages that preceded it
    interrupted_start: bool  # first submission abandoned: first_turn_input is not a clean total
    carried: tuple[CarriedOver, ...] = ()  # what a compaction re-attached, when the turn read is one after it
    later_listings: tuple[LaterListing, ...] = ()  # delivered listing records after the first of their kind


def main_transcripts(projects_dir: Path, limit: int | None, prefer: str | None = None,
                     since: datetime | None = None) -> list[Path]:
    """The ``limit`` (None: every one) most recently written main-session transcripts — those under ``prefer`` (a
    project's directory) first, so a project's own sessions are read even when others ran since. With ``since``,
    only those written since: one last written before it started before it too."""
    if (limit is not None and limit <= 0) or not projects_dir.is_dir():
        return []
    paths = [path for path in projects_dir.glob("*/*.jsonl")
             if path.is_file() and (since is None or path.stat().st_mtime >= since.timestamp())]
    return sorted(paths, key=lambda path: (path.parent.name != prefer, -path.stat().st_mtime))[:limit]


_ENVELOPE_TEXTS = ("type", "subtype", "timestamp", "sessionId", "cwd", "version", "entrypoint")
_ENVELOPE_OBJECTS = ("message", "attachment", "commandRun")
# What reading one record raises when a field deeper in it has a shape no harness version wrote: the reader
# skips that record and keeps the rest (``records`` already dropped those whose envelope is wrong).
UNREADABLE = (TypeError, ValueError, AttributeError, KeyError)


def _readable(record: dict[str, Any]) -> bool:
    """Whether a record's envelope has the shape every reader assumes: these fields, where present, are strings
    or objects, and a message's model, content and usage are what the harness writes."""
    if any(record.get(key) is not None and not isinstance(record[key], str) for key in _ENVELOPE_TEXTS):
        return False
    if any(record.get(key) is not None and not isinstance(record[key], dict) for key in _ENVELOPE_OBJECTS):
        return False
    message = record.get("message") or {}
    return (message.get("model") is None or isinstance(message["model"], str)) and (
        message.get("content") is None or isinstance(message["content"], (str, list))) and (
        message.get("usage") is None or isinstance(message["usage"], dict))


def records(raw_lines: Iterable[bytes]) -> Iterator[dict[str, Any]]:
    """Each JSON object line; a torn or foreign line is skipped, and so is one whose envelope has a shape no
    harness version writes — one odd record never stops a reading, from the harness's file or the archive's copy."""
    for raw in raw_lines:
        try:
            record = json.loads(raw)
        except ValueError:
            continue  # a torn final line from a live session
        if isinstance(record, dict) and _readable(record):
            yield record


def _lines(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("rb") as handle:
        yield from records(handle)


def _rendered(record: dict[str, Any]) -> str | None:
    parts = record.get("rendered")
    if not parts or not isinstance(parts, list):
        return None
    return "".join(part.get("content", "") if isinstance(part, dict) else str(part) for part in parts)


def rendered_shares(pieces: list[str], rendered: str | None) -> list[str | None]:
    """A record's ``rendered`` text cut into one share per piece of its content, in order, so the shares join back
    into exactly the rendered text: each piece with the framing before it, the last with the framing after it too.
    All None when nothing was rendered or a piece isn't found in it, in order — then each piece is shown as it is."""
    if not rendered or not pieces:
        return [None] * len(pieces)
    ends, cursor = [], 0
    for piece in pieces:
        found = rendered.find(piece, cursor)
        if found < 0:
            return [None] * len(pieces)
        cursor = found + len(piece)
        ends.append(cursor)
    starts = [0, *ends[:-1]]
    ends[-1] = len(rendered)
    return [rendered[start:end] for start, end in zip(starts, ends)]


def _timestamp(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class HeaderReader:
    """What a transcript says about its session, fed its lines in order: the one place a ``SessionHeader`` is built,
    and so the one place the interactive rule is applied (``headless``). The first of each: the working directory,
    the harness version, the entry point, the dated line, and the model that answered first (a ``<synthetic>``
    message names none). In a session's transcript subagent lines (``isSidechain``) say nothing about it; in a
    subagent's own transcript every line is one."""

    def __init__(self, subagent: bool = False) -> None:
        self.subagent = subagent
        self.project: str | None = None
        self.version: str | None = None
        self.entrypoint: str | None = None
        self.model: str | None = None
        self.started_at: datetime | None = None

    def feed(self, record: dict[str, Any]) -> None:
        """Take what one line says, the envelope already checked by ``records``."""
        if record.get("isSidechain") and not self.subagent:
            return
        self.project = self.project or record.get("cwd") or None
        self.version = self.version or record.get("version") or None
        self.entrypoint = self.entrypoint or record.get("entrypoint") or None
        at = _timestamp(record.get("timestamp"))
        self.started_at = self.started_at or (at if at and at.tzinfo else None)
        if self.model is None and record.get("type") == "assistant":
            named = (record.get("message") or {}).get("model")
            self.model = named if named and named != "<synthetic>" else None

    def header(self, session_id: str, fallback: datetime | None = None) -> SessionHeader | None:
        """The header, once the lines that say it were fed; None when no line is dated and no ``fallback`` start is
        given."""
        started = self.started_at or fallback
        if started is None:
            return None
        return SessionHeader(session_id, Path(self.project) if self.project else None, self.version, self.model,
                             started, interactive=not headless(self.entrypoint))


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(_text_of(block))
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                texts.append(_message_text(block.get("content")))
        return "\n".join(texts)
    return ""


def _text_of(block: dict[str, Any], key: str = "text") -> str:
    """A text block's text; one that isn't a string raises (``UNREADABLE``), so the record holding it is skipped."""
    text = block.get(key, "")
    if not isinstance(text, str):
        raise TypeError(f"a {key} block whose {key} is not a string")
    return text


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _is_prompt(record: dict[str, Any]) -> bool:
    """A prompt the user submitted, as opposed to what the harness writes in the user's role: injected text, local
    commands, interrupt markers, `!` shell lines, task notifications, compaction summaries. Where the line records
    its origin (2.1.28x: ``{"kind": "human"}``), that decides — VERIFIED 2026-10-03 on this machine's transcripts."""
    if record.get("isMeta") or record.get("isCompactSummary") or record.get("isVisibleInTranscriptOnly"):
        return False
    origin = record.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return False
    content = (record.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
        content = _message_text(content)
    return isinstance(content, str) and not content.lstrip().startswith(_COMMAND_MARKUP + _HARNESS_WRITTEN)


def queued_prompt(record: dict[str, Any]) -> str | None:
    """A prompt the user typed while the agent was working, delivered mid-turn: the harness writes it only as a
    ``queued_command`` attachment (``commandMode: "prompt"``), with no user line — VERIFIED 2026-10-03 on this
    machine's 2.1.285 transcripts (the queue's ``remove`` reason is ``absorbed_mid_turn``). A queued prompt sent
    after the turn ends is written as a normal user line instead, and has no such attachment. Its text, or None."""
    attachment = record.get("attachment")
    if record.get("type") != "attachment" or not isinstance(attachment, dict):
        return None
    if attachment.get("type") != "queued_command" or attachment.get("commandMode") != "prompt":
        return None
    origin = attachment.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return None
    text = _message_text(attachment.get("prompt"))
    if not text.strip() or text.lstrip().startswith(_COMMAND_MARKUP + _HARNESS_WRITTEN):
        return None
    return text


def typed_command(record: dict[str, Any]) -> str | None:
    """The slash command a line records the user typing — its name only, and only a well-formed one, so no other
    text can ever pass for a name. Never from tool output or injected text. The line is a user line that begins
    with command markup or — as 2.1.266 to 2.1.280 sometimes wrote a command, with no user line — a ``system``
    ``local_command`` line whose ``content`` does (VERIFIED 2026-10-04 on this machine's transcripts)."""
    if record.get("isMeta"):
        return None
    if record.get("type") == "system" and record.get("subtype") == "local_command":
        content = record.get("content")
    elif record.get("type") == "user":
        content = (record.get("message") or {}).get("content")
    else:
        return None
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        content = _message_text(content)
    if not isinstance(content, str) or not content.lstrip().startswith(_COMMAND_MARKUP[1:]):
        return None
    match = _COMMAND_NAME.search(content)
    name = match.group(1).strip().lstrip("/") if match else ""
    return name if _ENTITY_NAME.fullmatch(name) else None


def command_run(record: dict[str, Any]) -> str | None:
    """The command a ``local_command`` output line says ran (``commandRun``): its name, only a well-formed one."""
    if record.get("type") != "system" or record.get("subtype") != "local_command":
        return None
    run = record.get("commandRun") or {}
    name = run.get("command")
    name = name.lstrip("/") if isinstance(name, str) else ""
    return name if _ENTITY_NAME.fullmatch(name) else None


def _usage_total(usage: dict[str, Any]) -> int | None:
    counts = [usage.get(key) or 0 for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
    return sum(counts) if all(isinstance(n, int) for n in counts) else None


def snapshot_texts(attachment: dict[str, Any]) -> tuple[str | None, str | None]:
    """A ``prompt_snapshot``'s system prompt (its blocks joined) and tool definitions (as JSON), each None when the
    snapshot doesn't carry it: the one reading of a snapshot, for the configuration and the transcript reader alike.
    Its ``cliPrefix`` is not kept."""
    blocks = attachment.get("systemPrompt")
    prompt = "\n".join(block for block in blocks if isinstance(block, str)) if isinstance(blocks, list) else None
    tools = attachment.get("tools")
    definitions = [tool for tool in tools if isinstance(tool, dict)] if isinstance(tools, list) else None
    return prompt, json.dumps(definitions, ensure_ascii=False) if definitions is not None else None


class _Builder:
    """Accumulates one session's first turn, then keeps reading only for the tool list. The transcript reader feeds
    one the records from a compaction on, for what the context was rebuilt with; in a subagent's own transcript
    (``subagent``) every line is a sidechain one, and is read."""

    def __init__(self, path: Path, subagent: bool = False) -> None:
        self.path = path
        self.subagent = subagent
        self.session_id = path.stem
        self.fields = HeaderReader(subagent)
        self.system_prompt: str | None = None
        self.tools: str | None = None  # the tool definitions, as JSON
        self.tool_names: tuple[str, ...] = ()
        self.skill_listing: str | None = None
        self.skill_names: tuple[str, ...] = ()
        self.agent_listing: str | None = None
        self.agent_types: tuple[str, ...] = ()
        self.agent_built_ins: tuple[str, ...] = ()
        self.deferred_tools: str | None = None
        self.deferred_names: tuple[str, ...] = ()
        self.failed_mcp: tuple[str, ...] = ()
        self.instructions: list[InstructionFile] = []
        self.context: list[str] = []
        self.hook_outputs: list[HookOutput] = []
        self.hook_runs: list[HookRun] = []
        self.first_turn_input: int | None = None
        self.user_texts: list[str] = []
        self.first_turn_done = False
        self.segments: list[list[dict[str, Any]]] = [[]]  # first-turn records; [0] is session start
        self.interrupted = False
        self.carried: list[CarriedOver] = []
        self.later_listings: list[LaterListing] = []
        self.delivered: list[list[dict[str, Any]]] = []  # the first-turn segments counted delivered, once it closed

    def done(self) -> bool:
        return self.first_turn_done and self.tools is not None and self.system_prompt is not None

    def feed(self, record: dict[str, Any]) -> None:
        if record.get("isSidechain") and not self.subagent:
            return
        self.session_id = record.get("sessionId") or self.session_id
        self.fields.feed(record)
        kind = record.get("type")
        attachment = record.get("attachment")
        if isinstance(attachment, dict) and attachment.get("type") == "prompt_snapshot":
            self._snapshot(attachment)
            return
        if self.first_turn_done:
            return
        if kind == "assistant":
            message = record.get("message") or {}
            usage = message.get("usage")
            self.first_turn_input = _usage_total(usage) if isinstance(usage, dict) else None
            self._close_first_turn()
        elif kind == "user" and _is_prompt(record):
            self.segments.append([record])  # a genuine prompt starts a new submission
        else:
            self.segments[-1].append(record)

    def _close_first_turn(self) -> None:
        """Decide which first-turn records were delivered, then read them."""
        self.first_turn_done = True
        listed = [
            (record.get("attachment") or {}).get("type")
            for segment in self.segments
            for record in segment
            if (record.get("attachment") or {}).get("type") in LISTING_TYPES
        ]
        prompts = len(self.segments) - 1
        # A subagent is asked in more than one user line (a workflow agent: the harness's preamble, then its task),
        # by its caller, never abandoned by you: its start is never an interrupted one.
        self.interrupted = not self.subagent and (prompts > 1 or len(listed) != len(set(listed)))
        delivered = [self.segments[0], *self.segments[-1:]] if self.interrupted and prompts else self.segments
        self.delivered = delivered
        for segment in self.segments:  # hook runs are executions, not deliveries: keep them all
            for record in segment:
                attachment = record.get("attachment")
                if isinstance(attachment, dict) and attachment.get("type") == "hook_success" and not any(segment is d for d in delivered):
                    self._hook_run(attachment)
        for segment in delivered:
            for record in segment:
                attachment = record.get("attachment")
                try:
                    if record.get("type") == "user":
                        self.user_texts.append(_message_text((record.get("message") or {}).get("content")))
                    elif isinstance(attachment, dict):
                        self._attachment(attachment, record)
                except UNREADABLE:
                    continue  # a record of a shape no harness version writes: skipped, the rest read

    def _snapshot(self, attachment: dict[str, Any]) -> None:
        prompt, tools = snapshot_texts(attachment)
        if self.system_prompt is None and prompt is not None:
            self.system_prompt = prompt
        if self.tools is None and tools is not None:
            self.tools = tools
            self.tool_names = tuple(str(tool.get("name", "")) for tool in attachment["tools"] if isinstance(tool, dict))

    def _attachment(self, attachment: dict[str, Any], record: dict[str, Any]) -> None:
        kind = attachment.get("type")
        rendered = _rendered(record)
        # Each listing is read whole before any of it is kept: one of an unexpected shape raises, and is skipped.
        if kind == "skill_listing":
            content = attachment.get("content")
            listing = rendered or (content if isinstance(content, str) else "")
            names = _texts(attachment.get("names"))
            if self.skill_listing is None:
                self.skill_names, self.skill_listing = names, listing
            else:
                self._later(kind, listing, names)
        elif kind == "agent_listing_delta":
            listing = rendered or "\n".join(_texts(attachment.get("addedLines")))
            types, built_ins = _texts(attachment.get("addedTypes")), _texts(attachment.get("builtInTypes"))
            if self.agent_listing is None:
                self.agent_listing, self.agent_types, self.agent_built_ins = listing, types, built_ins
            else:
                self._later(kind, listing, types)
        elif kind == "deferred_tools_delta":
            listing = rendered or "\n".join(_texts(attachment.get("addedLines")))
            names = _texts(attachment.get("addedNames"))
            failed = tuple(
                str(server.get("name", "")) for server in attachment.get("failedMcpServers") or () if isinstance(server, dict)
            )
            if self.deferred_tools is None:
                self.deferred_tools, self.deferred_names, self.failed_mcp = listing, names, failed
            else:
                self._later(kind, listing, names)
        elif kind == "instructions":
            for item in attachment.get("files") or ():
                if isinstance(item, dict):
                    self.instructions.append(
                        InstructionFile(str(item.get("path", "")), str(item.get("type", "")), str(item.get("content", "")))
                    )
        elif kind in SESSION_CONTEXT_TYPES:
            text = rendered or _fallback_context_text(kind, attachment)
            if text:
                self.context.append(text)
        elif kind == "hook_success":
            self._hook_success(attachment, rendered)
        elif kind == "hook_additional_context":
            event, name = str(attachment.get("hookEvent", "")), str(attachment.get("hookName", ""))
            pieces = [item for item in attachment.get("content") or () if isinstance(item, str) and item]
            for item, shown in zip(pieces, rendered_shares(pieces, rendered)):
                self.hook_outputs.append(HookOutput(event, name, "additional_context", item, None, shown))
        elif kind == "async_hook_response":
            self._async_hook(attachment)
        elif kind in CARRIED_TYPES:
            self._carried(kind, attachment, rendered)

    def _later(self, kind: str, text: str, names: tuple[str, ...]) -> None:
        """A listing record after the first of its kind in the turn: kept when it says something the turn's earlier
        ones of that kind didn't (an exact re-send is not), for a briefing; the configuration reading reads the first."""
        first = {"skill_listing": self.skill_listing, "agent_listing_delta": self.agent_listing,
                 "deferred_tools_delta": self.deferred_tools}[kind]
        if text.strip() and text != first and all(text != later.text for later in self.later_listings if later.kind == kind):
            self.later_listings.append(LaterListing(kind, text, names))

    def _carried(self, kind: str, attachment: dict[str, Any], rendered: str | None) -> None:
        if kind == "invoked_skills":
            skills = [skill for skill in attachment.get("skills") or () if isinstance(skill, dict)]
            names = tuple(str(skill.get("name", "")) for skill in skills)
            text = rendered or "\n\n".join(str(skill.get("content") or "") for skill in skills)
            pieces = tuple(str(skill.get("content") or "") for skill in skills)
            self.carried.append(CarriedOver("skills", names, text, pieces))
            return
        content = attachment.get("content")
        inner = content.get("file") if isinstance(content, dict) else None
        name = str(attachment.get("displayPath") or attachment.get("filename") or "")
        text = rendered or (str(inner.get("content") or "") if isinstance(inner, dict) else "")
        self.carried.append(CarriedOver("file", (name,), text))

    def _hook_run(self, attachment: dict[str, Any]) -> HookRun:
        run = HookRun(
            event=str(attachment.get("hookEvent", "")),
            hook_name=str(attachment.get("hookName", "")),
            command=str(attachment.get("command", "")),
            stdout=str(attachment.get("stdout") or ""),
            content=str(attachment.get("content") or ""),
        )
        self.hook_runs.append(run)
        return run

    def _hook_success(self, attachment: dict[str, Any], rendered: str | None = None) -> None:
        run = self._hook_run(attachment)
        if run.event in DELIVERING_STDOUT_EVENTS and run.content:
            [shown] = rendered_shares([run.content], rendered)
            self.hook_outputs.append(HookOutput(run.event, run.hook_name, "stdout", run.content, run.command, shown))

    def _async_hook(self, attachment: dict[str, Any]) -> None:
        response = _json_object(attachment.get("response"))
        event, name = str(attachment.get("hookEvent", "")), str(attachment.get("hookName", ""))
        message = response.get("systemMessage")
        context = _json_object(response.get("hookSpecificOutput")).get("additionalContext")
        for text in (message, context):
            if isinstance(text, str) and text:
                self.hook_outputs.append(HookOutput(event, name, "async", text, None))

    def build(self) -> FirstTurn:
        modified = None if self.fields.started_at else _modified(self.path)
        return FirstTurn(
            path=self.path,
            header=self.fields.header(self.session_id, fallback=modified),
            dated=self.fields.started_at is not None,
            system_prompt=self.system_prompt,
            tool_definitions=self.tools,
            tool_names=self.tool_names,
            skill_listing=self.skill_listing,
            skill_names=self.skill_names,
            agent_listing=self.agent_listing,
            agent_types=self.agent_types,
            agent_built_ins=self.agent_built_ins,
            deferred_tools=self.deferred_tools,
            deferred_tool_names=self.deferred_names,
            failed_mcp_servers=self.failed_mcp,
            instruction_files=tuple(self.instructions),
            session_context=tuple(self.context),
            hook_outputs=tuple(self.hook_outputs),
            hook_runs=tuple(self.hook_runs),
            first_turn_input=self.first_turn_input,
            first_turn_user_text="\n".join(text for text in self.user_texts if text),
            interrupted_start=self.interrupted,
            carried=tuple(self.carried),
            later_listings=tuple(self.later_listings),
        )


def _modified(path: Path) -> datetime | None:
    """When a transcript was last written; None for one that isn't a file here (an archived copy, read from bytes)."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None


def _texts(value: object) -> tuple[str, ...]:
    """A list of strings, as the harness writes one; anything else raises, so the record holding it is skipped."""
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError("not a list of strings")
    return tuple(value)


def _fallback_context_text(kind: str, attachment: dict[str, Any]) -> str:
    """Text for session-context attachments written before ``rendered`` existed."""
    if kind == "date":
        return f"Today's date is {attachment.get('date', '')}."
    if kind == "session_context":
        context = attachment.get("context") or {}
        return "\n".join(str(value) for value in context.values()) if isinstance(context, dict) else ""
    if kind == "model":
        return str(attachment.get("text") or "")
    if kind == "environment":
        return json.dumps(attachment.get("snapshot") or {}, ensure_ascii=False)
    return ""


def parse_session(path: Path) -> FirstTurn:
    builder = _Builder(path)
    for record in _lines(path):
        try:
            builder.feed(record)
        except UNREADABLE:
            continue  # a record of a shape no harness version writes: skipped, the rest read
        if builder.done():
            break
    if not builder.first_turn_done:
        builder._close_first_turn()  # no model response yet: everything recorded so far
        builder.first_turn_input = None
    return builder.build()


@dataclass(frozen=True)
class HookRecord:
    """One recorded hook execution anywhere in a session, for health reporting and a run's trace."""

    session_id: str
    event: str
    hook_name: str
    command: str
    outcome: str  # "ok" | "error" | "timeout" | "cancelled"
    duration_ms: int | None
    detail: str  # stderr for errors, empty otherwise
    at: datetime | None = None
    tool_use_id: str | None = None  # the tool call it ran for (``toolUseID``), as the transcript records it


_HOOK_TYPES = {"hook_success": "ok", "hook_non_blocking_error": "error",
               "hook_error_during_execution": "error", "hook_cancelled": "cancelled"}


def _int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def hook_record(record: dict[str, Any], session_id: str) -> HookRecord | None:
    """The hook execution one main-thread record says happened, or None: the one place a hook record is read, for
    hook health (``hook_records``) and a run's trace alike. Raises ``UNREADABLE`` for a shape no version writes."""
    if record.get("isSidechain"):
        return None
    attachment = record.get("attachment") or {}
    kind = attachment.get("type")
    outcome = _HOOK_TYPES.get(kind) if isinstance(kind, str) else None
    if outcome is None:
        return None
    if outcome == "cancelled" and str(attachment.get("timedOut")).lower() == "true":
        outcome = "timeout"
    called = attachment.get("toolUseID")
    return HookRecord(
        session_id=record.get("sessionId") or session_id,
        event=str(attachment.get("hookEvent", "")),
        hook_name=str(attachment.get("hookName", "")),
        command=str(attachment.get("command") or ""),
        outcome=outcome,
        duration_ms=_int(attachment.get("durationMs")),
        detail=str(attachment.get("stderr") or "")[:300] if outcome == "error" else "",
        at=_timestamp(record.get("timestamp")),
        tool_use_id=called if isinstance(called, str) and called else None,
    )


def hook_records(path: Path) -> list[HookRecord]:
    """Every main-thread hook execution the transcript recorded. Streams; skips lines cheaply."""
    found: list[HookRecord] = []
    session_id = path.stem
    with path.open("rb") as handle:
        for raw in handle:
            if b'"hook_' not in raw:
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(record, dict) or not _readable(record):
                continue
            hook = hook_record(record, session_id)
            if hook is not None:
                found.append(hook)
    return found


# --- usage: what each turn used (docs/tech_impl/08-recorder.md) --------------------------------------

_COMMAND_NAME = re.compile(r"<command-name>/?([^<]+)</command-name>")
_ENTITY_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_:.@-]{0,159}")  # a tool, skill, command or agent name, nothing else
_DELEGATING_TOOLS = ("Agent", "Task")  # Task is the older name
_DEFAULT_AGENT = "general-purpose"  # what a delegation naming no subagent_type gets


def headless(entrypoint: str | None) -> bool:
    """Run by a program, not a person: `-p` (sdk-cli), the Agent SDKs, the GitHub action. VERIFIED here: sdk-cli;
    UNVERIFIED: the other names, taken from what the harness calls them."""
    return bool(entrypoint) and (entrypoint.startswith("sdk") or "action" in entrypoint)


def delegated_to(given: dict[str, Any]) -> str | None:
    """The agent a delegation runs: its subagent_type, the default when it names none; a fork of the caller is
    no agent of its own."""
    kind = given.get("subagent_type")
    if kind is None:
        return _DEFAULT_AGENT
    if not isinstance(kind, str) or kind == "fork" or not _ENTITY_NAME.fullmatch(kind):
        return None
    return kind


def skill_use(name: str) -> str:
    """The usage reference for a skill or command by name: they share one list."""
    return reference_to(Kind.SKILL.value, name)


def uses(name: str, given: dict[str, Any]) -> tuple[str, ...]:
    """What one tool call used, as usage references: the tool, the skill a Skill call invokes, the agent a
    delegation runs. One rule for usage (``turns_from``) and the transcript reader alike."""
    if not _ENTITY_NAME.fullmatch(name):
        return ()
    used = [reference_to(Kind.TOOL.value, name)]
    skill = str(given.get("skill") or "").lstrip("/")
    if name == "Skill" and _ENTITY_NAME.fullmatch(skill):
        used.append(skill_use(skill))
    agent = delegated_to(given) if name in _DELEGATING_TOOLS else None
    if agent:
        used.append(reference_to(Kind.AGENT.value, agent))
    return tuple(used)


@dataclass(frozen=True)
class ToolUse:
    """One ``tool_use`` block of an assistant line, as the transcript has it."""

    name: str
    id: str | None  # the block's id, which its ``tool_result`` names
    given: dict[str, Any]  # its input; {} when the block's input isn't an object


def tool_uses(record: dict[str, Any]) -> list[ToolUse]:
    """The tool calls an assistant line makes, in order: the one place they are read, for usage, the conversation
    reader and a run's trace. Blocks with no name are not calls; a name that isn't a string raises (``UNREADABLE``), so
    every reader skips the record holding it alike."""
    if record.get("type") != "assistant":
        return []
    found = []
    for block in (record.get("message") or {}).get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use" or not block.get("name"):
            continue
        if not isinstance(block["name"], str):
            raise TypeError("a tool_use block whose name is not a string")
        given = block.get("input") if isinstance(block.get("input"), dict) else {}
        ident = block.get("id")
        found.append(ToolUse(block["name"], ident if isinstance(ident, str) and ident else None, given))
    return found


def tool_results(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The ``tool_result`` blocks a user line carries, in order: each names the call it answers (``tool_use_id``)."""
    if record.get("type") != "user":
        return []
    content = (record.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"]


def usage_turns(path: Path) -> list[Turn]:
    return turns_from(path.stem, _lines(path))


def _turn_step(record: dict[str, Any], opened_by: str | None) -> tuple[str | bool, str | None, tuple[str, ...]]:
    """What one main-thread record does to usage: whether it opens a turn (the command's name when a typed command
    opens it), the model it names, the entities it used. ``opened_by`` is the command that opened the latest turn."""
    command = typed_command(record)
    if command:
        return command, None, (skill_use(command),)
    if record.get("type") == "user":
        return _is_prompt(record), None, ()
    if queued_prompt(record):
        return True, None, ()
    ran = command_run(record)
    if ran:  # a command's output line: the same use as the command line just before it, else a command of its own
        return (False if opened_by == ran else ran), None, (skill_use(ran),)
    message = record.get("message") or {}
    if record.get("type") != "assistant":
        return False, None, ()
    used: dict[str, None] = {}
    for call in tool_uses(record):
        used.update(dict.fromkeys(uses(call.name, call.given)))
    model = message.get("model")
    return False, (model if model and model != "<synthetic>" else None), tuple(used)


def turns_from(session_id: str, lines: Iterable[dict[str, Any]]) -> list[Turn]:
    """Each turn of a main-session transcript, with the entities it used — never what was typed.

    A turn starts at a genuine prompt — one typed while the agent worked included (``queued_prompt``) — or a
    slash command the user typed (``typed_command``; one older versions wrote only as a ``local_command`` system
    line included), or a command run with no line of its own (``command_run``). A command run locally never
    reaches the model, so the turn it opens holds the command alone: what the agent does next stays with the
    prompt it was answering — VERIFIED 2026-10-04: a ``/effort`` here was typed while the agent was mid-reply.
    A record of a shape no harness version writes is skipped and the rest read. Within a turn: every tool called
    (``tools/<name>``), skills invoked through the Skill tool or typed as a command (``skills/<name>``, a
    built-in command run locally included), agents delegated to (``agents/<subagent_type>``). What the harness
    writes in the user's role starts no turn, and a skill it attaches on its own (``isMeta`` command markup) is
    not your use. Subagent lines are skipped (v1 counts the delegation, not what the subagent did), as are the
    harness's own synthetic messages. Names are taken only in their well-formed shape: no other text is kept."""
    fields = HeaderReader()
    turns: list[dict[str, Any]] = []
    for record in lines:
        fields.feed(record)
        if record.get("isSidechain"):
            continue
        try:  # read whole before anything is kept: a record of a shape no harness version writes is skipped
            opens, model, used = _turn_step(record, turns[-1]["command"] if turns else None)
        except UNREADABLE:
            continue
        if opens:  # a command run locally (a system line) never reaches the model: its turn is the command's alone
            turns.append({"at": _timestamp(record.get("timestamp")), "model": None, "entities": {},
                          "command": opens if isinstance(opens, str) else None, "local": record.get("type") == "system"})
        working = next((t for t in reversed(turns) if not t["local"]), None) if record.get("type") == "assistant" else None
        target = working if record.get("type") == "assistant" else turns[-1] if turns else None
        if target is not None:
            target["model"] = model or target["model"]
            target["entities"].update(dict.fromkeys(used))
    header = fields.header(session_id)  # None only when no line is dated: then no turn is either
    return [Turn(header, index, t["at"], t["model"], False, tuple(t["entities"]))
            for index, t in enumerate(turns, 1) if t["at"] is not None and header is not None]
