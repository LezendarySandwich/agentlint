"""The closed vocabularies core logic reasons about, and the value objects adapters build on them.

Core branches only on the small generic enums here. Each adapter defines its own ``Enum`` whose
values are ``InjectionPoint`` / ``EntityKind`` instances; the harness-specific names (a particular
instruction file, a particular listing) live there and reach core only as these value objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Mechanism(Enum):
    """How text physically reaches the agent. Each implies a different discovery and ablation path."""

    HARNESS_BUILTIN = "harness_builtin"  # the harness's own prompt and tool definitions
    CONFIG_FILE = "config_file"  # settings files
    INSTRUCTION_FILE = "instruction_file"  # free-text instruction files
    PACKAGE_ENTITY = "package_entity"  # an entity definition shipped in a package
    HOOK_OUTPUT = "hook_output"  # text a user-configured command injects at runtime
    HARNESS_GENERATED = "harness_generated"  # listings the harness composes from what is installed


class Authority(Enum):
    """Position in the precedence order: harness > managed > user > project > package > runtime.

    ``HARNESS`` is the harness's own prompt and tools — not the organisation's policy, which is
    ``MANAGED``. Conflating them would make org policy look like it costs the harness's tokens.
    """

    HARNESS = "harness"
    MANAGED = "managed"
    USER = "user"
    PROJECT = "project"
    PACKAGE = "package"
    RUNTIME = "runtime"


class Load(Enum):
    """When a source's text is in the agent's context."""

    ALWAYS = "always"  # from the first turn of every session
    PER_MESSAGE = "per_message"  # re-injected with every user message; cost grows with the conversation
    ON_INVOKE = "on_invoke"  # when the entity it defines is invoked
    CONDITIONAL = "conditional"  # when a condition holds, e.g. before a particular tool call
    NOT_IN_CONTEXT = "not_in_context"  # configures the harness; the text itself never reaches the agent

    @property
    def in_first_turn(self) -> bool:
        """Whether this text is part of what the agent receives before the user's first words."""
        return self in (Load.ALWAYS, Load.PER_MESSAGE)


class EntityRole(Enum):
    """What kind of thing an entity is, as far as core logic needs to know."""

    TOOL = "tool"  # a function the agent calls directly
    SKILL = "skill"  # an instruction bundle the agent chooses by name or description
    AGENT = "agent"  # a sub-agent the agent delegates to
    COMMAND = "command"  # an executable the agent runs in a shell
    PATH = "path"  # a file or directory
    DOMAIN = "domain"  # a network location


_HARNESS = re.compile(r"[a-z][a-z0-9-]*")
_NAME = re.compile(r"[a-z][a-z0-9_]*")


def _check_identity(owner: str, harness: str, name: str, label: str) -> None:
    if not _HARNESS.fullmatch(harness):
        raise ValueError(f"{owner}.harness must be lowercase-kebab, got {harness!r}")
    if not _NAME.fullmatch(name):
        raise ValueError(f"{owner}.name must be lowercase_snake, got {name!r}")
    if not label.strip():
        raise ValueError(f"{owner}.label must not be empty")


@dataclass(frozen=True)
class InjectionPoint:
    """A specific way text reaches the agent in one harness.

    Adapters define these as members of their own Enum; core sees only this value object and
    reasons about ``mechanism``, never about ``name``.
    """

    harness: str  # lowercase-kebab harness id
    name: str  # stable identifier within the harness, lowercase_snake
    label: str  # what the user sees
    mechanism: Mechanism
    description: str = field(default="", compare=False)  # what this is, in a sentence, for text the user didn't write

    def __post_init__(self) -> None:
        _check_identity("InjectionPoint", self.harness, self.name, self.label)
        if not isinstance(self.mechanism, Mechanism):
            raise TypeError(f"InjectionPoint.mechanism must be a Mechanism, got {self.mechanism!r}")


@dataclass(frozen=True)
class EntityKind:
    """A specific kind of entity in one harness, e.g. its skills or its sub-agents.

    ``namespace`` groups kinds the agent chooses from in one list: two entities can only collide
    by name if they share a namespace. ``separator`` is how this harness prefixes a name with what
    provides it (``pkg:name``), so core can tell an entity's own name without knowing the convention;
    None when names carry no prefix.
    """

    harness: str
    name: str
    label: str
    role: EntityRole
    namespace: str
    separator: str | None = None

    def __post_init__(self) -> None:
        _check_identity("EntityKind", self.harness, self.name, self.label)
        if not isinstance(self.role, EntityRole):
            raise TypeError(f"EntityKind.role must be an EntityRole, got {self.role!r}")
        if not _NAME.fullmatch(self.namespace):
            raise ValueError(f"EntityKind.namespace must be lowercase_snake, got {self.namespace!r}")
