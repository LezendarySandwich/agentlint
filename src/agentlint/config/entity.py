"""Entities: named things rules refer to (docs/tech_impl/01-config-reader.md "Entity").

Rules point at resolved entity ids, never at free text. Most entities are declared on disk; some —
a harness's tools, its built-in entities, tools a server exposes — are only knowable from what a
session shows, and arrive through ``SessionDeliveries.entities``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agentlint.config.taxonomy import EntityKind


class Visibility(Enum):
    """What the agent sees of an entity when choosing between entities — observed, not declared."""

    DESCRIBED = "described"  # its name and its description
    NAME_ONLY = "name_only"  # its name alone: a listing cap or a deferred tool
    NOT_SHOWN = "not_shown"  # nothing: hidden from the agent, though a user may still invoke it

    @property
    def rank(self) -> int:
        """How much the agent sees of it, most first: 0 described, 1 name only, 2 not shown. Where an entity's
        visibility is unknown (None) it ranks after all three: ``rank_seen``."""
        return _VISIBILITY_RANK[self]

    @staticmethod
    def rank_seen(seen: Visibility | None) -> int:
        """``rank``, with unknown (no session shows it) last: what to prefer among namesakes."""
        return len(_VISIBILITY_RANK) if seen is None else seen.rank


_VISIBILITY_RANK = {Visibility.DESCRIBED: 0, Visibility.NAME_ONLY: 1, Visibility.NOT_SHOWN: 2}


@dataclass(frozen=True)
class Entity:
    id: str  # unique, adapter-defined
    name: str  # what the agent invokes it by
    kind: EntityKind
    provided_by: str  # source id
    description: str | None = None  # trigger text, if any
    aliases: tuple[str, ...] = ()  # other names it goes by, from the adapter or registry data; never model output

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise ValueError("Entity.id and Entity.name must not be empty")

    @property
    def short_name(self) -> str:
        """Its own name, without what provides it: what two namesakes from different providers share."""
        separator = self.kind.separator
        return self.name.rsplit(separator, 1)[-1] if separator else self.name
