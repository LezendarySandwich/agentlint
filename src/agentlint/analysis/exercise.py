"""Did a situation exercise its target? Whether any of its runs did the thing its "what matters" names (decided with the
user 2026-10-07; 07-analysis.md, "Exercising the target"). A situation none of whose runs did it never gave the target
a chance to change anything: its with/without runs would measure nothing, so it gets none, and it is written again.

Pure, and harness-agnostic: a thing is matched against a run's actions through the adapter's action terms
(``trace/terms.py``). No model reads a run: the generator named the thing as it wrote the situation, and this is plain
matching.
"""

from __future__ import annotations

from collections.abc import Iterable

from agentlint.probes.matters import Matters, WhatMatters
from agentlint.trace.model import Trace
from agentlint.trace.terms import ActionKind, Terms

_KINDS = {"skill": ActionKind.SKILL, "agent": ActionKind.AGENT, "program": ActionKind.SHELL}


def _same_name(named: str, done: str) -> bool:
    """A skill or agent named one way and invoked another: equal, or one is the other with a package's prefix
    (``brainstorming`` and ``superpowers:brainstorming``)."""
    return named == done or done.endswith(f":{named}") or named.endswith(f":{done}")


def does(thing: str, trace: Trace, terms: Terms) -> bool:
    """Whether any action of the run did ``thing`` (``matters.THING``'s form)."""
    kind, _, name = thing.partition(":")
    for action in trace.actions:
        said = terms(action)
        if kind == "tool":
            if said.name == name:
                return True
        elif kind == "file":
            if said.kind in (ActionKind.FILE, ActionKind.SEARCH) and any(
                    t == name or t.endswith(f"/{name.lstrip('./')}") for t in said.targets):
                return True
        elif said.kind is _KINDS.get(kind):
            if name == "*" and kind in ("skill", "agent"):
                return True
            if kind == "program" and name in said.targets:
                return True
            if kind in ("skill", "agent") and any(_same_name(name, t) for t in said.targets):
                return True
    return False


def ran(what: WhatMatters, trace: Trace, terms: Terms) -> bool:
    """Whether one run did what ``what`` is about: its thing for ``tool_present`` and ``targets``; either choice for
    ``which_tools`` (the situation is about choosing between them); both for ``order`` (the order is what's compared)."""
    assert what.uses is not None
    if what.kind is Matters.ORDER:
        return does(what.uses, trace, terms) and what.then is not None and does(what.then, trace, terms)
    if what.kind is Matters.WHICH_TOOLS and what.instead_of:
        return does(what.uses, trace, terms) or does(what.instead_of, trace, terms)
    return does(what.uses, trace, terms)


def exercised(what: WhatMatters | None, traces: Iterable[Trace], terms: Terms) -> bool | None:
    """Whether any of a situation's runs did what its "what matters" names; None when it names nothing to check (a
    situation written before 2026-10-07)."""
    if what is None or what.uses is None:
        return None
    return any(ran(what, trace, terms) for trace in traces)
