"""Checks that need Claude Code knowledge: the listing budget, transcript structure, permissions."""

from __future__ import annotations

import difflib
import re
from collections import Counter, defaultdict
from agentlint.adapters.claude_code.adapter import AGENT_LISTING, DEFERRED_TOOLS, SKILL_LISTING, Evidence
from agentlint.adapters.claude_code.hooks import _matcher_allows
from agentlint.adapters.claude_code.package_paths import executed_paths, hook_paths
from agentlint.adapters.claude_code.points import Point
from agentlint.checks import Check, NoSession, NotEvaluated, Partial
from agentlint.checks.text import line_of, prose_sources
from agentlint.config.content import FileText, ObservedText
from agentlint.config.formats import ContentFormat
from agentlint.config.markdown import blank_code
from agentlint.config.model import ConfigSnapshot, Source
from agentlint.config.reading import Reading
from agentlint.config.taxonomy import EntityRole, Load, Mechanism
from agentlint.report.finding import Confidence, Figure, Finding, FindingKind, Location, Severity


def evidence(reading: Reading) -> Evidence:
    """This adapter's evidence for a reading: what its checks judge by beyond the snapshot, from the same read."""
    if not isinstance(reading.evidence, Evidence):
        raise TypeError(f"not a Claude Code reading: its evidence is {type(reading.evidence).__name__}")
    return reading.evidence

BUDGET_FRACTION = 0.01  # settings.skillListingBudgetFraction default — VERIFIED in the 2.1.285 bundle
CHARS_PER_TOKEN = 3  # what the harness assumes for current models (4 for some older ones)
_ENTRY = re.compile(r"^- (.+?)(?:: (.*))?$")
AT_CAP = 0.9  # a listing this close to its budget is being cut; further below, a bare name just has no description


def _parts(snapshot: ConfigSnapshot, base: str) -> list[Source]:
    return [s for s in snapshot.sources if s.id == base or s.id.startswith(base + ":")]


def _context_window(model: str | None) -> int:
    return 1_000_000 if model and "[1m]" in model else 200_000


def _fraction(snapshot: ConfigSnapshot) -> float:
    """The configured budget fraction, from the last settings layer that sets one."""
    import json

    fraction = BUDGET_FRACTION
    for source in snapshot.sources:
        if source.provenance.point == Point.SETTINGS.value and hasattr(source.content, "text"):
            try:
                value = json.loads(source.content.text).get("skillListingBudgetFraction")
            except (ValueError, AttributeError):
                continue
            if isinstance(value, (int, float)):
                fraction = float(value)
    return fraction


def _budget(snapshot: ConfigSnapshot) -> int:
    return int(_context_window(snapshot.model) * CHARS_PER_TOKEN * _fraction(snapshot))


def _listing_cut(snapshot: ConfigSnapshot) -> bool | None:
    """Whether the cap is cutting descriptions: the delivered listing near its budget, and some listed
    skill shown by name only. None when no session showed the listing."""
    from agentlint.config.entity import Visibility

    parts = [p for p in _parts(snapshot, SKILL_LISTING) if isinstance(p.content, ObservedText)]
    if not parts:
        return None
    near_cap = sum(len(p.content.text) + 1 for p in parts) >= AT_CAP * _budget(snapshot)
    bare = any(snapshot.seen_as(e.id) is Visibility.NAME_ONLY for e in snapshot.entities
               if e.kind.namespace == "skills")
    return near_cap and bare


def listing_budget(snapshot: ConfigSnapshot) -> list[Finding]:
    parts = [p for p in _parts(snapshot, SKILL_LISTING) if isinstance(p.content, ObservedText)]
    if not parts:
        raise (NotEvaluated if snapshot.sessions else NoSession)("no recent session delivered a skill listing")
    from agentlint.config.entity import Visibility

    listed = {e.name for e in snapshot.entities  # what the session recorded as listed: a description's
              if snapshot.seen_as(e.id) in (Visibility.DESCRIBED, Visibility.NAME_ONLY)}  # bullets aren't entries
    described: Counter[str] = Counter()
    name_only: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    delivered_chars = 0
    for part in parts:
        owner = part.provenance.package or "your own and built-in"
        for line in part.content.text.splitlines():
            delivered_chars += len(line) + 1
            match = _ENTRY.match(line)
            if not match or match.group(1).strip() not in listed:
                continue
            if match.group(2):
                described[owner] += 1
            else:
                name_only[owner] += 1
                if len(examples[owner]) < 3:
                    examples[owner].append(match.group(1))
    total_name_only, total = sum(name_only.values()), sum(name_only.values()) + sum(described.values())
    budget = _budget(snapshot)
    if not total_name_only or delivered_chars < AT_CAP * budget:
        return []  # nothing is being cut: a bare name below the cap is one with no description to give
    by_package = "\n".join(
        f"{count:>4} name-only from {owner}" + (f" (e.g. {', '.join(examples[owner])})" if examples[owner] else "")
        for owner, count in name_only.most_common(8)
    )
    seen = max(len(p.content.seen_in) for p in parts)
    listing_tokens = sum(p.content.tokens.value for p in parts)
    step_chars = _context_window(snapshot.model) * CHARS_PER_TOKEN * 0.01
    step_tokens = round(step_chars * listing_tokens / delivered_chars) if delivered_chars else 0  # the listing's own ratio
    detail = (
        f"{total_name_only} of {total} listed skills and commands reach the model as a bare name, with no description. "
        f"The model is told to choose a skill when \"the task at hand is one a listed skill covers\", so for these it "
        f"has only the name to go on; you can still invoke them yourself with /name.\n"
        f"Why: the harness caps the listing at contextWindow × {CHARS_PER_TOKEN} × skillListingBudgetFraction "
        f"= {budget:,} characters for {snapshot.model or 'this model'}; the delivered listing is "
        f"≈{delivered_chars:,}. Descriptions go first to the harness's built-ins, then by your recent usage, then in "
        f"listing order, until the budget runs out.\n{by_package}"
    )
    return [LISTING_BUDGET.finding(
        kind=FindingKind.DELIVERY,
        severity=Severity.WARN,
        confidence=Confidence.CERTAIN,
        title=f"{total_name_only} of {total} skills reach the model as a bare name",
        detail=detail,
        discriminator="skill-listing",
        sources=tuple(p.id for p in parts),
        figures=(Figure("name-only", total_name_only, "entries", "entry"), Figure("listed", total, "entries", "entry"),
                 Figure("budget", budget, "chars", "char"), Figure("delivered", delivered_chars, "chars", "char"),
                 Figure("sessions showing it", seen, "sessions", "session")),
        suggested_action=("Disable packages you don't use (`/skills`), or raise `skillListingBudgetFraction` in settings — "
                          f"each +0.01 adds about {step_tokens:,} tokens to every request."),
    )]


_POINT_NAMES = {
    SKILL_LISTING: "skill listing entries",
    AGENT_LISTING: "agent listing entries",
    DEFERRED_TOOLS: "MCP tool names",
}


def package_footprint(snapshot: ConfigSnapshot) -> list[Finding]:
    if not snapshot.sessions:
        raise NoSession("no recent sessions show what packages deliver")
    costs: dict[str, Counter[str]] = defaultdict(Counter)
    resting_on: list[str] = []  # every declared always-on package source, observed or not: a stable set
    for source in snapshot.sources:
        package = source.provenance.package
        if not package or not source.activation.load.in_first_turn:
            continue
        resting_on.append(source.id)
        if source.tokens is None:
            continue
        if source.provenance.point.mechanism is Mechanism.HOOK_OUTPUT:
            part = "hook output, every message" if source.activation.load is Load.PER_MESSAGE else "hook output"
        else:
            part = next((name for base, name in _POINT_NAMES.items() if source.id.startswith(base)), "other")
        costs[package][part] += source.tokens.value
    if not costs:
        return []
    ranked = sorted(costs.items(), key=lambda item: -sum(item[1].values()))
    capped = bool(_listing_cut(snapshot))
    lines = []
    for package, parts in ranked[:15]:
        removable = sum(v for k, v in parts.items() if not (capped and k == "skill listing entries"))
        breakdown = ", ".join(f"{k} {v:,}" for k, v in parts.most_common())
        lines.append(f"≈{sum(parts.values()):>6,}  {package}  ({breakdown}; ≈{removable:,} would leave context if disabled)")
    if len(ranked) > 15:
        lines.append(f"… {len(ranked) - 15} more packages, ≈{sum(sum(p.values()) for _, p in ranked[15:]):,} tokens")
    total = sum(sum(parts.values()) for parts in costs.values())
    detail = "\n".join(lines) + ("\nThe skill listing is at its cap, so a package's share of it doesn't leave context "
                                 "when the package is disabled: the freed room goes to other skills' descriptions."
                                 if capped else "")
    return [PACKAGE_FOOTPRINT.finding(
        kind=FindingKind.COST,
        severity=Severity.INFO,
        confidence=Confidence.CERTAIN,
        title=f"{len(costs)} packages add ≈{total:,} tokens to every session",
        detail=detail,
        discriminator="by-package",
        sources=tuple(resting_on),
        figures=tuple(Figure(package, sum(parts.values()), "tokens", "token") for package, parts in ranked),
        suggested_action="Disable packages you don't use; `agentlint show --all` lists every source.",
    )]


def interrupted_start(evidence: Evidence) -> list[Finding]:
    recent = evidence.sessions
    if not recent:
        raise NoSession("no recent sessions to read")
    affected = [raw for raw in recent if raw.interrupted_start and not raw.skill_listing]
    if not affected:
        return []
    lines = "\n".join(f"{raw.header.started_at.astimezone():%Y-%m-%d %H:%M}  {raw.header.session_id[:8]}  "
                      f"{raw.header.project or '?'}  (harness {raw.header.harness_version})"
                      for raw in affected if raw.dated)  # local time, as every other time the CLI and the UI show
    return [INTERRUPTED_START.finding(
        kind=FindingKind.DELIVERY,
        severity=Severity.WARN,
        confidence=Confidence.CERTAIN,
        title=f"In {len(affected)} of your last {len(recent)} sessions the skill listing never reached the model",
        detail=(f"{lines}\nEach began with a prompt that was abandoned before any reply — cancelled, or followed by "
                "commands such as /model or /effort and sent again. The harness dropped that first submission's "
                "attachments and did not send the skill listing again, so for the whole session the agent had no list "
                "of your skills to choose from."),
        discriminator="skill-listing-dropped",
        figures=(Figure("affected", len(affected), "sessions", "session"),
                 Figure("examined", len(recent), "sessions", "session")),
        suggested_action="Start a session with your prompt and change /model or /effort after the first reply; "
                         "this is harness behaviour worth reporting to its maintainers.",
    )]


SLOW_START_MS = 5_000
CLUSTER_WINDOW_S = 600
MIN_FAILURES = 3  # fewer, and a failure may be transient: info, not warn
# No rates: the harness records failures and timeouts, but only some successful runs (none for many
# PreToolUse and UserPromptSubmit hooks), so how often a hook ran without failing isn't known.
_UNKNOWN_RUNS = "How often it ran without failing isn't recorded."


def _target(record) -> str | None:
    """A run's match target — "startup", a tool name — from its recorded "<Event>:<target>" name; None when it has none."""
    return record.hook_name.partition(":")[2] or None


def _running(command: str, failing, entries) -> list:
    """The hooks configured now that run this command for one of these runs' events and whose matcher accepts one of
    their targets: what a finding about those runs rests on and points at. The same command under another event, or
    under this event with a matcher that rejects every one of these runs, is a different hook — it never fired them —
    so it is not one of them (the tracer filters by the same matcher); a module hook names no command, so none is."""
    if not command:
        return []
    fired = {(r.event, _target(r)) for r in failing}
    found = {e.source_id: e for e in entries if not e.module and e.command == command
             and any(e.event == event and _matcher_allows(e, target) for event, target in fired)}
    return list(found.values())


def _clustered(records) -> bool:
    """Whether most of these events fall inside one ten-minute window (a busy machine, not a bad hook)."""
    times = sorted(r.at.timestamp() for r in records if r.at)
    if len(times) < 3:
        return False
    best = max(sum(1 for t in times if start <= t < start + CLUSTER_WINDOW_S) for start in times)
    return best * 2 > len(times)


def hook_health(evidence: Evidence) -> Partial:
    if not evidence.sessions:
        raise NoSession("no recent sessions to read")
    order = {raw.path.stem: index for index, raw in enumerate(evidence.sessions)}  # 0 = most recent
    records = evidence.hook_runs
    entries = evidence.hooks
    by_command: dict[str, list] = defaultdict(list)
    for record in records:
        by_command[record.command or record.hook_name].append(record)
    findings = []
    for command, runs in sorted(by_command.items()):
        short = command if len(command) <= 90 else command[:87] + "…"

        def hooks(failing) -> tuple[str, str, bool, str, dict]:
            """For these runs: their events, who declares the hooks that ran them, whether any is configured now, how
            the detail says it isn't (the command configured only for another event is not configured for these), and
            what the finding rests on and points at — the title's owner from the same entries, so the title and the
            package and file a row shows never disagree."""
            fired = {r.event for r in failing}
            events = "/".join(sorted(fired))
            running = _running(runs[0].command, failing, entries)
            # The same command declared now, but not as a hook that ran these: under another event, or under this
            # event with a matcher none of these runs' targets meets (then the label names the matcher).
            ran_ids = {e.source_id for e in running}
            others = [e for e in entries if runs[0].command and not e.module and e.command == runs[0].command
                      and e.source_id not in ran_ids]
            other = sorted({e.event if e.event not in fired else f"{e.event} on {e.matcher}" for e in others})
            targets = sorted({t for r in failing if (t := _target(r))})
            on = f" on {'/'.join(targets)}" if targets and any(e.event in fired for e in others) else ""
            owners = sorted({e.owner for e in running})
            owner = (owners[0] if len(owners) == 1 else "one of " + ", ".join(owners) if owners
                     else f"declared now only for {', '.join(other)}" if other else "not declared in your configuration now")
            ids = tuple(e.source_id for e in running)
            return (events, owner, bool(running), f" for {events}{on}" if other and not running else "",
                    {"sources": ids, "locations": tuple(Location(i) for i in ids)})

        def grade(failing, configured: bool, elsewhere: str) -> tuple[Severity, str, str]:
            """Severity, a note for the detail, and which advice fits: isolated, live or resolved."""
            newest = min(order.get(r.session_id, len(order)) for r in failing)
            last = max((r.at for r in failing if r.at), default=None)
            if len(failing) < MIN_FAILURES:
                return Severity.INFO, "Isolated: could be transient.", "isolated"
            if configured or newest <= 1:
                note = "" if configured else f"Not in your configuration files{elsewhere}: something enables it at launch."
                return Severity.WARN, note, "live"
            when = f"{last.astimezone():%Y-%m-%d}" if last else "an older session"
            return (Severity.INFO, f"Last seen {when}; no longer configured{elsewhere}, so probably already resolved.",
                    "resolved")

        def resolved_advice(elsewhere: str) -> str:
            return f"Nothing configured runs it{elsewhere} now; if it still fails, find what enables it at launch."

        def spread(failing) -> str:
            sessions = len({r.session_id for r in failing})
            return (f"{len(failing)} time{'s' if len(failing) != 1 else ''} in "
                    f"{sessions} session{'s' if sessions != 1 else ''}")

        def detail(*lines: str) -> str:
            return "\n".join(line for line in lines if line)

        errors = [r for r in runs if r.outcome == "error"]
        if errors:
            stderr = Counter(r.detail.strip().splitlines()[0] if r.detail.strip() else "(no stderr)" for r in errors)
            sample, _ = stderr.most_common(1)[0]
            events, owner, configured, elsewhere, rests = hooks(errors)
            severity, note, state = grade(errors, configured, elsewhere)
            findings.append(HOOK_HEALTH.finding(
                kind=FindingKind.HEALTH, severity=severity, confidence=Confidence.CERTAIN,
                title=f"A {events} hook failed {spread(errors)} ({owner})",
                detail=detail(f"Command: {short}", f"Most common error: {sample}", note, _UNKNOWN_RUNS),
                discriminator=f"error:{command}", **rests,
                figures=(Figure("failures", len(errors), "runs", "run"),
                         Figure("sessions", len({r.session_id for r in errors}), "sessions", "session")),
                suggested_action={"isolated": "Check whether it happens again before acting.",
                                  "live": "Fix or remove the hook: it fails when its event fires, and you pay for it each time.",
                                  "resolved": resolved_advice(elsewhere)}[state],
            ))
        timeouts = [r for r in runs if r.outcome == "timeout"]
        if timeouts:
            events, owner, configured, elsewhere, rests = hooks(timeouts)
            severity, note, state = grade(timeouts, configured, elsewhere)
            cause = ("Most of these happened within ten minutes of each other, which points at a busy machine "
                     "rather than the hook itself." if _clustered(timeouts) else "")
            findings.append(HOOK_HEALTH.finding(
                kind=FindingKind.HEALTH, severity=severity, confidence=Confidence.CERTAIN,
                title=f"A {events} hook was killed at its timeout {spread(timeouts)} ({owner})",
                detail=detail(f"Command: {short}", "Each time, its work did not complete, and anything it would have "
                              "injected or enforced was lost.", cause, note, _UNKNOWN_RUNS),
                discriminator=f"timeout:{command}", **rests,
                figures=(Figure("timeouts", len(timeouts), "runs", "run"),
                         Figure("sessions", len({r.session_id for r in timeouts}), "sessions", "session")),
                suggested_action={"isolated": "Check whether it happens again before acting.",
                                  "live": "Make the hook faster, or raise its timeout if the work is worth waiting for.",
                                  "resolved": resolved_advice(elsewhere)}[state],
            ))
        starts = sorted(r.duration_ms for r in runs if r.event == "SessionStart" and r.outcome == "ok" and r.duration_ms)
        if len(starts) >= 3 and starts[len(starts) // 2] >= SLOW_START_MS:
            median = starts[len(starts) // 2]
            _, owner, _, _, rests = hooks([r for r in runs if r.event == "SessionStart"])
            findings.append(HOOK_HEALTH.finding(
                kind=FindingKind.HEALTH, severity=Severity.INFO, confidence=Confidence.CERTAIN,
                title=f"A SessionStart hook takes a median {median / 1000:.1f}s ({owner})",
                detail=f"Command: {short}\n{len(starts)} runs, slowest {starts[-1] / 1000:.1f}s.",
                discriminator=f"slow:{command}", **rests,
                figures=(Figure("median", median, "ms", "ms"), Figure("slowest", starts[-1], "ms", "ms")),
                suggested_action="Session start waits on it; make it faster or move its work off the start path.",
            ))
    ran = set(by_command)
    configured = {entry.command for entry in entries}

    def judged(discriminator: str) -> bool:
        """Only a hook seen running in this window, or one no longer configured, can be judged fixed."""
        command = discriminator.split(":", 1)[1]
        return command in ran or command not in configured

    return Partial(findings, judged)


# --- unavailable-reference ---------------------------------------------------------------------

# Claude Code built-in tool names, current and historical. A capitalised word before "tool(s)" is
# only a tool reference if it is one of these — generic words ("AI tools") never are.
HISTORICAL_TOOLS = frozenset({
    "Agent", "Task", "Bash", "BashOutput", "KillShell", "KillBash", "Read", "Write", "Edit", "MultiEdit",
    "NotebookEdit", "NotebookRead", "Glob", "Grep", "LS", "WebFetch", "WebSearch", "TodoWrite", "TodoRead",
    "ExitPlanMode", "EnterPlanMode", "AskUserQuestion", "Skill", "SlashCommand", "ToolSearch",
})
_MCP_NAME = re.compile(r"mcp__[A-Za-z0-9_-]+__[A-Za-z0-9_.-]*[A-Za-z0-9_]")  # never ends on sentence punctuation
_SEPARATOR = r"\s*(?:,|/|\band\b|\bor\b)\s*"  # whole words only: "SlashCommand" holds no "and"
_TOOL_WORDS = re.compile(rf"\b((?:[A-Z][A-Za-z]+)(?:{_SEPARATOR}(?:[A-Z][A-Za-z]+))*)\s+tools?\b")
# Inside a prohibition, naming a denied tool agrees with the deny rule rather than contradicting it. Judged
# within the clause holding the name: words that negate what follows them count only before it ("never use
# X", but not "use X instead of Y"); words that describe a state count anywhere ("X is blocked here").
_NEGATES_WHAT_FOLLOWS = re.compile(r"(?i)\b(?:never|don'?t|do not|must not|should not|avoid|instead of|rather than|"
                                   r"not use|no longer use|stop using)\b")
_STATE = re.compile(r"(?i)\b(?:blocked|disabled|denied|forbidden|prohibited|not allowed)\b")
_SENTENCE_ENDS = (". ", "! ", "? ", "\n")
_CLAUSE_BREAK = re.compile(r";|\bso\b|\bbut\b")  # never commas or dashes: "never use A, B or C" is one prohibition


def _clause_around(text: str, offset: int) -> tuple[str, str]:
    """The clause holding ``offset``, as (the part before it, the whole clause)."""
    start = max(text.rfind(mark, 0, offset) for mark in _SENTENCE_ENDS) + 1
    ends = [i for i in (text.find(mark, offset) for mark in _SENTENCE_ENDS) if i != -1]
    sentence = text[start:min(ends) if ends else len(text)]
    at = offset - start
    breaks = list(_CLAUSE_BREAK.finditer(sentence))
    begin = max((b.end() for b in breaks if b.end() <= at), default=0)
    end = min((b.start() for b in breaks if b.start() >= at), default=len(sentence))
    return sentence[begin:at], sentence[begin:end]


def _prohibited(text: str, offset: int) -> bool:
    before, clause = _clause_around(text, offset)
    return bool(_NEGATES_WHAT_FOLLOWS.search(before) or _STATE.search(clause))


def _quote_at(text: str, offset: int, width: int = 160) -> str:
    """The sentence holding ``offset``, as one line; centred on it when the sentence is long."""
    start = max(text.rfind(mark, 0, offset) for mark in (". ", "! ", "? ", "\n")) + 1
    ends = [i for i in (text.find(mark, offset) for mark in (". ", "! ", "? ", "\n")) if i != -1]
    sentence = text[start:min(ends) + 1 if ends else None]
    at = offset - start
    if len(sentence) > width:
        left = max(0, min(at - width // 3, len(sentence) - width))
        sentence = ("…" if left else "") + sentence[left:left + width] + ("…" if left + width < len(sentence) else "")
    return " ".join(sentence.split())


def _deny_rules(snapshot: ConfigSnapshot) -> set[str]:
    import json

    rules: set[str] = set()
    for source in snapshot.sources:
        if source.provenance.point == Point.SETTINGS.value and hasattr(source.content, "text"):
            try:
                deny = (json.loads(source.content.text).get("permissions") or {}).get("deny") or []
            except (ValueError, AttributeError):
                continue
            rules |= {rule for rule in deny if isinstance(rule, str)}
    return rules


def _denied(tool: str, rules: set[str]) -> bool:
    return any(tool == rule or (rule.endswith("*") and tool.startswith(rule[:-1])) or tool.startswith(rule + "__")
               for rule in rules)


def unavailable_reference(snapshot: ConfigSnapshot, evidence: Evidence) -> list[Finding]:
    from agentlint.config.content import FileText
    from agentlint.report.finding import Location

    delivered = {e.name for e in snapshot.entities if e.kind.role is EntityRole.TOOL}
    if not delivered:
        raise (NotEvaluated if snapshot.sessions else NoSession)("no recent session shows which tools were delivered")
    rules = _deny_rules(snapshot)
    failed = {name for raw in evidence.sessions for name in raw.failed_mcp_servers}
    packages = evidence.packages
    vocabulary = HISTORICAL_TOOLS | {raw_tool for raw in evidence.sessions for raw_tool in raw.tool_names}
    refs: dict[str, list[tuple[Source, int]]] = defaultdict(list)
    for source in prose_sources(snapshot):
        text = source.content.text
        for match in _MCP_NAME.finditer(text):
            refs[match.group(0)].append((source, match.start()))
        for match in _TOOL_WORDS.finditer(text):
            for name in re.split(_SEPARATOR, match.group(1)):
                if name in vocabulary:
                    refs[name].append((source, match.start()))

    def where(hits):
        seen, locations = set(), []
        for source, offset in hits:
            line = line_of(source.content.text, offset) if isinstance(source.content, FileText) else None
            key = (source.id, line)
            if key not in seen:
                seen.add(key)
                locations.append(Location(source.id, line))
        return tuple(locations)

    def every_session(hits) -> bool:
        return any(source.activation.load.in_first_turn for source, _ in hits)

    findings: list[Finding] = []
    unconfigured: dict[str, list[str]] = defaultdict(list)
    for tool, hits in sorted(refs.items()):
        sources = tuple(dict.fromkeys(source.id for source, _ in hits))
        if _denied(tool, rules):
            asked = [(source, offset) for source, offset in hits if not _prohibited(source.content.text, offset)]
            if asked:
                source, offset = asked[0]
                findings.append(UNAVAILABLE_REFERENCE.finding(
                    kind=FindingKind.BROKEN, severity=Severity.WARN,
                    confidence=Confidence.CERTAIN,
                    title=f"Instructions name {tool} as a tool to use, but your permissions deny it",
                    detail=(f"\"{_quote_at(source.content.text, offset)}\"\nA deny rule in your settings forbids this "
                            "tool, so an instruction to use it can't be followed."
                            + (" The instruction is in context every session." if every_session(asked) else "")),
                    discriminator=f"denied:{tool}", sources=tuple(dict.fromkeys(s.id for s, _ in asked)),
                    locations=where(asked),
                    suggested_action="Remove the instruction, or the deny rule if the tool is actually allowed.",
                ))
            continue
        if tool in delivered:
            continue
        if not tool.startswith("mcp__"):
            findings.append(UNAVAILABLE_REFERENCE.finding(
                kind=FindingKind.BROKEN,
                severity=Severity.WARN if every_session(hits) else Severity.INFO, confidence=Confidence.CERTAIN,
                title=f"Instructions refer to a {tool} tool, which this harness does not provide",
                detail=(f"Recent sessions delivered no tool named {tool}; it may have been renamed or removed "
                        "(Claude Code renamed Task to Agent, for example)."
                        + (" The instruction is in context every session." if every_session(hits) else "")),
                discriminator=f"absent:{tool}", sources=sources, locations=where(hits),
                suggested_action=f"Update the instruction to a tool that exists, or remove it.",
            ))
            continue
        package = tool_package_for(tool, packages)
        server = tool.split("__")[1]
        server_tools = [d for d in delivered if d.startswith(f"mcp__{server}__")]
        if server_tools:  # the server connected and delivered other tools: this name is stale
            findings.append(UNAVAILABLE_REFERENCE.finding(
                kind=FindingKind.BROKEN, severity=Severity.WARN,
                confidence=Confidence.CERTAIN,
                title=f"Instructions name {tool}, but its server offers no such tool",
                detail=f"The server connected and delivered {len(server_tools)} other tools; this name is not one of them.",
                discriminator=f"stale:{tool}", sources=sources, locations=where(hits),
                suggested_action="Update the tool name to one the server actually provides.",
            ))
        else:
            unconfigured[server].append(tool)
    for server, tools in sorted(unconfigured.items()):
        hits = [hit for tool in tools for hit in refs[tool]]
        reason = ("its server failed to connect in recent sessions" if any(server == f.replace(":", "_") for f in failed)
                  else "no server of that name delivered tools in recent sessions")
        findings.append(UNAVAILABLE_REFERENCE.finding(
            kind=FindingKind.BROKEN, severity=Severity.INFO,
            confidence=Confidence.CERTAIN,
            title=(f"{len(tools)} tools from server \"{server}\" are named in instructions but unavailable here"
                   if len(tools) > 1 else f"A tool from server \"{server}\" is named in instructions but unavailable here"),
            detail=f"{', '.join(sorted(tools)[:6])}{' …' if len(tools) > 6 else ''}\nUnavailable because {reason}. "
                   "Instructions written for other setups often mention optional tools; this is information, not a defect.",
            discriminator=f"unavailable-server:{server}", sources=tuple(dict.fromkeys(s.id for s, _ in hits)),
            locations=where(hits),
        ))
    return findings


def tool_package_for(tool: str, packages) -> str | None:
    from agentlint.adapters.claude_code.listings import tool_package

    return tool_package(tool, packages)


# --- broken-reference: package paths ------------------------------------------------------------

_GUARDS = ("[ -f", "[ -e", "test -f", "test -e", "|| exit 0", "|| true")


def broken_path(snapshot: ConfigSnapshot, evidence: Evidence) -> list[Finding]:
    """broken-reference, adapter part: ${CLAUDE_PLUGIN_ROOT} paths that don't exist, where the harness
    or the agent would run them.

    Hook commands are run by definition. In package text, only paths handed to an interpreter
    (``python3 ${CLAUDE_PLUGIN_ROOT}/x.py``) count: prose mentions on real data were optional
    ("if it is present"), instructions about such paths, or documentation examples. Which paths exist was
    settled when the configuration was read (``package_paths.py``).
    """
    from agentlint.report.finding import Location

    roots = {package.id: package.root for package in evidence.packages}
    hits: dict[tuple[str, str], dict] = {}
    for entry in evidence.hooks:
        if not entry.package:
            continue
        for rel in hook_paths(entry.command):
            if (entry.package.id, rel) in evidence.missing_paths:
                found = hits.setdefault((entry.package.id, rel), {"places": [], "hook": True, "guarded": True})
                found["places"].append(Location(entry.source_id))
                found["guarded"] = found["guarded"] and any(guard in entry.command for guard in _GUARDS)
    for source in snapshot.sources:
        package = source.provenance.package
        if package not in roots or not hasattr(source.content, "text") or source.content.format is ContentFormat.CODE:
            continue
        text = source.content.text
        for rel, offset in executed_paths(text):
            if (package, rel) in evidence.missing_paths:
                found = hits.setdefault((package, rel), {"places": [], "hook": False, "guarded": False})
                found["places"].append(Location(source.id, text.count("\n", 0, offset) + 1))
    findings = []
    for (package, rel), found in sorted(hits.items()):
        guarded = found["hook"] and found["guarded"]
        what = "A hook command" if found["hook"] else "Instructions"
        findings.append(BROKEN_PATH.finding(
            kind=FindingKind.BROKEN,
            severity=Severity.INFO if guarded else Severity.ERROR, confidence=Confidence.CERTAIN,
            title=f"{what} in {package} run{'s' if found['hook'] else ''} {rel}, which isn't in the package",
            detail=(f"${{CLAUDE_PLUGIN_ROOT}}/{rel} resolves to {roots[package] / rel}, which does not exist."
                    + (" The command checks for it first, so it skips quietly instead of failing." if guarded else "")),
            discriminator=f"path:{package}:{rel}",
            locations=tuple(found["places"]),
            suggested_action="Restore the file or fix the path; the package may need reinstalling.",
        ))
    return findings


# --- broken-reference: entities named the Claude Code way --------------------------------------------

# A package entity is "pkg:name"; it is invoked as /pkg:name, Skill('pkg:name') or subagent_type: pkg:name.
CONFIDENT_MATCH = 0.9  # only then is a "did you mean" a recommendation rather than a guess
_NAME = r"[a-z0-9][a-z0-9_-]*:[a-z0-9][a-z0-9_-]*(?::[a-z0-9][a-z0-9_-]*)*"
_CODE_SPAN = re.compile(rf"`/?({_NAME})`")
_CALLS = [
    re.compile(rf"Skill\(\s*['\"]({_NAME})['\"]"),
    re.compile(rf"subagent_type['\"]?\s*[:=]\s*['\"]?({_NAME})['\"]?"),
]


def broken_entity_references(snapshot: ConfigSnapshot) -> list[Finding]:
    """broken-reference, adapter part: ``pkg:name`` references whose package exists but whose name doesn't.

    Only in an invocation context — an inline code span, ``Skill('…')`` or ``subagent_type: …`` (the
    calls count inside code blocks too: they are what the agent copies); bare mentions in prose are
    never flagged, since real data had feature-request titles and self-declared names that look the same.
    """
    names = {entity.name for entity in snapshot.entities}
    by_namespace: dict[str, list[str]] = defaultdict(list)
    for name in names:
        if ":" in name:
            by_namespace[name.split(":", 1)[0]].append(name)
    hits: dict[str, list[tuple[Source, int]]] = defaultdict(list)
    for source in prose_sources(snapshot):
        text = source.content.text
        for match in [*_CODE_SPAN.finditer(blank_code(text, spans=False)), *(m for call in _CALLS for m in call.finditer(text))]:
            reference = match.group(1)
            namespace = reference.split(":", 1)[0]
            if namespace in by_namespace and reference not in names:
                hits[reference].append((source, match.start(1)))
    findings = []
    for reference, places in sorted(hits.items()):
        namespace = reference.split(":", 1)[0]
        guess = difflib.get_close_matches(reference, by_namespace[namespace], n=1, cutoff=0.6)
        confident = bool(guess) and difflib.SequenceMatcher(None, reference, guess[0]).ratio() >= CONFIDENT_MATCH
        locations, seen = [], set()
        for source, offset in places:
            line = line_of(source.content.text, offset) if isinstance(source.content, FileText) else None
            if (source.id, line) not in seen:
                seen.add((source.id, line))
                locations.append(Location(source.id, line))
        findings.append(BROKEN_ENTITY_REFERENCE.finding(
            kind=FindingKind.BROKEN, severity=Severity.ERROR,
            confidence=Confidence.CERTAIN,
            title=f"References to {reference}, which doesn't exist in {namespace}",
            detail=(f"{namespace} is installed, but has nothing named {reference}; invoking it would fail."
                    + (f" Did you mean {guess[0]}?" if confident else
                       f" The closest existing name is {guess[0]}, which may or may not be what was meant." if guess else "")),
            discriminator=f"entity:{reference}", sources=tuple(dict.fromkeys(s.id for s, _ in places)),
            locations=tuple(locations),
            suggested_action=f"Change it to {guess[0]}." if confident else "Fix or remove the reference.",
        ))
    return findings


LISTING_BUDGET = Check("listing-budget", "skills that reach the model as a bare name, and why",
                       lambda reading: listing_budget(reading.snapshot))
PACKAGE_FOOTPRINT = Check("package-footprint", "what each package adds to every session",
                          lambda reading: package_footprint(reading.snapshot))
INTERRUPTED_START = Check("interrupted-start", "sessions whose skill listing never reached the model",
                          lambda reading: interrupted_start(evidence(reading)))
HOOK_HEALTH = Check("hook-health", "hooks that fail, time out, or slow session start",
                    lambda reading: hook_health(evidence(reading)),
                    by_text=False)  # judged by recorded runs, not by what the hooks it rests on printed
UNAVAILABLE_REFERENCE = Check("unavailable-reference", "instructions naming tools that are denied or absent",
                              lambda reading: unavailable_reference(reading.snapshot, evidence(reading)))
BROKEN_PATH = Check("broken-reference", "package paths that hooks or instructions run, but that don't exist",
                    lambda reading: broken_path(reading.snapshot, evidence(reading)))
BROKEN_ENTITY_REFERENCE = Check("broken-reference", "references to skills and agents that don't exist",
                                lambda reading: broken_entity_references(reading.snapshot))


def adapter_checks() -> list[Check]:
    """Each judges a Reading alone: the snapshot, and this adapter's Evidence from the same read."""
    return [LISTING_BUDGET, PACKAGE_FOOTPRINT, INTERRUPTED_START, HOOK_HEALTH, UNAVAILABLE_REFERENCE, BROKEN_PATH,
            BROKEN_ENTITY_REFERENCE]
