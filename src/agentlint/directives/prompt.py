"""The extraction prompt (12-directives.md, "The prompt"), and the reader's version.

One section of instruction text per call. The model lists the directives it gives about a thing the configuration
has, each with the exact words that say it; the strict check (``validate``) is what holds it to that.

**READER_VERSION is set by hand** (the user, 2026-10-08): raise it whenever the prompt or the check changes in a way
that could change what a section gives. Every directive stored under an older version is stale from then on — no check
uses it, and Read instructions shows its section as stale until it is read again.
"""

from __future__ import annotations

from agentlint.directives.model import Polarity

READER_VERSION = 2  # 2026-10-08: only directives to use or not use the thing, named in the quote (1: any directive)
MOST = 15  # directives one section may give: a long skill body can hold many


def prompt(text: str, where: str, loads: str) -> str:
    """The prompt for one section: its ``text``, ``where`` it comes from and when it ``loads`` into the agent's
    context, in words."""
    menu = "\n".join(f"  - {p.value}: {p.meaning}" for p in Polarity)
    return f"""You are reading the configuration of a coding agent, to list what it tells the agent to do.

Here is one section of it. It comes from {where}, and it reaches the agent {loads}.

---
{text}
---

List each directive this section gives about whether to USE a specific thing — invoke the skill, delegate to the
agent, call the tool, run the program, read or edit the file — or not to:
  - skill:<name> — a skill, by the name the text gives it; skill:* for "any skill" / "skills" in general
  - agent:<name> — a subagent, by name; agent:* for "any subagent" / "subagents" / "delegating" in general
  - tool:<name> — one of the agent's own tools, by its exact name (Read, Grep, Bash, WebFetch, an MCP tool's full name)
  - program:<name> — a program a shell command runs (git, rg, acme, npm)
  - file:<path> — a file or directory, by its path

For each, give:
  - polarity, one of:
{menu}
  - thing: as above
  - quote: the words of the section that say it, copied exactly, character for character, one contiguous span of at
    most 300 characters
  - when: when it applies, in at most twelve words (e.g. "for codebase exploration"), or null when it always applies

"Use X instead of Y" is two directives: prefer X, avoid Y. The quote must name the thing (its name, or "skill",
"agent", "subagent", "delegate" for any of them).

Leave out:
  - rules about how to behave once the thing is used ("send agents in one message", "wait for the agent to report"):
    they are not whether to use it
  - sentences that only mention the thing, describe it, or set limits on it
  - principles about models or AI in general that are not an instruction to use or not use a skill or agent
  - advice about tone, style, length or reasoning

Give at most {MOST} directives, the strongest first. If the section gives none, return an empty list: that is a normal
answer, and most sections give none."""
