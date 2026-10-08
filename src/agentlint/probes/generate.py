"""Generating a target's situations, keeping them, and reviewing them (04-probe-generator.md, "Generating").

The entry point for whatever surface comes next — the review UI first (D17); there is no command. A target's
situations are cached by its text's hash alone (D15): ``generate`` asks a model only when that text has none, or when
asked to regenerate. Every reply passes the strict check, every kept situation the contamination guard; what either
drops, and a target the generator wrote nothing for, is a diagnostic (D16), never a finding. At most one top-up call
follows the first, and only when something the first returned was lost.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from agentlint.config.content import content_hash
from agentlint.config.entity import Entity
from agentlint.probes.contamination import Hit, guard_for
from agentlint.probes.generator import GenerationFailed, Generator
from agentlint.probes.prompt import PROMPT_VERSION, prompt, target_text
from agentlint.probes.validate import Limits, Reason, Rejection, Situation, same_request, schema, situations, text_problem
from agentlint.rank.plan import AblationTarget
from agentlint.store import ReviewStatus, probes, transaction
from agentlint.store import diagnostics as diagnostics_store
from agentlint.store.diagnostics import Diagnostic
from agentlint.store.probes import Probe

MOST_CALLS = 2  # the first call and at most one top-up


class Kind(Enum):
    """The generator's diagnostics: what agentlint tells you about itself, never a finding about your setup."""

    NO_TEXT = "generator_no_text"  # the target has no text to write for (only its size is known): no call made
    FAILED = "generator_failed"  # a call got no reply
    REJECTED = "generator_rejected"  # the strict check rejected a reply, or some of its situations
    CONTAMINATED = "situation_contaminated"  # a situation named its own target, and was dropped
    EMPTY = "generator_empty"  # the generator wrote nothing usable for the target


class EditRefused(ValueError):
    """An edited text that a generated one would have been rejected for."""


@dataclass(frozen=True)
class Generated:
    target_id: str
    target_hash: str
    probes: tuple[Probe, ...]  # what the target text has now: kept from before when nothing new was obtained
    cached: bool  # no call was made: the target's text already had situations
    calls: int
    diagnostics: tuple[Diagnostic, ...]  # what this generation recorded


def target_hash(target: AblationTarget) -> str:
    """The cache key: the content hash of the target's text, nothing else (D15)."""
    return content_hash(target_text(target))


def probe_id(target_hash: str, situation: str) -> str:
    """16 hex digits of a hash of the target's text hash and the situation's text."""
    return hashlib.sha256(f"{target_hash}\n{situation}".encode("utf-8", "surrogatepass")).hexdigest()[:16]


def listed(db: sqlite3.Connection, target: AblationTarget) -> list[Probe]:
    """A target's situations, as kept for its text now, in the order they were written."""
    return probes.for_hash(db, target_hash(target))


def review(db: sqlite3.Connection, probe_id: str, status: ReviewStatus, now: datetime,
           edited_text: str | None = None) -> Probe:
    """Record what review made of a probe. An edited text must pass the rules a generated text passes
    (``validate.text_problem``), or EditRefused; whether it names its target is for review to show, from the guard."""
    if edited_text is not None:
        problem = text_problem(edited_text)
        if problem is not None:
            raise EditRefused(f"{problem[0].value} ({problem[1]})")
    return probes.set_review(db, probe_id, status, now, edited_text)


def _hit(hit: Hit) -> dict:
    term = hit.term
    return {"text": hit.text, "start": hit.start, "end": hit.end,
            "term": None if term is None else {"text": term.text, "kind": term.kind.name, "entity": term.entity_id}}


def _rejections(rejected: Iterable[Rejection]) -> list[dict]:
    return [{"reason": r.reason.name, "index": r.index, "detail": r.detail} for r in rejected]


def generate(db: sqlite3.Connection, target: AblationTarget, entities: Iterable[Entity], generator: Generator,
             model: str, *, now: datetime, regenerate: bool = False, limits: Limits = Limits()) -> Generated:
    """The target's situations: those its text already has, or — none, or ``regenerate`` — up to ``limits.most``
    new ones. New situations replace the old in one transaction, reviews and all; when no new one is obtained the old
    stay, and an EMPTY diagnostic says so. ``entities`` are the configuration's, for the contamination guard."""
    key = target_hash(target)
    existing = tuple(probes.for_hash(db, key))
    if existing and not regenerate:
        return Generated(target.id, key, existing, True, 0, ())
    found: list[Diagnostic] = []

    def diagnose(kind: Kind, message: str, **data) -> None:
        found.append(Diagnostic(kind.value, target.id, message, {"target_hash": key, **data}))

    kept: list[tuple[Situation, str | None]] = []  # each with the model that wrote it
    calls, notes = 0, []
    if not target_text(target).strip():
        diagnose(Kind.NO_TEXT, "This target has no text to write situations for (only its size is known), so no "
                               "call was made.")
    else:
        guard = guard_for(target, entities)
        known = knows(target, entities)
        while calls < MOST_CALLS and len(kept) < limits.most:
            ask = replace(limits, most=limits.most - len(kept))
            calls += 1
            try:
                reply = generator.generate(prompt(target, ask.most, [s.text for s, _ in kept]), schema(ask), model)
            except GenerationFailed as error:
                diagnose(Kind.FAILED, f"Call {calls} got no reply: {error}", call=calls)
                continue  # the top-up is the one retry
            checked = situations(reply.data, ask, known)
            if checked.note:
                notes.append(checked.note)
            lost, contaminated = list(checked.rejected), 0
            for index, situation in enumerate(checked.situations):
                if any(same_request(situation.text) == same_request(s.text) for s, _ in kept):
                    lost.append(Rejection(Reason.DUPLICATE, index, "repeats a situation an earlier call kept"))
                    continue
                hits = guard.hits(situation.text)
                if hits:
                    contaminated += 1
                    diagnose(Kind.CONTAMINATED, "A situation named its own target, so it was dropped.", call=calls,
                             situation=situation.text, matters=situation.matters.to_dict(), hits=[_hit(h) for h in hits])
                    continue
                kept.append((situation, reply.model_used))
            if lost:
                whole = "the whole reply" if not checked.accepted else "part of the reply"
                diagnose(Kind.REJECTED, f"The check rejected {whole} of call {calls}.", call=calls,
                         model=reply.model_used, rejected=_rejections(lost))
            if not lost and not contaminated:
                break  # it wrote what it meant to, fewer than asked included: nothing to top up
    if not kept:
        what = "the old ones are kept" if existing else "it has none"
        diagnose(Kind.EMPTY, f"The generator wrote no usable situation for this target; {what}.", calls=calls,
                 notes=notes)
    new = tuple(Probe(probe_id(key, s.text), target.id, key, s.text, s.matters.stored(), model_used, PROMPT_VERSION, now)
                for s, model_used in kept)
    with transaction(db):
        if new:
            probes.replace(db, key, new)
        for diagnostic in found:
            diagnostics_store.add(db, diagnostic, now)
    return Generated(target.id, key, new or existing, False, calls, tuple(found))


def knows(target: AblationTarget | None, entities: Iterable[Entity]) -> Callable[[str], bool]:
    """Whether the configuration has a thing a situation's matters names (``matters.THING``). Skills and agents are
    what it can list: one must be among its entities by name (a package's prefix optional), or ``skill:*`` /
    ``agent:*`` — any other is a name the generator made up, and the situation is rejected. Tools, programs and files
    it can't list (a harness's own tools, any program on the machine): those are taken as written, and one no run ever
    does shows as a situation that didn't exercise its target."""
    names: dict[str, set[str]] = {"skill": set(), "agent": set()}
    for entity in entities:
        role = entity.kind.role.value
        if role in names:
            for name in (entity.name, *entity.aliases):
                names[role].update((name, name.rsplit(":", 1)[-1]))

    def known(thing: str) -> bool:
        kind, _, name = thing.partition(":")
        if kind in ("skill", "agent"):
            return name == "*" or name in names[kind] or name.rsplit(":", 1)[-1] in names[kind]
        return True  # a tool, program or file: taken as written

    return known
