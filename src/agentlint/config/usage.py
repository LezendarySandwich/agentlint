"""Usage: which entities your turns actually used (docs/tech_impl/08-recorder.md).

Read from the transcripts the harness already writes — adapters turn their harness's transcripts
into ``Turn``s; nothing is installed and no prompt text is kept. Usage is counted, never stored
as a rate: a rate throws away its denominator, can't be recomputed over another window, can't be
merged, and carries no recency.

Entities are referred to as ``namespace/name`` ("skills/data:sql-query", "agents/code-search",
"tools/Bash"), not by id, so history matches the entities of whichever configuration is current.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from agentlint.config.entity import Entity
from agentlint.config.sessions import SessionHeader
from agentlint.config.taxonomy import EntityKind, EntityRole


@dataclass(frozen=True)
class Turn:
    """One turn — from one genuine prompt to the next — and the entities it used."""

    header: SessionHeader  # the session it is a turn of
    turn: int  # position in its session: with the session's id, the turn's identity
    at: datetime
    model: str | None  # the model that answered it; None when none did
    sidechain: bool  # a subagent's turn: kept, not counted as your usage
    entities: tuple[str, ...]  # namespace/name, each once

    @property
    def counts(self) -> bool:
        """Whether this turn is your usage: interactive (headless runs are kept but not counted), not a subagent's."""
        return self.header.interactive and not self.sidechain


class Counted(Protocol):
    """What counting needs of a turn — a ``Turn``, or one as the store keeps it (``store/usage.py``)."""

    @property
    def at(self) -> datetime: ...

    @property
    def counts(self) -> bool: ...

    @property
    def entities(self) -> tuple[str, ...]: ...


def reference_to(kind: EntityKind, name: str) -> str:
    """How usage refers to an entity: ``namespace/name``, the one place that form is built."""
    return f"{kind.namespace}/{name}"


def reference(entity: Entity) -> str:
    return reference_to(entity.kind, entity.name)


CHOSEN_ROLES = frozenset({EntityRole.SKILL, EntityRole.AGENT})  # what the agent chooses between by name or description


def chosen(kind: EntityKind) -> bool:
    """Whether the agent chooses entities of this kind from a list — skills and agents, not the tools it calls."""
    return kind.role in CHOSEN_ROLES


@dataclass(frozen=True)
class EntityUsage:
    reference: str
    invocations: int  # turns in the window that used it; the denominator is the index's turns_observed
    last_seen: datetime


@dataclass(frozen=True)
class UsageIndex:
    entities: dict[str, EntityUsage]
    turns_observed: int  # your turns in the window
    window_start: datetime | None
    history_start: datetime | None = None  # the oldest turn agentlint has ever kept

    def for_entity(self, entity: Entity) -> EntityUsage | None:
        return self.entities.get(reference(entity))

    def is_empty(self) -> bool:
        return self.turns_observed == 0


def build_index(turns: Iterable[Counted], *, window_start: datetime | None = None, window_end: datetime | None = None,
                history_start: datetime | None = None) -> UsageIndex:
    """Count, per entity, the turns in the window that used it — your turns only."""
    counted = [t for t in turns if t.counts and (window_start is None or t.at >= window_start)
               and (window_end is None or t.at <= window_end)]
    seen: dict[str, list[datetime]] = {}
    for turn in counted:
        for ref in turn.entities:
            seen.setdefault(ref, []).append(turn.at)
    entities = {ref: EntityUsage(ref, len(times), max(times)) for ref, times in seen.items()}
    return UsageIndex(entities, len(counted), window_start, history_start)


@dataclass(frozen=True)
class UsageSummary:
    """What `agentlint usage` prints: used entities ranked, and installed ones never used."""

    used: list[tuple[Entity, EntityUsage]] = field(default_factory=list)
    unused: list[Entity] = field(default_factory=list)
    unmatched: dict[str, int] = field(default_factory=dict)  # used, but not in this configuration (built-ins)


def summarise(entities: Sequence[Entity], index: UsageIndex) -> UsageSummary:
    """The entities the agent chooses between (``chosen``), used and never used, each reference once; and what turns
    used in those kinds' namespaces that no entity here defines. A namespace no entity here has is not known to be
    chosen, so nothing in it is reported."""
    picked = [e for e in entities if chosen(e.kind)]
    known = {reference(e) for e in picked}
    namespaces = {e.kind.namespace for e in picked}
    used = sorted(((e, index.entities[reference(e)]) for e in picked if reference(e) in index.entities),
                  key=lambda pair: (-pair[1].invocations, pair[0].name))
    seen_names: set[str] = set()
    unused = []
    for entity in sorted(picked, key=lambda e: e.name):
        if reference(entity) not in index.entities and reference(entity) not in seen_names:
            seen_names.add(reference(entity))
            unused.append(entity)
    unmatched = {ref: u.invocations for ref, u in index.entities.items()
                 if ref.split("/", 1)[0] in namespaces and ref not in known}
    deduped, emitted = [], set()
    for entity, usage in used:
        if usage.reference not in emitted:  # a skill and a command sharing a name: one choice, counted once
            emitted.add(usage.reference)
            deduped.append((entity, usage))
    return UsageSummary(deduped, unused, unmatched)
