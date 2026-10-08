"""A session as you would read it back: your prompts, the agent's replies and tool calls, what was injected —
for the live UI's transcript reader (docs/tech_impl/11-ui.md, "Sessions").

Adapters turn their harness's transcripts into this: each entry one of the ``Item`` kinds, holding only what that
kind has. Nothing here is stored: only `agentlint ui`, on this machine, behind its token, shows it. Long tool output and injected text are cut (``Clipped``), saying how much, so
even a long session stays readable in a browser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from agentlint.config.sessions import SessionHeader
from agentlint.config.taxonomy import Authority, InjectionPoint
from agentlint.config.tokens import TokenCount, TokenMethod

OUTPUT_LIMIT = 3000  # characters of a tool's output, or of injected text, kept for reading


class NoteKind(Enum):
    """What a note is: a line the harness or you wrote that is neither a prompt nor the agent's."""

    COMMAND = "command"  # a slash command you typed: `/name args`
    INTERRUPTED = "interrupted"  # you stopped the agent or a tool call (in a subagent: its caller or its run did)
    SHELL = "shell"  # a `!` shell command you ran, or its output
    NOTIFICATION = "notification"  # a background task finished
    HOOK_ERROR = "hook_error"  # a hook failed without stopping the agent
    RECAP = "recap"  # the harness's summary of the session so far
    COMPACTED = "compacted"  # the conversation was summarised here to free context


@dataclass(frozen=True)
class Clipped:
    """Text kept for reading, and how many characters were left out of it: the count travels with its text."""

    text: str
    omitted: int = 0

    def __post_init__(self) -> None:
        if self.omitted < 0:
            raise ValueError("Clipped.omitted must not be negative")


def clip(text: str, limit: int = OUTPUT_LIMIT) -> Clipped:
    """The first ``limit`` characters of ``text``, saying how many more there were."""
    return Clipped(text) if len(text) <= limit else Clipped(text[:limit], len(text) - limit)


@dataclass(frozen=True)
class Prompt:
    """What you typed — one typed while the agent worked included. In a subagent's transcript: what it was asked."""

    at: datetime | None
    text: str


@dataclass(frozen=True)
class Reply:
    """What the agent wrote back."""

    at: datetime | None
    text: str


@dataclass(frozen=True)
class Thinking:
    """The agent's reasoning, where the harness records it."""

    at: datetime | None
    text: str


@dataclass(frozen=True)
class ToolCall:
    """A tool call, with its input and what it returned."""

    at: datetime | None
    name: str  # the tool's name
    summary: str  # the call in one line: the command, the file, the skill
    input: str  # its input, in full
    result: Clipped | None = None  # what it returned, cut at OUTPUT_LIMIT; None when no result was recorded
    error: bool = False  # the tool reported an error
    entities: tuple[str, ...] = ()  # namespace/name of what it used
    subagent_ref: str | None = None  # the subagent transcripts this call started, in the adapter's terms: opaque to core,
    # which only hands it back to the adapter (``Adapter.is_subagent_ref``, ``Adapter.belongs``)


@dataclass(frozen=True)
class Injected:
    """Text the harness or a hook added to the conversation: a skill's body, hook context or output."""

    at: datetime | None
    by: str  # what injected it, in words: "hook context · <hook>", "expanded by the harness"
    text: Clipped


@dataclass(frozen=True)
class Note:
    """A line that is neither yours nor the agent's words: a command, an interrupt, a hook error, a recap."""

    at: datetime | None
    kind: NoteKind
    text: Clipped
    entities: tuple[str, ...] = ()  # namespace/name of what it used: a command's skill


class BriefingReason(Enum):
    """Why the harness gave the model its prompt and context at this point."""

    START = "start"  # before the first prompt was answered
    COMPACTED = "compacted"  # the context rebuilt after the conversation was summarised
    CHANGED = "changed"  # the harness's own prompt or tool definitions changed, with no rebuild


@dataclass(frozen=True)
class Share:
    """One source's share of a part that spans several ownership groups — a listing whose entries come from the
    harness and from packages: its authority, and its estimated tokens."""

    label: str  # what holds this share, in words: a package's name, "the listing's own part"
    authority: Authority
    tokens: TokenCount  # estimated from its share of the text
    source_id: str | None = None  # the configuration source it is, when the adapter traced it


@dataclass(frozen=True)
class BriefingPart:
    """One part of what the harness gave the model: its own prompt, its tool definitions, an instruction file, a
    listing, the session's context, hook output. Its exact recorded text is ``Conversation.texts[text_ref]``."""

    point: InjectionPoint  # where it reaches the agent, in the adapter's terms; core reads only its mechanism
    label: str  # what it is, in words: "System prompt", an instruction file's name
    detail: str  # what tells it apart: a file's path (absolute: the UI shortens it), a hook's name; "" when nothing
    count: int | None  # the entries it holds — tools, skills, agents, files — when it is a list of them
    authority: Authority | None  # whose it is; None when its ``shares`` span several groups, or when it was not traced
    shares: tuple[Share, ...]  # its split across groups when it spans more than one; empty otherwise
    source_id: str | None  # the configuration source it is, when the adapter traced it to one
    text_ref: str  # the content hash of its text, a key of ``Conversation.texts``
    tokens: TokenCount  # estimated from its text

    def __post_init__(self) -> None:
        if self.tokens.method is not TokenMethod.ESTIMATED:
            raise ValueError("BriefingPart.tokens are counted from its text, so must be ESTIMATED")
        if self.shares and self.authority is not None:
            raise ValueError("a BriefingPart split across groups has no one authority")


@dataclass(frozen=True)
class Briefing:
    """What the harness gave the model at one point, as the transcript recorded it: before the first prompt was
    answered, after a compaction rebuilt the context, or where the harness's own prompt or tool definitions changed.
    Only what was recorded — never reconstructed. ``recorded`` is whether the harness recorded its own prompt here;
    ``note`` says, in the adapter's words, what wasn't recorded or may not have reached the model."""

    DERIVED = ("tokens",)  # written to JSON beside the fields (serialize.to_data)

    at: datetime | None
    reason: BriefingReason
    recorded: bool
    note: str | None
    parts: tuple[BriefingPart, ...]

    @property
    def tokens(self) -> TokenCount:
        """Every part's estimated tokens, together."""
        return TokenCount(sum(part.tokens.value for part in self.parts), TokenMethod.ESTIMATED)


Item = Prompt | Reply | Thinking | ToolCall | Injected | Note | Briefing  # serialised with a "type" tag (serialize.py)


@dataclass(frozen=True)
class SessionListing:
    """A session transcript in the reader's list, without its contents: its header, and what the list shows."""

    header: SessionHeader
    title: str | None  # the harness's own name for the session, if it keeps one
    ended_at: datetime  # its last dated line
    prompts: int
    size: int  # bytes on disk


@dataclass(frozen=True)
class TranscriptFile:
    """A transcript the harness keeps — a session's, or one of its subagents' — as agentlint archives it."""

    handle: str  # the adapter's handle for it: opaque to core, which only hands it back (``Adapter.subagent_id``)
    size: int
    mtime: float
    session_id: str  # its own session's id, or for a subagent the session it ran in
    subagent: bool = False


@dataclass(frozen=True)
class Conversation:
    """A transcript, readable: its title — the harness's name for it, if it keeps one — and its entries. What the
    session was is its listing's header (``SessionListing``). ``texts`` holds each briefing part's text once, by its
    content hash, however many briefings re-sent it."""

    title: str | None
    items: tuple[Item, ...]
    texts: dict[str, str] = field(default_factory=dict)

