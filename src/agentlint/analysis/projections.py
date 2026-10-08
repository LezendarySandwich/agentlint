"""Projections: a run's trace reduced to the steps the stability screen compares (07-analysis.md, "Engine B").

``used`` is D10's rule for "your setup makes the agent inconsistent" (decided with the user 2026-10-04, Open Question
2): runs differ only when they used a different SET of tools, skills and agents — the order, repeats and the files or
arguments they were used on are ignored. The usage references come from the trace (``Action.uses``: the tool, the
skill a call invoked, the agent it delegated to), so the rule is harness-agnostic. The exact normalisation is fixed
after the noise study.

The with/without comparison (07-analysis.md, "Effect of a rule") compares two more, over the adapter's action terms
(``trace/terms.py``), so core never names a tool. A shell command counts as the programs it runs (decided with the user
2026-10-05: ``ls; grep … | head`` is ls, grep, head; arguments and wording ignored):

- ``effect`` — what "changes behaviour" compares: the SET of what a run used, each program a shell command ran, each
  skill and agent by name, every other tool by name, and files and searches by kind alone. Order, repeats and arguments
  are ignored, as in ``used``.
- ``strict`` — what "seems to do nothing" compares (D10: "no difference at all — the same tools, in the same order, on
  the same targets"): every action in order with what it acted on, a shell command's targets being its programs, and a
  path shown relative to the run (its working directory as ``./``, its home as ``~/``) so two runs' paths compare.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentlint.trace.model import Trace
from agentlint.trace.terms import ActionKind, Terms


def used(trace: Trace) -> tuple[str, ...]:
    """What the run used, as a sorted set of usage references (``namespace/name``): order, repeats and files ignored."""
    return tuple(sorted(trace.invoked()))


CANON_VERSION = 1  # the version of ``effect`` and ``strict``: raised whenever either rule changes, and stored with every
# result computed under it, so results of two rules are never compared (06-trace.md, "Normalisation must be versioned")


@dataclass(frozen=True)
class RunPlaces:
    """A run's own directories, as its runner made them: where a path the agent named is shown relative from."""

    work: str  # its working directory
    home: str  # its home (the variant home)


def effect(trace: Trace, terms: Terms) -> tuple[str, ...]:
    """What the run used, as a sorted set of terms: ``shell:<program>`` for each program a shell command ran,
    ``skill:<name>``, ``agent:<name>``, ``tool:<name>`` for any other tool, and ``file`` / ``search`` for the file tools
    by kind alone. Order, repeats, arguments and files are ignored. A shell command with no program read is ``shell``; a
    skill or agent call naming none is its kind."""
    used: set[str] = set()
    for action in trace.actions:
        said = terms(action)
        if said.kind in (ActionKind.SHELL, ActionKind.SKILL, ActionKind.AGENT):
            used.update(f"{said.kind.value}:{target}" for target in said.targets)
            if not said.targets:
                used.add(said.kind.value)
        elif said.kind in (ActionKind.FILE, ActionKind.SEARCH):
            used.add(said.kind.value)
        else:
            used.add(f"tool:{said.name}")
    return tuple(sorted(used))


def strict(trace: Trace, terms: Terms, places: RunPlaces | None = None) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """Every action in order as (kind, tool, what it acted on): a shell command's programs, a file's or search's path
    relative to the run (``places``; None leaves paths as recorded), a skill's or agent's name."""
    steps = []
    for action in trace.actions:
        said = terms(action)
        targets = said.targets
        if places is not None and said.kind in (ActionKind.FILE, ActionKind.SEARCH):
            targets = tuple(relative(path, places) for path in targets)
        steps.append((said.kind.value, said.name, targets))
    return tuple(steps)


def relative(path: str, places: RunPlaces) -> str:
    """A path under the run's working directory as ``./…`` (the directory itself ``.``), under its home as ``~/…``;
    any other path as written. The runner's directories are real paths, and an agent may name one either way (macOS's
    /tmp is /private/tmp), so both spellings are taken."""
    for root, shown in ((places.work, "."), (places.home, "~")):
        for spelling in {root, root.removeprefix("/private")}:
            if path == spelling:
                return shown
            if path.startswith(spelling.rstrip("/") + "/"):
                return f"{shown}/{path[len(spelling.rstrip('/')) + 1:]}"
    return path
