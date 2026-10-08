"""The report document the UI reads (ui/src/contract.ts): one reading of the configuration, its findings with their
lifecycle, what recent sessions loaded, and usage counts — built here, served by `agentlint ui` at /api/report, with
the texts it refers to by ``text_ref`` served apart (/api/texts; docs/tech_impl/11-ui.md, 10-reporting.md).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from agentlint.checks import CheckResults
from agentlint.config.assemble import could_show
from agentlint.config.content import FileText, ObservedText
from agentlint.config.entity import Entity
from agentlint.config.model import ConfigSnapshot, Overcounted, Reconciled
from agentlint.config.usage import UsageIndex, reference, summarise
from agentlint.config.serialize import snapshot_data, texts_out, to_data
from agentlint.config.taxonomy import Load
from agentlint.report.show import defined_names, label
from agentlint.store.findings import Lifecycle

REPORT_SCHEMA = 1


def contexts_data(snapshot: ConfigSnapshot) -> list[dict[str, Any]]:
    """What each recent session in this project loaded, most recent first: one to pick from."""
    return [context_data(snapshot, ref) for ref in snapshot.project_sessions]


def context_data(snapshot: ConfigSnapshot, ref) -> dict[str, Any]:
    """What one session in this project loaded before the first words, against what is configured to: text it
    showed, files read from disk (configured, not confirmed per session), what it could have shown and didn't,
    and untraced text before the first words — the latest session's, and whether this one showed it too. Sources
    and untraced text pass the same rule, ``Load.in_first_turn``. ``observed_total`` is the total the session
    observed whenever it is the one ``always_on`` was observed in, overcounted or not."""
    seen, files, missing = [], [], []
    for source in snapshot.sources:
        if not source.activation.load.in_first_turn:
            continue
        content = source.content
        if isinstance(content, FileText):
            files.append(source.id)
        elif isinstance(content, ObservedText) and ref in content.seen_in:
            seen.append(source.id)
        elif could_show(source, ref, snapshot.cwd, snapshot.harness_version):
            missing.append(source.id)
    untraced = [u.id for u in snapshot.untraced
                if u.load.in_first_turn and isinstance(u.content, ObservedText) and ref in u.content.seen_in]
    total = snapshot.always_on
    observed = (total.observed_total.value if isinstance(total, (Reconciled, Overcounted)) and total.session == ref
                else None)
    return {"session": to_data(ref), "seen": seen, "files": files, "missing": missing, "untraced": untraced,
            "observed_total": observed}


def loads_data() -> dict[str, dict[str, bool]]:
    """Each load's facts, so a reader never re-states the rule: ``in_first_turn`` is whether text loaded that way is
    part of what the agent receives before your first words."""
    return {load.value: {"in_first_turn": load.in_first_turn} for load in Load}


def usage_data(index: UsageIndex | None, days: int, entities: Sequence[Entity] = ()) -> dict[str, Any] | None:
    """Counts only — how many of your turns used each entity, and when last — never what was typed. ``summary`` is
    what `agentlint usage` prints, computed once: the skills and agents the agent chooses between that your turns
    used (entity id and reference, most used first, each reference once), those never used, and what turns used in
    those kinds that this configuration doesn't define."""
    if index is None:
        return None
    summary = summarise(entities, index)
    return {
        "days": days,
        "turns": index.turns_observed,
        "history_start": index.history_start.isoformat() if index.history_start else None,
        "entities": {ref: {"invocations": u.invocations, "last_seen": u.last_seen.isoformat()}
                     for ref, u in sorted(index.entities.items())},
        "summary": {
            "used": [{"entity": entity.id, "reference": u.reference} for entity, u in summary.used],
            "unused": [{"entity": entity.id, "reference": reference(entity)} for entity in summary.unused],
            "unmatched": dict(sorted(summary.unmatched.items())),
        },
    }


def report_data(snapshot: ConfigSnapshot, results: CheckResults, lifecycle: Lifecycle, *, summaries: Mapping[str, str],
                generated_at: datetime, version: str, usage: UsageIndex | None = None,
                usage_days: int, harness_label: str,
                package_label: Callable[[str], str], home_dir: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """The report the UI reads (ui/src/contract.ts), and the texts it refers to by ``text_ref``. ``harness_label`` is
    how the user knows the harness (its adapter's label), so the report names it whoever reads it. ``package_label`` is
    the adapter's short name for a package id; every package a source in the snapshot shipped in gets one, so the UI
    never takes an id apart (it shows the id where two packages share a label). ``home_dir`` is the user's home
    directory, so the UI writes a path under it in the ``~`` form and leaves every other path whole. ``not_recorded``
    is the lifecycle's note: why this reading's snapshot wasn't stored nor its findings recorded (None when they were),
    which the UI shows on Findings and What changed."""
    texts: dict[str, str] = {}
    data = texts_out(snapshot_data(snapshot), texts)
    names = defined_names(snapshot)
    return {
        "schema_version": REPORT_SCHEMA,
        "generated_at": generated_at.isoformat(),
        "agentlint_version": version,
        "harness_label": harness_label,
        "home_dir": str(home_dir),
        "snapshot": data,
        "labels": {source.id: label(source, names) for source in snapshot.sources},
        "package_labels": {package: package_label(package) for package in
                           sorted({s.provenance.package for s in snapshot.sources if s.provenance.package})},
        "loads": loads_data(),
        "contexts": contexts_data(snapshot),
        "usage": usage_data(usage, usage_days, snapshot.entities),
        "not_recorded": lifecycle.note,  # why this reading was neither stored nor recorded, in a sentence; None when it was
        "check": {
            "summaries": dict(summaries),  # check id -> what it looks for, in a sentence
            "not_evaluated": results.not_evaluated,
            "findings": [{"id": t.finding.id, "suppressed": t.suppressed, "suppression_lapsed": t.suppression_lapsed, **to_data(t.finding)}
                         for t in lifecycle.tracked],
        },
    }, texts
