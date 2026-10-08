"""token-budget: what is spending the always-on context, grouped by how it gets there.

Not a defect — the number nobody has. One finding, stated exactly as far as the data allows: an
observed total when a session reported one, otherwise an attributed lower bound.
"""

from __future__ import annotations

from collections import defaultdict

from agentlint.checks import Check
from agentlint.config.model import ConfigSnapshot, NotReconciled, Overcounted, Reconciled
from agentlint.config.taxonomy import Load, Mechanism
from agentlint.config.tokens import NOISE
from agentlint.report.finding import Confidence, Figure, Finding, FindingKind, Severity


_GROUPS = {
    Mechanism.HARNESS_BUILTIN: "the harness's own prompt and tool definitions",
    Mechanism.HARNESS_GENERATED: "listings the harness composes from what is installed",
    Mechanism.INSTRUCTION_FILE: "instruction files",
    Mechanism.CONFIG_FILE: "instruction text inside settings",
    Mechanism.PACKAGE_ENTITY: "package definitions",
}


def _group(mechanism: Mechanism, load: Load) -> str:
    if mechanism is Mechanism.HOOK_OUTPUT:
        return "hook output, every message" if load is Load.PER_MESSAGE else "hook output, once per session"
    return _GROUPS[mechanism]


UNKNOWN_ORIGIN = "text of unknown origin (delivered, not traced to configuration)"


def run(snapshot: ConfigSnapshot) -> list[Finding]:
    """Groups the outcome's own attribution — never a second sum over the sources — so the breakdown, the
    attributed figure and the remainder all count the same tokens."""
    outcome = snapshot.always_on
    by_id = {source.id: source for source in snapshot.sources}
    # every declared always-on source, observed or not: a stable set to rest on
    sources = [source.id for source in snapshot.sources if source.activation.load.in_first_turn]
    totals: dict[str, int] = defaultdict(int)
    for part in outcome.attribution:
        source = by_id.get(part.source_id) if part.source_id is not None else None
        group = _group(source.provenance.point.mechanism, source.activation.load) if source else UNKNOWN_ORIGIN
        totals[group] += part.tokens
    attributed = outcome.attributed
    figures = [Figure("attributed", attributed, "tokens", "token")]
    if isinstance(outcome, (Reconciled, Overcounted)):
        observed = outcome.observed_total.value
        title = f"Always-on context: {observed:,} tokens every session (observed)"
        figures.insert(0, Figure("observed total", observed, "tokens", "token"))
        if isinstance(outcome, Reconciled):
            share = outcome.unattributed.value / observed if observed else 0.0
            meaning = "within estimation noise" if share <= NOISE else "harness built-in we could not see"
            note = (f"Reconciled against session {outcome.session.session_id[:8]}: ≈{outcome.attributed:,} attributed, "
                    f"{outcome.unattributed.value:,} not attributed ({share:.1%}, {meaning}).")
            figures.append(Figure("not attributed", outcome.unattributed.value, "tokens", "token"))
        else:
            share = outcome.excess / observed if observed else 0.0
            note = (f"Estimates exceed what session {outcome.session.session_id[:8]} observed by {outcome.excess:,} "
                    f"tokens ({share:.1%}).")
    else:
        assert isinstance(outcome, NotReconciled)
        title = f"Always-on context: up to ≈{attributed:,} tokens per session (estimated)"
        note = (f"No observed total to check this against: {outcome.reason}. Each source counts what it delivered "
                "most recently, possibly in different sessions, so text a hook adds only sometimes is counted as "
                "if it were always there.")
    breakdown = "\n".join(f"≈{tokens:>7,}  {group}" for group, tokens in sorted(totals.items(), key=lambda x: -x[1]))
    return [CHECK.finding(
        kind=FindingKind.COST,
        severity=Severity.INFO,
        confidence=Confidence.CERTAIN,
        title=title,
        detail=f"{breakdown}\n{note}\nEstimated from text (≈, within about 2% in total).",
        discriminator="always-on",
        sources=tuple(sources),
        figures=tuple(figures),
        suggested_action="Run `agentlint show` to see every source, largest first.",
    )]


CHECK = Check("token-budget", "what is spending the always-on context", lambda reading: run(reading.snapshot))
