"""What changed between two snapshots of one project's configuration — `agentlint show --diff`.

Works on two ``ConfigSnapshot``s — the stored one read back by ``store/snapshots.py`` — with the model's own rules:
labels as `agentlint show` gives them, ``Load.in_first_turn``, a source's ``tokens``. A source whose declared hash
differs is **changed**; one present on a side only is **added** or **removed**. A source in context every session
whose configuration is the same but whose delivered text differs is listed apart — a hook printing something new, or
one whose output a session showed on one side only: the configuration didn't change, what was seen of it did.
Untraced text gets its own lines, so every token behind the always-on change has one. The always-on figure is
computed the same way on both sides — ``assemble.attributed_latest``, what the always-on sources and untraced text
attribute — and leaves out sources whose text no session showed; a token count that isn't known is shown as "?",
never as 0. Texts are named by their content hash, which is also how the store keeps them.

Entities are compared only when defined on disk: tools and built-ins known from sessions come and go with the
sessions read and the harness version, so their absence says nothing about configuration.
"""

from __future__ import annotations

import difflib
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from agentlint.config.assemble import attributed_latest
from agentlint.config.content import FileText, ObservedText
from agentlint.config.entity import Entity
from agentlint.config.model import ConfigSnapshot, Source
from agentlint.config.taxonomy import Load, Mechanism
from agentlint.report.paths import display
from agentlint.report.show import label

TEXT_LINES = 40  # per source, with --text

_HARNESS_MADE = (Mechanism.HARNESS_BUILTIN, Mechanism.HARNESS_GENERATED)


@dataclass(frozen=True)
class Change:
    kind: str  # "changed" | "added" | "removed" | "output" | "untraced"
    source_id: str
    label: str
    load: Load
    before: int | None  # tokens; None when absent on that side or not observed there
    after: int | None
    before_ref: str | None  # the text's content hash, when that side has text
    after_ref: str | None
    note: str = ""  # e.g. "newly observed", "no longer observed"

    @property
    def delta(self) -> int | None:
        """The token change, or None when a side that should have a count doesn't."""
        if self.kind == "added":
            return self.after
        if self.kind == "removed":
            return None if self.before is None else -self.before
        if self.before is None and self.after is None:
            return None
        if self.kind == "untraced" and (self.before is None) != (self.after is None):
            return (self.after or 0) - (self.before or 0)  # compared only when both readings could see it
        if self.before is None or self.after is None:
            return None
        return self.after - self.before


@dataclass(frozen=True)
class SnapshotDiff:
    before: ConfigSnapshot
    after: ConfigSnapshot
    always_before: int
    always_after: int
    changes: tuple[Change, ...]  # largest token change first, within each kind
    entities_added: tuple[tuple[str, str], ...]  # (kind label, name)
    entities_removed: tuple[tuple[str, str], ...]
    untraced_compared: bool = True  # both readings had a session from this project


def _tokens(source: Source) -> int | None:
    tokens = source.tokens
    return tokens.value if tokens is not None else None


def _ref(source: Source) -> str | None:
    """The source's text by its content hash — the name the store keeps it under — when it has text."""
    content = source.content
    return content.content_hash if isinstance(content, (FileText, ObservedText)) else None


def always_on(snapshot: ConfigSnapshot, *, untraced: bool = True) -> int:
    return attributed_latest(snapshot.sources, snapshot.untraced if untraced else ())


def knows_untraced(snapshot: ConfigSnapshot) -> bool:
    """Untraced text is read from this project's sessions only; without one, its absence means nothing."""
    return bool(snapshot.project_sessions)


def untraced_left_out(result: SnapshotDiff) -> bool:
    """Whether the comparison leaves untraced text out and that matters: one reading had no session from this project,
    and at least one side has untraced text. `show --diff` and the UI's What changed note it on this rule alone."""
    return not result.untraced_compared and bool(result.before.untraced or result.after.untraced)


def unseen(snapshot: ConfigSnapshot) -> int:
    """Always-on sources whose text no session showed: the always-on figure leaves them out."""
    return sum(1 for s in snapshot.sources if s.activation.load.in_first_turn and s.tokens is None)


def _declared_entities(snapshot: ConfigSnapshot) -> dict[tuple[str, str, str], Entity]:
    """Entities defined on disk, by what defines them — source, kind and name — not by entity id, whose form an
    adapter may change (2026-10-03: ids gained their scope) with nothing configured changing."""
    on_disk = {s.id for s in snapshot.sources
               if isinstance(s.content, FileText) and s.provenance.point.mechanism not in _HARNESS_MADE}
    return {(e.provided_by, e.kind.name, e.name): e for e in snapshot.entities if e.provided_by in on_disk}


def _untraced(snapshot: ConfigSnapshot) -> dict[str, tuple[int, list[str], Load]]:
    """Untraced always-on text by identity: tokens summed over the labels it arrived under, labels, load."""
    grouped: dict[str, tuple[int, list[str], Load]] = {}
    for u in snapshot.untraced:
        if not u.load.in_first_turn:
            continue
        ref = u.content.content_hash
        total, labels, load = grouped.get(ref, (0, [], u.load))
        grouped[ref] = (total + u.content.tokens.value, labels + [u.label], load)
    return grouped


def diff(before: ConfigSnapshot, after: ConfigSnapshot) -> SnapshotDiff:
    old = {s.id: s for s in before.sources}
    new = {s.id: s for s in after.sources}
    old_names, new_names = before.entities_by_source, after.entities_by_source
    changes = []

    def change(kind: str, sid: str) -> Change:
        b, a = old.get(sid), new.get(sid)
        shown = a or b
        return Change(kind, sid, label(shown, new_names if a else old_names), shown.activation.load,
                      _tokens(b) if b else None, _tokens(a) if a else None,
                      _ref(b) if b else None, _ref(a) if a else None)

    for sid in new.keys() - old.keys():
        changes.append(change("added", sid))
    for sid in old.keys() - new.keys():
        changes.append(change("removed", sid))
    for sid in old.keys() & new.keys():
        b, a = old[sid], new[sid]
        if b.declared_hash != a.declared_hash:
            changes.append(change("changed", sid))
            continue
        seen_before = isinstance(b.content, ObservedText)
        seen_after = isinstance(a.content, ObservedText)
        if a.activation.load.in_first_turn and (seen_before or seen_after) and _ref(b) != _ref(a):
            note = "" if seen_before and seen_after else ("newly observed" if seen_after else "no longer observed")
            changes.append(replace(change("output", sid), note=note))
    untraced = knows_untraced(before) and knows_untraced(after)
    then, now = (_untraced(before), _untraced(after)) if untraced else ({}, {})
    for ref in then.keys() | now.keys():
        b, a = then.get(ref), now.get(ref)
        if b and a and b[0] == a[0]:
            continue
        note = ("now delivered" if b is None else "no longer delivered" if a is None else
                f"under {len(a[1])} label{'s' if len(a[1]) != 1 else ''}, was {len(b[1])}")
        changes.append(Change("untraced", ref, "; ".join(dict.fromkeys((a or b)[1])), (a or b)[2],
                              b[0] if b else None, a[0] if a else None, ref if b else None, ref if a else None, note))
    order = {"changed": 0, "added": 1, "removed": 2, "output": 3, "untraced": 4}
    changes.sort(key=lambda c: (order[c.kind], -abs(c.delta or 0), c.label))
    old_entities = _declared_entities(before)
    new_entities = _declared_entities(after)
    added = sorted((new_entities[i].kind.label, new_entities[i].name) for i in new_entities.keys() - old_entities.keys())
    removed = sorted((old_entities[i].kind.label, old_entities[i].name) for i in old_entities.keys() - new_entities.keys())
    return SnapshotDiff(before, after, always_on(before, untraced=untraced), always_on(after, untraced=untraced),
                        tuple(changes), tuple(added), tuple(removed), untraced)


def _signed(n: int | None) -> str:
    return "?" if n is None else f"{n:+,}".replace("-", "−")


def _entity_line(word: str, pairs: tuple[tuple[str, str], ...]) -> list[str]:
    by_kind: dict[str, list[str]] = defaultdict(list)
    for kind, name in pairs:
        by_kind[kind].append(name)
    return [f"  {word} {len(names)} {kind}{'s' if len(names) != 1 else ''}: {', '.join(names[:6])}"
            + (f", … {len(names) - 6} more" if len(names) > 6 else "") for kind, names in sorted(by_kind.items())]


def _text_diff(change: Change, text_of: Callable[[str], str | None]) -> list[str]:
    before = text_of(change.before_ref) if change.before_ref else None
    after = text_of(change.after_ref) if change.after_ref else None
    if before is None or after is None:
        return []  # seen on one side only: nothing to set against
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=1))[2:]
    shown = [f"        {line}" for line in lines[:TEXT_LINES]]
    return shown + ([f"        … {len(lines) - TEXT_LINES} more lines"] if len(lines) > TEXT_LINES else [])


def render(result: SnapshotDiff, taken_before: datetime, *, text_of: Callable[[str], str | None] | None = None) -> str:
    before, after = result.before, result.after
    short = lambda snapshot: snapshot.fingerprint.removeprefix("sha256:")[:8]  # noqa: E731
    lines = [f"agentlint show --diff — {display(str(after.cwd))}",
             f"Comparing {taken_before.astimezone():%Y-%m-%d %H:%M} ({short(before)}) with now ({short(after)})"]
    for field, word in (("harness_version", "harness"), ("model", "model")):
        if getattr(before, field) != getattr(after, field):
            lines.append(f"{word} {getattr(before, field)} → {getattr(after, field)}")
    difference = result.always_after - result.always_before
    lines += ["", f"Always-on, attributed: ≈{result.always_before:,} → ≈{result.always_after:,} tokens "
                  f"({_signed(difference)})"]
    explained = sum(c.delta for c in result.changes if c.delta is not None and c.load.in_first_turn)
    if difference != explained:
        lines.append(f"  of which {_signed(difference - explained)} is text seen only then or only now (marked ?): "
                     "whether it changed in size isn't known")
    left_out = (unseen(before), unseen(after))
    if any(left_out):
        lines.append(f"  (leaves out always-on sources whose text no session showed: {left_out[0]} then, {left_out[1]} now)")
    if untraced_left_out(result):
        lines.append("  (leaves out untraced text: one of the readings had no session from this project)")
    headings = {"changed": "CHANGED — configuration that differs", "added": "ADDED", "removed": "REMOVED",
                "output": "SAME CONFIGURATION — delivered text changed, or seen only then or only now",
                "untraced": "UNTRACED TEXT — delivered every session, explained by no configuration"}
    for kind, heading in headings.items():
        group = [c for c in result.changes if c.kind == kind]
        if not group:
            continue
        lines += ["", heading]
        for c in group:
            note = f"  ({c.note})" if c.note else ""
            lines.append(f"  {_signed(c.delta):>8}  {c.label}  [{c.load.value.replace('_', ' ')}]{note}")
            if text_of is not None and kind in ("changed", "output"):
                lines += _text_diff(c, text_of)
    if result.entities_added or result.entities_removed:
        lines += ["", "ENTITIES (defined on disk)"] + _entity_line("added", result.entities_added) + _entity_line(
            "removed", result.entities_removed)
    if not result.changes and not result.entities_added and not result.entities_removed:
        lines += ["", "No source changed."]
    elif text_of is None and any(c.kind in ("changed", "output") for c in result.changes):
        lines += ["", "(--text prints what changed in each text)"]
    return "\n".join(lines) + "\n"
