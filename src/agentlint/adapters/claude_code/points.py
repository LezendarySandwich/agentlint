"""Where text reaches the agent in Claude Code, and the kinds of entity it has.

Each member's value is a core value object; core reasons only about its mechanism or role.
"""

from __future__ import annotations

from enum import Enum

from agentlint.config.taxonomy import EntityKind, EntityRole, InjectionPoint, Mechanism

HARNESS = "claude-code"


def _point(name: str, label: str, mechanism: Mechanism, description: str = "") -> InjectionPoint:
    return InjectionPoint(HARNESS, name, label, mechanism, description)


class Point(Enum):
    SYSTEM_PROMPT = _point("system_prompt", "system prompt", Mechanism.HARNESS_BUILTIN,
                           "Claude Code's own instructions to the model, sent every session. You can't edit or remove them.")
    TOOL_DEFINITIONS = _point("tool_definitions", "tool definitions", Mechanism.HARNESS_BUILTIN,
                              "The tools the model can call — each one's name, description and inputs — sent every session "
                              "by Claude Code.")
    SESSION_CONTEXT = _point("session_context", "session context (environment, date, git status)", Mechanism.HARNESS_GENERATED,
                             "Facts Claude Code adds as a session starts: the environment, today's date, git status, the "
                             "mode you're in.")
    SKILL_LISTING = _point("skill_listing", "skill listing", Mechanism.HARNESS_GENERATED,
                           "The menu of skills the model can choose from, written by Claude Code from what you have "
                           "installed. It is cut to a size budget, so many skills appear as a bare name. Disabling a "
                           "package shrinks it.")
    AGENT_LISTING = _point("agent_listing", "agent listing", Mechanism.HARNESS_GENERATED,
                           "The subagents the model can hand work to, written by Claude Code from what you have installed. "
                           "Disabling a package shrinks it.")
    DEFERRED_TOOLS = _point("deferred_tools", "deferred tool listing", Mechanism.HARNESS_GENERATED,
                            "Only the names of tools whose definitions aren't loaded up front — mostly MCP tools. The model "
                            "fetches a definition before calling one. Disabling a package or MCP server shrinks it.")
    CARRIED_FILES = _point("compaction_files", "files re-attached after compacting", Mechanism.HARNESS_GENERATED,
                           "Files the model read before a compaction, which Claude Code attaches again after it.")
    CARRIED_SKILLS = _point("compaction_skills", "skills carried over at compacting", Mechanism.HARNESS_GENERATED,
                            "The skills invoked before a compaction, whose text Claude Code attaches again after it.")
    MANAGED_INSTRUCTIONS = _point("managed_claude_md", "managed policy instructions", Mechanism.CONFIG_FILE,
                                  "Instructions your organisation's policy adds to every session.")
    SETTINGS = _point("settings", "settings file", Mechanism.CONFIG_FILE)
    CLAUDE_MD = _point("claude_md", "CLAUDE.md", Mechanism.INSTRUCTION_FILE)
    AGENTS_MD = _point("agents_md", "AGENTS.md", Mechanism.INSTRUCTION_FILE)
    MEMORY_INDEX = _point("memory_index", "memory index (MEMORY.md)", Mechanism.INSTRUCTION_FILE,
                          "The index of the memories Claude Code keeps for this project, loaded every session.")
    SKILL = _point("skill", "skill", Mechanism.PACKAGE_ENTITY)
    COMMAND = _point("command", "slash command", Mechanism.PACKAGE_ENTITY)
    WORKFLOW = _point("workflow", "workflow", Mechanism.PACKAGE_ENTITY)
    AGENT_DEFINITION = _point("agent_definition", "agent definition", Mechanism.PACKAGE_ENTITY)
    SESSION_START_HOOK = _point("session_start_hook", "hook: SessionStart", Mechanism.HOOK_OUTPUT,
                                "What a SessionStart hook printed. It is added to the start of every session.")
    USER_PROMPT_HOOK = _point("user_prompt_submit_hook", "hook: UserPromptSubmit", Mechanism.HOOK_OUTPUT,
                              "What a UserPromptSubmit hook printed. It is added to every message you send.")
    TOOL_HOOK = _point("tool_hook", "hook: tool call", Mechanism.HOOK_OUTPUT,
                       "What a hook printed around a tool call. It is added only when that tool runs.")
    OTHER_HOOK = _point("other_hook", "hook: other event", Mechanism.HOOK_OUTPUT)


def _kind(name: str, label: str, role: EntityRole, namespace: str, separator: str | None = None) -> EntityKind:
    return EntityKind(HARNESS, name, label, role, namespace, separator)


class Kind(Enum):
    TOOL = _kind("tool", "tool", EntityRole.TOOL, "tools")
    MCP_TOOL = _kind("mcp_tool", "MCP tool", EntityRole.TOOL, "tools")
    SKILL = _kind("skill", "skill", EntityRole.SKILL, "skills", ":")  # package entities are "pkg:name"
    COMMAND = _kind("command", "slash command", EntityRole.SKILL, "skills", ":")  # listed with skills
    WORKFLOW = _kind("workflow", "workflow", EntityRole.SKILL, "skills", ":")  # listed with skills
    AGENT = _kind("agent", "agent", EntityRole.AGENT, "agents", ":")


class Scope(Enum):
    """Where an entity is defined. Namesakes in two scopes are two entities."""

    PACKAGE = "package"  # in an enabled package; its name already carries the package ("pkg:name")
    USER = "user"  # ~/.claude
    PROJECT = "project"  # <cwd>/.claude
    SESSION = "session"  # known only from what a session showed: tools, built-ins, a package not found on disk


def entity_id(kind: Kind, scope: Scope, name: str) -> str:
    """The only way this adapter builds an entity id: kind, scope and name, so a user skill and a project skill
    with one name are two entities. Kind and scope hold no ':', so no two triples give one id."""
    return f"{kind.value.name}:{scope.value}:{name}"
