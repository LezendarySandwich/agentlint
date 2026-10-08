"""The contamination guard: code finds the names and copied words a situation must not contain."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agentlint.config.entity import Entity
from agentlint.config.model import Ablation, AblationPolicy, Declared
from agentlint.config.taxonomy import EntityKind, EntityRole, Load
from agentlint.config.assemble import assemble
from agentlint.probes.contamination import Guard, Term, TermKind, guard, guard_for
from agentlint.rank.plan import targets

from ..config.helpers import HARNESS, PROJECT, file_source

TOOL = EntityKind("test-harness", "tool", "tool", EntityRole.TOOL, "tools")
SKILL = EntityKind("test-harness", "skill", "skill", EntityRole.SKILL, "skills", ":")
READ = Entity("tool:Read", "Read", TOOL, "builtin")
GREP = Entity("tool:Grep", "Grep", TOOL, "builtin")
SEARCH = Entity("tool:search_files", "search_files", TOOL, "server")
SQL_SKILL = Entity("skill:sql", "data:sql-query", SKILL, "sql-source")
DEBUG = Entity("skill:debug", "debug", SKILL, "debug-source")
ENTITIES = (READ, GREP, SEARCH, SQL_SKILL, DEBUG)


def found(g: Guard, situation: str) -> list[str]:
    return [hit.text for hit in g.hits(situation)]


def test_a_situation_naming_a_tool_the_rule_names_is_caught_whatever_its_spelling() -> None:
    g = guard("Always use search_files instead of Grep when searching the repository.", ENTITIES)
    assert found(g, "Use search_files to find where we call the billing API.") == ["search_files"]
    assert found(g, "Run SEARCH-FILES over the repo for TODO comments.") == ["SEARCH-FILES"]
    assert found(g, "Can you Grep for the place we parse dates?") == ["Grep"]
    assert not g.contaminated("Find every place we call the billing API in this repo.")


def test_an_ordinary_word_is_not_a_tool_name() -> None:
    g = guard("Never edit generated files. Use Read before Edit.", ENTITIES)
    assert {t.text for t in g.terms} == {"Read"}  # Edit is no entity here; "Never edit" names nothing
    assert found(g, "Read the README and tell me what this repo does.") == []  # a capital where a sentence starts
    assert found(g, "Please read the README.") == []  # the word, not the tool
    assert found(g, "Open it with Read, then summarise.") == ["Read"]
    assert found(g, "Summarise it.\n- Read the setup guide first") == []


def test_a_capital_where_the_rule_starts_a_sentence_names_nothing() -> None:
    assert guard("Read the checklist before anything else.", ENTITIES).terms == ()


def test_a_plain_lowercase_name_is_named_only_as_the_targets_own() -> None:
    assert guard("Debug failures carefully and debug before you ask.", ENTITIES).terms == ()
    assert guard("When a test fails, use the `debug` skill.", ENTITIES).terms == ()  # `run` is a subcommand as often
    g = guard("Look at the logs first.", ENTITIES, own=[DEBUG])
    assert found(g, "Debug why the login test fails.") == ["Debug"]


def test_a_short_name_that_is_an_ordinary_word_neither_names_nor_is_forbidden() -> None:
    status = Entity("skill:status", "brain:status", SKILL, "brain")
    assert guard("Run `status` to see where things are.", [status]).terms == ()
    g = guard("Use brain:status when asked where things stand.", [status])
    assert [t.text for t in g.terms] == ["brain:status"]
    assert not g.contaminated("What's the status of the migration?")
    assert found(g, "Run brain:status for me.") == ["brain:status"]


def test_a_lone_ordinary_word_in_code_is_not_a_term_but_a_capital_in_code_always_names() -> None:
    g = guard("Set `name` and `description`; `true` is the default. `Bash(git:*)` is allowed.", ENTITIES + (
        Entity("tool:Bash", "Bash", TOOL, "builtin"),))
    assert {t.text for t in g.terms} == {"Bash", "Bash(git:*)"}
    assert not g.contaminated("Give the new service a name and a description.")


def test_naming_an_entity_forbids_every_name_it_goes_by() -> None:
    g = guard("For SQL questions use data:sql-query.", ENTITIES)
    assert {t.text for t in g.terms} == {"data:sql-query", "sql-query"}
    assert found(g, "Use sql-query to count yesterday's orders.") == ["sql-query"]
    assert found(g, "Run data:sql-query on the orders table.") == ["data:sql-query"]  # not twice
    assert guard("Ask sql-query for numbers.", ENTITIES).terms[0].entity_id == "skill:sql"


def test_the_targets_own_entity_is_forbidden_though_its_text_never_names_it() -> None:
    g = guard("Write the query, run it, and show the numbers in a table.", ENTITIES, own=[DEBUG, SQL_SKILL])
    assert {(t.text, t.kind) for t in g.terms} == {
        ("debug", TermKind.OWN_ENTITY), ("data:sql-query", TermKind.OWN_ENTITY), ("sql-query", TermKind.OWN_ENTITY)}
    assert found(g, "Help me debug the nightly job.") == ["debug"]
    assert not g.contaminated("Help me find why the nightly job is slow.")


def test_code_spans_and_snake_case_identifiers_are_names_too() -> None:
    text = ("Run `uv run pytest -q` before every commit. Read `docs/03-checklist.md` first. "
            "Never call billing_client directly.\n```\nfenced_only_name here\n```\nNot `x` or `[~]`.")
    g = guard(text, ENTITIES)
    assert {(t.text, t.kind) for t in g.terms} == {
        ("uv run pytest -q", TermKind.CODE), ("docs/03-checklist.md", TermKind.CODE),
        ("billing_client", TermKind.IDENTIFIER)}
    assert found(g, "Run uv  run pytest -q and fix what fails.") == ["uv  run pytest -q"]
    assert found(g, "What does docs/03-checklist.md say is next?") == ["docs/03-checklist.md"]
    assert found(g, "Why is Billing-Client slow?") == ["Billing-Client"]
    assert not g.contaminated("Which tests fail right now?")


def test_a_long_run_of_the_rules_own_words_is_caught_and_a_short_or_common_one_is_not() -> None:
    rule = "Never touch the payments ledger module without asking the finance team owner first."
    g = guard(rule, ())
    [hit] = g.hits("Please never touch the payments ledger module without asking, ok?")
    assert hit.copied and hit.term is None
    assert hit.text == "never touch the payments ledger module without asking"
    assert not g.contaminated("Can you touch the payments ledger module for me?")  # five words
    common = guard("If you are not sure what it is, then ask me.", ())
    assert not common.contaminated("If you are not sure what it is, look it up.")  # six words, mostly function words


def test_hits_carry_offsets_into_the_situation_in_order() -> None:
    g = guard("Prefer search_files over Grep.", ENTITIES)
    situation = "First Grep, then search_files."
    hits = g.hits(situation)
    assert [(h.text, situation[h.start:h.end]) for h in hits] == [("Grep", "Grep"), ("search_files", "search_files")]
    assert [h.term.kind for h in hits if h.term] == [TermKind.NAMED_ENTITY, TermKind.NAMED_ENTITY]


def test_a_name_is_listed_once_with_the_first_reason_given() -> None:
    g = Guard("", [Term("search_files", TermKind.OWN_ENTITY, "a"), Term("search-files", TermKind.CODE),
                   Term("Read", TermKind.CODE), Term("read", TermKind.CODE)])
    assert [(t.text, t.kind) for t in g.terms] == [("search_files", TermKind.OWN_ENTITY), ("Read", TermKind.CODE),
                                                   ("read", TermKind.CODE)]
    with pytest.raises(ValueError):
        Guard("", [], phrase_words=1)


def test_guard_for_reads_a_plan_targets_rules_and_its_sources_own_entities() -> None:
    skill_text = "---\ndescription: SQL\n---\n# Querying\nWrite the query in a scratch file and show the result.\n"
    notes = "# Notes\n## Search\nAlways use search_files instead of Grep.\n"
    sources = (file_source("notes", notes),
               replace(file_source("sql-source", skill_text, load=Load.ON_INVOKE),
                       ablation=AblationPolicy(Ablation.FILE_REMOVAL)))
    snapshot = assemble(Declared(HARNESS, PROJECT, "2.0", "m", sources, (SQL_SKILL,)), [])
    by_source = {t.source.id: t for t in targets(snapshot) if t.rules}
    entities = (*ENTITIES[:3], SQL_SKILL)
    notes_guard = guard_for(by_source["notes"], entities)
    assert {t.text for t in notes_guard.terms} == {"search_files", "Grep"}
    skill_guard = guard_for(by_source["sql-source"], entities)
    assert {(t.text, t.kind) for t in skill_guard.terms} == {
        ("data:sql-query", TermKind.OWN_ENTITY), ("sql-query", TermKind.OWN_ENTITY)}
    assert skill_guard.contaminated("Use sql-query to count orders.")


MCP = Entity("tool:mcp-search", "mcp__plugin_acme_hub__search_files", TOOL, "hub")
PURPOSE = Entity("agent:general-purpose", "general-purpose", SKILL, "builtin")


def test_a_distinctive_name_in_a_fenced_block_names_its_entity() -> None:
    g = guard("When searching:\n```bash\nsearch_files --pattern TODO\n```\n", ENTITIES)
    assert [t.text for t in g.terms] == ["search_files"]
    assert found(g, "Use search_files to find every TODO") == ["search_files"]
    g = guard("Look it up:\n```python\nmcp__plugin_acme_hub__search_files(query=q)\nspawn('general-purpose')\n```\n",
              (*ENTITIES, MCP, PURPOSE))
    assert {t.text for t in g.terms} == {MCP.name, "general-purpose"}
    assert found(g, "Call mcp__plugin_acme_hub__search_files for the TODOs") == [MCP.name]
    assert found(g, "Hand it to @general-purpose and wait") == ["general-purpose"]
    assert found(g, "Hand it to a general-purpose agent") == ["general-purpose"]  # before a role word: the name
    assert found(g, "Write a general-purpose retry helper") == []  # an English compound before a noun


def test_a_fenced_block_adds_no_capitalised_name_code_or_identifier() -> None:
    g = guard("Example:\n```python\nRead(path)\nbilling_client.fetch()\n```\n", ENTITIES)
    assert g.terms == ()  # Read in a program is as likely a class; a fence is a program, not a list of names
    assert found(g, "Open the billing_client module with Read") == []


@pytest.mark.parametrize("marker", ["1)", "1.", "(1)", "a)", "(a)", "B)"])
def test_a_list_items_first_word_is_a_sentence_start(marker: str) -> None:
    edit = Entity("tool:Edit", "Edit", TOOL, "builtin")
    g = guard(f"Before a change:\n{marker} Read the checklist.\n{marker} Edit the doc.", (*ENTITIES, edit))
    assert g.terms == ()
    assert found(g, "Can you open the file with Read and fix it") == []
    g = guard("Open files with Read.", ENTITIES)
    assert found(g, f"{marker} Read the config and tell me the port") == []
    assert found(g, f"Steps: {marker} Read the config") == []
    assert found(g, f"{marker} Open the config with Read") == ["Read"]


def test_a_letter_mid_sentence_is_no_list_marker() -> None:
    g = guard("Open files with Read.", ENTITIES)
    assert found(g, "Check option a) Read wins over b") == ["Read"]


DEEP_DIVE = Entity("skill:fireworks:deep-dive", "fireworks:deep-dive", SKILL, "fireworks")
DELEGATE = 'Delegate:\n```python\nAgent(subagent_type="general-purpose", prompt=p)\n```\n'


@pytest.mark.parametrize("situation", [
    "Write a general-purpose retry helper for our HTTP client.",
    "Write general-purpose code for the retry path.",
    "General-purpose helpers belong in utils, move them there.",
    "Can you do a deep-dive on why the cache misses?",
    "I want the deep-dive to cover the cold start too.",
])
def test_a_hyphenated_english_compound_is_the_word_not_the_name(situation: str) -> None:
    g = guard(DELEGATE + "For hard bugs use fireworks:deep-dive.", (*ENTITIES, PURPOSE, DEEP_DIVE))
    assert {t.text for t in g.terms} >= {"general-purpose", "deep-dive"}
    assert found(g, situation) == []


@pytest.mark.parametrize("situation, name", [
    ("Write a general-purpose retry helper for our HTTP client.", "general-purpose"),
    ("General-purpose helpers belong in utils, move them there.", "General-purpose"),
    ("Can you do a deep-dive on why the cache misses?", "deep-dive"),
])
def test_the_targets_own_compound_name_is_never_read_as_a_word(situation: str, name: str) -> None:
    assert found(guard("Look at the logs first.", ENTITIES, own=[PURPOSE, DEEP_DIVE]), situation) == [name]


@pytest.mark.parametrize("situation, name", [
    ("Spawn it with subagent_type=general-purpose please", "general-purpose"),
    ('Use the "general-purpose" agent for this one', "general-purpose"),
    ("Run `deep-dive` on the cache misses", "deep-dive"),
    ("Run /deep-dive on the cache misses", "deep-dive"),
    ("Use deep-dive to find why the cache misses", "deep-dive"),
    ("Use deep_dive on the cache misses", "deep_dive"),
])
def test_a_hyphenated_name_written_as_a_name_is_caught(situation: str, name: str) -> None:
    g = guard(DELEGATE + "For hard bugs use fireworks:deep-dive.", (*ENTITIES, PURPOSE, DEEP_DIVE))
    assert found(g, situation) == [name]


def test_a_hyphenated_compound_in_the_targets_prose_names_no_entity() -> None:
    assert guard("This is a general-purpose library for searching code.", (*ENTITIES, PURPOSE)).terms == ()
    assert guard("Use a general-purpose tool for searching code.", (*ENTITIES, PURPOSE)).terms != ()  # a role word
    assert [t.text for t in guard(DELEGATE, (*ENTITIES, PURPOSE)).terms] == ["general-purpose"]


def test_a_span_of_plain_words_is_code_only_when_its_first_word_is_a_program() -> None:
    g = guard("Stop when the `limit reached` banner shows; what `depends on` it is listed below.", ENTITIES)
    assert g.terms == ()
    assert found(g, "Keep going until the rate limit reached zero, then retry.") == []
    assert found(g, "List what depends on the cache module.") == []
    g = guard("Check `sl status` before `sl commit`; `sl amend` fixes the last one.", ENTITIES)
    assert {t.text for t in g.terms} == {"sl status", "sl commit", "sl amend"}
    assert found(g, "Run sl status and tell me what changed") == ["sl status"]
    g = guard("Run `git log` first.\n```bash\ngit log --oneline\n```\n", ENTITIES)
    assert [t.text for t in g.terms] == ["git log"]
    g = guard("Run `git log` first.\n```\n$ git log --oneline\n```\n", ENTITIES)
    assert [t.text for t in g.terms] == ["git log"]
    g = guard("Use `async def` here.\n```python\nasync def main(): ...\n```\n", ENTITIES)
    assert g.terms == ()  # a line of Python is no command line
    g = guard("Say `next friday` or `next monday`; `no change`, `no news` and `no idea` mean nothing.",
              ENTITIES)
    assert g.terms == ()
    assert found(g, "Schedule the review for next friday.") == []
    g = guard("Run `git log` first.", ENTITIES)
    assert g.terms == ()  # one span of plain words, and nothing says git is a program: left to the copied check
    g = guard("Run `uv run pytest -q` and `npm run build:prod`.", ENTITIES)
    assert {t.text for t in g.terms} == {"uv run pytest -q", "npm run build:prod"}


def test_a_capitalised_first_word_is_no_program() -> None:
    g = guard("End the summary with `Depends on D<number>` or `Depends on` alone.\n```\nDepends on D123\n```\n",
              ENTITIES)
    assert [t.text for t in g.terms] == ["Depends on D<number>"]  # a symbol makes it code; plain words don't
    assert found(g, "List what depends on the cache module.") == []


def test_a_shell_keyword_is_no_program() -> None:
    g = guard("Use `for weekly` or `for psc`.\n```bash\nfor f in *.md; do wc -l \"$f\"; done\n```\n", ENTITIES)
    assert g.terms == ()


AUTOPILOT = Entity("skill:ci", "diff:ci-autopilot", SKILL, "diff")
CREATOR = Entity("skill:creator", "skill-creator", SKILL, "creator")
REVIEW = Entity("skill:review", "code-review", SKILL, "review")


@pytest.mark.parametrize("own, situation, name", [
    (AUTOPILOT, "Use the ci-autopilot skill to get my diff green.", "ci-autopilot"),
    (AUTOPILOT, "Run ci-autopilot loop on D123 please.", "ci-autopilot"),
    (CREATOR, "Use the skill-creator skill to make a lint skill.", "skill-creator"),
    (CREATOR, "Run skill-creator now for a new lint skill.", "skill-creator"),
    (CREATOR, "Use skill-creator for this.", "skill-creator"),
    (REVIEW, "Invoke the code-review skill for this PR.", "code-review"),
    (REVIEW, "Use code-review skill here.", "code-review"),
    (REVIEW, "Do a code-review of my last change.", "code-review"),  # a listed English word, but its own name
])
def test_the_targets_own_hyphenated_skill_named_outright_is_caught(own: Entity, situation: str, name: str) -> None:
    assert found(guard("Iterate until CI is green.", ENTITIES, own=[own]), situation) == [name]


@pytest.mark.parametrize("text", [
    "Use the ci-autopilot skill when CI fails.",
    "When CI fails, run ci-autopilot first.",
])
def test_a_hyphenated_name_the_targets_text_uses_as_a_name_names_its_entity(text: str) -> None:
    g = guard(text, (*ENTITIES, AUTOPILOT))
    assert {t.text for t in g.terms} == {"diff:ci-autopilot", "ci-autopilot"}
    assert found(g, "Use the ci-autopilot skill to get my diff green.") == ["ci-autopilot"]
    assert found(g, "Run ci-autopilot loop on D123 please.") == ["ci-autopilot"]
    assert found(g, "Try ci-autopilot today on D123.") == ["ci-autopilot"]  # not an English word at all


@pytest.mark.parametrize("situation", [
    "Use the skill-creator skill to make a lint skill.",
    "Run skill-creator now for a new lint skill.",
    "Ask skill-creator for a new lint skill.",
])
def test_a_named_compound_that_is_no_english_word_is_always_the_name(situation: str) -> None:
    g = guard("To make a skill, use skill-creator.", (*ENTITIES, CREATOR))
    assert [t.text for t in g.terms] == ["skill-creator"]
    assert found(g, situation) == ["skill-creator"]


@pytest.mark.parametrize("situation", [
    "Ask general-purpose to look at the flaky test.",
    "Use the general-purpose subagent for the flaky test.",
    "Invoke deep-dive on the cache misses.",
])
def test_a_listed_compound_after_an_invoking_verb_or_before_a_role_word_is_the_name(situation: str) -> None:
    g = guard(DELEGATE + "For hard bugs use fireworks:deep-dive.", (*ENTITIES, PURPOSE, DEEP_DIVE))
    assert len(found(g, situation)) == 1
