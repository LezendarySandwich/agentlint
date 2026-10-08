"""duplicate-name: entities the agent chooses between that share a name.

Grouped by namespace (entities chosen from the same list) and short name — the name without
what provides it (the harness says how names are prefixed), so ``data:sql-query`` and
``analytics:sql-query`` collide. One name declared twice in
one package (a skill and a command) is one choice for the agent, not two namesakes; the one it was
shown stands for both.

Each group is described mechanically, never by judgement, from the files behind its members:

    identical copies         every declaration is the same file content as another
    N of M identical copies  some declarations are copies of each other; the rest differ
    same description         identical descriptions over different bodies — likely drifted copies
    different descriptions   could be drifted copies or unrelated namesakes; we don't guess which

Copies are judged between declarations — a name in one scope (one package, the user's, the
project's) — not between names: a user and a project skill of one name are two declarations, and
when only one of them is a copy the group is "N of M declarations", never "identical copies".
A member known only from a session (a harness built-in, a package not found on disk) has no file
to compare, so it is never counted as a copy, and a group with such members and one description is
"same description" with no claim about bodies. A declaration counts as a copy when any entity
behind it matches another declaration's file — a twin the agent wasn't shown still counts — and
each copy is marked with one it matches (``Member.identical_to``).

Every entity behind the group is one of the finding's ``members`` — name, kind, the source and where it sits, what
the agent saw of it, its description, its copy mark and symlink — by name, each entity of a name declared twice beside the other; the
renderers lay them out (the terminal's lines, the UI's columns) and ``detail`` says only what the exposure means.
A finding rests on every source behind its members, the listing that showed a session-only member
included: when that listing goes unobserved, the finding is neither fixed nor its suppression lapsed. Exposure comes from what the agent saw: **active**
when it was shown two or more of them, **latent** when it wasn't, and **unknown** when no recent
session shows what it saw.
"""

from __future__ import annotations

from collections import defaultdict

from agentlint.checks import Check, member
from agentlint.config.content import FileText
from agentlint.config.entity import Entity, Visibility
from agentlint.config.model import ConfigSnapshot
from agentlint.config.reading import FileFacts
from agentlint.config.taxonomy import Mechanism
from agentlint.report.finding import Confidence, Figure, Finding, FindingKind, Location, Severity

_SHOWN = (Visibility.DESCRIBED, Visibility.NAME_ONLY)


def _symlink(snapshot: ConfigSnapshot, facts: FileFacts, entity: Entity) -> str | None:
    """Where a member's file links to: a copy that is a symlink may be recreated by whatever installed it."""
    try:
        source = snapshot.source(entity.provided_by)
    except KeyError:
        return None
    link = facts.symlinks.get(source.provenance.file) if source.provenance.file else None
    return link[1] if link else None


def _scope(snapshot: ConfigSnapshot, entity: Entity) -> str:
    """Where a declaration sits, to tell apart two of one kind under one name (a user and a project skill)."""
    try:
        return f" ({snapshot.source(entity.provided_by).provenance.authority.value})"
    except KeyError:
        return ""


def file_hash(snapshot: ConfigSnapshot, entity: Entity) -> str | None:
    """The content hash of the file that defines this entity — None if it has no file of its own."""
    try:
        source = snapshot.source(entity.provided_by)
    except KeyError:
        return None
    if not isinstance(source.content, FileText) or source.provenance.point.mechanism in (
            Mechanism.HARNESS_GENERATED, Mechanism.HARNESS_BUILTIN):
        return None  # known from a session's listing, not from a file: nothing to compare
    return source.content.content_hash


def _plural(label: str, n: int) -> str:
    return label if n == 1 else (label[:-1] + "ies" if label.endswith("y") else label + "s")


def _noun(entities: list[Entity], count: int) -> str:
    """"skills" when every entity is a skill; "entries" when kinds are mixed (the detail names each one)."""
    labels = {e.kind.label for e in entities}
    return _plural(labels.pop(), count) if len(labels) == 1 else ("entry" if count == 1 else "entries")


def run(snapshot: ConfigSnapshot, facts: FileFacts) -> list[Finding]:
    groups: dict[tuple[str, str], dict[str, list[Entity]]] = defaultdict(lambda: defaultdict(list))
    for entity in snapshot.entities:
        groups[(entity.kind.namespace, entity.short_name)][entity.name].append(entity)
    findings = []
    for (namespace, name), by_name in sorted(groups.items()):
        if len(by_name) < 2:
            continue  # one name — perhaps declared twice in one place, which is not a choice between namesakes
        members = sorted((min(same, key=lambda e: Visibility.rank_seen(snapshot.seen_as(e.id))) for same in by_name.values()),
                         key=lambda e: e.name)
        seen = {e.id: snapshot.seen_as(e.id) for e in members}
        shown = [e for e in members if seen[e.id] in _SHOWN]
        described = [e for e in members if seen[e.id] is Visibility.DESCRIBED]
        exposure = ("active" if len(shown) >= 2 else
                    "unknown" if any(seen[e.id] is None for e in members) else "latent")

        # Copies are judged between declarations: a name in one scope (one package, the user's, the project's).
        # Each declaration counts as a copy when any entity behind it matches another declaration's file, so a twin
        # the agent wasn't shown still counts; a name declared in two scopes is two declarations, which can drift apart.
        everyone = [e for same in by_name.values() for e in same]
        files = {e.id: file_hash(snapshot, e) for e in everyone}
        scope = {e.id: _scope(snapshot, e) for e in everyone}
        units: dict[tuple[str, str], list[Entity]] = defaultdict(list)
        for e in everyone:
            units[(e.name, scope[e.id])].append(e)
        units_by_file: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for key, same in units.items():
            for e in same:
                if files[e.id] is not None and key not in units_by_file[files[e.id]]:
                    units_by_file[files[e.id]].append(key)
        copy_sets = [keys for keys in units_by_file.values() if len(keys) >= 2]
        copied = {key for keys in copy_sets for key in keys}
        shown_of = [min(same, key=lambda e: Visibility.rank_seen(snapshot.seen_as(e.id))) for same in units.values()]
        all_on_disk = all(any(files[e.id] is not None for e in same) for same in units.values())
        descriptions = {" ".join((e.description or "").split()) for e in shown_of}
        counted = "" if len(units) == len(members) else " declarations"  # a name declared in two scopes
        if copy_sets and len(copied) == len(units):
            variety = "identical copies" if len(copy_sets) == 1 else "two or more sets of identical copies"
            action = "Remove all but one copy." if len(copy_sets) == 1 else "Remove the extra copies in each set."
        elif copy_sets:
            variety = f"{len(copied)} of {len(units)}{counted} are identical copies"
            action = ("Remove the extra copies, then compare what remains: a member that differs may have drifted "
                      "from the copies, or may do a different job.")
        elif len(descriptions) == 1 and descriptions != {""}:
            variety = "same description, different bodies" if all_on_disk else "same description"
            action = ("Probably copies that have drifted apart: keep the one you want and remove the rest — or, if "
                      "they really do different jobs, rename one and say so in its description.")
        else:
            variety = "different descriptions"
            action = "Rename one so the names say what each does, or remove the one you don't use."

        # A copy mark goes on the very entity whose file matches — by entity, not by name — and names the entity of
        # another declaration it matches. Every entity behind the group is a member, a name's twins included.
        listed = [e for first in members for e in
                  (sorted(by_name[first.name], key=lambda o: (o.kind.label, o.id)) if len(by_name[first.name]) > 1
                   else [first])]
        twin_of: dict[str, str] = {}
        for e in listed:
            match = next((o for o in everyone if (o.name, scope[o.id]) != (e.name, scope[e.id])
                          and files[e.id] is not None and files[o.id] == files[e.id]), None)
            if match:
                twin_of[e.id] = match.id
        if exposure == "active":
            seen_text = (f"The agent was shown {len(shown)} of them ({len(described)} with a description), so asking "
                         f"for \"{name}\" leaves it to pick one.")
        elif exposure == "latent":
            seen_text = "Latent: the agent wasn't shown two of them in the latest session, but either could become visible."
        else:
            seen_text = "No recent session shows what the agent saw of them, so whether it chooses between them is unknown."

        locations = []
        for e in everyone:  # every file behind the group, the hidden twins' too
            try:
                source = snapshot.source(e.provided_by)
            except KeyError:
                continue
            if files[e.id] is not None and all(l.source_id != source.id for l in locations):
                locations.append(Location(source.id))
        findings.append(CHECK.finding(
            kind=FindingKind.COLLISION,
            severity=Severity.INFO if exposure == "latent" else Severity.WARN,
            confidence=Confidence.CERTAIN,
            title=f"{len(members)} {_noun(everyone, len(members))} named \"{name}\" — {variety}"
                  + ("" if exposure == "active" else f" ({exposure})" if exposure == "latent" else " (exposure unknown)"),
            detail=seen_text,
            discriminator=f"{namespace}:{name}",
            sources=tuple(dict.fromkeys(e.provided_by for e in everyone)),  # a session-only member's listing too:
            # unobserved, it keeps the group from being closed falsely (and a source unobserved can't lapse a suppression either)
            locations=tuple(locations),
            figures=(Figure("members", len(members), "names", "name"), Figure("shown", len(shown), "names", "name")),
            suggested_action=action,
            members=tuple(member(snapshot, e, identical_to=twin_of.get(e.id), symlink=_symlink(snapshot, facts, e))
                          for e in listed),
        ))
    return findings


CHECK = Check("duplicate-name", "entities the agent chooses between that share a name",
              lambda reading: run(reading.snapshot, reading.files),
              needs_change=True)  # a member only sessions show can leave the window with nothing changed
