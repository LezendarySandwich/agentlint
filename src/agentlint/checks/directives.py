"""Contradiction candidates and repeats among the configuration's directives (12-directives.md; decided with the user
2026-10-07). Suggestive: a model listed the directives, each held by code to an exact quote of its section and a thing
the configuration has, and code paired them (``directives/pair.py``). Whether two directives apply at once turns on
their ``when``, which no code judges: a contradiction is a candidate, both quotes shown, for you to judge.

The evidence is the reading's (``Reading.directives``): what was stored for the snapshot's sections, read when you
chose the sources to read. With nothing read yet neither check is evaluated.
"""

from __future__ import annotations

from collections.abc import Sequence

from agentlint.checks import Check, NotEvaluated
from agentlint.config.reading import DirectiveEvidence, Reading
from agentlint.directives.pair import Occurrence, contradictions, repeats
from agentlint.report.finding import Confidence, Figure, Finding, FindingKind, Location, Severity
from agentlint.report.show import label


def _evidence(reading: Reading) -> DirectiveEvidence:
    found = reading.directives
    if found is None or not found.read:
        raise NotEvaluated("no instruction text has been read for directives yet")
    return found


def _line(occurrence: Occurrence, reading: Reading) -> str:
    snapshot = reading.snapshot
    where = label(snapshot.source(occurrence.source), snapshot.entities_by_source)
    when = f" (when: {occurrence.directive.when})" if occurrence.directive.when else ""
    return (f'- {where}, line {occurrence.line}: "{occurrence.directive.quote}" — '
            f"{occurrence.directive.polarity.value.replace('_', ' ')} {occurrence.directive.thing}{when}")


def _places(occurrences: Sequence[Occurrence]) -> tuple[tuple[str, ...], tuple[Location, ...]]:
    sources = tuple(dict.fromkeys(o.source for o in occurrences))
    locations = tuple(dict.fromkeys(Location(o.source, o.line) for o in occurrences))
    return sources, locations


def _coverage(evidence: DirectiveEvidence) -> str:
    if evidence.read >= evidence.sections:
        return ""
    return f" Read so far: {evidence.read:,} of {evidence.sections:,} sections; others may say more."


def _contradictions(reading: Reading) -> list[Finding]:
    evidence = _evidence(reading)
    found = []
    seen: set[str] = set()
    snapshot = reading.snapshot
    for pair in contradictions(evidence.occurrences):
        f, g = pair.for_it, pair.against
        discriminator = f"{pair.toward}|{pair.away}|{f.source}|{f.directive.quote}|{g.source}|{g.directive.quote}"
        if discriminator in seen:
            continue
        seen.add(discriminator)
        sources, locations = _places((f, g))
        names = [label(snapshot.source(o.source), snapshot.entities_by_source) for o in (f, g)]
        found.append(CONFLICT.finding(
            kind=FindingKind.COLLISION, severity=Severity.WARN if pair.every_session else Severity.INFO,
            confidence=Confidence.SUGGESTIVE,
            title=f"{names[0]} says to use {f.directive.thing}; {names[1]} says not to use {g.directive.thing}",
            detail=("Whether both apply at once depends on when each applies, which is for you to judge."
                    f"{_coverage(evidence)}\n{_line(f, reading)}\n{_line(g, reading)}"),
            discriminator=discriminator, sources=sources, locations=locations,
            suggested_action="If both can apply at once, say in one of them which wins, or remove one."))
    return found


def _repeats(reading: Reading) -> list[Finding]:
    evidence = _evidence(reading)
    found = []
    for repeat in repeats(evidence.occurrences):
        sources, locations = _places(repeat.occurrences)
        said = "to use" if repeat.positive else "not to use"
        lines = "\n".join(_line(o, reading) for o in repeat.occurrences)
        found.append(REPEAT.finding(
            kind=FindingKind.COST, severity=Severity.INFO, confidence=Confidence.SUGGESTIVE,
            title=f"{len(sources)} sources in every session say {said} {repeat.thing}",
            detail=f"The agent reads it more than once in every session.{_coverage(evidence)}\n{lines}",
            discriminator=f"{repeat.thing}|{'toward' if repeat.positive else 'away'}", sources=sources,
            locations=locations, figures=(Figure("sources", len(sources), "sources", "source"),),
            suggested_action="Keep it in one place."))
    return found


CONFLICT = Check("conflicting-directives", "directives one source gives and another contradicts: candidates for you "
                 "to judge (suggestive)", _contradictions)
REPEAT = Check("repeated-directive", "the same directive in several sources that load in every session (suggestive)",
               _repeats)
