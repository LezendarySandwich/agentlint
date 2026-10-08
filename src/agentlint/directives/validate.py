"""The extraction reply's strict check (12-directives.md, "The check"): never repaired, every rejection counted.

A directive counts only when its quote is in the section's text (whitespace aside: a line break may fall anywhere in a
quote), its thing is one ``matters.THING`` allows and — a skill or an agent — one the configuration has, and its
polarity is on the menu. A model that made a quote up, or named a skill nobody has, loses that directive, and the
diagnostic says so: it never becomes a finding.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agentlint.directives.model import Directive, Polarity
from agentlint.directives.prompt import MOST
from agentlint.probes.matters import THING

QUOTE_MIN, QUOTE_MAX, WHEN_MAX, NOTE_MAX = 6, 300, 120, 1000


def schema() -> dict[str, Any]:
    """The JSON schema the reply is asked to fit."""
    return {
        "type": "object", "additionalProperties": False, "required": ["directives"],
        "properties": {
            "directives": {"type": "array", "maxItems": MOST, "items": {
                "type": "object", "additionalProperties": False, "required": ["polarity", "thing", "quote", "when"],
                "properties": {"polarity": {"type": "string", "enum": [p.value for p in Polarity]},
                               "thing": {"type": "string", "pattern": f"^(?:{THING.pattern})$"},
                               "quote": {"type": "string", "minLength": QUOTE_MIN, "maxLength": QUOTE_MAX},
                               "when": {"type": ["string", "null"], "maxLength": WHEN_MAX}}}},
            "note": {"type": ["string", "null"], "maxLength": NOTE_MAX},
        },
    }


class Reason(Enum):
    NOT_A_REPLY = "the reply is not an object with a directives list and nothing else"
    TOO_MANY = "more directives than were asked for"
    NOT_A_DIRECTIVE = "a directive is not an object with exactly polarity, thing, quote and when"
    BAD_POLARITY = "a directive's polarity is not one from the menu"
    BAD_THING = "a directive's thing is not a skill, agent, tool, program or file written as asked"
    UNKNOWN_THING = "a directive names a skill or agent this configuration doesn't have"
    BAD_QUOTE = "a directive's quote is not text of the allowed length"
    QUOTE_NOT_IN_TEXT = "a directive's quote is not in the section"
    QUOTE_DOESNT_NAME_THING = "a directive's quote doesn't name its thing"
    BAD_WHEN = "a directive's when is neither null nor short text"
    DUPLICATE = "a directive repeats an earlier one of the same section"


@dataclass(frozen=True)
class Rejection:
    reason: Reason
    index: int | None  # the directive's position; None for the whole reply
    detail: str = ""


@dataclass(frozen=True)
class Checked:
    directives: tuple[Directive, ...] = ()
    rejected: tuple[Rejection, ...] = field(default=())


_ANY = {"skill": ("skill",), "agent": ("agent", "delegat")}  # what a quote says to mean any skill, any agent


def names_thing(thing: str, quote: str) -> bool:
    """Whether the quote names its thing (2026-10-08): ``skill:*`` says "skill", ``agent:*`` "agent", "subagent" or
    "delegate"; a named thing its name — a skill or agent's without its package, - and _ alike — a program's, a tool's,
    a file's path or file name, as a word, in any case. The model labelled "AI generates inputs" agent:* once: the
    quote is what holds it to the thing."""
    kind, _, name = thing.partition(":")
    said = quote.lower().replace("_", "-")
    if name == "*":
        return any(word in said for word in _ANY.get(kind, ()))
    if kind == "file":
        names = {name.lower(), name.rsplit("/", 1)[-1].lower()}
    else:
        names = {name.rsplit(":", 1)[-1].lower().replace("_", "-")}
    return any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", said) for n in names if n)


def _squashed(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def check(data: object, text: str, known: Callable[[str], bool]) -> Checked:
    """The directives of a reply for the section ``text`` that pass, and every one that doesn't, with why."""
    if not isinstance(data, dict) or not isinstance(data.get("directives"), list) or set(data) - {"directives", "note"}:
        return Checked(rejected=(Rejection(Reason.NOT_A_REPLY, None),))
    given = data["directives"]
    if len(given) > MOST:
        return Checked(rejected=(Rejection(Reason.TOO_MANY, None, f"{len(given)} of at most {MOST}"),))
    body = _squashed(text)
    kept: list[Directive] = []
    rejected: list[Rejection] = []
    for index, item in enumerate(given):
        if not isinstance(item, dict) or set(item) != {"polarity", "thing", "quote", "when"}:
            rejected.append(Rejection(Reason.NOT_A_DIRECTIVE, index))
            continue
        polarity, thing, quote, when = item["polarity"], item["thing"], item["quote"], item["when"]
        if polarity not in {p.value for p in Polarity}:
            rejected.append(Rejection(Reason.BAD_POLARITY, index, str(polarity)[:40]))
        elif not isinstance(thing, str) or not THING.fullmatch(thing):
            rejected.append(Rejection(Reason.BAD_THING, index, str(thing)[:80]))
        elif not known(thing):
            rejected.append(Rejection(Reason.UNKNOWN_THING, index, thing[:80]))
        elif not isinstance(quote, str) or not QUOTE_MIN <= len(_squashed(quote)) <= QUOTE_MAX:
            rejected.append(Rejection(Reason.BAD_QUOTE, index))
        elif _squashed(quote) not in body:
            rejected.append(Rejection(Reason.QUOTE_NOT_IN_TEXT, index, _squashed(quote)[:120]))
        elif not names_thing(thing, quote):
            rejected.append(Rejection(Reason.QUOTE_DOESNT_NAME_THING, index, f"{thing}: {_squashed(quote)[:100]}"))
        elif when is not None and (not isinstance(when, str) or not when.strip() or len(when) > WHEN_MAX):
            rejected.append(Rejection(Reason.BAD_WHEN, index))
        else:
            directive = Directive(Polarity(polarity), thing, _squashed(quote), _squashed(when) if when else None)
            if any((d.polarity, d.thing, d.when) == (directive.polarity, directive.thing, directive.when) for d in kept):
                rejected.append(Rejection(Reason.DUPLICATE, index))
            else:
                kept.append(directive)
    return Checked(tuple(kept), tuple(rejected))
