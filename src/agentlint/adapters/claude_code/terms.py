"""What one of Claude Code's tool calls did, in core's terms (``trace/terms.py``): which of its tools runs a shell
command, reads or changes a file, searches the files, invokes a skill or delegates to a subagent — and what each acted
on. Everything else, MCP tools among them, is the tool alone.
"""

from __future__ import annotations

from typing import Any

from agentlint.adapters.claude_code.shell import programs
from agentlint.adapters.claude_code.transcripts import delegated_to
from agentlint.trace.model import Action
from agentlint.trace.terms import ActionKind, ActionTerms

_SHELL = frozenset({"Bash"})
_FILE = {"Read": "file_path", "Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path",
         "NotebookEdit": "notebook_path", "NotebookRead": "notebook_path"}  # the tool, and the input naming its file
_SEARCH = frozenset({"Grep", "Glob", "LS"})
_SKILL = frozenset({"Skill", "SlashCommand"})  # SlashCommand: the older name, its input a command line
_DELEGATING = frozenset({"Agent", "Task"})  # Task is the older name


def _text(given: dict[str, Any], key: str) -> str:
    value = given.get(key)
    return value if isinstance(value, str) else ""


def action_terms(action: Action) -> ActionTerms:
    name, given = action.tool, action.input
    if name in _SHELL:
        return ActionTerms(ActionKind.SHELL, name, programs(_text(given, "command")))
    if name in _FILE:
        path = _text(given, _FILE[name]) or _text(given, "path")
        return ActionTerms(ActionKind.FILE, name, (path,) if path else ())
    if name in _SEARCH:
        return ActionTerms(ActionKind.SEARCH, name, (_text(given, "path") or ".",))
    if name in _SKILL:
        skill = (_text(given, "skill") or _text(given, "command").split(" ", 1)[0]).lstrip("/")
        return ActionTerms(ActionKind.SKILL, name, (skill,) if skill else ())
    if name in _DELEGATING:
        agent = delegated_to(given)
        if agent is None and given.get("subagent_type") == "fork":
            agent = "fork"  # the caller's own fork: no agent of its own, but work delegated all the same
        return ActionTerms(ActionKind.AGENT, name, (agent,) if agent else ())
    return ActionTerms(ActionKind.OTHER, name)
