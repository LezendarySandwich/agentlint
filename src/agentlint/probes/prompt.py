"""The generation prompt (04-probe-generator.md, "The prompt"), version PROMPT_VERSION.

A situation runs in a directory holding the project's configuration and no code (the user's decision, 2026-10-04,
D1), so the prompt asks for requests that make sense there. Since version 2 (decided with the user 2026-10-05) it asks
only for requests the agent can answer by acting — looking something up with a tool or command, running a program,
creating or changing a file, using a skill or agent — never for one answerable from knowledge alone: in the first real
job, 3 of 6 situations got written answers only, and a test compares actions. It asks the model never to name the
target's own skill or tool; the contamination guard is what checks it. Changing the wording means raising
PROMPT_VERSION, which is recorded with every probe but invalidates none (D15).
"""

from __future__ import annotations

from collections.abc import Sequence

from agentlint.config.taxonomy import Load
from agentlint.probes.matters import Matters
from agentlint.rank.plan import AblationTarget

PROMPT_VERSION = 3  # 2026-10-07: what matters names its thing (2: answered only by acting; 1: anything relevant)

_WHEN = {
    Load.ALWAYS: "from the first message of every session",
    Load.PER_MESSAGE: "with every message the developer sends",
    Load.ON_INVOKE: "when what it defines is invoked",
    Load.NOT_IN_CONTEXT: "never as text: it configures how the agent runs",
}


def target_text(target: AblationTarget) -> str:
    """What the generator writes for, and what its cache is keyed on: the target's rules' text, in order. The same
    text the contamination guard reads (``contamination.guard_for``)."""
    return "\n\n".join(rule.text for rule in target.rules)


def _when(target: AblationTarget) -> str:
    activation = target.source.activation
    if activation.load is Load.CONDITIONAL:
        return f"when this holds: {activation.condition}"
    return _WHEN[activation.load]


def prompt(target: AblationTarget, most: int, kept: Sequence[str] = ()) -> str:
    """The prompt for up to ``most`` situations for ``target``. ``kept`` are situations already kept for it, which a
    top-up call is told not to repeat."""
    provenance = target.source.provenance
    menu = "\n".join(f"  - {m.value}: {m.meaning}" for m in Matters)
    already = ""
    if kept:
        listed = "\n".join(f"  - {text}" for text in kept)
        already = f"\nThese requests are already written and kept; write different ones:\n{listed}\n"
    return f"""You are writing test inputs for a tool that checks a coding agent's configuration.

Here is one part of that configuration (a rule, or a whole skill, agent or hook):

---
{target_text(target)}
---

Where it comes from: {provenance.point.label}, set at {provenance.authority.value} level.
When the agent sees it: {_when(target)}.

Write up to {most} requests a developer might type to the coding agent that would put the agent in a situation where
this part of its configuration becomes relevant.

Each request must be one the agent can only answer by acting: by looking something up with a tool or command, running
a program, creating or changing a file, or using a skill or agent. Never a question it could answer from what it
already knows: no advice, no explanations, no comparisons. The tool compares what the agent does, so a request
answered in words alone tests nothing.

The agent works in a directory that holds the project's configuration (its instruction files and settings) and no
code: no source files, no git history. Each request must make sense there: something the agent can act on from
nothing (create a file, set something up, run a command, look something up), never something that needs existing code.

Requirements:
- Natural phrasing: a real person typing to a coding agent, not a test case.
- Never name this skill, agent, tool, command or hook, nor any tool or skill its text names, and never quote its
  wording. The request must make it relevant without pointing at it.
- Vary them: not {most} rewordings of one request.
- For each request, say in "matters" which part of the agent's actions this configuration should change: a "kind",
  one of
{menu}
  and the thing it is about, "uses", written as skill:<name>, agent:<name>, tool:<name>, program:<name> (a program a
  shell command runs) or file:<path>, or skill:* / agent:* for any skill or agent. Name only what this part of the
  configuration names or plainly means — a skill, agent, tool, program or file it tells the agent to use or avoid.
  For "order" also give "then", what should come after "uses"; for "which_tools" you may give "instead_of", the one
  it should not pick. These names are for checking the agent's actions afterwards; the request itself still names
  none of them. A request whose runs never do "uses" tested nothing.
- If no request could make it relevant, return an empty list and say why in the note.
{already}
Reply with JSON only: {{"situations": [{{"text": "...", "matters": {{"kind": "...", "uses": "..."}}}}], "note": "..."}}
"""
