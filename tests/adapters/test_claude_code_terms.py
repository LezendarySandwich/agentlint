"""What each of Claude Code's tool calls did, in core's terms (adapters/claude_code/terms.py)."""

from __future__ import annotations

from typing import Any

import pytest

from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
from agentlint.adapters.claude_code.terms import action_terms
from agentlint.trace.model import Action
from agentlint.trace.terms import ActionKind, ActionTerms


def call(tool: str, given: dict[str, Any]) -> Action:
    return Action(0, tool, given, None, None, (), True, False, False)


@pytest.mark.parametrize(("tool", "given", "expected"), [
    ("Bash", {"command": "ls; acme x | head", "description": "List"}, ActionTerms(ActionKind.SHELL, "Bash", ("ls", "acme", "head"))),
    ("Bash", {}, ActionTerms(ActionKind.SHELL, "Bash", ())),
    ("Read", {"file_path": "/r/work/AGENTS.md"}, ActionTerms(ActionKind.FILE, "Read", ("/r/work/AGENTS.md",))),
    ("Edit", {"file_path": "a.py", "old_string": "x", "new_string": "y"}, ActionTerms(ActionKind.FILE, "Edit", ("a.py",))),
    ("Write", {"file_path": "n.md", "content": "c"}, ActionTerms(ActionKind.FILE, "Write", ("n.md",))),
    ("NotebookEdit", {"notebook_path": "n.ipynb"}, ActionTerms(ActionKind.FILE, "NotebookEdit", ("n.ipynb",))),
    ("Grep", {"pattern": "x", "path": "src"}, ActionTerms(ActionKind.SEARCH, "Grep", ("src",))),
    ("Glob", {"pattern": "**/*.py"}, ActionTerms(ActionKind.SEARCH, "Glob", (".",))),
    ("Skill", {"skill": "/tdd"}, ActionTerms(ActionKind.SKILL, "Skill", ("tdd",))),
    ("SlashCommand", {"command": "/review now"}, ActionTerms(ActionKind.SKILL, "SlashCommand", ("review",))),
    ("Agent", {"subagent_type": "reviewer", "prompt": "p"}, ActionTerms(ActionKind.AGENT, "Agent", ("reviewer",))),
    ("Task", {"prompt": "p"}, ActionTerms(ActionKind.AGENT, "Task", ("general-purpose",))),
    ("Agent", {"subagent_type": "fork"}, ActionTerms(ActionKind.AGENT, "Agent", ("fork",))),
    ("mcp__notes__post", {"text": "t"}, ActionTerms(ActionKind.OTHER, "mcp__notes__post")),
    ("WebFetch", {"url": "https://x"}, ActionTerms(ActionKind.OTHER, "WebFetch")),
])
def test_each_tool_is_its_kind_and_what_it_acted_on(tool: str, given: dict[str, Any], expected: ActionTerms) -> None:
    assert action_terms(call(tool, given)) == expected


def test_the_adapter_gives_them() -> None:
    assert ClaudeCodeAdapter.action_terms(None, call("Bash", {"command": "pwd"})) == ActionTerms(ActionKind.SHELL, "Bash", ("pwd",))  # type: ignore[arg-type]
