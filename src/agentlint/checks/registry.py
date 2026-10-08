"""Every harness-agnostic check, in the order they run."""

from __future__ import annotations

from agentlint.checks import (Check, delivery, directives, duplicate_name, emphasis, references, tested, token_budget,
                             trigger_overlap)

CORE_CHECKS: list[Check] = [
    token_budget.CHECK,
    duplicate_name.CHECK,
    trigger_overlap.CHECK,
    delivery.UNTRACED,
    delivery.DEFINITIONS,
    references.CHECK,
    emphasis.CHECK,
    tested.EXPECTATION,
    tested.CONSISTENCY,
    directives.CONFLICT,
    directives.REPEAT,
]
