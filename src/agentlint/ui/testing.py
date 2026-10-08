"""Tests from the UI: the plan, generated situations and their review, the one-time onboarding, jobs started, stopped,
resumed and salvaged in a worker of their own, and each job's results (docs/tech_impl/11-ui.md "Tests"; 05-runner.md
"The worker"; 04-probe-generator.md "Review"; 07-analysis.md "The stability screen in the UI").

``ui/server.py`` answers these endpoints through ``Testing`` (its ``Tests`` protocol); every write has already passed
the server's guard (token header, host, origin, JSON body), and every value is checked here, refused with ``Refused``.
Core and harness-agnostic: the composition root hands over how to read a project (``read``: the reading and the
static checks' findings), each harness's parts for testing (``Parts``: its adapter, launcher, generator and the
generator's model) and how a worker is started (``command``).

What is decided elsewhere and only carried out here:

- **The plan is the ranker's order** (``rank/plan.py``, phase 1: the stability screen) with no budget: every testable
  target, ranked. **A test's size follows what you tick** (decided 2026-10-05): the targets you tick (kept in the
  ``state`` table per harness and project; the top-ranked one when you never ticked any) × your situations per target ×
  your runs per situation. A job runs only the situations KEPT (D12): kept or edited, never dropped — a situation is
  written kept (decided 2026-10-05: review is the exception, not a gate) — at most ``situations_per_target`` of each
  ticked target, in the ranked order. A target with fewer kept runs those, and the total says so. The job's run ceiling
  is that total (AGENTS.md: every path that spawns runs has one).
- **Start is refused** before the onboarding is accepted (D18), inside one of the harness's own sessions (the adapter
  decides from the environment), and while any job's runner is alive (one job at a time: one credential, one rate
  limit).
- **A job runs in a worker** (``runner/worker.py``) the server launches in its own process group, which outlives the
  server; Stop sends it Ctrl-C and escalates to a kill; on macOS ``caffeinate -i -w <worker pid>`` keeps the Mac from
  idle-sleeping while it runs.
- **Results are the stability screen** (``analysis/stability.py``) with D10's rule for "inconsistent": the set of
  tools, skills and agents each run used, order and files ignored (``analysis/projections.used``). Runs that all used
  none are "no action", never agreed (decided 2026-10-05). No finding is made of a disagreement yet (how one reads is
  decided at v3), and an agreement is never called stable. Each run is listed with its actions in order, each one's
  input on one line (``rendered``: its string values, no key read by name, so no harness is assumed), and every job in
  GET /api/jobs carries its screens counted by state, kept per job and number of runs recorded (Tests › Results).
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import traceback
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from agentlint import settings, store
from agentlint.analysis import effect, expectation
from agentlint.directives import extract
from agentlint.directives.prompt import READER_VERSION
from agentlint.analysis.exercise import ran
from agentlint.analysis.stability import Environment
from agentlint.analysis.effect import EffectRun
from agentlint.analysis.projections import RunPlaces
from agentlint.analysis.stability import ScreenState
from agentlint.config.reading import Reading
from agentlint.probes.contamination import guard_for
from agentlint.probes.generate import EditRefused, generate, review, target_hash
from agentlint.probes.generator import Generator
from agentlint.probes.matters import WhatMatters
from agentlint.probes.matters import parsed as parse_matters
from agentlint.probes.matters import read as read_matters
from agentlint.probes.validate import Limits
from agentlint.rank.plan import AblationTarget, Phase, RunCounts, plan as rank
from agentlint.rank.plan import Plan as Ranked
from agentlint.report.finding import Finding
from agentlint.report.rank import name as target_name
from agentlint.runner import engine, execute, lock, worker
from agentlint.runner.execute import Launcher
from agentlint.runner import gate
from agentlint.runner.gate import NOT_FOLLOWED, by_comparison, closed_why, gate_open, index_of, screened, trace_of
from agentlint.runner.plan import BASELINE, WITHOUT, Comparison, Plan, Planned, two_step_plan
from agentlint.runner.variant import removal_refused
from agentlint.runner.status import ComparisonState, comparison_status, progress
from agentlint.store import JobStatus, ReviewStatus, StoreError, jobs, probes, reads, transaction
from agentlint.store import diagnostics as diagnostic_store
from agentlint.store import state as state_store
from agentlint.store import usage as usage_store
from agentlint.store import verdicts as verdict_store
from agentlint.store.diagnostics import Diagnostic
from agentlint.store.probes import NoSuchProbe, Probe
from agentlint.trace.model import Trace
from agentlint.trace.terms import Terms
from agentlint.ui.server import Params, Refused, ScopeError, Unreadable

E1_RUN_S = 53  # one run's wall-clock in E1 (2026-10-04), the estimate until this machine has recorded runs of its own
MOST_TICKED = 500  # target ids the ticked targets, a job, or one generate request hold (a project ranks a few dozen)
TICKED_PROJECTS = 50  # projects whose ticked targets are kept, the most recently ticked
KEPT_READINGS = 4  # projects whose last reading the server keeps for polling situations (as ui/server.py KEPT_TEXTS)
TEXT_MAX = 2000  # characters of an edited situation
MOST_SOURCES = 5000  # source ids one reading of directives takes: every source of a large configuration
ACTIONS_SHOWN = 60  # a run's actions listed in its job's results; the rest are counted (``more_actions``)
WHAT_MAX = 200  # characters of one action's input, rendered on one line (``rendered``)
_VALUE_READ = 4000  # characters of one input value read for that line: a run's own directory is well inside it
SCREENS = (ScreenState.AGREED, ScreenState.DISAGREED, ScreenState.NO_ACTION, ScreenState.INCOMPLETE,
           ScreenState.MIXED_ENVIRONMENT)  # a job's screens, counted in this order (GET /api/jobs ``screens``)
KEPT = (ReviewStatus.ACCEPTED, ReviewStatus.EDITED)  # what a job runs (D12)
_DECISIONS = {"keep": ReviewStatus.ACCEPTED, "drop": ReviewStatus.REJECTED, "edit": ReviewStatus.EDITED}
_SAID = {ReviewStatus.ACCEPTED: "kept", ReviewStatus.REJECTED: "dropped", ReviewStatus.EDITED: "edited"}  # review's
# words: a situation is written kept, and its ``reviewed`` time says whether a person kept it
_PROBE_ID = re.compile(r"[0-9a-f]{16}")
_JOB_ID_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")  # a job's or a reading's id
_TARGET_ID = re.compile(r"[^\x00-\x1f\x7f]{1,600}")  # a target id as the plan gives it: any text without control
# characters (an instruction section's id holds its heading, spaces and all), checked against the reading's targets
GENERATOR_ERROR = "generator_error"  # a generation that raised instead of returning: a diagnostic, never a finding

POLICY = ("Test runs follow your organisation's guardrails, as your own sessions do; no permission mode is changed. "
          "Every tool below that acts outside your machine — posting, booking, sending, publishing, deleting — is "
          "removed from every run, the same with and without what is tested.")
HOOKS = ("Your hooks run in every test run, as in your own sessions, with whatever they do (they are part of your "
         "configuration: a run without them wouldn't be your setup).")


@dataclass(frozen=True)
class Parts:
    """One harness's parts for testing, from the composition root: its adapter (a ``VariantHomes`` and a
    ``ConfigReader``), the launcher that runs one situation, the generator that writes situations, and the model the
    generator asks for."""

    adapter: Any
    launcher: Launcher
    generator: Generator
    generator_model: str


Scope = Callable[[Params], tuple[Any, Path]]  # the harness's adapter and the project a request names, checked
Read = Callable[[Any, Path], tuple[Reading, Sequence[Finding]]]  # a project read at your sessions setting, with the
# static checks' findings (what a check proves broken isn't run)


def _fields(body: Mapping[str, Any], *allowed: str) -> None:
    extra = sorted(set(body) - set(allowed))
    if extra:
        raise Refused(400, f"unexpected field{'s' if len(extra) > 1 else ''}: {', '.join(extra)}")


def _target_id(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and _TARGET_ID.fullmatch(value) is not None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def namespaced(refs: Sequence[str]) -> dict[str, list[str]]:
    """Usage references (``namespace/name``) grouped by namespace, each list sorted: what a run used, for a person."""
    grouped: dict[str, list[str]] = {}
    for ref in sorted(refs):
        space, _, item = ref.partition("/")
        grouped.setdefault(space if item else "", []).append(item or space)
    return grouped


class Testing:
    """The UI's tests: what ``ui/server.py`` answers the plan, situations, onboarding, jobs and results with."""

    __test__ = False  # not a pytest class, whatever its name

    def __init__(self, connect: Callable[[], sqlite3.Connection], *, scope: Scope, read: Read,
                 parts: Callable[[str], Parts | None], command: Callable[[str], list[str]],
                 environ: Mapping[str, str], keep_awake: worker.KeepAwake | None = None,
                 stale: Callable[[int, str], bool] = lock.stale, grace_s: float = worker.STOP_GRACE_S,
                 background: Callable[[Callable[[], None]], None] | None = None,
                 read_command: Callable[[str], list[str]] | None = None) -> None:
        self._connect = connect
        self._read_command = read_command  # how a reading's worker is launched; None: this server reads nothing
        self._scope, self._read, self._parts, self._command = scope, read, parts, command
        self.environ = environ
        self.keep_awake = keep_awake or worker.KeepAwake()
        self.stale, self.grace_s = stale, grace_s
        self._background = background or (lambda work: threading.Thread(target=work, daemon=True).start())
        self._lock = threading.Lock()
        self._launched: dict[str, lock.Process] = {}  # workers this server started, until they claim their job
        self._stopping: set[str] = set()  # jobs Stop was sent to
        self._generating: set[tuple[str, str]] = set()  # (project, target id) being generated now
        self._targets: dict[tuple[str, str], tuple[str, AblationTarget, Reading]] = {}  # (project, target id) ->
        # its text hash, the target and the reading it came from: the last reading of that project, for polling
        self._projects: list[str] = []  # the projects whose last reading is kept, the most recent last
        self._orders: dict[str, tuple[Reading, list[AblationTarget]]] = {}  # harness and project (``_where``) -> its
        # last reading and ranked order, the most recent last: what ``tick`` sizes a job from without reading again
        self._names: dict[tuple[str, str], dict[str, str]] = {}  # (harness, the project's real path) -> each target's
        # name by id, as the last reading of that project gives it (GET /api/plan's): what a job's results name targets by
        self._directive_snapshots: dict[str, Any] = {}  # project -> its snapshot as GET /api/directives last read it
        self._read_launched: dict[str, lock.Process] = {}  # reading workers this server started, until they claim
        self._screens: dict[tuple[str, str, int], tuple[dict[str, int], frozenset[str]]] = {}  # (job, when it was made, runs recorded) -> its
        # screens counted by state: runs are only ever added, and a job's plan and fingerprint never change

    # Reading, and what a reading leaves for the requests that poll.

    def _db(self) -> sqlite3.Connection:
        try:
            return self._connect()
        except (StoreError, sqlite3.Error, OSError) as error:
            raise Unreadable(f"the agentlint database: {error}") from error

    def _reading(self, params: Params) -> tuple[Any, Path, Reading, Sequence[Finding]]:
        adapter, project = self._scope(params)
        reading, findings = self._read(adapter, project)
        project_key = str(reading.snapshot.cwd)
        targets = rank_targets(reading)
        defined = reading.snapshot.entities_by_source
        named = {target.id: target_name(target, defined) for target in targets}  # for jobs' results, kept longer
        where = (adapter.name, os.path.realpath(project_key))
        with self._lock:
            self._names.pop(where, None)
            self._names[where] = named
            while len(self._names) > TICKED_PROJECTS:  # small, but not kept for every project ever read
                del self._names[next(iter(self._names))]
            if project_key in self._projects:
                self._projects.remove(project_key)
            self._projects.append(project_key)
            kept = set(self._projects[-KEPT_READINGS:])  # a reading is megabytes: the last few projects' only
            self._projects = self._projects[-KEPT_READINGS:]
            for key in [key for key in self._targets if key[0] == project_key or key[0] not in kept]:
                del self._targets[key]
            for target in targets:
                self._targets[(project_key, target.id)] = (target_hash(target), target, reading)
        return adapter, project, reading, findings

    def _terms(self, harness: str | None) -> Terms | None:
        """The action terms of a job's harness's adapter, which the gate and the verdicts read runs through; None when
        this server has no parts for it."""
        parts = self._parts(harness) if harness else None
        return getattr(parts.adapter, "action_terms", None) if parts is not None else None

    def _parts_for(self, adapter: Any) -> Parts:
        made = self._parts(adapter.name)
        if made is None:
            raise Refused(409, f"agentlint can't run {adapter.label} tests yet")
        return made

    def _nested(self, adapter: Any) -> str | None:
        return adapter.nested(self.environ)

    # The plan.

    def _ranked(self, db: sqlite3.Connection, reading: Reading, findings: Sequence[Finding],
                current: settings.Settings) -> Ranked:
        """The ranker's order with no budget (a test runs what you tick): every testable target, and those skipped."""
        snapshot = reading.snapshot
        return rank(snapshot, findings=findings, usage=usage_store.index(db),
                    statuses=verdict_store.statuses(db, str(snapshot.cwd)), budget=None,
                    phase=Phase.STABILITY, counts=_counts(current))

    def _rank(self, params: Params) -> tuple[Any, Path, Reading, Ranked, settings.Settings, dict[str, str]]:
        """Read the project and rank its targets, keeping the order for the requests that tick (``tick``): the adapter,
        the project, the reading, the ranking, the settings in effect and each stored setting that can't be used."""
        adapter, project, reading, findings = self._reading(params)
        db = self._db()
        try:
            current, problems = settings.read(db)
            ranked = self._ranked(db, reading, findings, current)
        finally:
            db.close()
        where = _where(adapter, project)
        with self._lock:
            self._orders.pop(where, None)
            self._orders[where] = (reading, ordered(ranked))
            while len(self._orders) > KEPT_READINGS:  # a reading is megabytes: the last few projects' only
                del self._orders[next(iter(self._orders))]
        return adapter, project, reading, ranked, current, problems

    def _sized(self, db: sqlite3.Connection, adapter: Any, reading: Reading, order: Sequence[AblationTarget],
               ticked: Sequence[str], current: settings.Settings) -> tuple[Plan, dict[str, Any]]:
        """The job the ticked targets make now, and what the page shows of it (``job_answer``): both steps where a
        variant can remove the target (D9), its consistency runs only where none can yet."""
        project = Path(reading.snapshot.cwd)
        job, given, refused = job_plan(db, order, ticked, current,
                                       lambda target: removal_refused(adapter, target, project))
        names = reading.snapshot.entities_by_source
        blocked = _nothing_to_run(given, names, _model(current, reading)[0])
        return job, job_answer(job, given, current, _seconds_per_run(db), names, blocked, refused)

    def plan(self, params: Params) -> dict[str, Any]:
        """GET /api/plan: every testable target in the ranker's order, each with its situations by review and how many
        are kept; those that can't be tested, with why; the targets you ticked (the top-ranked one when you never ticked
        any); the job they make — N targets × situations per target × runs per situation, the real total when fewer
        are kept, about how long, and why it would run nothing (``job.blocked``, as Start would be refused) — and every
        tool the adapter judged, removed from every run or kept, with why and your override applied (``tools``)."""
        adapter, project, reading, ranked, current, _ = self._rank(params)
        order = ordered(ranked)
        names = reading.snapshot.entities_by_source
        db = self._db()
        try:
            ticked = _ticked(db, _where(adapter, project), order)
            _, job = self._sized(db, adapter, reading, order, ticked, current)
            situations = {target.id: _review_counts(db, target) for target in order}
        finally:
            db.close()
        snapshot = reading.snapshot

        def entry(target: AblationTarget) -> dict[str, Any]:
            counts = situations[target.id]
            return {"id": target.id, "name": target_name(target, names), "tokens": target.tokens,
                    "load": target.source.activation.load.value, "situations": counts,
                    "kept": counts["kept"] + counts["edited"]}

        return {
            "harness": snapshot.harness, "project": str(snapshot.cwd), "phase": ranked.phase.value,
            "counts": {"situations_per_target": current.situations_per_target,
                       "runs_per_situation": current.runs_per_situation},
            "targets": [entry(target) for target in order],
            "skipped": [{"id": item.target.id, "name": target_name(item.target, names), "why": item.why.value,
                         "detail": item.detail} for item in ranked.skipped],
            "ticked": ticked, "job": job,
            "tools": tool_list(adapter.tool_verdicts(reading), current),
        }

    def tick(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/plan/ticked ``{targets, harness?, project?}``: the targets to test, kept for this harness and project
        so a reload keeps them (an empty list too: nothing ticked), and the job they make now, as GET /api/plan's
        ``job`` — from the project's last reading, so it answers at once; ``job`` is None when the server has no reading
        of it (read the plan again). Each id must be a target that reading ranked."""
        _fields(body, "targets", "harness", "project")
        ids = body.get("targets")
        if not isinstance(ids, list) or len(ids) > MOST_TICKED or not all(_target_id(i) for i in ids):
            raise Refused(400, f"targets must be a list of up to {MOST_TICKED} target ids, as GET /api/plan gives them")
        adapter, project = self._scope(_scope_of(body))
        where = _where(adapter, project)
        ids = list(dict.fromkeys(ids))
        with self._lock:
            known = self._orders.get(where)
        if known is not None:
            ranked_ids = {target.id for target in known[1]}
            unknown = [i for i in ids if i not in ranked_ids]
            if unknown:
                raise Refused(400, f"no testable target {', '.join(unknown[:5])} in {project} now")
        db = self._db()
        try:
            _store_ticked(db, where, ids, datetime.now(UTC))
            if known is None:
                return {"ticked": ids, "job": None}
            reading, order = known
            current = settings.read(db)[0]
            ticked = _ticked(db, where, order)
            _, job = self._sized(db, adapter, reading, order, ticked, current)
        finally:
            db.close()
        return {"ticked": ticked, "job": job}

    # Situations: generated in the background, reviewed one by one.

    def generate(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/situations/generate ``{target_ids, harness?, project?, regenerate?}``: write up to your situations
        per target for each target in the background, as ``probes.generate`` does — cached by the target's text unless
        ``regenerate`` (a target written before keeps the situations it has, however many); whatever comes back empty,
        rejected or naming its own target is a diagnostic."""
        _fields(body, "target_ids", "harness", "project", "regenerate")
        ids = body.get("target_ids")
        # As many as can be ticked: 'Write situations' sends every ticked target with none written, in one request.
        if not isinstance(ids, list) or not ids or len(ids) > MOST_TICKED or not all(
                _target_id(i) for i in ids):
            raise Refused(400, f"target_ids must be a list of 1 to {MOST_TICKED} target ids, as GET /api/plan gives them")
        regenerate = body.get("regenerate", False)
        if not isinstance(regenerate, bool):
            raise Refused(400, "regenerate must be true or false")
        adapter, _ = self._scope(_scope_of(body))
        parts = self._parts_for(adapter)
        if (nested := self._nested(adapter)) is not None:
            raise Refused(409, nested)
        _, _, reading, _ = self._reading(_scope_of(body))
        project = str(reading.snapshot.cwd)
        by_id = {target.id: target for target in rank_targets(reading)}
        unknown = [i for i in dict.fromkeys(ids) if i not in by_id]
        if unknown:
            raise Refused(400, f"no target {', '.join(unknown[:5])} in {project} now")
        wanted = [by_id[i] for i in dict.fromkeys(ids)]
        db = self._db()
        try:
            limits = Limits(most=settings.read(db)[0].situations_per_target)
        finally:
            db.close()
        with self._lock:
            busy = [t.id for t in wanted if (project, t.id) in self._generating]
            if busy:
                raise Refused(409, f"already writing situations for {', '.join(busy[:5])}")
            self._generating.update((project, t.id) for t in wanted)
        entities = reading.snapshot.entities

        def work() -> None:
            for target in wanted:
                try:
                    db = self._connect()
                    try:
                        generate(db, target, entities, parts.generator, parts.generator_model,
                                 now=datetime.now(UTC), regenerate=regenerate, limits=limits)
                    except Exception as error:  # noqa: BLE001 - agentlint's own failure: a diagnostic, never a finding
                        diagnostic_store.add(db, Diagnostic(
                            GENERATOR_ERROR, target.id, f"Writing situations failed: {type(error).__name__}: {error}",
                            {"traceback": traceback.format_exc()[-4000:]}), datetime.now(UTC))
                    finally:
                        db.close()
                except Exception:  # noqa: BLE001 - not even the database: the terminal running the UI shows it
                    traceback.print_exc()
                finally:
                    with self._lock:
                        self._generating.discard((project, target.id))

        self._background(work)
        return {"generating": [t.id for t in wanted], "project": project}

    def situations(self, params: Params) -> dict[str, Any]:
        """GET /api/situations?target=&harness=&project=: the target's situations as kept for its text now, each with
        its review and what matters in it, whether some are being written now, and its diagnostics."""
        target_id = params.get("target", "")
        if not _target_id(target_id):
            raise Refused(400, "name the target: ?target=<id>, as GET /api/plan gives it")
        _, project = self._scope(params)
        key = (str(project), target_id)
        with self._lock:
            known = self._targets.get(key)
        if known is None:  # never read here: read it now (the plan, a generation and this share one reading)
            _, _, reading, _ = self._reading(params)
            key = (str(reading.snapshot.cwd), target_id)
            with self._lock:
                known = self._targets.get(key)
            if known is None:
                raise Refused(404, f"no target {target_id} in {reading.snapshot.cwd} now")
        hashed, target, _ = known
        db = self._db()
        try:
            listed = probes.for_hash(db, hashed)
            said = diagnostic_store.listed(db, subject=target_id)[:20]
        finally:
            db.close()
        with self._lock:
            generating = key in self._generating
        return {"target": target_id, "generating": generating, "situations": [situation(p) for p in listed],
                "diagnostics": [{"kind": d.kind, "message": d.message, "created": _iso(d.created)} for d in said]}

    def review(self, probe_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/situations/<id> ``{decision: keep|drop|edit, text?}``: what review made of one situation. An
        edited text must pass the rules a generated one passes (else 400); whether it names its target is said, from
        the contamination guard, when the server has read that target."""
        if not _PROBE_ID.fullmatch(probe_id):
            raise Refused(404, "no such situation")
        decision = body.get("decision")
        if decision not in _DECISIONS:
            raise Refused(400, "decision must be keep, drop or edit")
        _fields(body, "decision", "text") if decision == "edit" else _fields(body, "decision")
        text = body.get("text") if decision == "edit" else None
        if decision == "edit" and (not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX):
            raise Refused(400, f"an edit needs its text: 1 to {TEXT_MAX:,} characters")
        db = self._db()
        try:
            try:
                done = review(db, probe_id, _DECISIONS[decision], datetime.now(UTC),
                              text.strip() if isinstance(text, str) else None)
            except EditRefused as error:
                raise Refused(400, f"this text would be rejected if the generator wrote it: {error}") from error
            except NoSuchProbe as error:
                raise Refused(404, str(error)) from error
        finally:
            db.close()
        answer = situation(done)
        if done.edited_text is not None:
            with self._lock:
                known = next((v for (_, tid), v in self._targets.items() if tid == done.target_id), None)
            if known is not None:
                _, target, reading = known
                hits = guard_for(target, reading.snapshot.entities).hits(done.edited_text)
                answer["names_its_target"] = [hit.text for hit in hits]
        return answer

    # Onboarding: what the one-time screen shows, and its single yes.

    def _accepted(self, db: sqlite3.Connection) -> bool:
        found = state_store.get(db, state_store.ONBOARDED)
        return bool(found and isinstance(found[0], dict) and found[0].get("accepted") is True)

    def _running(self, db: sqlite3.Connection) -> list[str]:
        """The jobs a live process runs now: a lock a live runner holds, or a worker this server started that hasn't
        claimed its job yet."""
        held = {job.job_id for job in jobs.held(db, self.stale)}
        with self._lock:
            launched = dict(self._launched)
        for job_id, process in launched.items():
            if not self.stale(process.pid, process.started):
                held.add(job_id)
            else:
                with self._lock:
                    self._launched.pop(job_id, None)
        return sorted(held)

    def _blocked(self, db: sqlite3.Connection, adapter: Any | None) -> list[str]:
        """Why Start is disabled now, each reason once, in words to act on; [] when it isn't."""
        reasons = []
        if adapter is not None:
            if self._parts(adapter.name) is None:
                reasons.append(f"agentlint can't run {adapter.label} tests yet")
            elif (nested := self._nested(adapter)) is not None:
                reasons.append(nested)
        running = self._running(db)
        if running:
            reasons.append(f"a test is running ({', '.join(running)}); stop it, or wait for it, first")
        return reasons

    def onboarding(self, params: Params) -> dict[str, Any]:
        """GET /api/onboarding: what the one-time screen shows (D18) — how many runs Start would make of the targets
        you ticked (as GET /api/plan's ``job``) and about how long, the tools removed from every run, that hooks run, the
        harness telemetry line, the keep-awake line, and each test setting with its default — and whether it was
        accepted."""
        adapter, project, reading, ranked, current, problems = self._rank(params)
        order = ordered(ranked)
        db = self._db()
        try:
            ticked = _ticked(db, _where(adapter, project), order)
            _, job = self._sized(db, adapter, reading, order, ticked, current)
            accepted = self._accepted(db)
            blocked = self._blocked(db, adapter)
        finally:
            db.close()
        model, model_from = _model(current, reading)
        tools = tool_list(adapter.tool_verdicts(reading), current)
        described = [e for e in settings.describe(current, problems, []) if e["group"] == "tests"]
        for entry in described:
            if entry["key"] == "model":
                entry["default"] = execute.default_model(reading)  # the model your sessions use, as read now
                entry["value"] = model
        return {
            "accepted": accepted, "blocked": blocked + job["blocked"],
            "runs": job["runs"], "situations": job["situations"], "seconds": job["seconds"],
            "seconds_per_run": job["seconds_per_run"], "seconds_from": job["seconds_from"], "job": job,
            "model": {"value": model, "from": model_from},
            "removed_tools": tools, "policy": POLICY, "hooks": HOOKS, "telemetry": adapter.telemetry(),
            "keep_awake": self.keep_awake.line(), "settings": described,
        }

    def accept(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/onboarding ``{accepted: true}``: the one-time onboarding was accepted; never asked again."""
        _fields(body, "accepted")
        if body.get("accepted") is not True:
            raise Refused(400, "accepted must be true: close the screen to decide later")
        db = self._db()
        try:
            state_store.claim(db, state_store.ONBOARDED, {"accepted": True, "by": "ui"}, datetime.now(UTC))
        finally:
            db.close()
        return {"accepted": True}

    # Jobs.

    def _start(self, db: sqlite3.Connection, job_id: str) -> None:
        """Launch the job's worker in its own process group, and keep the Mac awake while it runs."""
        process = worker.launch(job_id, self._command(job_id), environ=self.environ)
        worker.reap(process)
        started = lock.process_start(process.pid)
        if started is not None:
            with self._lock:
                self._launched[job_id] = lock.Process(process.pid, started)
                self._stopping.discard(job_id)
            self.keep_awake.hold(process.pid)

    def create_job(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/jobs ``{targets, harness?, project?}``: freeze a job of the targets given (the ticked ones), in the
        ranked order, from the situations KEPT only (D12), at most your situations per target of each, each run
        your runs per situation; its run ceiling is that total — refused before the onboarding is accepted, inside an
        agent session, while another job's runner is alive, with no model, no target, or a target with nothing kept —
        then launch its worker."""
        _fields(body, "targets", "harness", "project")
        ids = body.get("targets")
        if not isinstance(ids, list) or len(ids) > MOST_TICKED or not all(_target_id(i) for i in ids):
            raise Refused(400, f"targets must be a list of up to {MOST_TICKED} target ids, as GET /api/plan gives them: "
                               "the targets to test")
        adapter, _ = self._scope(_scope_of(body))
        db = self._db()
        try:
            if not self._accepted(db):
                raise Refused(409, "accept the test onboarding first: it says what a test runs, and for how long")
            blocked = self._blocked(db, adapter)
        finally:
            db.close()
        if blocked:
            raise Refused(409, blocked[0])
        parts = self._parts_for(adapter)
        _, project, reading, ranked, current, problems = self._rank(_scope_of(body))
        if problems:
            key, why = next(iter(problems.items()))
            raise Refused(409, f"{why}; change {key} in Settings before starting a test")
        order = ordered(ranked)
        ranked_ids = {target.id for target in order}
        unknown = [i for i in dict.fromkeys(ids) if i not in ranked_ids]
        if unknown:
            raise Refused(400, f"no testable target {', '.join(unknown[:5])} in {project} now")
        db = self._db()
        try:
            job, sized = self._sized(db, adapter, reading, order, ids, current)
            if sized["blocked"]:
                raise Refused(409, sized["blocked"][0])
            model, _ = _model(current, reading)
            assert model is not None
            removed = execute.removed_tools(adapter.tool_verdicts(reading), keep=current.keep_tools,
                                            remove=current.remove_tools)
            if self._running(db):  # again, now that the reading is done: one job at a time
                raise Refused(409, "a test is running; stop it, or wait for it, first")
            try:
                job_id = execute.create(db, parts.adapter, project=project, model=model, plan=job,
                                        run_ceiling=job.size, timeout_s=current.run_timeout_s,  # the total: the ceiling
                                        removed_tools=removed, harness=adapter.name)
            except ValueError as error:
                raise Refused(409, str(error)) from error
            self._start(db, job_id)
            made = jobs.load(db, job_id)
            assert made is not None
            return {"job": self._entry(db, made)}
        finally:
            db.close()

    def jobs(self, params: Params) -> dict[str, Any]:
        """GET /api/jobs: every job of every project, the most recent first — where it stands in words, its targets by
        name, situations, runs recorded against planned, its screens counted by state (Tests › Results), launches that
        recorded nothing, comparisons per progress state, last progress, whether its runner is alive and whether the Mac
        is kept awake for it — the project the request names (its real path, as a job records its project; the page
        keeps that project's jobs for Tests › Results), and why Start is disabled now, if it is. A live worker the Mac
        isn't kept awake for (this server started after it) is held now."""
        adapter, project = None, None
        if params.get("harness") or params.get("project"):
            adapter, project = self._scope(params)
        else:  # the default scope's harness, so Start shows disabled inside a session whatever the page asks
            try:
                adapter, project = self._scope({})
            except (Refused, ScopeError, Unreadable):
                adapter = None
        db = self._db()
        try:
            every = jobs.listed(db)
            listed = [self._entry(db, job, hold=True) for job in every]
            accepted = self._accepted(db)
            blocked = self._blocked(db, adapter)
        finally:
            db.close()
        kept = {job.job_id for job in every}
        with self._lock:  # a job forgotten or pruned takes its screens with it
            for gone in [key for key in self._screens if key[0] not in kept]:
                del self._screens[gone]
        return {"jobs": listed, "project": os.path.realpath(project) if project is not None else None,
                "accepted": accepted, "blocked": blocked, "keep_awake": self.keep_awake.line()}

    def _named(self, harness: str | None, project: str) -> dict[str, str]:
        """Each target's name by id as the server's last reading of this project gives it (GET /api/plan's names); {}
        when it has read none since it started. A job made before jobs named their harness takes any harness's."""
        real = os.path.realpath(project)
        with self._lock:
            found = [named for (name, where), named in self._names.items()
                     if where == real and harness in (None, name)]
        return found[-1] if found else {}

    def _screened_job(self, db: sqlite3.Connection, job: jobs.Job, plan: Plan,
                      recorded: int) -> tuple[dict[str, int], frozenset[str]]:
        """How many of the job's situations stand at each screen state, as its results screen them, and which of its
        with/without comparisons have their gate closed (D9: they spend no runs, so the planned runs leave them out):
        kept per job and number of runs recorded, so a job that took no run since is never screened again."""
        key = (job.job_id, _iso(job.created) or "", recorded)
        with self._lock:
            known = self._screens.get(key)
        if known is not None:
            return dict(known[0]), known[1]
        rows = jobs.runs(db, job.job_id)  # the traces: read only when the job recorded a run since
        counts = screen_counts(plan, rows, job.fingerprint)
        shut = gate.closed(plan, by_comparison(rows), job.fingerprint, self._terms(job.harness))
        with self._lock:
            for old in [k for k in self._screens if k[0] == job.job_id]:
                del self._screens[old]
            self._screens[(job.job_id, key[1], len(rows))] = (counts, shut)
        return dict(counts), shut

    def _entry(self, db: sqlite3.Connection, job: jobs.Job, *, hold: bool = False) -> dict[str, Any]:
        plan = Plan.from_json(job.plan)
        recorded = jobs.recorded_keys(db, job.job_id)
        screens, shut = self._screened_job(db, job, plan, len(recorded))
        found = progress(plan, recorded, job.launches, shut)
        with self._lock:
            launched = self._launched.get(job.job_id)
        runner = worker.runner_of(db, job.job_id, launched, self.stale)
        if runner is None:
            with self._lock:
                self._stopping.discard(job.job_id)
        elif hold:
            self.keep_awake.hold(runner.pid)
        with self._lock:
            stopping = job.job_id in self._stopping
        named = self._named(job.harness, job.project)
        about = dict.fromkeys(c.about for c in plan.comparisons if c.about is not None)  # in the order they run
        return {
            "job_id": job.job_id, "project": job.project, "harness": job.harness, "status": job.status.value,
            "standing": standing(job.status, runner is not None, stopping and runner is not None),
            "targets": [{"id": target, "name": named.get(target, target)} for target in about],
            "situations": len({c.situation for c in plan.comparisons}),
            "screens": screens,
            "created": _iso(job.created), "last_progress": _iso(job.last_progress), "model": job.model,
            "harness_version": job.harness_version, "run_ceiling": job.run_ceiling, "timeout_s": job.timeout_s,
            "runs": {"recorded": found.runs_recorded, "planned": found.runs_planned},
            "launched_without_a_run": found.launched_without_a_run,
            "comparisons": {state.value: n for state, n in found.comparisons},
            "in_flight": job.running[0] if job.running else None, "running": list(job.running),
            "runner": {"alive": runner is not None, "pid": runner.pid if runner else None,
                       "stopping": stopping and runner is not None},
            "keep_awake": runner is not None and self.keep_awake.available and self.keep_awake.holding(runner.pid),
            "removed_tools": list(plan.removed_tools),
            "log": str(worker.log_path(job.job_id)), "log_tail": _tail(worker.log_path(job.job_id)),
        }

    # Directives (12-directives.md): the sources you can have read, and readings of them as jobs.

    def directives(self, params: Params) -> dict[str, Any]:
        """GET /api/directives: every source that can be read for directives, with how much of it is read — sections
        whose fingerprint is stored, of all — and what the rest hold in tokens; and the project's reading still open,
        if one is (``reading``, as GET /api/reads lists it). Keeps the reading of the project, which a reading's plan is
        made from at once."""
        _, _, reading, _ = self._reading(params)
        project = str(reading.snapshot.cwd)
        with self._lock:
            self._directive_snapshots[project] = reading.snapshot
        db = self._db()
        try:
            found = extract.statuses(db, reading.snapshot)
            opened = next((r for r in reads.listed(db, project) if r.status is reads.Status.OPEN), None)
            entry = self._read_entry(db, opened) if opened is not None else None
        finally:
            db.close()
        return {"project": project, "version": READER_VERSION, "sources": [_directive_source(s) for s in found],
                "reading": entry}

    def _read_runner(self, read: reads.Read) -> lock.Process | None:
        if read.runner_pid is not None and not self.stale(read.runner_pid, read.runner_started or ""):
            return lock.Process(read.runner_pid, read.runner_started or "")
        with self._lock:
            launched = self._read_launched.get(read.read_id)
        if launched is not None and not self.stale(launched.pid, launched.started):
            return launched
        return None

    def _read_entry(self, db: sqlite3.Connection, read: reads.Read) -> dict[str, Any]:
        found = reads.counts(db, read.read_id)
        planned = sum(found.values())
        runner = self._read_runner(read)
        if runner is not None:
            standing = "running"
        elif read.status is reads.Status.COMPLETE:
            standing = "complete" + (f" · {found['failed']} failed" if found["failed"] else "")
        else:
            standing = "stopped: Resume reads the rest"
        try:
            tail = _tail(worker.log_path(read.read_id))
        except ValueError:
            tail = []
        return {"read_id": read.read_id, "kind": "reading", "project": read.project, "harness": read.harness,
                "status": read.status.value, "standing": standing, "created": _iso(read.created),
                "finished": _iso(read.finished), "sources": read.sources,
                "sections": {"planned": planned, **found}, "runner": {"alive": runner is not None}, "log_tail": tail}

    def read_directives(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/directives/read ``{source_ids, harness?, project?}``: plan a reading of these sources — their
        sections not read yet — from the project as GET /api/directives last read it, and launch its worker at once.
        ``read`` None when everything selected is read already. Refused while one of the project's readings runs."""
        _fields(body, "source_ids", "harness", "project")
        ids = body.get("source_ids")
        if not isinstance(ids, list) or not ids or len(ids) > MOST_SOURCES or not all(_target_id(i) for i in ids):
            raise Refused(400, f"source_ids must be a list of 1 to {MOST_SOURCES} source ids, as GET /api/directives "
                               "gives them")
        adapter, project_path = self._scope(_scope_of(body))
        parts = self._parts_for(adapter)
        if (nested := self._nested(adapter)) is not None:
            raise Refused(409, nested)
        if self._read_command is None:
            raise Refused(404, "this server reads no instructions")
        with self._lock:
            snapshot = next((v for k, v in self._directive_snapshots.items()
                             if os.path.realpath(k) == os.path.realpath(str(project_path))), None)
        if snapshot is None:
            snapshot = self._reading(_scope_of(body))[2].snapshot
        project = str(snapshot.cwd)
        known = {s.id for s in extract.readable(snapshot)}
        unknown = [i for i in dict.fromkeys(ids) if i not in known]
        if unknown:
            raise Refused(400, f"no source {', '.join(unknown[:5])} to read in {project} now")
        db = self._db()
        try:
            running = [r for r in reads.listed(db, project) if r.status is reads.Status.OPEN and self._read_runner(r)]
            if running:
                raise Refused(409, f"reading {running[0].read_id} is reading this project's instructions now: stop it "
                                   "first, or wait")
            now = datetime.now(UTC)
            read_id = engine.new_job_id(now)
            planned = extract.plan_read(db, snapshot, list(dict.fromkeys(ids)), read_id=read_id, harness=adapter.name,
                                        model=parts.generator_model, now=now)
            if not planned:
                return {"read": None, "project": project}
            self._launch_read(read_id)
            return {"read": self._read_entry(db, reads.load(db, read_id)), "project": project}  # type: ignore[arg-type]
        finally:
            db.close()

    def _launch_read(self, read_id: str) -> None:
        assert self._read_command is not None
        process = worker.launch(read_id, self._read_command(read_id), environ=self.environ)
        worker.reap(process)
        started = lock.process_start(process.pid)
        if started is not None:
            with self._lock:
                self._read_launched[read_id] = lock.Process(process.pid, started)
            self.keep_awake.hold(process.pid)

    def reads(self, params: Params) -> dict[str, Any]:
        """GET /api/reads: every reading of instructions, the most recent first, as ``_read_entry`` gives it."""
        db = self._db()
        try:
            return {"reads": [self._read_entry(db, r) for r in reads.listed(db)]}
        finally:
            db.close()

    def read_detail(self, read_id: str) -> dict[str, Any]:
        """GET /api/reads/<id>: a reading and each of its sections — its source, line, where it stands, how many
        directives it gave, or why it failed — in the order they are read."""
        db = self._db()
        try:
            read = self._reading_job(db, read_id)
            return {**self._read_entry(db, read), "sections_list": [
                {"source_id": s.planned.source_id, "source": s.planned.source_name, "line": s.planned.line,
                 "loads": s.planned.loads, "status": s.status.value, "directives": s.directives, "message": s.message}
                for s in reads.sections(db, read_id)]}
        finally:
            db.close()

    def _reading_job(self, db: sqlite3.Connection, read_id: str) -> reads.Read:
        found = reads.load(db, read_id) if _JOB_ID_SHAPE.fullmatch(read_id) else None
        if found is None:
            raise Refused(404, f"no reading {read_id}")
        return found

    def stop_read(self, read_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/reads/<id>/stop: Ctrl-C to its worker's process group; the sections under way finish."""
        _fields(body)
        db = self._db()
        try:
            read = self._reading_job(db, read_id)
            runner = self._read_runner(read)
            if runner is None:
                raise Refused(409, f"reading {read_id} isn't running")
            try:
                worker.stop(runner, grace_s=self.grace_s)
            except worker.WorkerRefused as error:
                raise Refused(409, str(error)) from error
            return {"read": self._read_entry(db, read)}
        finally:
            db.close()

    def resume_read(self, read_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/reads/<id>/resume: launch its worker again, for its sections still pending or failed."""
        _fields(body)
        db = self._db()
        try:
            read = self._reading_job(db, read_id)
            if self._read_runner(read) is not None:
                raise Refused(409, f"reading {read_id} is running")
            found = reads.counts(db, read_id)
            if not found["pending"] and not found["failed"] and not found["reading"]:
                raise Refused(409, f"reading {read_id} has nothing left to read")
            if self._read_command is None:
                raise Refused(404, "this server reads no instructions")
            self._launch_read(read_id)
            return {"read": self._read_entry(db, read)}
        finally:
            db.close()

    def _job(self, db: sqlite3.Connection, job_id: str) -> jobs.Job:
        if not worker._JOB_ID.fullmatch(job_id):
            raise Refused(404, "no such job")
        job = jobs.load(db, job_id)
        if job is None:
            raise Refused(404, f"no job {job_id}: forgotten, or pruned")
        return job

    def stop(self, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/jobs/<id>/stop: Ctrl-C to the worker's process group — verified as the job's runner by pid, start
        time and process group — then a kill after a grace period, and the harness run a kill orphaned stopped."""
        _fields(body)
        db = self._db()
        try:
            job = self._job(db, job_id)
            with self._lock:
                launched = self._launched.get(job_id)
            runner = worker.runner_of(db, job_id, launched, self.stale)
            if runner is None:
                raise Refused(409, f"job {job_id} isn't running")
            try:
                worker.stop(runner, grace_s=self.grace_s,
                            after_kill=lambda: execute.stop_left_running(execute.runs_root() / job_id))
            except worker.WorkerRefused as error:
                raise Refused(409, str(error)) from error
            with self._lock:
                self._stopping.add(job_id)
            return {"job": self._entry(db, job)}
        finally:
            db.close()

    def resume(self, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/jobs/<id>/resume: launch the job's worker again, to run what its frozen plan has left."""
        _fields(body)
        db = self._db()
        try:
            job = self._job(db, job_id)
            if job.status is not JobStatus.OPEN:
                raise Refused(409, f"job {job_id} is {job.status.value}: nothing more runs in it")
            if job.harness is None:
                raise Refused(409, f"job {job_id} was made before jobs named their harness; start a new test")
            made = self._parts(job.harness)
            if made is None:
                raise Refused(409, f"agentlint can't run {job.harness} tests")
            blocked = self._blocked(db, made.adapter)
            if blocked:
                raise Refused(409, blocked[0])
            self._start(db, job_id)
            return {"job": self._entry(db, job)}
        finally:
            db.close()

    def salvage(self, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/jobs/<id>/salvage: close a job that can't (or needn't) go on, keeping what completed, and delete
        what its runs left. Refused while a live process runs it: a runner holding its lock, or a worker this server
        launched that hasn't claimed it yet (stop it first)."""
        _fields(body)
        db = self._db()
        try:
            self._job(db, job_id)
            with self._lock:
                launched = self._launched.get(job_id)
            if worker.runner_of(db, job_id, launched, self.stale) is not None:
                raise Refused(409, f"job {job_id} is running: stop it before salvaging it")
            try:
                found, undeleted = execute.salvage(db, job_id, stale=self.stale)
            except engine.JobClosed as error:
                raise Refused(409, str(error)) from error
            except jobs.JobLocked as error:
                raise Refused(409, f"{error}") from error
            job = jobs.load(db, job_id)
            assert job is not None
            return {"job": self._entry(db, job), "undeleted": list(undeleted),
                    "comparisons": [{"comparison": s.comparison, "state": s.state.value} for s in found]}
        finally:
            db.close()

    def results(self, job_id: str) -> dict[str, Any]:
        """GET /api/jobs/<id>/results: each situation's stability screen, with the target it was written for by name
        (the server's last reading of the project, else its id), what matters in it (the situation's tag in the store,
        None once it is regenerated or forgotten), each run with its actions, and its with/without comparison's verdict
        (``effect``, D9); each target's verdict across its situations (``targets``); the job's runs recorded and
        planned, both steps, a gated-off comparison's left out (as GET /api/jobs counts them); and the run under way
        (``in_flight``; every run under way, ``running``, several with runs_at_once), so the page can say what is
        running now."""
        db = self._db()
        try:
            job = self._job(db, job_id)
            plan = Plan.from_json(job.plan)
            rows = jobs.runs(db, job_id)
            matters = {c.situation: _matters_of(db, c.situation) for c in plan.comparisons}
        finally:
            db.close()
        names = self._named(job.harness, job.project)
        terms = self._terms(job.harness)
        situations = situation_results(plan, rows, job.fingerprint, names=names, matters=matters, terms=terms)
        shut = gate.closed(plan, by_comparison(rows), job.fingerprint, terms)
        found = progress(plan, {row.run_key for row in rows}, job.launches, shut)
        return {"job_id": job_id, "project": job.project, "created": _iso(job.created), "status": job.status.value,
                "fingerprint": job.fingerprint, "situations": situations,
                "targets": target_results(plan, situations, names),
                "runs": {"recorded": found.runs_recorded, "planned": found.runs_planned},  # both steps, the skipped out
                "in_flight": job.running[0] if job.running else None, "running": list(job.running)}


def _scope_of(body: Mapping[str, Any]) -> Params:
    """A write's scope, as a request's query names it: only strings are taken."""
    scope = {key: body[key] for key in ("harness", "project") if key in body}
    if not all(isinstance(value, str) and value for value in scope.values()):
        raise Refused(400, "harness and project, when given, are the names GET /api/scope lists")
    return scope


def _directive_source(status: extract.SourceStatus) -> dict[str, Any]:
    source = status.source
    state = ("read" if status.read == status.sections else "stale" if status.stale == status.sections - status.read
             and status.read == 0 else "not read" if status.read == 0 else "partly read")
    return {"id": source.id, "name": status.name, "load": source.activation.load.value,
            "authority": source.provenance.authority.value, "package": source.provenance.package,
            "sections": status.sections, "read": status.read, "stale": status.stale, "tokens": status.tokens,
            "state": state}


def rank_targets(reading: Reading) -> list[AblationTarget]:
    from agentlint.rank.plan import targets

    return targets(reading.snapshot)


def situation(probe: Probe) -> dict[str, Any]:
    """One situation as review shows it: its text (the edit, when edited), what matters in it and what that means,
    its review in review's words, where it came from, and when a person reviewed it (``reviewed``: None for one kept
    as written, which nobody reviewed)."""
    return {"id": probe.probe_id, "target": probe.target_id, "text": probe.text, "generated": probe.situation,
            "edited": probe.edited_text is not None, **_matters_fields(probe.matters),
            "review": _SAID[probe.review], "model": probe.model, "created": _iso(probe.created),
            "reviewed": _iso(probe.reviewed)}


def _review_counts(db: sqlite3.Connection, target: AblationTarget) -> dict[str, int]:
    counts = {said: 0 for said in _SAID.values()}
    for probe in probes.for_hash(db, target_hash(target)):
        counts[_SAID[probe.review]] += 1
    return counts


def _where(adapter: Any, project: Path) -> str:
    """The harness and project a request names, as one key: what the ticked targets and a kept ranking are kept by."""
    return f"{adapter.name} {project}"


def _counts(current: settings.Settings) -> RunCounts:
    return RunCounts(situations=current.situations_per_target, stability=current.runs_per_situation)


def ordered(ranked: Ranked) -> list[AblationTarget]:
    """Every testable target, in the ranker's order (with no budget nothing is deferred; deferred ones follow)."""
    return [item.target for item in (*ranked.planned, *ranked.deferred)]


def _ticked(db: sqlite3.Connection, where: str, order: Sequence[AblationTarget]) -> list[str]:
    """The targets you ticked for this harness and project, in the ranked order — one no longer ranked left out — or,
    when you never ticked any here, the top-ranked one."""
    found = state_store.get(db, state_store.TICKED)
    held = found[0] if found and isinstance(found[0], dict) else {}
    stored = held.get(where)
    if not isinstance(stored, list):
        return [order[0].id] if order else []
    wanted = {i for i in stored if isinstance(i, str)}
    return [target.id for target in order if target.id in wanted]


def _store_ticked(db: sqlite3.Connection, where: str, ids: Sequence[str], now: datetime) -> None:
    """Keep the ticked targets of this harness and project, the most recently ticked last; the oldest projects' go past
    TICKED_PROJECTS."""
    with transaction(db):
        found = state_store.get(db, state_store.TICKED)
        held = dict(found[0]) if found and isinstance(found[0], dict) else {}
        held.pop(where, None)
        held[where] = list(ids)
        while len(held) > TICKED_PROJECTS:
            del held[next(iter(held))]
        state_store.put(db, state_store.TICKED, held, now)


def job_plan(db: sqlite3.Connection, order: Sequence[AblationTarget], ticked: Collection[str],
             current: settings.Settings, refused: Callable[[AblationTarget], str | None] = lambda target: None,
             ) -> tuple[Plan, list[tuple[AblationTarget, int]], dict[str, str]]:
    """The job Start makes (D12) of the ticked targets, in the ranked order, both steps target by target (D9): of each,
    the first ``situations_per_target`` situations kept for its text (kept or edited, in the order they were written),
    each a stability comparison of ``runs_per_situation`` runs, then — when a variant can remove the target
    (``refused`` says why not) — each one's with/without comparison of ``runs_per_situation`` a side, gated on its stability
    comparison. Also each ticked target with how many situations it gives (fewer than ``situations_per_target`` when
    fewer are kept, 0 when none is), and why each ticked target that can't be removed is tested for consistency only."""
    wanted, most = set(ticked), current.situations_per_target
    planned: list[Planned] = []
    given: list[tuple[AblationTarget, int]] = []
    why_not: dict[str, str] = {}
    for target in order:
        if target.id not in wanted:
            continue
        kept = [p for p in probes.for_hash(db, target_hash(target)) if p.review in KEPT][:most]
        given.append((target, len(kept)))
        reason = refused(target)
        if reason is not None:
            why_not[target.id] = reason
        planned.append(Planned(target.id, tuple((p.probe_id, p.text, _matters_planned(p.matters)) for p in kept),
                               reason is None))
    return (two_step_plan(planned, runs=current.runs_per_situation, runs_per_side=current.runs_per_situation), given,
            why_not)


def job_answer(job: Plan, given: Sequence[tuple[AblationTarget, int]], current: settings.Settings,
               seconds: tuple[int, bool], names: Mapping[str, Any], blocked: list[str],
               refused: Mapping[str, str] | None = None) -> dict[str, Any]:
    """What the page shows of a job: its targets (ids, in order), situations and runs — ``runs`` is the most Start can
    run, every gate open, and its ceiling; ``most`` is what the ticked targets would make with every situation kept,
    more than ``runs`` only when a target has fewer kept (``short``) — the runs per side of the with/without step, the
    targets tested for consistency only and why (``consistency_only``: no variant can remove them yet), about how long
    at most — the runs in rounds of ``runs_at_once`` — from what, and why it would run nothing (``blocked``)."""
    per_target, per = current.situations_per_target, current.runs_per_situation
    side = per  # one run count for every step (the user, 2026-10-07)
    each, measured = seconds
    refused = refused or {}
    per_situation = {target.id: per + (0 if target.id in refused else side) for target, _ in given}  # with, then without
    return {
        "targets": [target.id for target, _ in given],
        "situations": sum(c.phase is Phase.STABILITY for c in job.comparisons), "runs": job.size,
        "most": sum(per_target * per_situation[target.id] for target, _ in given), "situations_per_target": per_target,
        "runs_per_situation": per,
        "short": [{"id": target.id, "name": target_name(target, names), "kept": n}
                  for target, n in given if n < per_target],
        "consistency_only": [{"id": target.id, "name": target_name(target, names), "why": refused[target.id]}
                             for target, _ in given if target.id in refused],
        "seconds": -(-job.size // current.runs_at_once) * each, "seconds_per_run": each,
        "runs_at_once": current.runs_at_once,
        "seconds_from": "this machine's recorded runs" if measured else "E1's 53 s run",
        "blocked": blocked,
    }


def _nothing_to_run(given: Sequence[tuple[AblationTarget, int]], names: Mapping[str, Any],
                    model: str | None) -> list[str]:
    """Why Start is refused for these ticked targets, in words to act on: none ticked, a ticked target with nothing
    kept (each named), or no model."""
    said = []
    if not given:
        said.append("no target is ticked: tick the targets to test in the plan")
    for target, n in given:
        if n == 0:
            said.append(f"no situation is kept for {target_name(target, names)}: write situations for it and keep "
                        "some, or untick it")
    if model is None:
        said.append("agentlint can't tell which model your sessions use: set model in Settings")
    return said


def _model(current: settings.Settings, reading: Reading) -> tuple[str | None, str | None]:
    """The model a new job asks for, and where it comes from: your model setting, else your sessions'."""
    if current.model:
        return current.model, "setting"
    found = execute.default_model(reading)
    return found, "sessions" if found else None


def tool_list(verdicts: Sequence[Any], current: settings.Settings) -> list[dict[str, Any]]:
    """Every tool the adapter judged, with your override applied (``keep_tools``, ``remove_tools``): removed or kept,
    and why. The removed ones are what a new job removes (``execute.removed_tools``)."""
    keep, remove = set(current.keep_tools), set(current.remove_tools)
    listed = []
    for verdict in verdicts:
        if verdict.removed and verdict.name in keep:
            listed.append({"name": verdict.name, "removed": False, "why": f"kept by your keep_tools ({verdict.why})"})
        elif not verdict.removed and verdict.name in remove:
            listed.append({"name": verdict.name, "removed": True, "why": f"removed by your remove_tools ({verdict.why})"})
        else:
            listed.append({"name": verdict.name, "removed": verdict.removed, "why": verdict.why})
    judged = {v.name for v in verdicts}
    listed += [{"name": name, "removed": True, "why": "removed by your remove_tools"}
               for name in current.remove_tools if name not in judged]
    return listed


def _seconds_per_run(db: sqlite3.Connection) -> tuple[int, bool]:
    """About how long one run takes here: the mean of this machine's recorded runs once there are 3, else E1's."""
    row = db.execute("SELECT count(*) AS n, avg(duration_ms) AS mean FROM runs").fetchone()
    if row["n"] >= 3 and row["mean"]:
        return max(1, round(row["mean"] / 1000)), True
    return E1_RUN_S, False


def _tail(path: Path, lines: int = 20) -> list[str]:
    try:
        with open(path, "rb") as log:
            log.seek(0, 2)
            size = log.tell()
            log.seek(max(0, size - 16_384))
            return log.read().decode("utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def screen_counts(plan: Plan, rows: Sequence[jobs.Run], fingerprint: str) -> dict[str, int]:
    """How many of the plan's stability comparisons stand at each screen state, in ``SCREENS``' order, every state
    named: what Tests › Results lists for each job. The same screens as ``situation_results``."""
    counts = {state.value: 0 for state in SCREENS}
    grouped = by_comparison(rows)
    for comparison in plan.comparisons:
        if comparison.phase is Phase.STABILITY:
            counts[screened(grouped.get(comparison.id, ()), fingerprint).screen.state.value] += 1
    return counts


def situation_results(plan: Plan, rows: Sequence[jobs.Run], fingerprint: str, *, names: Mapping[str, str] | None = None,
                      matters: Mapping[str, str | None] | None = None, terms: Terms | None = None) -> list[dict[str, Any]]:
    """Each situation of a job — each stability comparison — with its screen (07-analysis.md): the runs recorded for
    it, screened with D10's rule for "inconsistent" (``used``: the set of tools, skills and agents a run used), each run
    with its outcome, how long it took and its actions (``run_entry``). A run whose trace couldn't be read (a timeout
    before the harness wrote anything) is listed apart (``unscreened``) and screened with nothing — never as a run that
    did nothing. Then, when the plan gives the situation a with/without comparison (D9), its ``effect``
    (``effect_entry``): None when it has none. ``names``: each target's name by id (its id when absent); ``matters``:
    each situation's what-matters tag by its id; ``terms``: the adapter's action terms (None: no verdict can be read)."""
    names, matters = names or {}, matters or {}
    grouped = by_comparison(rows)
    answers = []
    recorded = {row.run_key for row in rows}
    for comparison in plan.comparisons:
        if comparison.phase is not Phase.STABILITY:
            continue  # a with/without comparison is its situation's effect, below
        status = comparison_status(comparison, recorded)
        tag = matters.get(comparison.situation)
        found = screened(grouped.get(comparison.id, ()), fingerprint)
        outcomes = sorted([(index, row.outcome) for index, row, _ in found.read]
                          + [(index, row.outcome) for index, row in found.unread])
        paired = plan.gated_by(comparison.id)
        what = _planned_matters(comparison) if terms is not None else None
        answers.append({
            "comparison": comparison.id, "situation": comparison.situation, "about": comparison.about,
            "name": None if comparison.about is None else names.get(comparison.about, comparison.about),
            "text": comparison.text, **_matters_fields(tag),
            "planned": comparison.runs, "recorded": dict(status.recorded), "progress": status.state.value,
            "state": found.screen.state.value,
            "runs": [{**run_entry(index, row, trace), **({"met": ran(what, trace, terms)} if what and terms else {})}
                     for index, row, trace in found.read],
            "unscreened": [{"run": index, "outcome": row.outcome, "seconds": _seconds(row)} for index, row in found.unread],
            "outcomes": [{"run": index, "outcome": outcome} for index, outcome in outcomes],
            "modal_share": found.screen.modal_share,
            "branches": [{"runs": list(b.runs), "count": b.count, "of": found.screen.runs, "used": namespaced(b.steps)}
                         for b in found.screen.branches],
            "differing": list(found.screen.differing),
            "environments": [{"harness_version": e.harness_version, "models": list(e.models)}
                             for e in found.screen.environments],
            "effect": None if paired is None else effect_entry(plan, paired, grouped, recorded, fingerprint, terms),
        })
    return answers


def effect_entry(plan: Plan, paired: Comparison, grouped: Mapping[str, Sequence[jobs.Run]], recorded: Collection[str],
                 fingerprint: str, terms: Terms | None) -> dict[str, Any]:
    """A situation's with/without comparison, for its result: how far it got (``planned`` a side, ``recorded`` per
    side, ``progress``) and ``state`` — "waiting" until its gate decides (its situation's stability runs aren't all
    recorded), "skipped" when the gate closed (``why``: the situation's screen state when its runs didn't agree, or
    "not exercised" when they agreed but none did what its what matters names), "unreadable"
    when the job's harness has no adapter here to read its actions, else "measured": the verdict from the runs recorded
    so far (``analysis/effect.py``; incomplete until each side has 3), its reason, evidence and counts, and each side's
    runs (``sides``) as the situation's are listed."""
    status = comparison_status(paired, recorded)
    entry: dict[str, Any] = {"comparison": paired.id, "planned": paired.runs, "recorded": dict(status.recorded),
                             "progress": status.state.value}
    gate_comparison, gate_rows = plan.comparison(paired.gate or ""), grouped.get(paired.gate or "", ())
    what = _planned_matters(gate_comparison)
    if paired.sides == (WITHOUT,) and what is not None:
        return expectation_entry(entry, what, gate_comparison, gate_rows, grouped.get(paired.id, ()), fingerprint, terms)
    opened = gate_open(gate_comparison, gate_rows, fingerprint, terms)
    if opened is None:
        return {**entry, "state": "waiting"}
    if opened is False:  # why: the situation's screen state, or that none of its runs did its thing
        return {**entry, "state": "skipped", "why": closed_why(gate_comparison, gate_rows, fingerprint, terms)}
    if terms is None:
        return {**entry, "state": "unreadable"}
    sides: dict[str, list[EffectRun]] = {BASELINE: [], WITHOUT: []}
    shown: dict[str, list[dict[str, Any]]] = {BASELINE: [], WITHOUT: []}
    unread: list[dict[str, Any]] = []
    for row in grouped.get(paired.id, ()):
        variant, index = row.run_key.split(":")[1], index_of(row)
        trace = trace_of(row)
        if trace is None:
            unread.append({"side": variant, "run": index, "outcome": row.outcome, "seconds": _seconds(row)})
            continue
        sides[variant].append(EffectRun(index, trace, fingerprint, effect_places(row)))
        shown[variant].append(run_entry(index, row, trace))
    verdict = effect.situation(sides[BASELINE], sides[WITHOUT], terms, comparison=paired.id)
    return {**entry, "state": "measured", **verdict.to_dict(), "sides": shown, "unscreened": unread}


def _planned_matters(comparison: Comparison) -> WhatMatters | None:
    """What a plan froze of a situation's expectation (format 5): it, when it names a thing; None for a plan from before."""
    if not comparison.matters:
        return None
    what = parse_matters(comparison.matters)
    return what if what.uses else None


def _readable(rows: Sequence[jobs.Run], fingerprint: str) -> list[tuple[int, jobs.Run, Trace]]:
    found = []
    for row in rows:
        trace = trace_of(row)
        if trace is not None:
            found.append((index_of(row), row, trace))
    return found


def expectation_entry(entry: dict[str, Any], what: WhatMatters, gate_comparison: Comparison,
                      with_rows: Sequence[jobs.Run], without_rows: Sequence[jobs.Run], fingerprint: str,
                      terms: Terms | None) -> dict[str, Any]:
    """A situation's result as its expectation's strength (decided with the user 2026-10-07, ``analysis/expectation.py``):
    ``kind`` "expectation", what it expects (``expected``, in words), and ``state`` — "waiting" until its runs with the
    target are all in, "unreadable" with no adapter to read them, "skipped" when too few could be read or they ran in
    mixed environments (``why``), else "measured": its verdict (not followed — never run without — works, partial,
    adds nothing, backfires, or incomplete while its runs without are coming), the counts each side, its strength, what
    the runs did instead when not followed, and its runs without the target, each saying whether it met it."""
    entry = {**entry, "kind": "expectation", "expected": what.words()}
    recorded = {row.run_key for row in with_rows}
    if any(run.key not in recorded for run in gate_comparison.planned()):
        return {**entry, "state": "waiting"}
    if terms is None:
        return {**entry, "state": "unreadable"}
    why = closed_why(gate_comparison, with_rows, fingerprint, terms)
    if why is not None and why != NOT_FOLLOWED:
        return {**entry, "state": "skipped", "why": why}
    with_runs = [expectation.Run(trace, _environment(trace, fingerprint)) for _, _, trace in _readable(with_rows, fingerprint)]
    if why == NOT_FOLLOWED:
        found = expectation.situation(what, with_runs, None, terms)
        return {**entry, "state": "measured", **found.to_dict(), "sides": {"without": []}}
    without = _readable(without_rows, fingerprint)
    found = expectation.situation(what, with_runs,
                                  [expectation.Run(trace, _environment(trace, fingerprint)) for _, _, trace in without], terms)
    shown = [{**run_entry(index, row, trace), "met": ran(what, trace, terms)} for index, row, trace in without]
    return {**entry, "state": "measured", **found.to_dict(), "sides": {"without": shown}}


def _environment(trace: Trace, fingerprint: str) -> Environment:
    return Environment(trace.harness_version, tuple(sorted(set(trace.models))), fingerprint)


def effect_places(row: jobs.Run) -> RunPlaces:
    """A run's own directories, as its runner made them (``runs.noindex/<job>/<run key>/{work,home}``, real paths;
    ``runs/…`` for a run made before 2026-10-07, as its trace spells them)."""
    root = execute.runs_root()
    legacy = execute.legacy_runs_root()
    if f"{legacy / row.job_id}/" in row.trace and f"{root / row.job_id}/" not in row.trace:
        root = legacy
    run = root / row.job_id / execute.run_dir_name(row.run_key)
    return RunPlaces(str(run / execute.WORK), str(run / execute.HOME))


def target_results(plan: Plan, situations: Sequence[dict[str, Any]], names: Mapping[str, str]) -> list[dict[str, Any]]:
    """Each target of the job, in the order it ran, with its verdict across its situations (``effect.target``): None
    when the job gives it no with/without step (no variant could remove it); a situation still waiting or skipped
    counts as one that couldn't say, so a target is never called one that does nothing on fewer situations than it
    has."""
    order = list(dict.fromkeys(c.about for c in plan.comparisons if c.phase is Phase.STABILITY and c.about))
    answers = []
    for target in order:
        mine = [s for s in situations if s["about"] == target]
        effects = [s["effect"] for s in mine]
        if not any(e is not None for e in effects):
            answers.append({"about": target, "name": names.get(target, target), "effect": None})
            continue
        if all(e is not None and e.get("kind") == "expectation" for e in effects):
            strengths = [expectation.Strength(e["verdict"], e["with"]["met"], e["with"]["runs"],
                                              (e["without"] or {}).get("met"), (e["without"] or {}).get("runs"),
                                              None if e.get("strength") is None else Fraction(e["strength"]).limit_denominator(1000))
                         if e["state"] == "measured" else expectation.Strength(expectation.INCOMPLETE, 0, 0)
                         for e in effects]
            answers.append({"about": target, "name": names.get(target, target), "kind": "expectation",
                            "effect": expectation.target(strengths).to_dict()})
            continue
        verdicts = [effect.SituationVerdict(e["measured"], e["reason"], (e["runs"]["baseline"], e["runs"]["without"]))
                    if e is not None and e["state"] == "measured"
                    else effect.SituationVerdict(effect.UNMEASURABLE, (e or {}).get("state", "no with/without step"), (0, 0))
                    for e in effects]
        answers.append({"about": target, "name": names.get(target, target), "effect": effect.target(verdicts).to_dict()})
    return answers


def run_entry(index: int, row: jobs.Run, trace: Trace) -> dict[str, Any]:
    """One run as a job's results list it: its index (from 0, as its run key counts), outcome, seconds, and its actions
    in order — each its tool, its input on one line (``rendered``, the run's own directories shown relative) and what
    else it used (the skill it invoked, the agent it delegated to: its usage references besides its own tool, grouped
    by namespace as a branch's ``used`` is) — the first ACTIONS_SHOWN, then how many more."""
    places = run_places(row.job_id, row.run_key)
    return {
        "run": index, "outcome": row.outcome, "seconds": _seconds(row),
        "actions": [{"tool": action.tool, "what": rendered(action.input, places),
                     "uses": namespaced([ref for ref in action.uses if ref.partition("/")[2] != action.tool])}
                    for action in trace.actions[:ACTIONS_SHOWN]],
        "more_actions": max(0, len(trace.actions) - ACTIONS_SHOWN),
    }


def _seconds(row: jobs.Run) -> float:
    return round(row.duration_ms / 1000, 1)


_PATH_END = r"(?=/|[^\w.\-]|$)"  # a directory's path ends where no name character follows it


def run_places(job_id: str, key: str) -> tuple[tuple[re.Pattern[str], str], ...]:
    """The run's own directories, each with the short form an action's input shows it in: its working directory as
    '.', its variant home as '~' (``runs/<job>/<run key>/{work,home}``, ``runner/execute.py``), spelled as the harness
    records them (real paths) and as agentlint's home is given."""
    given = Path(os.path.abspath(store.home()))
    roots = dict.fromkeys([execute.runs_root(), given / execute.RUNS,  # and where runs lived before 2026-10-07
                           execute.legacy_runs_root(), given / execute.LEGACY_RUNS])
    places = []
    for root in roots:
        run = root / job_id / execute.run_dir_name(key)
        places += [(re.compile(re.escape(str(run / execute.WORK)) + _PATH_END), "."),
                   (re.compile(re.escape(str(run / execute.HOME)) + _PATH_END), "~")]
    return tuple(places)


def rendered(given: Mapping[str, Any], places: Sequence[tuple[re.Pattern[str], str]] = ()) -> str:
    """An action's input on one line, for a person: its string values in order — nested ones too, no key read by name,
    so no harness's tools are assumed — each with ``places`` shown short and its whitespace collapsed, joined by
    " · ", cut to WHAT_MAX characters."""
    parts: list[str] = []
    length = 0
    for value in _strings(given):
        text = value[:_VALUE_READ]
        for pattern, short in places:
            text = pattern.sub(short, text)
        text = " ".join(text.split())
        if not text:
            continue
        parts.append(text)
        length += len(text) + 3
        if length > WHAT_MAX:  # enough for the line
            break
    line = " · ".join(parts)
    return line if len(line) <= WHAT_MAX else line[:WHAT_MAX - 1] + "…"


def _strings(value: Any) -> Iterator[str]:
    """Every string in a JSON value, in order: a mapping's values, a list's items, depth first."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def standing(status: JobStatus, alive: bool, stopping: bool) -> str:
    """Where a job stands, in words: whether a live process runs it now, else its stored status."""
    if alive:
        return "stopping" if stopping else "running"
    return {JobStatus.OPEN: "paused: Resume goes on where it stopped", JobStatus.COMPLETE: "complete",
            JobStatus.SALVAGED: "closed: what completed is kept",
            JobStatus.CEILING: "stopped at its run ceiling"}[status]


def _matters_of(db: sqlite3.Connection, probe_id: str) -> str | None:
    """A situation's what-matters tag, from the store; None once it was regenerated away or forgotten."""
    probe = probes.load(db, probe_id)
    return probe.matters if probe is not None else None


def _matters(stored: str | None) -> WhatMatters | None:
    """A situation's what matters, read from what the store keeps; None for none, or for what can't be read."""
    if stored is None:
        return None
    try:
        return read_matters(stored)
    except (ValueError, KeyError, TypeError):
        return None


def _matters_fields(stored: str | None) -> dict[str, Any]:
    """A situation's what matters as an answer gives it: the menu choice (``matters``), what it means
    (``matters_meaning``) and the thing it names, in words (``matters_thing``: "skill:*"; None for a situation written
    before things were named)."""
    what = _matters(stored)
    if what is None:
        return {"matters": None, "matters_meaning": None, "matters_thing": None}
    return {"matters": what.kind.value, "matters_meaning": what.kind.meaning, "matters_thing": what.words() or None}


def _matters_planned(stored: str) -> dict[str, Any] | None:
    """What a test's plan freezes of a situation's what matters: it, when it names a thing to check."""
    what = _matters(stored)
    return what.to_dict() if what is not None and what.uses is not None else None


