"""Terminal rendering of findings: grouped by severity, most actionable first.

Errors and warnings print in full; info prints one line each unless ``detail`` — the terminal is
the quick look and the CI gate, the UI is where findings are explored. Nothing is said about what is new
or what disappeared since an earlier check: judging either from presence and absence proved brittle
(docs/02-design.md, 2026-10-03). What you suppressed is counted, and marked when its inputs changed.
"""

from __future__ import annotations

from agentlint.checks import CheckResults
from agentlint.config.model import ConfigSnapshot
from agentlint.report.finding import Confidence, Finding, Location, Member, Severity
from agentlint.report.paths import display, place
from agentlint.report.show import label
from agentlint.store.findings import Lifecycle

_WIDTH = 20
SHORT_ID = 8  # enough to suppress by; any unique prefix works
_QUOTED = 150  # characters of a member's description, on one line
_MOST_SEVERE_FIRST = sorted(Severity, key=lambda severity: -severity.rank)


def _lifecycle_line(lifecycle: Lifecycle) -> str | None:
    """What you dismissed, and why a reading wasn't recorded — nothing about what is new or gone."""
    parts = [lifecycle.note] if not lifecycle.recorded and lifecycle.note else []
    if lifecycle.suppressed_count:
        parts.append(f"{lifecycle.suppressed_count} suppressed")
    return " · ".join(parts) or None


def _members(finding: Finding) -> list[str]:
    """What a finding sets side by side, one line each — the facts the UI lays out in columns, from the same list:
    name, kind and where it sits (its package, else its scope), what the agent saw of it, its copy mark, a symlink,
    and its description."""
    def where(member: Member) -> str | None:
        return member.package or (member.scope.value if member.scope else None)

    places: dict[str, set[str | None]] = {}
    for member in finding.members:
        places.setdefault(member.name, set()).add(where(member))
    by_entity = {member.entity: member for member in finding.members}

    def called(member: Member) -> str:  # its name, and where it sits when that name sits in more than one place
        place = where(member)
        return member.name + (f" ({place})" if place and len(places[member.name]) > 1 else "")

    lines = []
    for member in finding.members:
        place = where(member)
        parts = [member.name, f"{member.kind} ({place})" if place else member.kind,
                 f"[{member.seen.value.replace('_', ' ') if member.seen else 'not known'}]"]
        if member.identical_to:
            parts.append(f"(identical to {called(by_entity[member.identical_to])})")
        if member.symlink:
            parts.append(f"(a symlink to {member.symlink}; removing it may not stick)")
        if member.description:
            text = " ".join(member.description.split())
            parts.append(f'"{text[:_QUOTED]}{"…" if len(text) > _QUOTED else ""}"')
        lines.append("  ".join(parts))
    return lines


def where(snapshot: ConfigSnapshot, location: Location) -> str:
    """A location as a person reads it: its source's file (and the place inside), else the source's label — the
    harness's own text is in no file — else, for a source this snapshot doesn't have, its id."""
    try:
        source = snapshot.source(location.source_id)
    except KeyError:
        return location.source_id
    return place(source.provenance) or label(source, snapshot.entities_by_source)


def _full(snapshot: ConfigSnapshot, finding: Finding, head: str, indent: str) -> list[str]:
    lines = [head]
    lines += [indent + display(line) for line in _members(finding)]
    lines += [indent + display(detail) for detail in finding.detail.splitlines()]
    for location in finding.locations[:5]:
        lines.append(f"{indent}at {where(snapshot, location)}" + (f":{location.line}" if location.line else ""))
    if len(finding.locations) > 5:
        lines.append(f"{indent}… and {len(finding.locations) - 5} more places")
    if finding.suggested_action:
        lines.append(f"{indent}→ {finding.suggested_action}")
    lines.append(f"{indent}id {finding.id}")
    return lines


def render(snapshot: ConfigSnapshot, results: CheckResults, lifecycle: Lifecycle | None = None, *, detail: bool = False) -> str:
    findings = [t.finding for t in lifecycle.shown] if lifecycle is not None else results.findings
    counts = {severity: sum(1 for f in findings if f.severity is severity) for severity in Severity}
    summary = ", ".join(f"{counts[s]} {s.value}" for s in _MOST_SEVERE_FIRST if counts[s])
    lines = [
        f"agentlint check — {snapshot.harness} {snapshot.harness_version or ''} · {display(str(snapshot.cwd))} · "
        f"{len(findings)} finding{'s' if len(findings) != 1 else ''}" + (f" ({summary})" if summary else ""),
    ]
    said = _lifecycle_line(lifecycle) if lifecycle is not None else None
    if said:
        lines.append(said)
    lapsed = {t.finding.id for t in lifecycle.tracked if t.suppression_lapsed} if lifecycle else set()
    width = max([_WIDTH, *(len(f.check) + 2 for f in findings), *(len(c) + 2 for c in results.not_evaluated)])
    indent = " " * (width + 2)
    for severity in _MOST_SEVERE_FIRST:
        group = [f for f in findings if f.severity is severity]
        if not group:
            continue
        compact = severity is Severity.INFO and not detail
        lines += ["", severity.value.upper()]
        for finding in group:
            confidence = "" if finding.confidence is Confidence.CERTAIN else f"  [{finding.confidence.value}]"
            tag = "  [dismissed before; inputs changed]" if finding.id in lapsed else ""
            head = f"  {finding.check:<{width}}{finding.title}{confidence}{tag}"
            lines += [f"{head}  · {finding.id[:SHORT_ID]}"] if compact else _full(snapshot, finding, head, indent)
        if compact:
            lines.append(f"{indent}(one line each; --detail prints them in full)")
    if results.not_evaluated:
        lines += ["", "NOT EVALUATED"]
        lines += [f"  {check_id:<{width}}{why}" for check_id, why in results.not_evaluated.items()]
    return "\n".join(lines) + "\n"
