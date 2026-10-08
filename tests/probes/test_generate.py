"""Generating a target's situations against a scripted generator: the cache, the checks, the top-up, diagnostics."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from agentlint.config.assemble import assemble
from agentlint.config.model import Ablation, AblationPolicy, Declared
from agentlint.config.taxonomy import Load
from agentlint.probes.fake import FakeGenerator
from agentlint.probes.generate import EditRefused, Kind, generate, listed, probe_id, review, target_hash
from agentlint.probes.generator import GenerationFailed, Reply
from agentlint.probes.matters import Matters, read
from agentlint.probes.prompt import PROMPT_VERSION, prompt
from agentlint.rank.plan import targets
from agentlint.store import ReviewStatus, connect, diagnostics

from ..config.helpers import HARNESS, PROJECT, file_source
from .test_contamination import ENTITIES, SQL_SKILL

T0 = datetime(2026, 10, 4, 1, 0, tzinfo=UTC)
NOTES = "# Notes\n## Search\nAlways use search_files instead of Grep when you look through code.\n"
SKILL = "---\ndescription: SQL\n---\n# Querying\nWrite the query in a scratch file and show the result.\n"
ONE = "Set up a small Python script that counts words in a text file I'll paste in."
TWO = "Plan a command-line todo app in Go and write the first file."
THREE = "Explain how I would look for every TODO comment once I clone our repo here."


def _targets():
    sources = (file_source("notes", NOTES),
               replace(file_source("sql-source", SKILL, load=Load.ON_INVOKE),
                       ablation=AblationPolicy(Ablation.FILE_REMOVAL)))
    snapshot = assemble(Declared(HARNESS, PROJECT, "2.0", "m", sources, (SQL_SKILL,)), [])
    return {t.source.id: t for t in targets(snapshot) if t.rules}


def _reply(*texts: str, matters: str = "which_tools", note: str | None = None) -> dict:
    return {"situations": [{"text": t, "matters": {"kind": matters, "uses": "skill:*", **({"then": "agent:*"} if matters == "order" else {})}} for t in texts], "note": note}


def kinds(found) -> list[str]:
    return [d.kind for d in found]


def test_a_target_gets_its_situations_once_and_then_from_the_cache() -> None:
    db, target = connect(), _targets()["notes"]
    fake = FakeGenerator([_reply(ONE, TWO, THREE, matters="targets")], model_used="haiku-x")
    first = generate(db, target, ENTITIES, fake, "cheap", now=T0)
    assert (first.cached, first.calls, first.diagnostics) == (False, 1, ())
    assert [p.situation for p in first.probes] == [ONE, TWO, THREE]
    probe = first.probes[0]
    assert (probe.target_id, probe.target_hash, probe.matters, probe.model, probe.prompt_version, probe.created,
            probe.review, probe.reviewed) == (target.id, target_hash(target), '{"kind": "targets", "uses": "skill:*"}', "haiku-x",
                                              PROMPT_VERSION, T0, ReviewStatus.ACCEPTED, None)  # kept, unreviewed
    assert probe.probe_id == probe_id(target_hash(target), ONE) and len(probe.probe_id) == 16
    (call,) = fake.calls
    assert call.model == "cheap" and call.prompt == prompt(target, 3)
    assert call.schema["properties"]["situations"]["maxItems"] == 3
    again = generate(db, target, ENTITIES, fake, "cheap", now=T0 + timedelta(days=1))
    assert (again.cached, again.calls, again.probes) == (True, 0, first.probes)
    assert listed(db, target) == list(first.probes)


def test_the_cache_key_is_the_target_text_alone() -> None:
    db, target = connect(), _targets()["notes"]
    generate(db, target, ENTITIES, FakeGenerator([_reply(ONE)]), "cheap", now=T0)
    renamed = replace(target, id="elsewhere#notes")  # another target with the same text shares them
    assert generate(db, renamed, ENTITIES, FakeGenerator(), "other-model", now=T0).cached
    edited = _targets_with_notes(NOTES.replace("Grep", "grep -r"))
    assert target_hash(edited) != target_hash(target)
    assert not generate(db, edited, ENTITIES, FakeGenerator([_reply(TWO)]), "cheap", now=T0).cached


def test_a_shared_text_s_probes_carry_the_target_whose_generation_wrote_them() -> None:
    db, target = connect(), _targets()["notes"]
    generate(db, target, ENTITIES, FakeGenerator([_reply(ONE)]), "cheap", now=T0)
    renamed = replace(target, id="elsewhere#notes")
    hit = generate(db, renamed, ENTITIES, FakeGenerator(), "cheap", now=T0)
    assert [p.target_id for p in hit.probes] == [target.id]  # a cache hit rewrites nothing
    generate(db, renamed, ENTITIES, FakeGenerator([_reply(ONE, TWO)]), "cheap", now=T0, regenerate=True)
    assert [p.target_id for p in listed(db, target)] == [renamed.id, renamed.id]  # the latest, not the first


def _targets_with_notes(text: str):
    snapshot = assemble(Declared(HARNESS, PROJECT, "2.0", "m", (file_source("notes", text),), ()), [])
    (target,) = [t for t in targets(snapshot) if t.rules and "Search" in t.rules[0].text]
    return target


def test_regenerating_replaces_the_situations_and_drops_their_reviews() -> None:
    db, target = connect(), _targets()["notes"]
    first = generate(db, target, ENTITIES, FakeGenerator([_reply(ONE, TWO)]), "cheap", now=T0)
    review(db, first.probes[0].probe_id, ReviewStatus.REJECTED, T0)
    again = generate(db, target, ENTITIES, FakeGenerator([_reply(ONE, THREE)]), "cheap", now=T0, regenerate=True)
    assert [(p.situation, p.review, p.reviewed) for p in listed(db, target)] == [
        (ONE, ReviewStatus.ACCEPTED, None), (THREE, ReviewStatus.ACCEPTED, None)]  # kept as written, the drop gone
    assert again.probes == tuple(listed(db, target))


def test_a_regeneration_that_gets_nothing_keeps_the_old_situations() -> None:
    db, target = connect(), _targets()["notes"]
    generate(db, target, ENTITIES, FakeGenerator([_reply(ONE)]), "cheap", now=T0)
    again = generate(db, target, ENTITIES, FakeGenerator([GenerationFailed("down"), GenerationFailed("down")]),
                     "cheap", now=T0, regenerate=True)
    assert [p.situation for p in again.probes] == [ONE] == [p.situation for p in listed(db, target)]
    assert kinds(again.diagnostics) == [Kind.FAILED.value, Kind.FAILED.value, Kind.EMPTY.value]
    assert "old ones are kept" in again.diagnostics[-1].message


def test_a_situation_naming_its_target_is_dropped_as_a_diagnostic_and_topped_up_once() -> None:
    db, target = connect(), _targets()["notes"]
    named = "Use search_files to find where we parse dates in a new project."
    fake = FakeGenerator([_reply(ONE, named, TWO), _reply(THREE, matters="order")])
    result = generate(db, target, ENTITIES, fake, "cheap", now=T0)
    assert [(p.situation, read(p.matters).kind.value) for p in result.probes] == [(ONE, "which_tools"), (TWO, "which_tools"),
                                                                             (THREE, "order")]
    assert result.calls == 2 and kinds(result.diagnostics) == [Kind.CONTAMINATED.value]
    dropped = result.diagnostics[0]
    assert dropped.subject == target.id and dropped.data["situation"] == named
    assert dropped.data["hits"][0]["text"] == "search_files"
    assert dropped.data["hits"][0]["term"]["kind"] == "NAMED_ENTITY"
    top_up = fake.calls[1]
    assert top_up.schema["properties"]["situations"]["maxItems"] == 1
    assert top_up.prompt == prompt(target, 1, [ONE, TWO]) and ONE in top_up.prompt
    assert [d.kind for d in diagnostics.listed(db, subject=target.id)] == [Kind.CONTAMINATED.value]
    assert db.execute("SELECT count(*) FROM findings").fetchone()[0] == 0  # never a finding (D16)


def test_a_situation_naming_the_targets_own_skill_is_dropped() -> None:
    db, skill = connect(), _targets()["sql-source"]
    fake = FakeGenerator([_reply("Write a sql-query for last week's signups in a new file.", ONE),
                          _reply(TWO)])
    result = generate(db, skill, ENTITIES, fake, "cheap", now=T0)
    assert [p.situation for p in result.probes] == [ONE, TWO]
    assert result.diagnostics[0].data["hits"][0]["term"]["kind"] == "OWN_ENTITY"


def test_there_is_at_most_one_top_up() -> None:
    db, target = connect(), _targets()["notes"]
    fake = FakeGenerator([_reply(ONE, "Fix it."), _reply("Do it."), _reply(THREE)])
    result = generate(db, target, ENTITIES, fake, "cheap", now=T0, )
    assert result.calls == 2 and fake.remaining == 1
    assert [p.situation for p in result.probes] == [ONE]
    assert kinds(result.diagnostics) == [Kind.REJECTED.value, Kind.REJECTED.value]
    assert result.diagnostics[0].data["rejected"] == [{"reason": "TOO_SHORT", "index": 1, "detail": "7 characters"}]


def test_fewer_situations_than_asked_with_nothing_lost_get_no_top_up() -> None:
    db, target = connect(), _targets()["notes"]
    fake = FakeGenerator([_reply(ONE, note="only one fits")])
    result = generate(db, target, ENTITIES, fake, "cheap", now=T0)
    assert (result.calls, [p.situation for p in result.probes], result.diagnostics) == (1, [ONE], ())


def test_an_empty_result_is_a_diagnostic_carrying_the_note_and_nothing_is_kept() -> None:
    db, target = connect(), _targets()["notes"]
    result = generate(db, target, ENTITIES, FakeGenerator([_reply(note="too vague to test")]), "cheap", now=T0)
    assert (result.calls, result.probes) == (1, ())
    (empty,) = result.diagnostics
    assert (empty.kind, empty.data["notes"], empty.data["calls"]) == (Kind.EMPTY.value, ["too vague to test"], 1)
    assert listed(db, target) == [] and len(diagnostics.listed(db)) == 1
    assert db.execute("SELECT count(*) FROM findings").fetchone()[0] == 0


def test_a_whole_reply_rejected_is_retried_once_and_a_duplicate_of_a_kept_one_is_lost() -> None:
    db, target = connect(), _targets()["notes"]
    fake = FakeGenerator([{"situations": [{"text": ONE}]}, _reply(TWO, "plan a command-line TODO app in Go, and "
                                                                       "write the first file")])
    result = generate(db, target, ENTITIES, fake, "cheap", now=T0)
    assert [p.situation for p in result.probes] == [TWO]
    first, second = result.diagnostics
    assert first.data["rejected"][0]["reason"] == "NOT_A_SITUATION" and "part of the reply" in first.message
    assert second.data["rejected"][0]["reason"] == "DUPLICATE"


def test_a_reply_with_a_field_the_schema_does_not_allow_is_rejected_whole() -> None:
    db, target = connect(), _targets()["notes"]
    fake = FakeGenerator([{"situations": [], "claim": None}, _reply(ONE)])
    result = generate(db, target, ENTITIES, fake, "cheap", now=T0)
    assert "the whole reply" in result.diagnostics[0].message and [p.situation for p in result.probes] == [ONE]


def test_the_model_that_answered_is_recorded_per_situation() -> None:
    db, target = connect(), _targets()["notes"]
    fake = FakeGenerator([Reply(_reply(ONE, "Fix it."), "model-a", "{}"), Reply(_reply(TWO), None, "{}")])
    result = generate(db, target, ENTITIES, fake, "cheap", now=T0)
    assert [(p.situation, p.model) for p in result.probes] == [(ONE, "model-a"), (TWO, None)]


def test_a_target_without_text_makes_no_call() -> None:
    db, target = connect(), _targets()["notes"]
    bare = replace(target, rules=())
    fake = FakeGenerator()
    result = generate(db, bare, ENTITIES, fake, "cheap", now=T0)
    assert fake.calls == [] and kinds(result.diagnostics) == [Kind.NO_TEXT.value, Kind.EMPTY.value]


def test_review_takes_an_edit_only_if_a_generated_text_would_pass() -> None:
    db, target = connect(), _targets()["notes"]
    (probe,) = generate(db, target, ENTITIES, FakeGenerator([_reply(ONE)]), "cheap", now=T0).probes
    edited = review(db, probe.probe_id, ReviewStatus.EDITED, T0, "Write a tiny word counter in Python.")
    assert (edited.review, edited.text) == (ReviewStatus.EDITED, "Write a tiny word counter in Python.")
    with pytest.raises(EditRefused):
        review(db, probe.probe_id, ReviewStatus.EDITED, T0, "Use search\u200b_files")
    assert review(db, probe.probe_id, ReviewStatus.REJECTED, T0).review is ReviewStatus.REJECTED


def test_the_prompt_says_where_the_situation_runs_and_lists_the_menu() -> None:
    target = _targets()["notes"]
    text = prompt(target, 3)
    assert "holds the project's configuration (its instruction files and settings) and no\ncode" in text
    assert "Always use search_files instead of Grep" in text
    assert all(m.value in text for m in Matters)
    assert "already written" not in text and "already written" in prompt(target, 1, [ONE])
    assert "instructions file, set at user level" in text and "from the first message of every session" in text
    skill = prompt(_targets()["sql-source"], 2)
    assert "when what it defines is invoked" in skill and "up to 2 requests" in skill


def test_the_prompt_asks_only_for_requests_answered_by_acting_and_for_the_thing_each_is_about() -> None:
    """Decided with the user 2026-10-05: 3 of the first real job's 6 situations got written answers only; and 2026-10-07:
    what matters names its thing, so a situation whose runs never do it is caught (version 3)."""
    text = " ".join(prompt(_targets()["notes"], 3).split())
    assert PROMPT_VERSION == 3
    assert ('and the thing it is about, "uses", written as skill:<name>, agent:<name>, tool:<name>, program:<name> (a '
            "program a shell command runs) or file:<path>, or skill:* / agent:* for any skill or agent.") in text
    assert "A request whose runs never do \"uses\" tested nothing." in text
    assert '"matters": {"kind": "...", "uses": "..."}' in text
    assert ("Each request must be one the agent can only answer by acting: by looking something up with a tool or "
            "command, running a program, creating or changing a file, or using a skill or agent.") in text
    assert "Never a question it could answer from what it already knows: no advice, no explanations, no comparisons." in text
    assert "plan, explain" not in text  # version 1 offered both, and both are answered in words
    assert "Never name this skill, agent, tool, command or hook" in text  # a situation still never names its target


def test_a_skill_or_agent_the_configuration_doesnt_have_is_made_up_and_tools_programs_files_are_taken_as_written() -> None:
    from agentlint.probes.generate import knows

    known = knows(_targets()["notes"], ENTITIES)
    skills = [e.name for e in ENTITIES if e.kind.role.value == "skill"]
    assert skills and known(f"skill:{skills[0]}") and known(f"skill:some-package:{skills[0].rsplit(':', 1)[-1]}")
    assert known("skill:*") and known("agent:*") and not known("skill:made-up-skill") and not known("agent:nobody")
    assert known("tool:Bash") and known("program:acme") and known("file:docs/notes.md")
