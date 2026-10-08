"""The one finding type every check emits, static or behavioural (docs/tech_impl/10-reporting.md).

A finding is anything surfaced to the user. Three independent axes describe it:

    severity    how much it should worry you         info | warn | error
    kind        what sort of thing it is              cost | collision | broken | delivery | ...
    confidence  how sure we are                       certain | measured | suggestive

Identity is the check plus a check-chosen ``discriminator`` — whatever makes this finding *this*
finding (the shared name, the entity pair, the source and the reference). Never timestamps, scores
or membership: removing one of four same-named entities must not turn the finding into a new one.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum

from agentlint.config.entity import Visibility
from agentlint.config.taxonomy import Authority

_CHECK_ID = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")


class Severity(Enum):
    INFO = "info"  # worth knowing
    WARN = "warn"  # probably costing you behaviour or tokens; worth acting on
    ERROR = "error"  # broken: something configured cannot work as written

    @property
    def rank(self) -> int:
        """How severe, as a number to compare: 0 info, 1 warn, 2 error — higher is more severe."""
        return _SEVERITY_RANK[self]


class Confidence(Enum):
    CERTAIN = "certain"  # mechanically true
    MEASURED = "measured"  # observed by running the agent, under stated conditions
    SUGGESTIVE = "suggestive"  # a pattern worth a look, not a claim


class FindingKind(Enum):
    COST = "cost"  # what something costs in context
    COLLISION = "collision"  # the agent may pick the wrong one of several
    BROKEN = "broken"  # refers to something missing or unusable
    DELIVERY = "delivery"  # what reaches the agent differs from what is configured
    HEALTH = "health"  # a configured component fails at runtime
    EMPHASIS = "emphasis"  # how loudly instructions are written (a heuristic)
    BEHAVIOUR = "behaviour"  # measured by running the agent


_SEVERITY_RANK = {Severity.INFO: 0, Severity.WARN: 1, Severity.ERROR: 2}


@dataclass(frozen=True)
class Location:
    """A place a finding points at: a source, by id, and a line within that source's text. Renderers show it as the
    source's file (``paths.place``) or, for text no file holds, its label, and link it by id."""

    source_id: str  # one of the snapshot's sources
    line: int | None = None  # 1-indexed within the source's text; None for the source as a whole

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("Location.source_id must not be empty")
        if self.line is not None and self.line < 1:
            raise ValueError(f"a location's line counts from 1, got {self.line}")


@dataclass(frozen=True)
class Figure:
    """A number the finding rests on — machine-readable, so the UI need not parse prose."""

    label: str
    value: int | float
    unit: str  # "tokens", "chars", "sessions", "percent", ...
    unit_one: str  # the unit for a value of exactly 1 — "token", "session", "percent" — so no reader guesses a plural


@dataclass(frozen=True)
class Member:
    """One of the entities a finding sets side by side — namesakes, near-identical descriptions — as data, so every
    renderer shows the same facts from one place: the terminal prints these, the UI lays them out in columns, and the
    finding's ``detail`` says only why they matter."""

    entity: str  # entity id, one of the snapshot's entities
    name: str  # what the agent invokes it by
    kind: str  # its kind's label: "skill", "slash command"
    source: str  # the id of the source that provides it
    scope: Authority | None = None  # where that source sits; None when the snapshot has no such source
    package: str | None = None  # the package that source shipped in (Provenance.package)
    seen: Visibility | None = None  # what the agent saw of it in the latest session; None when no session shows it
    description: str | None = None  # its trigger text, whole
    identical_to: str | None = None  # the entity id of another member whose file is the same content as this one's
    symlink: str | None = None  # where its file links to, when it is a symlink: removing the link may not stick


@dataclass(frozen=True)
class Finding:
    check: str  # public check id: legible on its own, never renamed casually
    kind: FindingKind
    severity: Severity
    confidence: Confidence
    title: str  # one line, specific
    detail: str  # what we did and what we saw
    discriminator: str  # what makes this finding this finding, within its check
    sources: tuple[str, ...] = ()  # source ids involved
    locations: tuple[Location, ...] = ()
    figures: tuple[Figure, ...] = ()
    suggested_action: str | None = None  # an action, or None when there honestly isn't one
    members: tuple[Member, ...] = ()  # what it sets side by side, in order; empty for a check that compares nothing

    def __post_init__(self) -> None:
        if not _CHECK_ID.fullmatch(self.check):
            raise ValueError(f"check ids are lowercase-kebab, got {self.check!r}")
        if not self.title.strip() or not self.discriminator.strip():
            raise ValueError("a finding needs a title and a discriminator")
        if (self.confidence is Confidence.MEASURED) != (self.kind is FindingKind.BEHAVIOUR):
            raise ValueError("MEASURED confidence is for behavioural findings, and only those")
        entities = {member.entity for member in self.members}
        if len(entities) != len(self.members):
            raise ValueError("a finding lists each member once")
        if any(m.identical_to is not None and m.identical_to not in entities - {m.entity} for m in self.members):
            raise ValueError("a member is identical only to another member of the same finding")

    @property
    def id(self) -> str:
        return hashlib.sha256(f"{self.check}|{self.discriminator}".encode("utf-8", "surrogatepass")).hexdigest()[:16]

    @property
    def sort_key(self) -> tuple[int, str]:
        """Most severe first, then by check; within a check, the order it emitted (sort stably)."""
        return (-self.severity.rank, self.check)
