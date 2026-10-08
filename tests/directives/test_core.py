"""Directives' pure core: the reply's strict check (an exact quote in the text, a thing the configuration has) and the
pairing — contradiction candidates across sources, repeats across text in every session."""

from __future__ import annotations

from agentlint.directives.model import Directive, Polarity, read, stored
from agentlint.directives.pair import Occurrence, contradictions, key, repeats
from agentlint.directives.prompt import MOST, prompt
from agentlint.directives.validate import Reason, check, names_thing, schema

TEXT = """# Exploring

ALWAYS use the code-search agent over the Explore agent when exploring the codebase.
Never run `grep -r` yourself:
use search_files instead."""



def known(thing: str) -> bool:
    kind = thing.partition(":")[0]
    return thing in {"agent:code-search", "agent:Explore", "agent:*", "skill:*"} if kind in ("skill", "agent") else True


def item(polarity: str, thing: str, quote: str, when: str | None = None) -> dict:
    return {"polarity": polarity, "thing": thing, "quote": quote, "when": when}


def test_a_directive_counts_only_with_a_quote_from_the_text_and_a_thing_the_configuration_has() -> None:
    reply = {"directives": [
        item("prefer", "agent:code-search", "ALWAYS use the code-search agent over the Explore agent", "exploring the codebase"),
        item("avoid", "agent:Explore", "use the code-search agent over the Explore agent"),
        item("must_not", "program:grep", "Never run `grep -r` yourself: use search_files"),  # across a line break
        item("must", "skill:made-up", "ALWAYS use the code-search agent"),  # a skill nobody has
        item("must", "tool:Read", "always read every file first"),  # not in the text
        item("sometimes", "tool:Read", "Never run"),
        item("must", "the search tool", "Never run `grep -r`"),
        item("prefer", "agent:code-search", "ALWAYS use the code-search agent", "exploring the codebase"),  # again
    ]}
    checked = check(reply, TEXT, known)
    assert [(d.polarity, d.thing) for d in checked.directives] == [
        (Polarity.PREFER, "agent:code-search"), (Polarity.AVOID, "agent:Explore"), (Polarity.MUST_NOT, "program:grep")]
    assert checked.directives[2].quote == "Never run `grep -r` yourself: use search_files"  # whitespace squashed
    assert [(r.reason, r.index) for r in checked.rejected] == [
        (Reason.UNKNOWN_THING, 3), (Reason.QUOTE_NOT_IN_TEXT, 4), (Reason.BAD_POLARITY, 5), (Reason.BAD_THING, 6),
        (Reason.DUPLICATE, 7)]


def test_a_reply_of_the_wrong_shape_is_rejected_whole_and_none_is_a_normal_answer() -> None:
    assert check({"directives": []}, TEXT, known).directives == ()
    assert check({"directives": [], "note": None}, TEXT, known).rejected == ()
    assert [r.reason for r in check({"items": []}, TEXT, known).rejected] == [Reason.NOT_A_REPLY]
    many = {"directives": [item("must", "tool:Read", "Exploring")] * (MOST + 1)}
    assert [r.reason for r in check(many, TEXT, known).rejected] == [Reason.TOO_MANY]
    asked = schema()["properties"]["directives"]
    assert asked["maxItems"] == MOST and asked["items"]["required"] == ["polarity", "thing", "quote", "when"]
    assert "copied exactly" in prompt(TEXT, "AGENTS.md", "in every session") and TEXT in prompt(TEXT, "x", "y")


def test_directives_round_trip_as_stored() -> None:
    given = (Directive(Polarity.MUST, "skill:*", "invoke the skill", "if one might apply"),)
    assert read(stored(given)) == given


def at(polarity: Polarity, thing: str, source: str, every: bool = True, when: str | None = None) -> Occurrence:
    return Occurrence(Directive(polarity, thing, f"{polarity.value} {thing}", when), source, f"{source}#s", 3, every)


def test_each_pair_of_quotes_toward_and_away_from_a_thing_in_two_sources_is_a_candidate() -> None:
    found = contradictions([
        at(Polarity.MUST_NOT, "agent:*", "system-prompt", when="unless the user asks"),  # don't use subagents unless asked
        at(Polarity.MUST, "agent:pkg:explorer", "hook", when="for codebase exploration"),  # a named one: always a candidate
        at(Polarity.MUST, "skill:*", "hook-2"),  # if a skill might apply, invoke it: always applies
        at(Polarity.AVOID, "skill:*", "AGENTS.md", when="for small edits"),
        at(Polarity.PREFER, "program:rg", "skill-body", every=False),
        at(Polarity.MUST_NOT, "program:RG", "other-body", every=False),
        at(Polarity.PREFER, "tool:Grep", "same"), at(Polarity.AVOID, "tool:Grep", "same"),  # one source qualifying itself
        at(Polarity.MUST, "agent:*", "a", when="for research"), at(Polarity.MUST_NOT, "agent:*", "b", when="for edits"),
    ])
    assert [(c.toward, c.away, c.for_it.source, c.against.source, c.every_session) for c in found] == [
        ("agent:explorer", "agent:*", "hook", "b", True), ("agent:explorer", "agent:*", "hook", "system-prompt", True),
        ("skill:*", "skill:*", "hook-2", "AGENTS.md", True), ("program:rg", "program:rg", "skill-body", "other-body", False)]
    # "agent:* for research" against "agent:* for edits" (two scopes) and against "unless asked": neither always applies
    assert key("agent:pkg:explorer") == "agent:explorer" and key("file:./docs/a.md") == "file:docs/a.md"


def test_a_quote_must_name_its_thing() -> None:
    assert names_thing("agent:*", "Delegate exploration to subagents") and names_thing("skill:*", "invoke the skill")
    assert not names_thing("agent:*", "AI generates inputs, never evaluates outputs")
    assert names_thing("agent:pkg:code_search", "ALWAYS use the code-search agent") and names_thing("tool:Read", "read it")
    assert not names_thing("tool:Read", "already done") and names_thing("file:docs/03-checklist.md", "update 03-checklist.md")
    assert names_thing("program:git", "Never run `git push`") and not names_thing("program:git", "a digit")


# The user's first real reading (2026-10-07): fourteen quotes the model labelled agent:*, none about whether to use
# subagents, paired into one "contradiction". The quote check and the pairing rules leave nothing of it.
FIRST_READING = [
    ("must", "AI generates inputs", None), ("must", "When in doubt, report nothing", "when uncertain about findings"),
    ("must", "Any code path that spawns runs needs a run-count ceiling and must be resumable.", "when spawning agent runs"),
    ("must", "keep doing other useful work until its completion notification arrives", "after launching code-search agent"),
    ("must", "When you cannot proceed without its answer, end your turn", "when unable to proceed without agent answer"),
    ("must", "When you launch multiple agents for independent work, send them in a single message with multiple tool uses "
             "so they run concurrently.", "when launching multiple agents for independent work"),
    ("must_not", "never evaluates outputs", None), ("must_not", "A model never decides whether a test passed", "when evaluating tests"),
    ("avoid", "Anything that needs no AI and no agent runs ships before anything that does.", "when non-agent alternatives exist"),
    ("must_not", "no loops inside a run", "in workflow runs"), ("must_not", "≤ ~15 agents and ~30 min per run", "per workflow run"),
    ("must_not", "do NOT re-resolve them in skills or agents", "when using configuration variables"),
    ("must_not", "Do not deliver a final answer or post a verdict while a code-search agent you launched has not reported",
                 "with pending code-search agent"),
    ("must_not", "it does not guess", "when evaluating submissions"),
]


def test_the_first_real_readings_agent_quotes_make_no_candidate() -> None:
    kept = []
    for n, (polarity, quote, when) in enumerate(FIRST_READING):
        checked = check({"directives": [item(polarity, "agent:*", quote, when)]}, quote, known)
        kept += [Occurrence(d, f"source-{n % 6}", "s", 1, True) for d in checked.directives]
    assert len(kept) < len(FIRST_READING)  # the quotes that don't say agent, subagent or delegate are thrown out
    assert contradictions(kept) == []


def test_a_repeat_is_the_same_directive_in_two_sources_in_every_session() -> None:
    found = repeats([
        at(Polarity.MUST, "tool:Read", "AGENTS.md"), at(Polarity.PREFER, "tool:Read", "hook"),  # both toward Read
        at(Polarity.MUST, "tool:Read", "skill-body", every=False),  # loads only when invoked: not counted
        at(Polarity.MUST, "tool:Bash", "AGENTS.md"), at(Polarity.MUST, "tool:Bash", "AGENTS.md"),  # one source
        at(Polarity.MUST_NOT, "tool:Read", "x", every=False),
    ])
    assert [(r.thing, r.positive, sorted({o.source for o in r.occurrences})) for r in found] == [
        ("tool:Read", True, ["AGENTS.md", "hook"])]


def test_a_newer_reader_version_makes_what_an_older_one_read_stale_and_replaces_it() -> None:
    from datetime import UTC, datetime

    from agentlint.store import connect
    from agentlint.store import directives as store

    db, now = connect(), datetime(2026, 10, 8, tzinfo=UTC)
    old = (Directive(Polarity.MUST, "agent:*", "AI generates inputs"),)
    store.save(db, "sha256:a", 1, old, "haiku", now)
    assert store.load(db, ["sha256:a"], 2) == {} and store.versions(db, ["sha256:a", "sha256:b"]) == {"sha256:a": 1}
    store.save(db, "sha256:a", 2, (), "haiku", now)  # read again under the new version: it found nothing
    assert store.load(db, ["sha256:a"], 2)["sha256:a"].directives == () and store.load(db, ["sha256:a"], 1) == {}
