"""Did a situation exercise its target (analysis/exercise.py): any of its runs did the thing its what matters names."""

from __future__ import annotations

from typing import Any

from agentlint.analysis.exercise import does, exercised
from agentlint.probes.matters import Matters, WhatMatters
from agentlint.trace.model import Action, Trace
from agentlint.trace.terms import ActionKind, ActionTerms


def terms(action: Action) -> ActionTerms:
    given = action.input
    if action.tool == "sh":
        return ActionTerms(ActionKind.SHELL, "sh", tuple(given["programs"]))
    if action.tool in ("skill", "agent"):
        return ActionTerms(ActionKind(action.tool), action.tool, (given["name"],))
    if action.tool == "read":
        return ActionTerms(ActionKind.FILE, "read", (given["path"],))
    return ActionTerms(ActionKind.OTHER, action.tool)


def trace(*calls: tuple[str, dict[str, Any]]) -> Trace:
    return Trace("s", "2.0", ("m",), True, tuple(Action(i, t, g, None, None, (), True, False, False)
                                                 for i, (t, g) in enumerate(calls)), ())


def test_a_thing_is_matched_against_a_runs_actions() -> None:
    run = trace(("sh", {"programs": ["ls", "acme"]}), ("skill", {"name": "superpowers:brainstorming"}),
                ("read", {"path": "./docs/notes.md"}), ("WebFetch", {}))
    assert does("program:acme", run, terms) and not does("program:grep", run, terms)
    assert does("skill:brainstorming", run, terms)  # a package's prefix either side
    assert does("skill:*", run, terms) and not does("agent:*", run, terms)
    assert does("file:docs/notes.md", run, terms) and does("tool:WebFetch", run, terms)


def test_a_situation_none_of_whose_runs_did_its_thing_was_not_exercised() -> None:
    # the user's power-engineer hook: "if a skill might apply, invoke it" — three runs that answered in words or ran ls
    what = WhatMatters(Matters.TOOL_PRESENT, "skill:*")
    assert exercised(what, [trace(), trace(("sh", {"programs": ["ls"]})), trace()], terms) is False
    assert exercised(what, [trace(), trace(("skill", {"name": "tdd"}))], terms) is True  # one run is enough


def test_order_needs_both_things_and_which_tools_either() -> None:
    order = WhatMatters(Matters.ORDER, "program:acme", then="tool:Write")
    assert exercised(order, [trace(("sh", {"programs": ["acme"]}))], terms) is False
    assert exercised(order, [trace(("sh", {"programs": ["acme"]}), ("Write", {}))], terms) is True
    pick = WhatMatters(Matters.WHICH_TOOLS, "skill:data:sql", instead_of="skill:analytics:sql")
    assert exercised(pick, [trace(("skill", {"name": "analytics:sql"}))], terms) is True  # it chose: the choice is exercised


def test_a_situation_written_before_things_were_named_has_nothing_to_check() -> None:
    assert exercised(WhatMatters(Matters.ORDER), [trace()], terms) is None
    assert exercised(None, [trace()], terms) is None
