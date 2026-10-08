"""The plan: a filter, then a sort, cut at a budget counted in runs (docs/tech_impl/03-ranker.md). With no budget
(``budget=None``: a test from the UI, which runs the targets you tick) nothing is cut: every testable target, in order.

No AI and no weights. A line of the plan is what one variant removes: a section, where its text can be cut out
of the copy; the whole skill or hook, where it can only be removed whole (decided 2026-10-03). Whatever nothing
could measure is skipped with its reason, so the plan says what can't be checked as plainly as what will be. Of
the rest, what reaches the agent in every session or message comes first, then the most tokens. Making a plan
spends nothing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from agentlint.config.content import FileText, NotObserved, SizeOnly
from agentlint.config.model import Ablation, ConfigSnapshot, Rule, Source
from agentlint.config.taxonomy import Load
from agentlint.config.usage import UsageIndex
from agentlint.report.finding import Finding, Severity


class Phase(Enum):
    STABILITY = "stability"  # each situation, the configuration as it is: does the agent agree with itself?
    ABLATION = "ablation"  # with and without the piece: does it change what the agent does?


@dataclass(frozen=True)
class RunCounts:
    """Fixed up front, never escalated mid-flight."""

    situations: int = 3  # per line of the plan, written by the probe generator
    stability: int = 3  # three disagreeing runs prove instability; three agreeing ones prove nothing
    ablation: int = 10  # three is too few permutations to detect an effect

    def per_target(self, phase: Phase) -> int:
        if phase is Phase.STABILITY:
            return self.situations * self.stability  # the configuration as it is, only
        return self.situations * 2 * self.ablation  # with and without the piece


def cuttable(source: Source) -> bool:
    """Whether a variant can cut one rule out of this source: only when the rules are regions of a file the variant
    rewrites. A skill file is dropped whole; a hook's output is the hook's to print, so cutting its entry out of
    the settings removes all of it."""
    return source.ablation.mechanism is Ablation.SPAN_EXCISION and isinstance(source.content, FileText)


@dataclass(frozen=True)
class AblationTarget:
    """What one variant removes: one rule, or a whole source no variant can take apart."""

    id: str  # the rule's id, or the source's for a whole source
    source: Source
    rules: tuple[Rule, ...]

    @property
    def whole(self) -> bool:
        return not self.rules or not cuttable(self.source)

    @property
    def tokens(self) -> int:
        return sum(rule.tokens.value for rule in self.rules)


def targets(snapshot: ConfigSnapshot) -> list[AblationTarget]:
    """The configuration's rules, grouped into what a variant can remove. A source with no rules — text no session
    examined showed, or only its size known — is still a target, with none, so the plan can say why it is skipped."""
    found: list[AblationTarget] = []
    for source in snapshot.sources:
        rules = source.rules()
        if rules and cuttable(source):
            found += [AblationTarget(rule.id, source, (rule,)) for rule in rules]
        else:  # removed whole: one question, does this matter at all
            found.append(AblationTarget(source.id, source, rules))
    return found


@dataclass(frozen=True)
class TargetStatus:
    """What has been measured about a target, across jobs: the `verdicts` table."""

    target_id: str
    last_measured: datetime | None
    last_fingerprint: str | None
    last_job_id: str | None
    verdict: Mapping[str, Any] | None  # RuleVerdict (07-analysis.md) once the runner writes one
    situations_tried: int

    def current(self, fingerprint: str) -> bool:
        """Measured under this configuration. A verdict from another fingerprint has not been measured."""
        return self.verdict is not None and self.last_fingerprint == fingerprint


class Skip(Enum):
    UNTESTABLE = "can't be tested by removal"
    NOT_IN_CONTEXT = "never in the agent's context"
    NO_TEXT = "no text to test"
    MEASURED = "measured under this configuration"
    SETTLED = "settled by a static check"
    UNUSED = "on invoke, with no record of use"


@dataclass(frozen=True)
class Planned:
    target: AblationTarget
    runs: int


@dataclass(frozen=True)
class Skipped:
    target: AblationTarget
    why: Skip
    detail: str


@dataclass(frozen=True)
class Plan:
    phase: Phase
    budget: int | None  # None: no budget, nothing deferred
    counts: RunCounts
    planned: tuple[Planned, ...]
    deferred: tuple[Planned, ...]  # testable and ranked, past the budget
    skipped: tuple[Skipped, ...]

    @property
    def runs(self) -> int:
        return sum(p.runs for p in self.planned)


def order(target: AblationTarget) -> tuple:
    """Every session or message first — in context on every request — then the most tokens."""
    return (0 if target.source.activation.load.in_first_turn else 1, -target.tokens, target.id)


def plan(snapshot: ConfigSnapshot, *, findings: Sequence[Finding], usage: UsageIndex | None,
         statuses: Mapping[str, TargetStatus], budget: int | None, phase: Phase = Phase.ABLATION,
         counts: RunCounts = RunCounts()) -> Plan:
    provided = snapshot.entities_by_source
    proven: dict[str, list[tuple[int, Finding]]] = defaultdict(list)  # source id -> lines a static check proved broken
    for finding in findings:
        if finding.severity is Severity.ERROR:
            for location in finding.locations:
                if location.line is not None:
                    proven[location.source_id].append((location.line, finding))

    def skip(target: AblationTarget) -> Skipped | None:
        def because(why: Skip, detail: str) -> Skipped:
            return Skipped(target, why, detail)

        source = target.source
        if source.ablation.mechanism is Ablation.NONE:
            return because(Skip.UNTESTABLE, source.ablation.blocked_reason or "")
        if source.activation.load is Load.NOT_IN_CONTEXT:
            return because(Skip.NOT_IN_CONTEXT, "its text never reaches the model")
        if not target.rules:  # nothing to write situations about, and nothing a verdict could be credited to
            content = source.content
            return because(Skip.NO_TEXT, content.reason if isinstance(content, (NotObserved, SizeOnly)) else "it is empty")
        status = statuses.get(target.id)
        if status is not None and status.current(snapshot.fingerprint):
            when = f" on {status.last_measured.astimezone():%Y-%m-%d}" if status.last_measured else ""
            return because(Skip.MEASURED, f"measured{when}; nothing it depends on has changed")
        if not target.whole:  # a proven break settles the section it is in, not whether a whole skill matters
            (rule,) = target.rules
            for line, finding in proven.get(source.id, ()):  # a finding points into a source by its id
                if rule.start_line <= line <= rule.end_line:
                    return because(Skip.SETTLED, finding.title)
        if source.activation.load is Load.ON_INVOKE:
            entities = provided.get(source.id, ())
            if not entities:
                return because(Skip.UNUSED, "nothing records invoking it")
            if usage is None or usage.is_empty():
                return because(Skip.UNUSED, "no usage history yet")
            if not any(usage.for_entity(e) for e in entities):
                return because(Skip.UNUSED, f"never used in {usage.turns_observed:,} of your turns")
        return None

    candidates, skipped = [], []
    for target in targets(snapshot):
        reason = skip(target)
        if reason:
            skipped.append(reason)
        else:
            candidates.append(Planned(target, counts.per_target(phase)))
    candidates.sort(key=lambda p: order(p.target))
    planned, deferred, spent = [], [], 0
    for item in candidates:  # the ranked prefix that fits: the order is the point
        if not deferred and (budget is None or spent + item.runs <= budget):
            planned.append(item)
            spent += item.runs
        else:
            deferred.append(item)
    return Plan(phase, budget, counts, tuple(planned), tuple(deferred), tuple(skipped))
