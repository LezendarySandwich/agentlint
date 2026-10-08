"""Terminal rendering of a plan: what will be tested, in order, what won't fit, and what can't be — with why
(docs/tech_impl/03-ranker.md, "Plan output")."""

from __future__ import annotations

from collections import defaultdict

from agentlint.config.locators import Whole, describe
from agentlint.config.model import Ablation
from agentlint.config.taxonomy import Load
from agentlint.rank.plan import AblationTarget, Phase, Plan, Planned, Skip, Skipped
from agentlint.report.show import Defined, label

_LOAD = {Load.ALWAYS: "every session", Load.PER_MESSAGE: "every message", Load.ON_INVOKE: "when invoked",
         Load.CONDITIONAL: "conditional", Load.NOT_IN_CONTEXT: "not in context"}
_PHASE = {Phase.STABILITY: "phase 1, stability", Phase.ABLATION: "phase 2, ablation"}


def name(target: AblationTarget, names: Defined) -> str:
    """A section by where it is; a whole source by what it is, and how many parts it has."""
    if target.whole:
        parts = len(target.rules)
        return f"{label(target.source, names)}" + (f" (whole, {parts} parts)" if parts > 1 else " (whole)")
    (rule,) = target.rules
    return label(target.source, names) + ("" if isinstance(rule.locator, Whole) else f" {describe(rule.locator)}")


def _row(index: int, item: Planned, names: Defined) -> str:
    load = _LOAD[item.target.source.activation.load]
    return f"  {index:>3}  {item.target.tokens:>7,}  {load:<13}  {item.runs:>4}  {name(item.target, names)}"


def render(plan: Plan, names: Defined, *, everything: bool = False, limit: int = 30) -> str:
    per = plan.counts.per_target(plan.phase)
    how = (f"{plan.counts.situations} situations × {plan.counts.stability} runs" if plan.phase is Phase.STABILITY
           else f"{plan.counts.situations} situations × 2 variants × {plan.counts.ablation} runs")
    lines = [f"agentlint rank — {_PHASE[plan.phase]} · budget {plan.budget:,} runs · "
             f"{len(plan.planned):,} planned, {plan.runs:,} runs ({per} each: {how})", ""]
    if plan.planned:
        what = "what its situations are about" if plan.phase is Phase.STABILITY else "what a variant removes"
        lines.append(f"  {'#':>3}  {'≈tokens':>7}  {'loaded':<13}  {'runs':>4}  {what}")
        shown = plan.planned if everything else plan.planned[:limit]
        lines += [_row(i, item, names) for i, item in enumerate(shown, 1)]
        if len(shown) < len(plan.planned):
            lines.append(f"       … {len(plan.planned) - len(shown):,} more planned (--all lists them)")
    else:
        lines.append("  Nothing fits: " + ("raise --runs." if plan.deferred else "nothing here can be tested yet."))
    if plan.deferred:
        need = sum(item.runs for item in plan.deferred)
        lines += ["", f"NOT THIS TIME  {len(plan.deferred):,} more need {need:,} more runs "
                      f"(--runs {plan.budget + need:,} plans them all)"]
        if everything:
            lines += [_row(i, item, names) for i, item in enumerate(plan.deferred, len(plan.planned) + 1)]
    if plan.skipped:
        lines += ["", f"SKIPPED  {len(plan.skipped):,}"]
        groups: dict[Skip, list[Skipped]] = defaultdict(list)
        for item in plan.skipped:
            groups[item.why].append(item)
        for why, items in sorted(groups.items(), key=lambda pair: -len(pair[1])):
            counted: dict[str, int] = defaultdict(int)
            for item in items:
                counted[item.detail] += 1
            details = sorted((d for d in counted if d), key=lambda d: -counted[d])
            lines.append(f"  {len(items):>7,}  {why.value}" + (f" — {details[0]}" if len(details) == 1 else ""))
            if everything:
                lines += [f"           {name(item.target, names)}" + (f"  ({item.detail})" if len(details) > 1 else "")
                          for item in items]
            elif len(details) > 1:
                lines.append(f"           e.g. {details[0]}; {details[1]}")
    lines += ["", "Nothing has run: this is the plan. --explain <id or name> says why one is where it is."]
    return "\n".join(lines) + "\n"


def explain(plan: Plan, wanted: str, names: Defined) -> str | None:
    """Why one target is where it is; None when nothing matches. Matched by id — a rule's id finds the whole skill or
    hook it belongs to — or by a unique part of an id or name."""
    every = [*plan.planned, *plan.deferred, *plan.skipped]
    matches = ([item for item in every if item.target.id == wanted or wanted in {r.id for r in item.target.rules}] or
               [item for item in every if wanted.lower() in item.target.id.lower() or wanted.lower() in name(item.target, names).lower()])
    if not matches:
        return None
    if len(matches) > 1:
        shown = "\n".join(f"  {item.target.id}" for item in matches[:10])
        return f"{len(matches):,} match {wanted!r}; name one by its id:\n{shown}\n"
    item = matches[0]
    target = item.target
    head = f"{name(target, names)}\n  id {target.id}\n"
    if target.whole and target.source.ablation.mechanism is not Ablation.NONE:
        head += "  removed whole: no variant can cut out one part of it\n"
    load = _LOAD[target.source.activation.load]
    if isinstance(item, Skipped):
        return head + f"  SKIPPED: {item.why.value}" + (f" — {item.detail}" if item.detail else "") + "\n"
    rank = [*plan.planned, *plan.deferred].index(item) + 1
    why = (f"loaded {load}, so in context on every request" if target.source.activation.load.in_first_turn
           else f"loaded {load}; what loads every session or message comes first") + f"; ≈{target.tokens:,} tokens"
    if item in plan.planned:
        return head + f"  PLANNED #{rank} of {len(plan.planned):,}: {why}. {item.runs} runs.\n"
    return head + (f"  NOT THIS TIME: #{rank} of {len(plan.planned) + len(plan.deferred):,} testable; the budget of "
                   f"{plan.budget:,} runs fits {len(plan.planned):,}. {why}.\n")
