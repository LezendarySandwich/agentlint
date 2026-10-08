"""What matters in a situation: one choice from a small fixed menu, naming the thing it is about (04-probe-generator.md,
"What matters").

The user's idea, 2026-10-04: say, for each situation, which part of the agent's actions the target should change. Since
2026-10-07 (the user: "we only need one menu and the agent can decide from it") the choice names its thing, from what the
configuration has: ``{"kind": "tool_present", "uses": "skill:*"}`` — so code can check whether a run did it, and a
situation none of whose runs did it is one that never exercised its target (``analysis/exercise.py``). A model writes
it, as it writes the situation; it is an input, and a run's actions are matched against it by code: no model ever
decides whether a test passed (AGENTS.md non-negotiable 2).

A thing is ``<kind>:<name>``: ``skill:<name>`` or ``skill:*`` (any skill), ``agent:<name>`` or ``agent:*``,
``tool:<name>``, ``program:<name>`` (a program a shell command runs), ``file:<path>``. Situations written before this
recorded the menu choice alone (``WhatMatters.thing`` None): nothing to check them against.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Matters(Enum):
    WHICH_TOOLS = "which_tools"  # which tool, skill or agent it picks among those that could do the job
    ORDER = "order"  # the order it does things in
    TARGETS = "targets"  # which files or programs it reads, edits or runs
    TOOL_PRESENT = "tool_present"  # whether it does a particular thing at all

    @property
    def meaning(self) -> str:
        """The menu line the prompt shows the model, and review shows a person."""
        return _MEANINGS[self]


_MEANINGS = {
    Matters.WHICH_TOOLS: "which tool, skill or agent the agent picks among those that could do the job",
    Matters.ORDER: "the order the agent does things in",
    Matters.TARGETS: "which files or programs the agent reads, edits or runs",
    Matters.TOOL_PRESENT: "whether the agent does a particular thing at all",
}

THING_KINDS = ("skill", "agent", "tool", "program", "file")
THING = re.compile(r"(skill|agent):(\*|[^\s:*][^\s*]{0,159})|(tool|program):[^\s:*][^\s*]{0,159}|file:[^\s*]{1,300}")
# Each kind's things: what the menu choice names, and which of those it needs.
_NEEDS = {Matters.TOOL_PRESENT: ("uses",), Matters.TARGETS: ("uses",), Matters.WHICH_TOOLS: ("uses",),
          Matters.ORDER: ("uses", "then")}
_ALLOWS = {Matters.TOOL_PRESENT: ("uses",), Matters.TARGETS: ("uses",), Matters.WHICH_TOOLS: ("uses", "instead_of"),
           Matters.ORDER: ("uses", "then")}


@dataclass(frozen=True)
class WhatMatters:
    """A menu choice and the thing it names: ``uses`` (for ``order``, what comes first), ``then`` (for ``order``, what
    follows it), ``instead_of`` (for ``which_tools``, what it shouldn't pick). ``uses`` None: written before things were
    named."""

    kind: Matters
    uses: str | None = None
    then: str | None = None
    instead_of: str | None = None

    def things(self) -> tuple[str, ...]:
        return tuple(t for t in (self.uses, self.then, self.instead_of) if t)

    def to_dict(self) -> dict[str, Any]:
        found: dict[str, Any] = {"kind": self.kind.value}
        for key in ("uses", "then", "instead_of"):
            if getattr(self, key):
                found[key] = getattr(self, key)
        return found

    def stored(self) -> str:
        """How the store keeps it: the menu choice alone when it names nothing (as before 2026-10-07), else JSON."""
        return self.kind.value if self.uses is None else json.dumps(self.to_dict(), sort_keys=True)

    def words(self) -> str:
        """What it names, for a person: "skill:*", "skill:a instead of skill:b", "program:acme, then tool:Write"; "" for
        none."""
        if self.uses is None:
            return ""
        if self.then:
            return f"{self.uses}, then {self.then}"
        if self.instead_of:
            return f"{self.uses} instead of {self.instead_of}"
        return self.uses


def problem(data: object) -> str | None:
    """What is wrong with a reply's ``matters``, exactly as the schema says it; None when nothing is."""
    if not isinstance(data, dict) or "kind" not in data:
        return "not an object with a kind"
    kind = data["kind"]
    if not isinstance(kind, str) or kind not in {m.value for m in Matters}:
        return f"kind {kind!r} is not one from the menu"
    menu = Matters(kind)
    unknown = sorted(set(data) - {"kind", *_ALLOWS[menu]})
    if unknown:
        return f"{kind} takes no {', '.join(unknown)}"
    missing = [key for key in _NEEDS[menu] if key not in data]
    if missing:
        return f"{kind} needs {', '.join(missing)}"
    for key in _ALLOWS[menu]:
        if key in data and (not isinstance(data[key], str) or not THING.fullmatch(data[key])):
            return f"{key} {str(data[key])[:60]!r} is not a thing: skill:<name>, skill:*, agent:<name>, agent:*, tool:<name>, program:<name> or file:<path>"
    return None


def parsed(data: dict[str, Any]) -> WhatMatters:
    """A reply's ``matters`` that ``problem`` found nothing wrong with."""
    return WhatMatters(Matters(data["kind"]), data.get("uses"), data.get("then"), data.get("instead_of"))


def read(stored: str) -> WhatMatters:
    """What the store keeps, back: JSON since 2026-10-07, a bare menu value before."""
    if stored.startswith("{"):
        data = json.loads(stored)
        return parsed(data)
    return WhatMatters(Matters(stored))
