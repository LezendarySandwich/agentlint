"""untraced-injection and definition-size: what reaches the agent that configuration doesn't explain,
and what an invocation can cost."""

from __future__ import annotations

import hashlib

from agentlint.checks import Check, NoSession
from agentlint.config.model import ConfigSnapshot
from agentlint.config.taxonomy import Load
from agentlint.report.finding import Confidence, Figure, Finding, FindingKind, Location, Severity

LARGE_DEFINITION = 10_000  # tokens: a bite worth knowing about before invoking


_FREQUENCY = (Load.PER_MESSAGE, Load.ALWAYS)  # most often first


def untraced_injection(snapshot: ConfigSnapshot) -> list[Finding]:
    if not snapshot.project_sessions:  # untraced text is read from this project's sessions only
        raise NoSession("no recent session in this project to compare delivered text against", in_project=True)
    groups: dict[str, list] = {}
    for untraced in snapshot.untraced:  # one text can arrive under several labels (events, matchers): one finding
        identity = hashlib.sha256(" ".join(untraced.content.text.split()).encode("utf-8", "surrogatepass")).hexdigest()[:16]
        groups.setdefault(f"{untraced.point.name}:{identity}", []).append(untraced)
    findings = []
    for discriminator, group in groups.items():
        content = group[0].content
        tokens = content.tokens.value
        load = next((l for l in _FREQUENCY if any(u.load is l for u in group)), group[0].load)
        when = {Load.PER_MESSAGE: "with every message", Load.ALWAYS: "at the start of every session"}.get(
            load, "when its condition holds")
        seen = {ref.session_id for u in group for ref in u.content.seen_in}
        labels = "; ".join(dict.fromkeys(u.label for u in group))
        opening = " ".join(content.text.split())[:160]
        findings.append(UNTRACED.finding(
            kind=FindingKind.DELIVERY,
            severity=Severity.INFO,
            confidence=Confidence.CERTAIN,
            title=f"≈{tokens:,} tokens injected {when} that no configuration explains",
            detail=(f"\"{opening}{'…' if len(content.text) > 160 else ''}\"\n"
                    f"Seen in {len(seen)} of the {content.sessions_examined} sessions examined in this project. "
                    f"{labels}."),
            discriminator=discriminator,
            figures=(Figure("tokens", tokens, "tokens", "token"), Figure("sessions", len(seen), "sessions", "session")),
            suggested_action="If you don't recognise it, look for a command that fetches or generates text at runtime — "
                             "nothing on disk contains it.",
        ))
    return findings


def definition_size(snapshot: ConfigSnapshot) -> list[Finding]:
    names = {source_id: entities[0].name for source_id, entities in snapshot.entities_by_source.items()}
    large = sorted((s for s in snapshot.sources if s.activation.load is Load.ON_INVOKE and s.tokens is not None
                    and s.tokens.value >= LARGE_DEFINITION), key=lambda s: -s.tokens.value)
    if not large:
        return []
    lines = [f"≈{s.tokens.value:>7,}  {s.provenance.point.label} {names.get(s.id) or s.provenance.file or s.id}"
             for s in large[:15]]
    if len(large) > 15:
        lines.append(f"… and {len(large) - 15} more")
    return [DEFINITIONS.finding(
        kind=FindingKind.COST,
        severity=Severity.INFO,
        confidence=Confidence.CERTAIN,
        title=f"{len(large)} invocable definitions are over {LARGE_DEFINITION:,} tokens",
        detail=("\n".join(lines) + "\nThese are the sizes of the definitions on disk. How much of each reaches the model "
                "when invoked is not measured until an invocation is recorded."),
        discriminator="large-definitions",
        sources=tuple(s.id for s in large),
        locations=tuple(Location(s.id) for s in large),
        figures=tuple(Figure(names.get(s.id, s.id), s.tokens.value, "tokens", "token") for s in large),
        suggested_action="Before invoking one of these, expect a large context bite; split or trim definitions you own.",
    )]


UNTRACED = Check("untraced-injection", "delivered text that traces to nothing configured",
                 lambda reading: untraced_injection(reading.snapshot))
DEFINITIONS = Check("definition-size", "invocable definitions large enough to matter",
                    lambda reading: definition_size(reading.snapshot))
