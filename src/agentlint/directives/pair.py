"""Joining directives across sections (12-directives.md, "Pairing"): plain code over the checked directives.

- **A contradiction candidate**: one quote says toward a thing (must, prefer) and one from another source away from it
  (must not, avoid) — the same thing, or ``skill:*`` / ``agent:*`` against a skill or agent of theirs ("don't use
  subagents unless asked" against "always use agent X for exploration"); any-of-a-kind against any-of-a-kind only when
  one side always applies. Whether both apply at once depends on their ``when``, which code can't judge: each pair of
  quotes is a candidate for you to judge (the user, 2026-10-07; one pair at a time since 2026-10-08).
- **A repeat**: the same directive — the same thing, the same direction — in two or more sources that load in every
  session, so the agent reads it more than once in each. A repeat in text that loads only when invoked costs nothing
  until then, and isn't one.

A thing is compared by kind and name: a skill or agent by its name without a package's prefix, a program by its name
in lower case, a file by its path without a leading ``./``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from agentlint.directives.model import Directive


@dataclass(frozen=True)
class Occurrence:
    """A directive where it was found: the source and the section (rule) of it, and whether that source loads in every
    session."""

    directive: Directive
    source: str
    section: str
    line: int
    every_session: bool


@dataclass(frozen=True)
class Contradiction:
    """Two quotes from two sources, one toward a thing and one away from it: a candidate to judge, one pair at a time."""

    toward: str  # the thing one side says to use, as compared ("agent:explorer")
    away: str  # the thing the other side says not to ("agent:*", or the same thing)
    for_it: Occurrence
    against: Occurrence

    @property
    def every_session(self) -> bool:
        """Whether one side or the other is in every session's context."""
        return self.for_it.every_session or self.against.every_session


@dataclass(frozen=True)
class Repeat:
    thing: str
    positive: bool
    occurrences: tuple[Occurrence, ...]


def key(thing: str) -> str:
    """A thing as directives are compared by."""
    kind, _, name = thing.partition(":")
    if kind in ("skill", "agent"):
        return f"{kind}:{name if name == '*' else name.rsplit(':', 1)[-1]}"
    if kind == "program":
        return f"program:{name.lower()}"
    if kind == "file":
        return f"file:{name.removeprefix('./')}"
    return thing


def _covers(away: str, toward: str) -> bool:
    if away == toward:
        return True
    kind, _, name = away.partition(":")
    return name == "*" and toward.startswith(f"{kind}:")


def _sources(occurrences: Iterable[Occurrence]) -> set[str]:
    return {o.source for o in occurrences}


def contradictions(occurrences: Sequence[Occurrence]) -> list[Contradiction]:
    """Every pair of quotes, from two sources, one toward a thing and one away from it — the ones in every session's
    context first. Two directives about any skill (or any agent) clash only when one of them always applies (no
    ``when``): "use subagents for exploration" and "don't use subagents for small edits" are two scopes, not a
    contradiction (2026-10-08). A named thing against any of its kind ("always use agent X" and "never use subagents")
    is always a candidate."""
    toward = [o for o in occurrences if o.directive.polarity.positive]
    away = [o for o in occurrences if not o.directive.polarity.positive]
    found = []
    for f in toward:
        for g in away:
            t, a = key(f.directive.thing), key(g.directive.thing)
            if f.source == g.source or not _covers(a, t):
                continue  # one source qualifying itself ("use X, but not for Y") is not two sources disagreeing
            if t == a and t.endswith(":*") and f.directive.when and g.directive.when:
                continue
            found.append(Contradiction(t, a, f, g))
    return sorted(found, key=lambda c: (not c.every_session, c.toward, c.away, c.for_it.source, c.for_it.line,
                                        c.against.source, c.against.line))


def repeats(occurrences: Sequence[Occurrence]) -> list[Repeat]:
    """Every directive two or more sources that load in every session give."""
    grouped: dict[tuple[str, bool], list[Occurrence]] = defaultdict(list)
    for occurrence in occurrences:
        if occurrence.every_session:
            grouped[(key(occurrence.directive.thing), occurrence.directive.polarity.positive)].append(occurrence)
    return [Repeat(thing, positive, tuple(found)) for (thing, positive), found in sorted(grouped.items())
            if len(_sources(found)) >= 2]
