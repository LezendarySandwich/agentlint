"""Static checks: free, fast, no AI, no agent runs (docs/tech_impl/02-static-checks.md).

A check is a pure function of the ``Reading`` it is given — the snapshot, the filesystem facts taken
when it was read, and the adapter's own evidence from the same read — to findings: it never reads the
disk or state another reading left behind (``config/reading.py``). Core checks are harness-agnostic;
an adapter contributes checks that need harness knowledge (a listing's budget formula, transcript
structure) through ``Adapter.checks()``, emitting the same Finding type. An id may have several
contributors — core and adapter both find broken references — and selecting it runs them all.

What check results say about findings that are *absent* matters as much as what they find, because the
finding store reads absence as closed (``store/findings.py``). So a check can say it lacked its input
altogether (``NotEvaluated``), that it could judge only some of its earlier findings this time (``Partial``),
or that its findings can disappear without anything they rest on changing (``needs_change``) — a
ranking moved, or a member only sessions show left the window — in which case such a finding is
closed only when something it rests on changed. A check that judges by something other than the text of the
sources its findings rest on says so (``by_text=False``: hook-health reads recorded runs, not what a hook
printed), so a source whose text no session showed doesn't keep its findings open.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from agentlint.config.entity import Entity
from agentlint.config.model import ConfigSnapshot
from agentlint.config.reading import Reading
from agentlint.report.finding import Finding, Member


@dataclass(frozen=True)
class Partial:
    """A check's findings, from evidence that could judge only some of its earlier findings."""

    findings: list[Finding]
    judged: Callable[[str], bool]  # discriminator -> whether this evidence could tell if it still holds


def member(snapshot: ConfigSnapshot, entity: Entity, *, identical_to: str | None = None,
           symlink: str | None = None) -> Member:
    """An entity as a finding lists it beside others: what provides it, where that sits, what the agent saw of it."""
    try:
        provenance = snapshot.source(entity.provided_by).provenance
    except KeyError:
        provenance = None
    return Member(entity.id, entity.name, entity.kind.label, entity.provided_by,
                  provenance.authority if provenance else None, provenance.package if provenance else None,
                  snapshot.seen_as(entity.id), entity.description, identical_to, symlink)


@dataclass(frozen=True)
class Check:
    id: str  # public: used in Finding.check, --only, and what the user reads
    summary: str
    run: Callable[[Reading], list[Finding] | Partial]  # pure: everything it judges by is in the reading
    needs_change: bool = False  # findings can vanish with nothing they rest on changed: closed only by a change
    by_text: bool = True  # judged by its sources' text: one still configured that no session showed keeps a finding open

    def finding(self, **fields) -> Finding:
        """A finding of this check: ``Finding(check=self.id, ...)``, so its id is written once, where it is declared."""
        return Finding(check=self.id, **fields)


class UnknownCheck(ValueError):
    pass


class CheckError(Exception):
    """A check could not run because of bad input it was given — such as a malformed user marker
    file — as opposed to a defect in the configuration it checks, which is a finding."""


class NotEvaluated(Exception):
    """A check lacks the input to say anything — no recent sessions, too few sources to compare.

    Not the same as finding nothing: its earlier findings stay open rather than being closed. The message says what was missing."""


class NoSession(NotEvaluated):
    """NotEvaluated because no session was read — none at all, or (``in_project``) none in the project's directory.
    When the reading left out sessions from before you last forgot, the run says so instead of the message."""

    def __init__(self, message: str, *, in_project: bool = False) -> None:
        super().__init__(message)
        self.in_project = in_project


def _lacking(missing: NotEvaluated, reading: Reading) -> str:
    if isinstance(missing, NoSession) and reading.forgotten_before is not None:
        where = " in this project" if missing.in_project else ""
        return f"no session{where} since you forgot ({reading.forgotten_before.astimezone():%Y-%m-%d %H:%M})"
    return str(missing)


def _judges_everything(discriminator: str) -> bool:
    return True


@dataclass(frozen=True)
class CheckResults:
    findings: list[Finding]  # most severe first; within a check, the order it emitted
    evaluated: tuple[str, ...]  # check ids whose every contributor ran with its input
    not_evaluated: dict[str, str]  # check id -> what it lacked
    judged: dict[str, Callable[[str], bool]] = field(default_factory=dict)  # check id -> scope, if partial
    needs_change: frozenset[str] = frozenset()  # check ids whose findings are closed only by a change
    not_by_text: frozenset[str] = frozenset()  # check ids that judge by something other than their sources' text

    def judges(self, check_id: str, discriminator: str) -> bool:
        """Whether these results could tell if an earlier finding of that check still holds."""
        return check_id in self.evaluated and self.judged.get(check_id, _judges_everything)(discriminator)


def select(checks: Sequence[Check], only: Sequence[str] | None) -> list[Check]:
    if not only:
        return list(checks)
    known = {check.id for check in checks}
    unknown = [name for name in only if name not in known]
    if unknown:
        raise UnknownCheck(f"unknown check(s): {', '.join(unknown)}; known: {', '.join(sorted(known))}")
    return [check for check in checks if check.id in only]


def run_checks(reading: Reading, checks: Sequence[Check]) -> CheckResults:
    findings: list[Finding] = []
    lacking: dict[str, str] = {}
    scopes: dict[str, list[Callable[[str], bool]]] = {}
    for check in checks:
        try:
            produced = check.run(reading)
        except NotEvaluated as missing:
            lacking.setdefault(check.id, _lacking(missing, reading))
            continue
        if isinstance(produced, Partial):
            scopes.setdefault(check.id, []).append(produced.judged)
            produced = produced.findings
        for finding in produced:
            if finding.check != check.id:
                raise AssertionError(f"check {check.id} emitted a finding labelled {finding.check}")
            findings.append(finding)
    ids = [finding.id for finding in findings]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise AssertionError(f"two findings share an identity: {sorted(duplicates)}")
    evaluated = tuple(dict.fromkeys(check.id for check in checks if check.id not in lacking))
    judged = {check_id: (lambda d, parts=tuple(parts): all(part(d) for part in parts))
              for check_id, parts in scopes.items()}  # with several contributors, every one must judge it
    needs_change = frozenset(check.id for check in checks if check.needs_change)
    not_by_text = frozenset(check.id for check in checks if not check.by_text) - {  # every contributor must say so
        check.id for check in checks if check.by_text}
    ordered = sorted(findings, key=lambda f: f.sort_key)  # stable: each check's own order survives
    return CheckResults(ordered, evaluated, lacking, judged, needs_change, not_by_text)
