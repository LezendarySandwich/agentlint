"""What one action of a run did, in core's words: its kind, its name and what it acted on (07-analysis.md, "Effect of
a rule"). The adapter says it for each trace ``Action`` (``TraceReader.action_terms``), because only it knows which of
its harness's tools runs a shell command, reads a file or starts a subagent; core compares runs through these terms
and never names a tool.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from agentlint.trace.model import Action


class ActionKind(Enum):
    SHELL = "shell"  # a shell command; its targets are the programs it runs, in order
    FILE = "file"  # a file read or changed; its target is the path
    SEARCH = "search"  # a search of the files; its target is the path searched
    SKILL = "skill"  # a skill invoked; its target is the skill's name
    AGENT = "agent"  # work delegated to a subagent; its target is the agent's name
    OTHER = "other"  # any other tool, an MCP tool among them: the tool is all there is to compare


@dataclass(frozen=True)
class ActionTerms:
    kind: ActionKind
    name: str  # the tool's name, as the harness names it
    targets: tuple[str, ...] = ()  # what it acted on, as above; () when nothing more than the tool can be told


Terms = Callable[[Action], ActionTerms]  # an adapter's ``action_terms``, as core's projections take it
