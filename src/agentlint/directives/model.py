"""A directive: one thing a section of instruction text tells the agent to do, or not do, with a thing the
configuration has — a skill, an agent, a tool, a program or a file — and when (12-directives.md, "The form")."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Polarity(Enum):
    MUST = "must"  # always, required: "always use X", "you must invoke X"
    MUST_NOT = "must_not"  # forbidden: "never use X", "do not use X"
    PREFER = "prefer"  # favoured over the alternatives: "use X instead of Y", "prefer X"
    AVOID = "avoid"  # disfavoured: "avoid X", "X only as a last resort"

    @property
    def positive(self) -> bool:
        """Toward the thing (must, prefer) or away from it (must not, avoid)."""
        return self in (Polarity.MUST, Polarity.PREFER)

    @property
    def meaning(self) -> str:
        return _MEANINGS[self]


_MEANINGS = {
    Polarity.MUST: "the agent must do it, or always does it",
    Polarity.MUST_NOT: "the agent must never do it",
    Polarity.PREFER: "the agent should favour it over the alternatives",
    Polarity.AVOID: "the agent should avoid it, or use it only as a last resort",
}


@dataclass(frozen=True)
class Directive:
    polarity: Polarity
    thing: str  # matters.THING's form: skill:<name>|skill:*|agent:<name>|agent:*|tool:<name>|program:<name>|file:<path>
    quote: str  # the words of the section that say it, exactly as written there
    when: str | None = None  # when it applies, in a few words; None: always

    def to_dict(self) -> dict[str, Any]:
        return {"polarity": self.polarity.value, "thing": self.thing, "quote": self.quote, "when": self.when}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Directive:
        return cls(Polarity(data["polarity"]), data["thing"], data["quote"], data.get("when"))


def stored(directives: tuple[Directive, ...]) -> str:
    return json.dumps([d.to_dict() for d in directives], sort_keys=True)


def read(text: str) -> tuple[Directive, ...]:
    return tuple(Directive.from_dict(d) for d in json.loads(text))
