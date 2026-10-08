"""Behaviour findings from tests (decided with the user 2026-10-07): a tested target that didn't make the agent do what
its situations expect — every run with it, none without — and one whose runs with it weren't consistent.

Judged by recorded runs, never by a model (non-negotiables 1 and 2): what each situation expects was named by the
generator as an input, and ``analysis/expectation.py`` counts the runs that did it. The evidence is the reading's
(``Reading.tests``, ``runner/tested.py``): each target's latest decided test, only while the target's text is the
text its situations were written for. A reading that took no test results (``tests`` None) evaluates neither check,
so their earlier findings stay open; a later test that says "works" closes one.
"""

from __future__ import annotations

from agentlint.checks import Check, NotEvaluated
from agentlint.config.locators import Whole
from agentlint.config.reading import Reading, TestedSituation, TestedTarget
from agentlint.rank.plan import AblationTarget, targets
from agentlint.report.finding import Confidence, Figure, Finding, FindingKind, Location, Severity
from agentlint.report.rank import name

_TITLE = {
    "not followed": "{name}: the agent didn't do what it is for",
    "backfires": "{name}: the agent does what it asks less often with it",
    "adds nothing": "{name}: the agent does the same without it",
    "partial": "{name}: followed only partly",
}
_SEVERITY = {"not followed": Severity.WARN, "backfires": Severity.WARN, "adds nothing": Severity.WARN,
             "partial": Severity.INFO}
_ACTION = {
    "not followed": "The agent doesn't act on it in these situations: make what it asks more specific or more "
                    "prominent, or remove it if it isn't needed.",
    "backfires": "With it the agent does what it asks less often: reword it, or remove it.",
    "adds nothing": "The agent does this without it: removing it saves its context, unless it matters in situations "
                    "these don't cover.",
    "partial": "It helps, not every time: making what it asks more specific may close the gap.",
}


def _evidence(reading: Reading) -> list[tuple[AblationTarget, TestedTarget]]:
    if reading.tests is None:
        raise NotEvaluated("no test results were read")
    by_id = {target.id: target for target in targets(reading.snapshot)}
    return [(by_id[tested.target], tested) for tested in reading.tests.targets if tested.target in by_id]


def _location(target: AblationTarget) -> Location:
    if target.whole or isinstance(target.rules[0].locator, Whole):
        return Location(target.source.id)
    return Location(target.source.id, target.rules[0].start_line)


def _runs(met: int | None, runs: int | None) -> str:
    return f"{met} of {runs}"


def _situation_line(situation: TestedSituation) -> str:
    said = f'"{situation.text}" expects {situation.expected}: {_runs(situation.with_met, situation.with_runs)} runs with it'
    if situation.verdict == "not followed":
        instead = ", ".join(f"{term} in {n}" for term, n in situation.instead)
        return f"{said}, so never run without{f' (what they did instead, by runs: {instead})' if instead else ''} — not followed"
    strength = "" if situation.strength is None else f", strength {situation.strength:+.2f}"
    return f"{said}, {_runs(situation.without_met, situation.without_runs)} without{strength} — {situation.verdict}"


def _strength(reading: Reading) -> list[Finding]:
    found = []
    defined = reading.snapshot.entities_by_source
    for target, tested in _evidence(reading):
        if tested.verdict not in _TITLE:
            continue
        short = [s for s in tested.situations if s.verdict != "works"]
        lines = "\n".join(f"- {_situation_line(s)}" for s in tested.situations)
        detail = (f"In the test of {tested.tested:%Y-%m-%d}, {len(short)} of {len(tested.situations)} situations fell "
                  f"short. Each counts the runs with the target that did what the situation expects, then as many "
                  f"without it; it works only when every run with it did and none without.\n{lines}")
        figures = [Figure("situations falling short", len(short), "situations", "situation")]
        if tested.strength is not None:
            figures.append(Figure("mean strength (1 is perfect)", round(tested.strength, 2), "", ""))
        found.append(EXPECTATION.finding(
            kind=FindingKind.BEHAVIOUR, severity=_SEVERITY[tested.verdict], confidence=Confidence.MEASURED,
            title=_TITLE[tested.verdict].format(name=name(target, defined)), detail=detail, discriminator=target.id,
            sources=(target.source.id,), locations=(_location(target),), figures=tuple(figures),
            suggested_action=_ACTION[tested.verdict]))
    return found


def _consistency(reading: Reading) -> list[Finding]:
    found = []
    defined = reading.snapshot.entities_by_source
    for target, tested in _evidence(reading):
        differed = [s for s in tested.situations if not s.consistent]
        if not differed:
            continue
        lines = "\n".join(f'- "{s.text}"' for s in differed)
        found.append(CONSISTENCY.finding(
            kind=FindingKind.BEHAVIOUR, severity=Severity.INFO, confidence=Confidence.MEASURED,
            title=f"{name(target, defined)}: the agent's runs differed in {len(differed)} of {len(tested.situations)} "
                  "tested situations",
            detail=(f"In the test of {tested.tested:%Y-%m-%d}, the runs with the target didn't all use the same tools, "
                    f"skills and agents in these situations: the agent can't make up its mind there.\n{lines}"),
            discriminator=target.id, sources=(target.source.id,), locations=(_location(target),),
            figures=(Figure("situations whose runs differed", len(differed), "situations", "situation"),),
            suggested_action=None))
    return found


EXPECTATION = Check("tested-expectation", "tested targets that didn't make the agent do what their situations expect, "
                    "every run with them and none without (measured)", _strength, by_text=False)
CONSISTENCY = Check("tested-consistency", "tested targets whose runs with them didn't use the same tools, skills and "
                    "agents (measured)", _consistency, by_text=False)
