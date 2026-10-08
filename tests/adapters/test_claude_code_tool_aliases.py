"""A server's tool goes by its own name too, so a situation that says "use search_files" names it."""

from __future__ import annotations

from types import SimpleNamespace

from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
from agentlint.adapters.claude_code.points import Kind
from agentlint.probes.contamination import TermKind, guard

FULL = "mcp__plugin_acme_hub__search_files"


def observed(tool_names: tuple[str, ...] = (), deferred: tuple[str, ...] = ()):
    raw = SimpleNamespace(tool_names=tool_names, deferred_tool_names=deferred, skill_listing=None, skill_names=(),
                          agent_listing=None, agent_types=())
    return {e.name: e for e in ClaudeCodeAdapter._observed_entities(raw, SimpleNamespace(entities=()))}


def test_an_mcp_tool_goes_by_its_own_name_and_keeps_its_full_name() -> None:
    entities = observed(tool_names=("Read", "mcp__srv__defined_in_full"), deferred=(FULL, "mcp__other__search"))
    tool = entities[FULL]
    assert tool.kind is Kind.MCP_TOOL.value
    assert tool.short_name == FULL  # no separator: two servers' search_files are not namesakes
    assert tool.aliases == ("search_files",)
    assert entities["mcp__srv__defined_in_full"].aliases == ("defined_in_full",)
    assert entities["Read"].aliases == ()
    assert entities["mcp__other__search"].aliases == ("search",)


def test_naming_an_mcp_tool_by_its_full_name_forbids_its_own_name() -> None:
    entities = observed(deferred=(FULL,))
    g = guard(f"Search code with {FULL}, never grep.", entities.values())
    assert {(t.text, t.kind) for t in g.terms} == {(FULL, TermKind.NAMED_ENTITY),
                                                   ("search_files", TermKind.NAMED_ENTITY)}
    assert [h.text for h in g.hits("Use search_files to find every TODO in src.")] == ["search_files"]
    assert g.hits("Find every TODO in src.") == ()


def test_an_mcp_tools_own_name_written_alone_names_it() -> None:
    g = guard("Prefer `search_files` to grep.", observed(deferred=(FULL,)).values())
    assert FULL in {t.text for t in g.terms}


def test_an_ordinary_word_alias_neither_names_nor_is_forbidden() -> None:
    entities = observed(deferred=("mcp__other__search",))
    assert guard("Always search before you edit.", entities.values()).terms == ()
    g = guard("Use mcp__other__search for code.", entities.values())
    assert [t.text for t in g.terms] == ["mcp__other__search"]
    assert g.hits("Search the repo for the billing client.") == ()
    own = guard("Look at the logs first.", (), own=entities.values())
    assert own.hits("Search the repo for the billing client.") == ()
