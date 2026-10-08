"""Terminal rendering of an assembled configuration, ranked by what it costs.

Every number says how it was obtained: ``≈`` marks estimates from text. The always-on total is
stated exactly as far as the data allows and no further (docs/tech_impl/01-config-reader.md).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agentlint.config.content import NotObserved, ObservedText
from agentlint.config.entity import Entity
from agentlint.config.locators import describe
from agentlint.config.model import ConfigSnapshot, NotReconciled, Overcounted, Reconciled, Source
from agentlint.config.regions import FormatError
from agentlint.config.taxonomy import Load, Mechanism
from agentlint.config.tokens import NOISE
from agentlint.report.paths import display, place

LARGEST_INVOCABLE = 5


def _n(value: int) -> str:
    return f"{value:,}"


Defined = Mapping[str, Sequence[Entity]]  # source id -> the entities it defines: ``ConfigSnapshot.entities_by_source``


def defined_names(snapshot: ConfigSnapshot) -> Defined:
    """What ``label`` names a snapshot's sources by: the entities each defines. (report/document.py still asks here.)"""
    return snapshot.entities_by_source


def label(source: Source, provided: Defined) -> str:
    point = source.provenance.point.label
    if source.provenance.point.mechanism in (Mechanism.HARNESS_BUILTIN, Mechanism.HARNESS_GENERATED):
        # composed by the harness: the entities it lists are not what it is; a per-package slice says whose it is
        return point + (f" · {source.provenance.package}" if source.provenance.package else "")
    defined = provided.get(source.id)
    if defined:
        return f"{point} {defined[0].name}" + (f" (+{len(defined) - 1})" if len(defined) > 1 else "")
    if source.provenance.package:
        return f"{point} · {source.provenance.package}"
    where = place(source.provenance)
    return f"{point} · {where}" if where else point


def _seen(source: Source) -> str:
    content = source.content
    if isinstance(content, ObservedText):
        return f"seen in {len(content.seen_in)}/{content.sessions_examined} sessions"
    return ""


def _total_lines(snapshot: ConfigSnapshot) -> list[str]:
    outcome = snapshot.always_on
    if isinstance(outcome, Reconciled):
        total = outcome.observed_total.value
        share = outcome.unattributed.value / total if total else 0.0
        meaning = "within estimation noise" if share <= NOISE else "harness built-in we could not see"
        return [
            f"ALWAYS-ON  {_n(total)} tokens   (observed in session {outcome.session.session_id[:8]}, "
            f"{outcome.session.started_at.astimezone():%Y-%m-%d})",
            f"  ≈{_n(outcome.attributed):>8}  attributed to what that session delivered",
            f"   {_n(outcome.unattributed.value):>8}  not attributed — {meaning} ({share:.1%})",
        ]
    if isinstance(outcome, Overcounted):
        total = outcome.observed_total.value
        share = outcome.excess / total if total else 0.0
        meaning = ("within estimation noise" if share <= NOISE
                   else "more than estimation noise explains: the reader or estimator is off")
        return [
            f"ALWAYS-ON  {_n(total)} tokens   (observed in session {outcome.session.session_id[:8]}, "
            f"{outcome.session.started_at.astimezone():%Y-%m-%d})",
            f"  ≈{_n(outcome.attributed):>8}  attributed — exceeds the observed total by {_n(outcome.excess)} "
            f"({share:.1%}), {meaning}",
        ]
    assert isinstance(outcome, NotReconciled)
    return [
        f"ALWAYS-ON  ≈{_n(outcome.attributed)} tokens attributed",
        f"  no observed total to check it against: {outcome.reason}",
    ]


def render(snapshot: ConfigSnapshot, *, always_only: bool = False, limit: int | None = 25) -> str:
    names = snapshot.entities_by_source
    versions = {header.harness_version or "unknown" for header in snapshot.sessions}
    lines = [
        f"agentlint show — {snapshot.harness} {snapshot.harness_version or '(version unknown)'} · "
        f"model {snapshot.model or '(unknown)'} · {display(str(snapshot.cwd))}",
        f"{len(snapshot.sessions)} recent sessions examined · {_n(len(snapshot.sources))} sources · "
        f"{_n(len(snapshot.entities))} entities · fingerprint {snapshot.fingerprint[7:19]}",
        *([f"recent sessions ran harness versions {', '.join(sorted(versions, reverse=True))}"] if len(versions) > 1 else []),
        "",
        *_total_lines(snapshot),
        "",
    ]
    first_turn = [s for s in snapshot.sources if s.activation.load.in_first_turn and s.tokens is not None]
    generated: dict[str, list[Source]] = {}  # listings split per package are shown as one line each
    ranked: list[tuple[int, str, str, str, str]] = []
    for s in first_turn:
        if s.provenance.point.mechanism is Mechanism.HARNESS_GENERATED:
            generated.setdefault(s.provenance.point.label, []).append(s)
        else:
            ranked.append((s.tokens.value, s.activation.load.value.replace("_", " "), s.provenance.authority.value,
                           label(s, names), _seen(s)))
    for point_label, parts in generated.items():
        packages = sum(1 for part in parts if part.provenance.package)
        name = point_label + (f" ({packages} packages contribute)" if packages else "")
        seen = max((_seen(part) for part in parts), key=len, default="")
        ranked.append((sum(part.tokens.value for part in parts), "always", "runtime", name, seen))
    ranked += [
        (u.content.tokens.value, u.load.value.replace("_", " "), "runtime", u.label,
         f"seen in {len(u.content.seen_in)}/{u.content.sessions_examined} sessions")
        for u in snapshot.untraced if u.load.in_first_turn
    ]
    ranked.sort(key=lambda row: -row[0])
    shown = ranked if limit is None else ranked[:limit]
    lines.append(f"  {'≈tokens':>8}  {'load':<11}  {'authority':<9}  source")
    for tokens, load, authority, name, seen in shown:
        lines.append(f"  {_n(tokens):>8}  {load:<11}  {authority:<9}  {name}" + (f"   [{seen}]" if seen else ""))
    if len(ranked) > len(shown):
        hidden = ranked[len(shown):]
        lines.append(f"  {_n(sum(r[0] for r in hidden)):>8}  … {len(hidden)} smaller sources (--all to list them)")
    unseen = [s for s in snapshot.sources if s.activation.load.in_first_turn and isinstance(s.content, NotObserved)]
    if unseen:
        lines.append(f"  {len(unseen)} more are configured to load every session but no examined session showed "
                     f"their text (e.g. hooks that printed nothing)")
    if always_only:
        return "\n".join(lines) + "\n"
    lines.append("")
    invocable = sorted((s for s in snapshot.sources if s.activation.load is Load.ON_INVOKE and s.tokens is not None),
                       key=lambda s: -s.tokens.value)
    if invocable:
        lines.append(f"ON INVOKE    {_n(len(invocable))} definitions, loaded only when invoked. Largest definitions:")
        for source in invocable[:LARGEST_INVOCABLE]:
            lines.append(f"  {_n(source.tokens.value):>8}  {label(source, names)}")
    for load, title in ((Load.CONDITIONAL, "CONDITIONAL  injected when a condition holds"),
                        (Load.NOT_IN_CONTEXT, "CONFIG       configures the harness; never in context")):
        group = [s for s in snapshot.sources if s.activation.load is load]
        if group:
            lines.append(f"{title}: {_n(len(group))} sources")
    lines += ["", "≈ estimated from text (typically within ±7% per source, ~2% in total)."]
    return "\n".join(lines) + "\n"


def render_rules(snapshot: ConfigSnapshot, source_id: str) -> str:
    source = snapshot.source(source_id)
    names = snapshot.entities_by_source
    lines = [f"{label(source, names)}", f"  id {source.id}", f"  {place(source.provenance) or 'in no file'}", ""]
    try:
        rules = source.rules()
    except FormatError as error:
        return "\n".join(lines + [f"  cannot divide into rules: {error}"]) + "\n"
    if not rules:
        reason = getattr(source.content, "reason", "its text is not known")
        return "\n".join(lines + [f"  no rules: {reason}"]) + "\n"
    lines.append(f"  {'≈tokens':>8}  {'lines':<9}  rule")
    for rule in rules:
        span = f"{rule.start_line}-{rule.end_line}"
        lines.append(f"  {_n(rule.tokens.value):>8}  {span:<9}  {describe(rule.locator)}")
    return "\n".join(lines) + "\n"
