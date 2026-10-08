"""What recent sessions show the harness actually delivered.

Disk is the truth about what is configured. Sessions add only what disk cannot know: the text hooks
print, the harness's own prompt and tool definitions, and the observed token total. Adapters
produce these records; ``assemble()`` merges them with what was read from disk.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agentlint.config.entity import Entity, Visibility
from agentlint.config.formats import ContentFormat
from agentlint.config.taxonomy import InjectionPoint, Load
from agentlint.config.tokens import TokenCount, TokenMethod


def same_dir(a: str | Path | None, b: str | Path | None) -> bool:
    """Whether two paths name the same directory (spelled differently, or through a symlink); never when either
    is unknown."""
    if a is None or b is None or str(a) == "" or str(b) == "":
        return False
    return os.path.normpath(a) == os.path.normpath(b) or os.path.realpath(a) == os.path.realpath(b)


@dataclass(frozen=True)
class SessionHeader:
    """What one session was, as its transcript says — one header per transcript, the same in a reading's sessions,
    the transcript reader's listing and usage turns. None where the transcript doesn't say: never an empty string
    or a word standing for "unknown". Adapters build it in one place, which also owns the interactive rule."""

    session_id: str
    project: Path | None  # the session's working directory
    harness_version: str | None  # the harness version it started on
    model: str | None  # the model that answered first
    started_at: datetime  # timezone-aware
    interactive: bool = True  # a person at the keyboard; headless runs get different tools and context

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("SessionHeader.session_id must not be empty")
        if self.started_at.tzinfo is None:
            raise ValueError("SessionHeader.started_at must be timezone-aware")
        if "" in (self.harness_version, self.model):
            raise ValueError("SessionHeader says None for what the transcript doesn't say, not an empty value")


@dataclass(frozen=True)
class Delivery:
    """One piece of text a session delivered to the agent.

    ``source_id`` is the declared source the adapter traced it to, or None when the adapter could
    not trace it to anything on disk — that text was still in context and still costs tokens.

    ``key`` is what tells untraced text from other untraced text at the same point, from one reading to the next;
    empty, the label does. An adapter sets it when its label carries something that changes between readings —
    the text's opening, a count of hooks it might be — so the ``Untraced`` it becomes keeps its id.
    """

    point: InjectionPoint
    source_id: str | None
    format: ContentFormat
    text: str
    load: Load
    label: str  # describes the text, especially when it cannot be traced
    key: str = ""  # groups untraced text across readings; empty: the label

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("Delivery.label must not be empty")


@dataclass(frozen=True)
class SessionDeliveries:
    """What one session delivered to the agent: its header, the texts, and what only a session can show."""

    header: SessionHeader
    deliveries: tuple[Delivery, ...]
    always_on_total: TokenCount | None  # tokens in context before the user's first words
    entities: tuple[Entity, ...] = ()  # entities only a session can show (tools, built-ins)
    visibility: tuple[tuple[str, Visibility], ...] = ()  # entity id -> what the agent saw of it

    def __post_init__(self) -> None:
        if self.always_on_total is not None and self.always_on_total.method is TokenMethod.ESTIMATED:
            raise ValueError("a session's always-on total comes from usage numbers, never an estimate")
