"""Branches side by side, with the first step where they part marked (06 "Human-readable form").

A finding names what differed, never a hash: each branch's runs and steps on one line, and a caret line under the
first position where the branches' steps are not all the same. Steps before it are equal by definition, so the
position lines up in every branch.
"""

from __future__ import annotations

from collections.abc import Sequence

from agentlint.analysis.stability import Branch

ARROW = " → "
ENDED = "(ends)"  # where a branch has no step at the position: it stopped there


def first_divergence(sequences: Sequence[Sequence[str]]) -> int | None:
    """The first position at which the sequences are not all the same — one ending there counts as differing — or
    None when they are all equal (or there are fewer than two)."""
    if len(sequences) < 2:
        return None
    for position in range(max(len(s) for s in sequences)):
        at = {s[position] if position < len(s) else None for s in sequences}
        if len(at) > 1:
            return position
    return None


def _label(runs: Sequence[int]) -> str:
    return ("run " if len(runs) == 1 else "runs ") + ", ".join(str(n) for n in runs)


def render(branches: Sequence[Branch]) -> str:
    """Each branch as ``runs 0, 1  →  step → step``, then, when they differ, a line marking the first step that
    differs. A branch with no steps shows ``(ends)``."""
    if not branches:
        return ""
    labels = [_label(branch.runs) for branch in branches]
    width = max(len(label) for label in labels)
    position = first_divergence([branch.steps for branch in branches])
    lines = []
    for label, branch in zip(labels, branches):
        steps = list(branch.steps)
        if position is not None and position >= len(steps):
            steps.append(ENDED)
        lines.append(f"{label.ljust(width)}  →  {ARROW.join(steps) if steps else ENDED}")
    if position is None:
        return "\n".join(lines)
    common = ARROW.join(branches[0].steps[:position])
    offset = width + len("  →  ") + len(common) + (len(ARROW) if position else 0)
    marked = max(len(b.steps[position]) if position < len(b.steps) else len(ENDED) for b in branches)
    lines.append(" " * offset + "^" * marked + " differs")
    return "\n".join(lines)
