"""The assembled configuration: sources, the rules inside them, entities, and the always-on budget.

A Source is a container: one artifact that reaches the agent. A Rule is an addressable region
inside it: what we ablate where a variant can cut it out alone, else the whole source goes — either is an
ablation target (rank/plan.py; docs/tech_impl/01-config-reader.md "Source vs Rule").

Every class checks its own invariants on construction, so an object that exists is consistent: counts are never
negative, reasons and labels never empty, and a value that follows from others (a rule's id, tokens and hash) is
computed here or checked against them. Two values are trusted to their maker instead: a snapshot's ``config_hash`` and
``fingerprint`` (``assemble`` computes them, and a stored snapshot reads back with the ones it was stored under), and
a source's ``declared_hash`` when its content is not file text (only the adapter knows what the user controls there).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from enum import Enum
from pathlib import Path

from agentlint.config.content import Content, FileText, NotObserved, ObservedText, content_hash, tokens_of
from agentlint.config.entity import Entity, Visibility
from agentlint.config.locators import Locator, locator_key
from agentlint.config.regions import Granularity
from agentlint.config.sessions import SessionHeader, same_dir
from agentlint.config.taxonomy import Authority, InjectionPoint, Load
from agentlint.config.tokens import TokenCount, TokenMethod, estimated


@dataclass(frozen=True)
class Activation:
    load: Load
    condition: str | None = None  # natural-language trigger, e.g. "before Bash calls"

    def __post_init__(self) -> None:
        if self.load is Load.CONDITIONAL and not self.condition:
            raise ValueError("a CONDITIONAL source must say what its condition is")


@dataclass(frozen=True)
class Provenance:
    """Where a source comes from. Two fields say where it is: ``file``, the file on disk its text is read from or
    configured in, and ``anchor``, where inside that file when the source is one part of it (a key, a JSON pointer).
    Core compares ``file`` and never looks inside ``anchor``: only the adapter that wrote it knows its grammar.
    Text no file holds — the harness's own, what it composes — has neither; its point's label says what it is."""

    point: InjectionPoint  # how it reaches the agent; carries the harness and the mechanism
    file: Path | None  # the file it is read from or configured in; None for text no file holds
    authority: Authority
    package: str | None = None  # the package it shipped in, if any
    anchor: str | None = None  # where inside ``file``, when the source is one part of it; opaque to core

    def __post_init__(self) -> None:
        if self.file is not None and not isinstance(self.file, Path):
            raise TypeError(f"Provenance.file must be a Path, got {self.file!r}")
        if self.anchor is not None and (self.file is None or not self.anchor.strip()):
            raise ValueError("Provenance.anchor names a place inside a file: it needs a file, and must not be empty")

    @property
    def harness(self) -> str:
        return self.point.harness


class Ablation(Enum):
    FILE_REMOVAL = "file_removal"  # drop the file from the variant's config tree
    SPAN_EXCISION = "span_excision"  # rewrite the file in the variant with a region removed
    SETTINGS_DISABLE = "settings_disable"  # flip or remove an entry in the variant's settings
    NONE = "none"


@dataclass(frozen=True)
class AblationPolicy:
    mechanism: Ablation
    blocked_reason: str | None = None  # required exactly when mechanism is NONE
    linked_sources: tuple[str, ...] = ()  # removing this necessarily removes these; no adapter fills it yet

    def __post_init__(self) -> None:
        blocked = self.mechanism is Ablation.NONE
        if blocked != bool(self.blocked_reason and self.blocked_reason.strip()):
            raise ValueError("blocked_reason is required when ablation is NONE, and only then")

    @classmethod
    def blocked(cls, reason: str) -> AblationPolicy:
        return cls(Ablation.NONE, reason)


@dataclass(frozen=True)
class Rule:
    """A region of a source's text. ``id``, ``tokens`` and ``content_hash`` follow from the rest, so they are
    computed here rather than passed in: a rule cannot carry a hash or a count of some other text."""

    id: str = field(init=False)  # "<source id>#<locator key>"
    source_id: str
    locator: Locator
    text: str  # the excerpt, for display and probe generation
    tokens: TokenCount = field(init=False)  # ESTIMATED from text
    content_hash: str = field(init=False)
    start_line: int  # 1-indexed, inclusive, within the source's text
    end_line: int

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("Rule.source_id must not be empty")
        if not 1 <= self.start_line <= self.end_line:
            raise ValueError(f"a rule's lines run forward from line 1: got {self.start_line}-{self.end_line}")
        object.__setattr__(self, "id", f"{self.source_id}#{locator_key(self.locator)}")
        object.__setattr__(self, "tokens", estimated(self.text))
        object.__setattr__(self, "content_hash", content_hash(self.text))


@dataclass(frozen=True)
class Source:
    id: str
    provenance: Provenance
    activation: Activation
    declared_hash: str  # what the user controls (file bytes, a command's config); the fingerprint's only input
    content: Content
    ablation: AblationPolicy

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Source.id must not be empty")
        if not self.declared_hash.startswith("sha256:"):
            raise ValueError("Source.declared_hash must be a sha256: digest")
        if isinstance(self.content, FileText) and self.declared_hash != self.content.content_hash:
            raise ValueError("a source read from a file declares exactly that file's text")
        if self.id in self.ablation.linked_sources:
            raise ValueError("a source cannot be linked to itself")

    @property
    def tokens(self) -> TokenCount | None:
        return tokens_of(self.content)

    def rules(self, granularity: Granularity = Granularity.COARSE) -> tuple[Rule, ...]:
        """Rules inside this source. Only text can be divided; other content has none."""
        if not isinstance(self.content, (FileText, ObservedText)):
            return ()
        return tuple(
            Rule(source_id=self.id, locator=region.locator, text=region.text,
                 start_line=region.start_line, end_line=region.end_line)
            for region in self.content.regions(granularity)
        )


def _unique(what: str, ids: list[str]) -> None:
    duplicates = sorted(i for i, n in Counter(ids).items() if n > 1)
    if duplicates:
        raise ValueError(f"duplicate {what} ids: {duplicates}")


@dataclass(frozen=True)
class Declared:
    """Everything an adapter reads from disk: deterministic, no sessions, no AI.

    Sources whose text only a session can show (a command's output, the harness's own prompt) are
    declared with NotObserved content; ``assemble()`` fills them in from sessions.
    """

    harness: str
    cwd: Path
    harness_version: str | None  # None when disk does not say
    model: str | None
    sources: tuple[Source, ...]
    entities: tuple[Entity, ...]

    def __post_init__(self) -> None:
        ids = [source.id for source in self.sources]
        _unique("source", ids)
        _unique("entity", [entity.id for entity in self.entities])
        for source in self.sources:
            if source.provenance.harness != self.harness:
                raise ValueError(f"source {source.id} belongs to {source.provenance.harness}, not {self.harness}")
            if isinstance(source.content, ObservedText):
                raise ValueError(f"source {source.id}: observed text comes from sessions, not from disk")
        known = set(ids)
        dangling = sorted(entity.id for entity in self.entities if entity.provided_by not in known)
        if dangling:
            raise ValueError(f"entities provided by unknown sources: {dangling}")


@dataclass(frozen=True)
class Untraced:
    """Text a session delivered that could not be traced to anything declared on disk.

    It was still in context and still cost tokens, so it is counted — but it is not declared
    configuration, so it never enters the fingerprint.

    ``id`` follows from where it arrived and its ``key`` — what ``assemble`` groups deliveries by, unique in a
    snapshot, which the adapter chose not to vary from reading to reading (``Delivery.key``; empty, the label) — never
    from its place in the list or from a label that names its opening line or a count of hooks: a link to it (the UI's
    ``#/config/untraced/<id>``) keeps naming the same thing after reading again.
    """

    id: str = field(init=False)  # 12 hex digits of a SHA-256 over the point's harness and name and the key
    point: InjectionPoint
    label: str
    load: Load
    content: ObservedText
    key: str = ""  # Delivery.key of what it groups; empty (and in snapshots written before 2026-10-04): the label

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("Untraced.label must say what the text arrived as")
        key = f"{self.point.harness}\0{self.point.name}\0{self.key or self.label}".encode("utf-8", "surrogatepass")
        object.__setattr__(self, "id", hashlib.sha256(key).hexdigest()[:12])


@dataclass(frozen=True)
class Attribution:
    """Tokens one thing delivered before the first words: a declared source, by id, or text no source explains
    (``source_id`` None, under the ``label`` it arrived with). Readers group these — by authority, by mechanism —
    rather than summing sources again, so every figure built on an always-on outcome counts the same tokens."""

    source_id: str | None
    tokens: int
    label: str | None = None

    def __post_init__(self) -> None:
        if self.tokens < 0:
            raise ValueError("an attribution counts tokens, never a negative number")
        if (self.source_id is None) == (self.label is None):
            raise ValueError("an attribution names a source, or (untraced text) a label — exactly one")


def _sum(attribution: tuple[Attribution, ...]) -> int:
    return sum(a.tokens for a in attribution)


def _check_observed(owner: str, total: TokenCount) -> None:
    """Reconciled and Overcounted both rest on a total a session reported, so both refuse an estimate."""
    if total.method is TokenMethod.ESTIMATED:
        raise ValueError(f"{owner}.observed_total is what a session reported, never an estimate")


@dataclass(frozen=True)
class Reconciled:
    """An observed always-on total, split into what we attributed and the remainder.

    ``attribution`` is what the anchor session itself delivered (plus files on disk), computed once in assemble;
    ``attributed`` and ``unattributed`` are derived from it."""

    DERIVED = ("attributed", "unattributed")  # written to JSON beside the fields (serialize.to_data)

    observed_total: TokenCount  # OBSERVED or DERIVED
    attribution: tuple[Attribution, ...]
    session: SessionHeader  # where the total was observed

    def __post_init__(self) -> None:
        _check_observed("Reconciled", self.observed_total)
        if self.attributed > self.observed_total.value:
            raise ValueError("Reconciled requires attributed <= observed_total; more is Overcounted")

    @property
    def attributed(self) -> int:
        """Estimated tokens of everything we could attribute in the anchor session."""
        return _sum(self.attribution)

    @property
    def unattributed(self) -> TokenCount:
        """DERIVED by subtraction: the harness built-in we could not see."""
        return TokenCount(self.observed_total.value - self.attributed, TokenMethod.DERIVED)


@dataclass(frozen=True)
class Overcounted:
    """We attributed more than the session observed: our estimates or our reader are off.

    Reported rather than hidden; a negative remainder would be a confident lie. ``attribution`` is what the anchor
    session delivered, as for Reconciled.
    """

    DERIVED = ("attributed", "excess")

    observed_total: TokenCount  # OBSERVED or DERIVED, as for Reconciled
    attribution: tuple[Attribution, ...]
    session: SessionHeader

    def __post_init__(self) -> None:
        _check_observed("Overcounted", self.observed_total)
        if self.attributed <= self.observed_total.value:
            raise ValueError("Overcounted requires attributed > observed_total")

    @property
    def attributed(self) -> int:
        return _sum(self.attribution)

    @property
    def excess(self) -> int:
        return self.attributed - self.observed_total.value


@dataclass(frozen=True)
class NotReconciled:
    """No suitable session observed a total, so the attributed figure is a lower bound.

    ``attribution`` is each source's latest observation, possibly from different sessions."""

    DERIVED = ("attributed",)

    attribution: tuple[Attribution, ...]
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("NotReconciled.reason must say why no total was observed")

    @property
    def attributed(self) -> int:
        return _sum(self.attribution)


AlwaysOn = Reconciled | Overcounted | NotReconciled


@dataclass(frozen=True)
class ConfigSnapshot:
    harness: str
    harness_version: str | None
    model: str | None
    cwd: Path
    sources: tuple[Source, ...]
    entities: tuple[Entity, ...]
    untraced: tuple[Untraced, ...]
    sessions: tuple[SessionHeader, ...]  # every session examined, most recent first
    always_on: AlwaysOn
    visibility: tuple[tuple[str, Visibility], ...]  # entity id -> what the agent saw of it, latest session
    config_hash: str  # over declared sources only: "did the configuration change?"
    fingerprint: str  # config_hash + harness_version + model: "can these results be compared?"

    def __post_init__(self) -> None:
        _unique("source", [source.id for source in self.sources])
        _unique("entity", [entity.id for entity in self.entities])  # session entities merged in too
        _unique("untraced", [item.id for item in self.untraced])
        known = {source.id for source in self.sources}
        dangling = sorted(entity.id for entity in self.entities if entity.provided_by not in known)
        if dangling:
            raise ValueError(f"entities provided by unknown sources: {dangling}")

    @property
    def project_sessions(self) -> tuple[SessionHeader, ...]:
        """The sessions examined that ran in this snapshot's directory, most recent first."""
        return tuple(header for header in self.sessions if same_dir(header.project, self.cwd))

    def seen_as(self, entity_id: str) -> Visibility | None:
        """What the agent saw of an entity in the latest session that showed it; None if unknown."""
        return dict(self.visibility).get(entity_id)

    @cached_property
    def entities_by_source(self) -> Mapping[str, tuple[Entity, ...]]:
        """Source id -> the entities it provides, in order; a source that provides none is absent. Built once."""
        provided: dict[str, list[Entity]] = {}
        for entity in self.entities:
            provided.setdefault(entity.provided_by, []).append(entity)
        return {source_id: tuple(entities) for source_id, entities in provided.items()}

    def source(self, source_id: str) -> Source:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise KeyError(source_id)

    def rules(self, granularity: Granularity = Granularity.COARSE) -> tuple[Rule, ...]:
        return tuple(rule for source in self.sources for rule in source.rules(granularity))


__all__ = [
    "Ablation",
    "AblationPolicy",
    "Activation",
    "AlwaysOn",
    "Attribution",
    "ConfigSnapshot",
    "Declared",
    "Entity",
    "NotObserved",
    "NotReconciled",
    "Overcounted",
    "Provenance",
    "Reconciled",
    "Rule",
    "Source",
    "Untraced",
]
