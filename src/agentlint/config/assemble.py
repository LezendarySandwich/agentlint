"""Merge what disk declares with what recent sessions show, into one ConfigSnapshot.

Adapters produce a ``Declared`` (disk) and a list of ``SessionDeliveries`` records (the recent sessions). The merge
rules live here, once, so every adapter gets the same behaviour:

- A declared source with NotObserved content takes its text from the most recent eligible session
  that delivered it, and records every session it was seen in.
- Project-scoped sources only learn from sessions in this project; the harness's own built-in text
  only from sessions on the same harness version. Anything else learns from any session.
- Text a session delivered that traces to nothing on disk becomes ``Untraced``: counted, never
  fingerprinted.
- The always-on total comes from the most recent session in this project on this harness version,
  and is reconciled against what *that* session delivered (plus files on disk) — not against the
  latest observation of each source, which may come from a session that delivered different text
  (an interactive session's tool set differs from a headless one's). The outcome carries that ``Attribution``,
  source by source, computed here once; its sums are derived from it, and readers group it.
- Entities only a session can show come from the most recent session on this harness version that
  reports any; declared entities win on id, and an observed entity must name a declared source.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from agentlint.config.content import NotObserved, observed_text, tokens_of
from agentlint.config.entity import Entity
from agentlint.config.model import (
    AlwaysOn,
    Attribution,
    ConfigSnapshot,
    Declared,
    NotReconciled,
    Overcounted,
    Reconciled,
    Source,
    Untraced,
)
from agentlint.config.sessions import Delivery, SessionDeliveries, SessionHeader, same_dir
from agentlint.config.taxonomy import Authority, Mechanism
from agentlint.config.tokens import estimate_tokens


def _digest(payload: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def config_hash(sources: Sequence[Source]) -> str:
    """Hash of the declared configuration only. Observed text never enters it."""
    return _digest(
        sorted([s.id, s.declared_hash, s.provenance.authority.value, s.activation.load.value] for s in sources)
    )


def fingerprint(config: str, harness_version: str | None, model: str | None) -> str:
    """What makes two results comparable: the same configuration on the same harness and model."""
    return _digest([config, harness_version, model])


def _in_project(session: SessionDeliveries, declared: Declared) -> bool:
    return same_dir(session.header.project, declared.cwd)


def _same_version(session: SessionDeliveries, declared: Declared) -> bool:
    return declared.harness_version is None or session.header.harness_version == declared.harness_version


def could_show(source: Source, header: SessionHeader, cwd: Path, harness_version: str | None) -> bool:
    """Whether a session could have delivered a source: a project's sources only in that project's
    sessions, the harness's own text only in sessions on the same version."""
    if source.provenance.authority is Authority.PROJECT and not same_dir(header.project, cwd):
        return False
    if source.provenance.point.mechanism is Mechanism.HARNESS_BUILTIN and harness_version is not None:
        return header.harness_version == harness_version
    return True


def _eligible(source: Source, declared: Declared, recent: list[SessionDeliveries]) -> list[SessionDeliveries]:
    return [s for s in recent if could_show(source, s.header, declared.cwd, declared.harness_version)]


def _join(parts: list[Delivery]) -> str:
    return "\n".join(part.text for part in parts)


def _observe(source: Source, declared: Declared, recent: list[SessionDeliveries]) -> Source:
    if not isinstance(source.content, NotObserved):
        return source
    eligible = _eligible(source, declared, recent)
    eligible = [s for s in eligible if s.header.interactive] + [s for s in eligible if not s.header.interactive]
    hits = [
        (session, parts)
        for session in eligible
        if (parts := [d for d in session.deliveries if d.source_id == source.id])
    ]
    if not hits:
        reason = (
            f"not delivered in any of the {len(eligible)} sessions examined"
            if eligible
            else source.content.reason
        )
        return replace(source, content=NotObserved(len(eligible), reason))
    _, latest = hits[0]
    seen_in = tuple(sorted((session.header for session, _ in hits), key=lambda header: header.started_at, reverse=True))
    return replace(source, content=observed_text(latest[0].format, _join(latest), seen_in, len(eligible)))


def _untraced(declared: Declared, recent: list[SessionDeliveries]) -> tuple[Untraced, ...]:
    """Untraceable deliveries from the most recent session in this project.

    Only this project's sessions are used: another project's untraceable text may belong to
    configuration that does not apply here.
    """
    local = [s for s in recent if _in_project(s, declared)]
    if not local:
        return ()
    known = {source.id for source in declared.sources}

    def untraced_parts(session: SessionDeliveries) -> dict[tuple, list[Delivery]]:
        groups: dict[tuple, list[Delivery]] = {}
        for delivery in session.deliveries:
            if delivery.source_id is None or delivery.source_id not in known:
                groups.setdefault((delivery.point, delivery.key or delivery.label), []).append(delivery)
        return groups

    per_session = [(session, untraced_parts(session)) for session in local]
    found = []
    for key, parts in per_session[0][1].items():
        seen_in = tuple(session.header for session, groups in per_session if key in groups)
        point = key[0]
        content = observed_text(parts[0].format, _join(parts), seen_in, len(local))
        found.append(Untraced(point, parts[0].label, parts[0].load, content, parts[0].key))
    return tuple(found)


def latest_attribution(sources: Sequence[Source], untraced: Sequence[Untraced]) -> tuple[Attribution, ...]:
    """What each always-on source last delivered (possibly in different sessions), and each untraced text."""
    found = [Attribution(source.id, tokens.value) for source in sources
             if source.activation.load.in_first_turn and (tokens := tokens_of(source.content)) is not None]
    found += [Attribution(None, u.content.tokens.value, u.label) for u in untraced if u.load.in_first_turn]
    return tuple(found)


def attributed_latest(sources: Sequence[Source], untraced: Sequence[Untraced]) -> int:
    """The sum of ``latest_attribution``: what `show --diff` compares, computed the same way on both sides."""
    return sum(a.tokens for a in latest_attribution(sources, untraced))


def _attribution_in(session: SessionDeliveries, declared: Declared) -> tuple[Attribution, ...]:
    """What a session's first turn delivered that we can attribute: files on disk, plus what that same session
    delivered for each observable source, plus its untraced text, summed per label it arrived under."""
    delivered: dict[str | None, list[Delivery]] = {}
    for delivery in session.deliveries:
        if delivery.load.in_first_turn:
            delivered.setdefault(delivery.source_id, []).append(delivery)
    found = []
    known = set()
    for source in declared.sources:
        known.add(source.id)
        if not source.activation.load.in_first_turn:
            continue
        if isinstance(source.content, NotObserved):
            if parts := delivered.get(source.id, []):
                found.append(Attribution(source.id, estimate_tokens(_join(parts))))
        elif (tokens := tokens_of(source.content)) is not None:
            found.append(Attribution(source.id, tokens.value))
    untraced: dict[str, int] = {}
    for source_id, parts in delivered.items():
        if source_id is None or source_id not in known:
            for part in parts:
                untraced[part.label] = untraced.get(part.label, 0) + estimate_tokens(part.text)
    return tuple(found) + tuple(Attribution(None, tokens, label) for label, tokens in untraced.items())


def _reconcile(declared: Declared, recent: list[SessionDeliveries], latest: tuple[Attribution, ...]) -> AlwaysOn:
    for session in recent:
        if _in_project(session, declared) and _same_version(session, declared) and session.always_on_total:
            total = session.always_on_total
            in_session = _attribution_in(session, declared)
            if sum(a.tokens for a in in_session) <= total.value:
                return Reconciled(total, in_session, session.header)
            return Overcounted(total, in_session, session.header)
    if not recent:
        reason = "no sessions examined"
    else:
        reason = "no recent session in this project on this harness version reported a total"
    return NotReconciled(latest, reason)


def _observed_entities(declared: Declared, recent: list[SessionDeliveries]) -> tuple[Entity, ...]:
    """The union over sessions on this harness version: a headless run's smaller tool set must not
    make an interactive session's tools look missing."""
    known_ids = {entity.id for entity in declared.entities}
    sources = {source.id for source in declared.sources}
    found: dict[str, Entity] = {}
    for session in recent:  # most recent first, so the newest description wins
        if not _same_version(session, declared):
            continue
        for entity in session.entities:
            if entity.id not in known_ids and entity.provided_by in sources:
                found.setdefault(entity.id, entity)
    return tuple(found.values())


def _visibility(recent: list[SessionDeliveries]) -> tuple:
    """From the most recent session that reports any: what the agent saw of each entity."""
    for session in [s for s in recent if s.header.interactive] + [s for s in recent if not s.header.interactive]:
        if session.visibility:
            return session.visibility
    return ()


def assemble(declared: Declared, sessions: Sequence[SessionDeliveries]) -> ConfigSnapshot:
    recent = sorted(sessions, key=lambda s: s.header.started_at, reverse=True)
    sources = tuple(_observe(source, declared, recent) for source in declared.sources)
    untraced = _untraced(declared, recent)
    config = config_hash(declared.sources)
    return ConfigSnapshot(
        harness=declared.harness,
        harness_version=declared.harness_version,
        model=declared.model,
        cwd=declared.cwd,
        sources=sources,
        entities=declared.entities + _observed_entities(declared, recent),
        untraced=untraced,
        sessions=tuple(session.header for session in recent),
        always_on=_reconcile(declared, recent, latest_attribution(sources, untraced)),
        visibility=_visibility(recent),
        config_hash=config,
        fingerprint=fingerprint(config, declared.harness_version, declared.model),
    )
