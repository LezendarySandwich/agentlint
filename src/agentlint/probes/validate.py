"""The strict check every generator reply passes before anything of it is kept (04 "Checking a reply").

A reply fits or it doesn't. Nothing is repaired: no trimming, no truncating a long list, no coercing a number to a
string. A reply whose shape is wrong is rejected whole; a situation that doesn't fit is rejected alone, and every
rejection is counted with its reason, so a generator that keeps missing shows up as numbers, not as silence.
Standard library only: the schema handed to the model and this check say the same thing, and this is what decides.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agentlint.probes.matters import THING, Matters, WhatMatters, parsed
from agentlint.probes.matters import problem as matters_problem


@dataclass(frozen=True)
class Limits:
    most: int = 3  # situations asked for; more is a reply that didn't follow the request, rejected whole
    min_chars: int = 10  # shorter isn't a request anyone types to a coding agent
    max_chars: int = 600  # a typed request, not a document
    max_note_chars: int = 1000

    def __post_init__(self) -> None:
        if self.most < 1:
            raise ValueError("Limits.most must be at least 1")
        if not 1 <= self.min_chars <= self.max_chars:
            raise ValueError("Limits needs 1 <= min_chars <= max_chars")
        if self.max_note_chars < 0:
            raise ValueError("Limits.max_note_chars must not be negative")


def schema(limits: Limits = Limits()) -> dict[str, Any]:
    """The JSON schema a generator is asked to fit: a list of {text, matters} and an optional note, nothing else.
    ``matters`` is one choice of the fixed menu (``matters.Matters``) naming its thing (since 2026-10-07): ``uses``,
    and for ``order`` what follows it (``then``), for ``which_tools`` optionally what it shouldn't pick (``instead_of``),
    each ``<kind>:<name>`` (``matters.THING``). One shape per kind (``anyOf``), so ``order`` without ``then`` doesn't
    fit the schema at all (2026-10-07: the generator wrote one, and the check could only drop it)."""
    thing = {"type": "string", "pattern": f"^(?:{THING.pattern})$"}

    def shape(kinds: list[str], required: list[str], optional: list[str] = ()) -> dict[str, Any]:  # type: ignore[assignment]
        return {"type": "object", "additionalProperties": False, "required": ["kind", *required],
                "properties": {"kind": {"type": "string", "enum": kinds},
                               **{name: thing for name in (*required, *optional)}}}

    matters = {"anyOf": [shape([Matters.ORDER.value], ["uses", "then"]),
                         shape([Matters.WHICH_TOOLS.value], ["uses"], ["instead_of"]),
                         shape([m.value for m in Matters if m not in (Matters.ORDER, Matters.WHICH_TOOLS)], ["uses"])]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["situations"],
        "properties": {
            "situations": {
                "type": "array",
                "maxItems": limits.most,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["text", "matters"],
                    "properties": {"text": {"type": "string", "minLength": limits.min_chars,
                                            "maxLength": limits.max_chars},
                                   "matters": matters},
                },
            },
            "note": {"type": ["string", "null"], "maxLength": limits.max_note_chars},
        },
    }


class Reason(Enum):
    # the whole reply
    NOT_AN_OBJECT = "the reply is not a JSON object"
    UNKNOWN_FIELD = "the reply has a field the schema doesn't allow"
    NO_SITUATIONS = "the reply has no situations field"
    NOT_A_LIST = "situations is not a list"
    TOO_MANY = "more situations than were asked for"
    BAD_NOTE = "note is neither a string nor null"
    NOTE_TOO_LONG = "note is longer than allowed"
    # one situation
    NOT_A_SITUATION = "a situation is not an object with exactly the fields text and matters"
    TEXT_NOT_A_STRING = "a situation's text is not a string"
    TOO_SHORT = "a situation is shorter than allowed (blank counts)"
    TOO_LONG = "a situation is longer than allowed"
    CONTROL_CHARACTER = "a situation holds a control character other than a newline (LF or CRLF) or tab"
    INVISIBLE_CHARACTER = "a situation holds an invisible character (format, default-ignorable, line or paragraph separator)"
    BAD_MATTERS = "a situation's matters is not a menu choice naming its thing"
    UNKNOWN_THING = "a situation's matters names something this configuration doesn't have"
    DUPLICATE = "a situation repeats an earlier one"

    @property
    def whole_reply(self) -> bool:
        return self in _WHOLE_REPLY


_WHOLE_REPLY = frozenset({Reason.NOT_AN_OBJECT, Reason.UNKNOWN_FIELD, Reason.NO_SITUATIONS, Reason.NOT_A_LIST,
                          Reason.TOO_MANY, Reason.BAD_NOTE, Reason.NOTE_TOO_LONG})
_FIELDS = frozenset({"situations", "note"})
_EDGE_PUNCTUATION = ".,!?;:\"'`\u2018\u2019\u201c\u201d"  # sentence punctuation and quotes at a word's edges
_ZWJ = "\u200d"


@dataclass(frozen=True)
class Rejection:
    reason: Reason
    index: int | None  # the situation's position in the reply; None when the whole reply is rejected
    detail: str = ""

    def __post_init__(self) -> None:
        if (self.index is None) != self.reason.whole_reply:
            raise ValueError(f"{self.reason.name} needs index {'None' if self.reason.whole_reply else 'set'}")


@dataclass(frozen=True)
class Situation:
    text: str  # verbatim, as the model wrote it
    matters: WhatMatters  # the menu choice and the thing it names: what its runs are checked to have done


@dataclass(frozen=True)
class Checked:
    situations: tuple[Situation, ...]  # in the reply's order
    note: str | None
    rejected: tuple[Rejection, ...]

    @property
    def texts(self) -> tuple[str, ...]:
        return tuple(s.text for s in self.situations)

    @property
    def accepted(self) -> bool:
        """Whether the reply's shape fit; individual situations may still have been rejected."""
        return not any(r.index is None for r in self.rejected)

    def counts(self) -> Counter[Reason]:
        return Counter(r.reason for r in self.rejected)


def same_request(text: str) -> str:
    """What two situations share when they are the same request: the text case-folded, spacing collapsed, and
    sentence punctuation and quotes stripped from each word's edges. Symbols in or on a word stay (C and C++,
    src/a.py and src-a.py differ), and a word that is all punctuation goes; a text that would leave nothing is its
    own key, so two different texts never meet at an empty one."""
    words = text.casefold().split()
    kept = [w.strip(_EDGE_PUNCTUATION) for w in words]
    return " ".join(w for w in kept if w) or " ".join(words)


def situations(data: object, limits: Limits = Limits(), known: Callable[[str], bool] = lambda thing: True) -> Checked:
    """Check a parsed reply against ``schema(limits)``, and each thing a situation's matters names against ``known``
    (whether the configuration has it). Never raises on what a model wrote."""
    whole = _reply_problems(data, limits)
    if whole:
        return Checked((), None, tuple(whole))
    assert isinstance(data, Mapping)
    kept: list[Situation] = []
    seen: dict[str, int] = {}
    rejected: list[Rejection] = []
    for index, item in enumerate(data["situations"]):
        problem = _item_problem(item, limits)
        if problem is not None:
            rejected.append(Rejection(problem[0], index, problem[1]))
            continue
        what = parsed(item["matters"])
        unknown = [thing for thing in what.things() if not known(thing)]
        if unknown:
            rejected.append(Rejection(Reason.UNKNOWN_THING, index, ", ".join(unknown)[:200]))
            continue
        text = item["text"]
        key = same_request(text)
        if key in seen:
            rejected.append(Rejection(Reason.DUPLICATE, index, f"repeats situation {seen[key]}"))
            continue
        seen[key] = index
        kept.append(Situation(text, what))
    return Checked(tuple(kept), data.get("note"), tuple(rejected))


def _reply_problems(data: object, limits: Limits) -> list[Rejection]:
    if not isinstance(data, Mapping):
        return [Rejection(Reason.NOT_AN_OBJECT, None, type(data).__name__)]
    found = []
    unknown = sorted(str(k) for k in data if k not in _FIELDS)
    if unknown:
        found.append(Rejection(Reason.UNKNOWN_FIELD, None, ", ".join(unknown)))
    if "situations" not in data:
        found.append(Rejection(Reason.NO_SITUATIONS, None))
    elif not isinstance(data["situations"], list):
        found.append(Rejection(Reason.NOT_A_LIST, None, type(data["situations"]).__name__))
    elif len(data["situations"]) > limits.most:
        found.append(Rejection(Reason.TOO_MANY, None, f"{len(data['situations'])} for {limits.most} asked"))
    note = data.get("note")
    if note is not None and not isinstance(note, str):
        found.append(Rejection(Reason.BAD_NOTE, None, type(note).__name__))
    elif isinstance(note, str) and len(note) > limits.max_note_chars:
        found.append(Rejection(Reason.NOTE_TOO_LONG, None, f"{len(note)} characters"))
    return found


def _item_problem(item: object, limits: Limits) -> tuple[Reason, str] | None:
    if not isinstance(item, Mapping) or set(item) != {"text", "matters"}:
        return Reason.NOT_A_SITUATION, type(item).__name__ if not isinstance(item, Mapping) else ", ".join(
            sorted(str(k) for k in item))
    problem = text_problem(item["text"], limits)
    if problem is not None:
        return problem
    wrong = matters_problem(item["matters"])  # exactly the schema: no case folding, no trimming
    if wrong is not None:
        return Reason.BAD_MATTERS, wrong[:200]
    return None


def text_problem(text: object, limits: Limits = Limits()) -> tuple[Reason, str] | None:
    """What is wrong with one situation's text, by the same rules a reply's situations pass; None when nothing is.
    Review uses it for an edited text."""
    if not isinstance(text, str):
        return Reason.TEXT_NOT_A_STRING, type(text).__name__
    if len(text.strip()) < limits.min_chars:
        return Reason.TOO_SHORT, f"{len(text.strip())} characters"
    if len(text) > limits.max_chars:
        return Reason.TOO_LONG, f"{len(text)} characters"
    return _character_problem(text)


def _character_problem(text: str) -> tuple[Reason, str] | None:
    """A control character other than LF, CRLF or tab; or an invisible character that would hide a name from the
    contamination guard while a reader, or the agent, still sees it: a format character (zero-width space, bidi
    override), a line or paragraph separator, a surrogate, private use, or any other Unicode default-ignorable code
    point (combining grapheme joiner, variation selectors, Hangul fillers, tags). The joiners and selectors of an emoji
    stay: a zero-width joiner between two symbols, a variation selector right after a symbol (or a keycap's digit),
    and the tags of a well-formed subdivision flag. Other unassigned code points stay, since a newer emoji may be one
    here."""
    for i, c in enumerate(text):
        category = unicodedata.category(c)
        if category == "Cc":
            if c in "\n\t" or (c == "\r" and text[i + 1:i + 2] == "\n"):
                continue
            return Reason.CONTROL_CHARACTER, f"U+{ord(c):04X} at {i}"
        if (category in ("Cf", "Cs", "Co", "Zl", "Zp") or _default_ignorable(c)) and not _in_emoji(text, i):
            return Reason.INVISIBLE_CHARACTER, f"U+{ord(c):04X} at {i}"
    return None


# Unicode's Default_Ignorable_Code_Point (DerivedCoreProperties.txt): drawn as nothing, whatever their category.
_DEFAULT_IGNORABLE = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
                      (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
                      (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
                      (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))
_EMOJI_PUNCTUATION = frozenset("\u203c\u2049\u3030\u303d")  # punctuation that takes an emoji presentation
_KEYCAP = "\u20e3"
_BLACK_FLAG = "\U0001f3f4"  # a subdivision flag is this, 2 to 6 tag digits or lowercase letters, and a cancel tag
_TAG_FIRST, _CANCEL_TAG = 0xE0020, 0xE007F


def _default_ignorable(c: str) -> bool:
    code = ord(c)
    return any(low <= code <= high for low, high in _DEFAULT_IGNORABLE)


def _emoji_base(c: str) -> bool:
    """A symbol an emoji sequence can build on; an ASCII one (+ = ^ $) never is, so it can't shelter a name."""
    return bool(c) and (c in _EMOJI_PUNCTUATION or (not c.isascii() and unicodedata.category(c) in ("So", "Sm", "Sk",
                                                                                                     "Sc")))


def _in_emoji(text: str, i: int) -> bool:
    c, before, after = text[i], text[i - 1:i], text[i + 1:i + 2]
    if c == _ZWJ:
        return ((_emoji_base(before) or before in ("\ufe0f", _KEYCAP))
                and bool(after) and unicodedata.category(after) == "So")
    if c in ("\ufe0e", "\ufe0f"):
        return _emoji_base(before) or (bool(before) and before in "0123456789#*" and after == _KEYCAP)
    if _TAG_FIRST <= ord(c) <= _CANCEL_TAG:
        return _in_flag(text, i)
    return False


def _in_flag(text: str, i: int) -> bool:
    """Whether the tag at ``i`` is part of a well-formed subdivision flag: the black flag, then 2 to 6 tag digits or
    lowercase tag letters, then the cancel tag. Any other run of tags spells ASCII a model reads and a person can't
    see."""
    j = i
    while j > 0 and _TAG_FIRST <= ord(text[j - 1]) <= _CANCEL_TAG:
        j -= 1
    k = i
    while k < len(text) and _TAG_FIRST <= ord(text[k]) <= _CANCEL_TAG:
        k += 1
    spec, end = text[j:k - 1], text[k - 1]
    return (text[j - 1:j] == _BLACK_FLAG and ord(end) == _CANCEL_TAG and 2 <= len(spec) <= 6
            and all(0xE0030 <= ord(t) <= 0xE0039 or 0xE0061 <= ord(t) <= 0xE007A for t in spec))
