"""A test runs both steps, target by target (D9): a situation's with/without comparison runs only when its stability
runs, recorded earlier in the same job, opened its gate (runner/gate.py, the engine and the job's progress). A
situation that names its thing opens it when at least one run did the thing (2026-10-07); one written before then, when
its runs agreed. A situation that expects something runs its second step only without the target: its stability runs
are the runs with it; one that expects nothing (these first tests) runs both sides."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from agentlint.runner import engine
from agentlint.runner.engine import Environment, Stopped, create_job, run_job
from agentlint.runner.engine import RunRequest, RunResult
from agentlint.runner.gate import closed, gate_open
from agentlint.runner.lock import Process
from agentlint.runner.plan import Planned, two_step_plan
from agentlint.runner.status import ComparisonState, progress
from agentlint.store import JobStatus, connect, jobs
from agentlint.trace.model import Action, Trace

ENV = Environment("fp-1", "2.1.285", "opus")
T0 = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
ME = Process(4242, "started-now")


def trace(*tools: str) -> dict:
    actions = tuple(Action(i, tool, {}, None, None, (f"tools/{tool}",), True, False, False) for i, tool in enumerate(tools))
    return Trace("s", "2.1.285", ("opus",), True, actions, ()).to_dict()


class Scripted:
    """An executor whose runs use the tools ``used(key)`` names, each a real trace."""

    def __init__(self, used: Callable[[str], tuple[str, ...]], die_at: str | None = None) -> None:
        self.used, self.die_at = used, die_at
        self.launched: list[str] = []

    def run(self, request: RunRequest) -> RunResult:
        key = request.run.key
        self.launched.append(key)
        if key == self.die_at:
            raise KeyboardInterrupt
        return RunResult("ok", trace(*self.used(key)), session_id=f"s-{key}", harness_version="2.1.285", model="opus",
                         cost_usd=None)


def plan():
    return two_step_plan([Planned("hook-a", (("p1", "Run the report"), ("p2", "Find the table")), True)],
                         runs=3, runs_per_side=3)


def job(db) -> str:
    made = plan()
    return create_job(db, project="/p", environment=ENV, plan=made, run_ceiling=made.size, timeout_s=300, now=T0,
                      job_id="job-1")


def run(db, executor):
    return run_job(db, "job-1", executor, environment=ENV, process=ME, stale=lambda pid, started: False,
                   wait=lambda seconds: None)


def test_a_situation_whose_runs_agreed_gets_its_with_without_runs_and_one_whose_runs_disagreed_spends_none() -> None:
    db = connect()
    job(db)
    # p1's three runs agree (Read each time); p2's don't (Read, Read, Bash)
    fake = Scripted(lambda key: ("Bash",) if key == "cmp-0002:baseline:2" else ("Read",))
    report = run(db, fake)
    assert fake.launched == [*(f"cmp-0001:baseline:{n}" for n in range(3)), *(f"cmp-0002:baseline:{n}" for n in range(3)),
                             "cmp-0003:baseline:0", "cmp-0003:without:0", "cmp-0003:baseline:1", "cmp-0003:without:1",
                             "cmp-0003:baseline:2", "cmp-0003:without:2"]  # cmp-0004, p2's, never runs
    assert (report.stopped, report.recorded, report.remaining) == (Stopped.COMPLETE, 12, 0)
    assert jobs.load(db, "job-1").status is JobStatus.COMPLETE
    states = {s.comparison: s.state for s in engine.job_statuses(db, "job-1")}
    assert states == {"cmp-0001": ComparisonState.COMPLETE, "cmp-0002": ComparisonState.COMPLETE,
                      "cmp-0003": ComparisonState.COMPLETE, "cmp-0004": ComparisonState.SKIPPED}
    shut = closed(plan(), engine.gate.by_comparison(jobs.runs(db, "job-1")), "fp-1")
    assert shut == {"cmp-0004"}
    assert progress(plan(), jobs.recorded_keys(db, "job-1"), 12, shut).runs_planned == 12  # the skipped six no longer planned


def test_runs_that_took_no_action_open_no_gate() -> None:
    db = connect()
    job(db)
    fake = Scripted(lambda key: () if key.startswith("cmp-0001") else ("Read",))
    run(db, fake)
    assert not any(key.startswith("cmp-0003") for key in fake.launched)  # p1 took no action: nothing to compare
    assert any(key.startswith("cmp-0004") for key in fake.launched)


def test_a_resume_decides_the_gate_the_same_from_the_runs_recorded() -> None:
    db = connect()
    job(db)
    with pytest.raises(KeyboardInterrupt):
        run(db, Scripted(lambda key: ("Read",), die_at="cmp-0003:without:1"))
    fake = Scripted(lambda key: ("Read",))
    report = run(db, fake)
    assert fake.launched[0] == "cmp-0003:without:1"  # where it stopped: the gate opened again from the same runs
    assert report.stopped is Stopped.COMPLETE and report.interrupted == ("cmp-0003:without:1",)


def test_a_gate_is_undecided_until_every_planned_run_of_its_comparison_is_recorded() -> None:
    db = connect()
    job(db)
    with pytest.raises(KeyboardInterrupt):
        run(db, Scripted(lambda key: ("Read",), die_at="cmp-0001:baseline:2"))
    rows = engine.gate.by_comparison(jobs.runs(db, "job-1"))
    assert gate_open(plan().comparison("cmp-0001"), rows.get("cmp-0001", []), "fp-1") is None
    assert closed(plan(), rows, "fp-1") == frozenset()  # undecided is not closed


def _terms(action):
    from agentlint.trace.terms import ActionKind, ActionTerms
    return ActionTerms(ActionKind.SKILL if action.tool == "Skill" else ActionKind.OTHER, action.tool,
                       ("tdd",) if action.tool == "Skill" else ())


def _expecting_job(db):
    made = two_step_plan([Planned("hook-a", (("p1", "Run the report", {"kind": "tool_present", "uses": "skill:*"}),
                                             ("p2", "Find the table", {"kind": "tool_present", "uses": "skill:*"})), True)],
                         runs=3, runs_per_side=3)
    create_job(db, project="/p", environment=ENV, plan=made, run_ceiling=made.size, timeout_s=300, now=T0, job_id="job-1")
    return made


def test_runs_that_never_did_what_the_situation_expects_open_no_gate() -> None:
    """2026-10-07: the situation expects a skill; its runs agreed (Read every time) but none invoked one, so the target
    was not followed — a finding — and nothing is run without it."""
    from agentlint.runner.gate import NOT_FOLLOWED, closed_why
    terms = _terms
    db = connect()
    made = _expecting_job(db)
    fake = Scripted(lambda key: ("Skill",) if key.startswith("cmp-0002") else ("Read",))
    run_job(db, "job-1", fake, environment=ENV, process=ME, stale=lambda pid, started: False, wait=lambda s: None,
            terms=terms)
    assert not any(key.startswith("cmp-0003") for key in fake.launched)  # p1: Read, Read, Read — never a skill
    assert any(key.startswith("cmp-0004") for key in fake.launched)  # p2 invoked one: measured without it
    rows = engine.gate.by_comparison(jobs.runs(db, "job-1"))
    assert closed_why(made.comparison("cmp-0001"), rows["cmp-0001"], "fp-1", terms) == NOT_FOLLOWED
    assert closed_why(made.comparison("cmp-0001"), rows["cmp-0001"], "fp-1") is None  # without the adapter's terms: as before


def test_runs_that_did_what_the_situation_expects_once_open_the_gate_though_they_disagreed() -> None:
    """The user, 2026-10-07: a situation whose runs weren't consistent is a finding, and still runs without the target
    when at least one run met the expectation — how often it is met without it is the comparison."""
    db = connect()
    _expecting_job(db)
    fake = Scripted(lambda key: ("Skill",) if key == "cmp-0001:baseline:1" else ("Read",))
    run_job(db, "job-1", fake, environment=ENV, process=ME, stale=lambda pid, started: False, wait=lambda s: None,
            terms=_terms)
    assert [key for key in fake.launched if key.startswith("cmp-0003")] == [f"cmp-0003:without:{n}" for n in range(3)]
    assert not any(key.startswith("cmp-0004") for key in fake.launched)  # p2: never a skill
