"""The strict reply check: what fits is kept verbatim, what doesn't is rejected and counted, nothing is repaired."""

from __future__ import annotations

import pytest

from agentlint.probes.matters import THING, Matters, WhatMatters, read
from agentlint.probes.validate import (Checked, Limits, Reason, Rejection, Situation, same_request, schema, situations,
                                       text_problem)

GOOD = "Find every place we call the billing API in this repo."
M = {"kind": "which_tools", "uses": "skill:*"}  # a menu choice naming its thing (2026-10-07)
MO = {"kind": "order", "uses": "program:acme", "then": "tool:Write"}
THING_SCHEMA = {"type": "string", "pattern": f"^(?:{THING.pattern})$"}


def reasons(checked: Checked) -> list[tuple[Reason, int | None]]:
    return [(r.reason, r.index) for r in checked.rejected]


def test_a_reply_that_fits_is_kept_verbatim_in_order() -> None:
    texts = ["  " + GOOD, "Why does the login test fail on CI?\nIt passes locally.", "Rename the config loader."]
    checked = situations({"situations": [{"text": t, "matters": M} for t in texts], "note": "three"})
    assert checked.texts == tuple(texts)
    assert checked.note == "three" and checked.accepted and checked.rejected == ()


def test_an_empty_list_with_a_note_fits_and_the_note_is_optional() -> None:
    assert situations({"situations": [], "note": "too vague to test"}) == Checked((), "too vague to test", ())
    assert situations({"situations": []}) == Checked((), None, ())
    assert situations({"situations": [], "note": None}).accepted


@pytest.mark.parametrize("data, reason", [
    ([{"text": GOOD, "matters": M}], Reason.NOT_AN_OBJECT),
    ("{}", Reason.NOT_AN_OBJECT),
    (None, Reason.NOT_AN_OBJECT),
    ({}, Reason.NO_SITUATIONS),
    ({"situations": {"text": GOOD, "matters": M}}, Reason.NOT_A_LIST),
    ({"situations": "Find it."}, Reason.NOT_A_LIST),
    ({"situations": [{"text": GOOD, "matters": M}], "claim": None}, Reason.UNKNOWN_FIELD),
    ({"situations": [], "note": 3}, Reason.BAD_NOTE),
    ({"situations": [], "note": "x" * 1001}, Reason.NOTE_TOO_LONG),
])
def test_a_reply_of_the_wrong_shape_is_rejected_whole(data: object, reason: Reason) -> None:
    checked = situations(data)
    assert not checked.accepted
    assert checked.texts == () and checked.note is None
    assert reason in checked.counts()


def test_too_many_situations_reject_the_reply_instead_of_being_cut_to_the_count() -> None:
    four = {"situations": [{"text": f"{GOOD} Variant {n}.", "matters": MO} for n in range(4)]}
    assert reasons(situations(four)) == [(Reason.TOO_MANY, None)]
    assert len(situations(four, Limits(most=4)).situations) == 4


def test_every_whole_reply_problem_is_counted() -> None:
    checked = situations({"items": [], "note": 1})
    assert checked.counts() == {Reason.UNKNOWN_FIELD: 1, Reason.NO_SITUATIONS: 1, Reason.BAD_NOTE: 1}


@pytest.mark.parametrize("item, reason", [
    ("Find the billing calls.", Reason.NOT_A_SITUATION),
    ({"text": GOOD, "fixture": "repo-small", "matters": M}, Reason.NOT_A_SITUATION),
    ({"situation": GOOD}, Reason.NOT_A_SITUATION),
    ({"text": GOOD}, Reason.NOT_A_SITUATION),  # no matters
    ({"text": GOOD + " Twice.", "matters": "WHICH_TOOLS"}, Reason.BAD_MATTERS),  # exactly a menu value
    ({"text": GOOD + " Twice.", "matters": " order"}, Reason.BAD_MATTERS),
    ({"text": GOOD + " Twice.", "matters": "style"}, Reason.BAD_MATTERS),
    ({"text": GOOD + " Twice.", "matters": None}, Reason.BAD_MATTERS),
    ({"text": GOOD + " Twice.", "matters": ["order"]}, Reason.BAD_MATTERS),
    ({"text": 42, "matters": M}, Reason.TEXT_NOT_A_STRING),
    ({"text": True, "matters": M}, Reason.TEXT_NOT_A_STRING),
    ({"text": None, "matters": M}, Reason.TEXT_NOT_A_STRING),
    ({"text": "   \n  ", "matters": M}, Reason.TOO_SHORT),
    ({"text": "Fix it.", "matters": M}, Reason.TOO_SHORT),
    ({"text": "x" * 601, "matters": M}, Reason.TOO_LONG),
    ({"text": GOOD + "\x00", "matters": M}, Reason.CONTROL_CHARACTER),
    ({"text": GOOD + "\x1b[31m", "matters": M}, Reason.CONTROL_CHARACTER),
    ({"text": GOOD + "\rThen run the tests.", "matters": M}, Reason.CONTROL_CHARACTER),  # a lone CR rewrites the line it ends
    ({"text": "Use search\u200b_files to find X", "matters": M}, Reason.INVISIBLE_CHARACTER),
    ({"text": "Find the \u202ebilling\u202c module now", "matters": M}, Reason.INVISIBLE_CHARACTER),
    ({"text": GOOD + "\u2028Then run the tests.", "matters": M}, Reason.INVISIBLE_CHARACTER),
    ({"text": GOOD + "\u2029Then run the tests.", "matters": M}, Reason.INVISIBLE_CHARACTER),
    ({"text": "\ufeff" + GOOD, "matters": M}, Reason.INVISIBLE_CHARACTER),
    ({"text": "Use search\u200d_files to find X", "matters": M}, Reason.INVISIBLE_CHARACTER),
    ({"text": GOOD + "\ue000", "matters": M}, Reason.INVISIBLE_CHARACTER),
])
def test_a_situation_that_does_not_fit_is_rejected_alone(item: object, reason: Reason) -> None:
    checked = situations({"situations": [{"text": GOOD, "matters": M}, item]})
    assert checked.accepted
    assert checked.texts == (GOOD,)
    assert reasons(checked) == [(reason, 1)]


def test_duplicates_are_the_same_words_whatever_the_case_spacing_or_punctuation() -> None:
    texts = [GOOD, "find every place we call the  billing API in this repo", "FIND EVERY PLACE WE CALL THE BILLING "
             "API IN THIS REPO!", "Find every place we call the payments API in this repo."]
    checked = situations({"situations": [{"text": t, "matters": M} for t in texts]}, Limits(most=4))
    assert checked.texts == (GOOD, texts[3])
    assert reasons(checked) == [(Reason.DUPLICATE, 1), (Reason.DUPLICATE, 2)]
    assert checked.rejected[0].detail == "repeats situation 0"
    assert same_request("Run it,  now!") == same_request("run it now")


def test_a_rejected_situation_does_not_count_as_seen_for_duplicates() -> None:
    checked = situations({"situations": [{"text": GOOD + "\x00", "matters": M}, {"text": GOOD, "matters": M}]})
    assert checked.texts == (GOOD,)
    assert reasons(checked) == [(Reason.CONTROL_CHARACTER, 0)]


def test_the_schema_says_what_the_check_enforces() -> None:
    limits = Limits(most=5, min_chars=12, max_chars=300, max_note_chars=200)
    s = schema(limits)
    assert s["additionalProperties"] is False and s["required"] == ["situations"]
    assert set(s["properties"]) == {"situations", "note"}
    situation = s["properties"]["situations"]
    assert situation["maxItems"] == 5
    assert situation["items"]["additionalProperties"] is False
    assert situation["items"]["required"] == ["text", "matters"]
    assert situation["items"]["properties"] == {
        "text": {"type": "string", "minLength": 12, "maxLength": 300},
        "matters": {"anyOf": [  # one shape per kind (2026-10-07)
            {"type": "object", "additionalProperties": False, "required": ["kind", "uses", "then"],
             "properties": {"kind": {"type": "string", "enum": ["order"]}, "uses": THING_SCHEMA, "then": THING_SCHEMA}},
            {"type": "object", "additionalProperties": False, "required": ["kind", "uses"],
             "properties": {"kind": {"type": "string", "enum": ["which_tools"]}, "uses": THING_SCHEMA,
                            "instead_of": THING_SCHEMA}},
            {"type": "object", "additionalProperties": False, "required": ["kind", "uses"],
             "properties": {"kind": {"type": "string", "enum": ["targets", "tool_present"]}, "uses": THING_SCHEMA}}]}}
    assert s["properties"]["note"]["maxLength"] == 200


def test_limits_and_rejections_refuse_nonsense() -> None:
    for bad in (dict(most=0), dict(min_chars=0), dict(min_chars=10, max_chars=5), dict(max_note_chars=-1)):
        with pytest.raises(ValueError):
            Limits(**bad)
    with pytest.raises(ValueError):
        Rejection(Reason.TOO_MANY, 2)
    with pytest.raises(ValueError):
        Rejection(Reason.DUPLICATE, None)


def test_a_crlf_line_ending_tab_and_emoji_joiner_are_kept_verbatim() -> None:
    texts = ["Fix the build please.\r\nThen run tests.", "Fix the lint please.\n\tThen run tests.",
             "Add a \U0001f468\u200d\U0001f469\u200d\U0001f467 icon to the family page",
             "Add a \U0001f3f3\ufe0f\u200d\U0001f308 flag to the settings page"]
    checked = situations({"situations": [{"text": t, "matters": M} for t in texts]}, Limits(most=4))
    assert checked.texts == tuple(texts)
    assert checked.rejected == ()


def test_a_rejected_character_is_named_with_its_position() -> None:
    checked = situations({"situations": [{"text": "Use search\u200b_files to find X", "matters": M}]})
    assert checked.rejected[0].detail == "U+200B at 10"


@pytest.mark.parametrize("first, second", [
    ("Port this module to C", "Port this module to C++"),
    ("Open src/a.py and fix it", "Open src-a.py and fix it"),
    ("Rename f() to g() in utils", "Rename f to g in utils"),
    ("\U0001f680\U0001f680\U0001f680\U0001f680\U0001f680\U0001f680\U0001f680\U0001f680\U0001f680\U0001f680",
     "\U0001f41b\U0001f41b\U0001f41b\U0001f41b\U0001f41b\U0001f41b\U0001f41b\U0001f41b\U0001f41b\U0001f41b"),
    ("?!?!?!?!?!?!", "!?!?!?!?!?!?"),
])
def test_requests_that_differ_in_symbols_are_not_duplicates(first: str, second: str) -> None:
    checked = situations({"situations": [{"text": first, "matters": M}, {"text": second, "matters": M}]})
    assert checked.texts == (first, second)
    assert same_request(first) and same_request(second)


def test_sentence_punctuation_and_quotes_at_word_edges_do_not_make_a_new_request() -> None:
    assert same_request('Port this to C. "Now," she said: `quickly`!') == same_request(
        "port this to c now she said quickly")
    assert same_request("Port it to C++ .") == same_request("port it to c++")


# Default-ignorable code points outside Cf: each is drawn as nothing, so each would hide a name from the guard.
@pytest.mark.parametrize("ch", ["\u034f", "\ufe0f", "\ufe00", "\U000e0100", "\u3164", "\uffa0", "\u115f", "\u17b4",
                                "\u180b", "\u2065", "\U000e0041", "\u00ad"])
def test_a_default_ignorable_character_between_letters_is_rejected(ch: str) -> None:
    checked = situations({"situations": [{"text": "Use search" + ch + "_files to find X", "matters": M}]})
    assert checked.texts == ()
    assert reasons(checked) == [(Reason.INVISIBLE_CHARACTER, 0)]
    assert checked.rejected[0].detail == f"U+{ord(ch):04X} at 10"


@pytest.mark.parametrize("text", [
    "Add a \u2764\ufe0f reaction button to the comments",  # a variation selector after a symbol
    "Show \u2194\ufe0e arrows between the two panes",  # text presentation of a math symbol
    "Put a 1\ufe0f\u20e3 badge on the first step",  # a keycap
    "Use the \U0001f3f4\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f flag for the locale",  # tags
    "Flag it \U0001f3f4\U000e0075\U000e0073\U000e0063\U000e0061\U000e007f at the end",  # usca
    "Mark \U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f\U0001f3f4\U000e0067\U000e0062"
    "\U000e0077\U000e006c\U000e0073\U000e007f both",  # two flags in a row
    "Add a \U0001f469\U0001f3fd\u200d\U0001f4bb avatar to the team page",  # joiner after a skin tone
    "Mark it \u203c\ufe0f in the release notes",  # emoji punctuation
])
def test_the_selectors_joiners_and_tags_of_an_emoji_stay(text: str) -> None:
    checked = situations({"situations": [{"text": text, "matters": M}]})
    assert checked.texts == (text,)


@pytest.mark.parametrize("text", [
    "Use search_\ufe0ffiles to find X",  # after an underscore: inside a name
    "Use docs/\ufe0f03-checklist.md for it",  # after a slash: inside a path
    "Set a=\ufe0fb in the config file",  # after an ASCII symbol
    "Use search\u200d\U0001f600 to find X",  # a joiner after a letter
    "Use the \U000e0067\U000e0062 tags on their own here",  # tags with no flag before them
])
def test_an_emoji_exception_does_not_shelter_a_name(text: str) -> None:
    assert reasons(situations({"situations": [{"text": text, "matters": M}]})) == [(Reason.INVISIBLE_CHARACTER, 0)]


def _tags(ascii_text: str) -> str:
    return "".join(chr(0xE0000 + ord(c)) for c in ascii_text)


@pytest.mark.parametrize("tags", [
    _tags("USE SEARCH_FILES NOW"),  # capitals, a space, an underscore, and no cancel tag
    _tags("USE SEARCH_FILES NOW") + "\U000e007f",  # the same, cancelled
    _tags("GBSCT") + "\U000e007f",  # uppercase letters
    _tags("gb sct") + "\U000e007f",  # a space
    _tags("gbsct"),  # no cancel tag
    _tags("g") + "\U000e007f",  # too short
    _tags("usesearchfiles") + "\U000e007f",  # too long
    "\U000e007f",  # a cancel tag alone
    _tags("gbsct") + "\U000e007f" + _tags("x"),  # a tag after the cancel tag
])
def test_tags_after_a_black_flag_stay_only_as_a_subdivision_flag(tags: str) -> None:
    checked = situations({"situations": [{"text": "Find the TODO comments in the repo \U0001f3f4" + tags, "matters": M}]})
    assert checked.texts == ()
    assert reasons(checked) == [(Reason.INVISIBLE_CHARACTER, 0)]


def test_a_default_ignorable_character_kept_would_have_hidden_the_name_from_the_guard() -> None:
    from agentlint.probes.contamination import guard
    text = "Use search\u034f_files to find X"
    assert guard("Run `search_files` first.", []).hits(text) == ()  # why the reply check must reject it
    assert situations({"situations": [{"text": text, "matters": M}]}).situations == ()


def test_each_kept_situation_carries_what_matters_in_it_a_menu_choice_naming_its_thing() -> None:
    reply = {"situations": [{"text": GOOD, "matters": {"kind": "targets", "uses": "file:docs/notes.md"}},
                            {"text": "Rename the config loader.", "matters": MO}]}
    assert situations(reply).situations == (
        Situation(GOOD, WhatMatters(Matters.TARGETS, "file:docs/notes.md")),
        Situation("Rename the config loader.", WhatMatters(Matters.ORDER, "program:acme", then="tool:Write")))


def fits(matters: dict, shapes: list[dict]) -> bool:
    """Whether ``matters`` fits one of the schema's shapes, by what they say: the kind, the fields required, no other."""
    return any(matters.get("kind") in shape["properties"]["kind"]["enum"] and set(shape["required"]) <= set(matters)
               and set(matters) <= set(shape["properties"]) for shape in shapes)


def test_the_schema_has_one_shape_per_kind_so_order_without_then_doesnt_fit() -> None:
    """2026-10-07: the generator wrote an order with no then, which the check could only drop; the schema now says so
    first, one shape per kind (anyOf)."""
    shapes = schema()["properties"]["situations"]["items"]["properties"]["matters"]["anyOf"]
    kinds = [kind for shape in shapes for kind in shape["properties"]["kind"]["enum"]]
    assert sorted(kinds) == sorted(m.value for m in Matters)  # every kind, once
    assert fits({"kind": "order", "uses": "program:acme", "then": "tool:Write"}, shapes)
    assert not fits({"kind": "order", "uses": "program:acme"}, shapes)  # order needs then
    assert not fits({"kind": "targets", "uses": "skill:*", "then": "tool:Read"}, shapes)  # only order takes then
    assert fits({"kind": "which_tools", "uses": "skill:a", "instead_of": "skill:b"}, shapes)
    assert not fits({"kind": "tool_present", "uses": "skill:a", "instead_of": "skill:b"}, shapes)
    for kind in ("targets", "tool_present", "which_tools"):
        assert fits({"kind": kind, "uses": "skill:*"}, shapes)


@pytest.mark.parametrize(("matters", "why"), [
    ("which_tools", "not an object with a kind"),  # the menu choice alone, as before 2026-10-07: names nothing
    ({"kind": "style", "uses": "skill:*"}, "kind 'style' is not one from the menu"),
    ({"kind": "order", "uses": "skill:*"}, "order needs then"),
    ({"kind": "targets", "uses": "skill:*", "then": "tool:Read"}, "targets takes no then"),
    ({"kind": "tool_present", "uses": "the acme CLI"}, "uses 'the acme CLI' is not a thing"),
    ({"kind": "tool_present", "uses": "tool:*"}, "uses 'tool:*' is not a thing"),  # only skills and agents take any
])
def test_what_matters_must_be_a_menu_choice_naming_a_thing(matters: object, why: str) -> None:
    checked = situations({"situations": [{"text": GOOD, "matters": matters}]})
    assert reasons(checked) == [(Reason.BAD_MATTERS, 0)] and checked.rejected[0].detail.startswith(why)


def test_a_thing_the_configuration_doesnt_have_rejects_the_situation() -> None:
    known = {"skill:*", "program:acme"}.__contains__
    reply = {"situations": [{"text": GOOD, "matters": {"kind": "tool_present", "uses": "skill:made-up"}},
                            {"text": "Rename the config loader.", "matters": {"kind": "targets", "uses": "program:acme"}}]}
    checked = situations(reply, known=known)
    assert checked.texts == ("Rename the config loader.",)
    assert reasons(checked) == [(Reason.UNKNOWN_THING, 0)] and checked.rejected[0].detail == "skill:made-up"


def test_what_matters_is_stored_as_json_and_read_back_and_an_old_menu_value_still_reads() -> None:
    what = WhatMatters(Matters.WHICH_TOOLS, "skill:data:sql-query", instead_of="skill:analytics:sql-query")
    assert read(what.stored()) == what and what.words() == "skill:data:sql-query instead of skill:analytics:sql-query"
    assert read("order") == WhatMatters(Matters.ORDER) and read("order").words() == ""  # written before: names nothing
    assert all(m.meaning for m in Matters)


def test_text_problem_judges_one_text_by_the_reply_rules() -> None:
    assert text_problem(GOOD) is None
    assert text_problem("Fix it.") == (Reason.TOO_SHORT, "7 characters")
    assert text_problem("Use search\u200b_files to find X") == (Reason.INVISIBLE_CHARACTER, "U+200B at 10")
    assert text_problem(3)[0] is Reason.TEXT_NOT_A_STRING
