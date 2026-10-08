"""Runs at once (runs_at_once): the engine keeps several runs out, each in a thread of its own, while it alone writes
the database — the plan's order for what starts next, a gated comparison only once its gate's runs are all recorded,
the ceiling counting the runs out, retries backing off without holding the others, and every run still out stopped
and waited for when the resume ends in an exception."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest

from agentlint.rank.plan import Phase
from agentlint.runner import engine
from agentlint.runner.engine import Environment, Retry, RunResult, Stopped, create_job, run_job
from agentlint.runner.fake import FakeExecutor as Fake
from agentlint.runner.lock import Process
from agentlint.runner.plan import Comparison, Plan
from agentlint.store import JobStatus, connect, jobs

ENV = Environment("fp-1", "2.1.285", "opus")
T0 = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
ME = Process(4242, "started-now")


class FakeExecutor(Fake):
    """The fake executor, its runs a real trace that reads a file: s1's runs agree, so its gate opens."""

    def run(self, request):
        made = super().run(request)
        action = {"seq": 0, "tool": "Read", "input": {"file_path": "/a"}, "message_id": "m", "call_id": "c",
                  "uses": ["tools/Read"], "answered": True, "error": False, "denied": False}
        trace = {"session_id": "s", "harness_version": "2.1.285", "models": ["opus"], "headless": True,
                 "actions": [action], "fired": []}
        return RunResult(made.outcome, trace, made.session_id, "2.1.285", "opus", None)


def plan() -> Plan:
    """A situation's 3 consistency runs, then its 3 runs without the target, gated on them; then another situation."""
    return Plan((Comparison("s1", Phase.STABILITY, "p1", "Run the report", None, 3, "t", matters=None),
                 Comparison("a1", Phase.ABLATION, "p1", "Run the report", "t", 3, "t", gate="s1", sides=("without",)),
                 Comparison("s2", Phase.STABILITY, "p2", "Find the table", None, 3, "t")))


def make(db, ceiling: int = 100) -> str:
    return create_job(db, project="/p", environment=ENV, plan=plan(), run_ceiling=ceiling, timeout_s=300, now=T0,
                      job_id="job-1")


def run(db, executor, at_once: int, **options):
    return run_job(db, "job-1", executor, environment=ENV, process=ME, stale=lambda pid, started: False,
                   wait=lambda seconds: None, at_once=at_once, **options)


class Overlapping(FakeExecutor):
    """Records how many runs were out at once, holding each run until ``together`` are out (or a short wait)."""

    def __init__(self, together: int) -> None:
        super().__init__(outcome=lambda request: "ok")
        self.together, self.out, self.most = together, 0, 0
        self.lock = threading.Lock()
        self.full = threading.Condition(self.lock)
        self.order: list[str] = []

    def run(self, request):
        with self.full:
            self.out += 1
            self.most = max(self.most, self.out)
            self.order.append(request.run.key)
            self.full.notify_all()
            self.full.wait_for(lambda: self.out >= self.together, timeout=0.2)
        try:
            return super().run(request)
        finally:
            with self.lock:
                self.out -= 1


def test_three_at_once_keeps_three_runs_out_and_records_every_one() -> None:
    db = connect()
    make(db)
    fake = Overlapping(3)
    report = run(db, fake, 3)
    assert fake.most == 3 and report.stopped is Stopped.COMPLETE and report.recorded == 9
    assert jobs.recorded_keys(db, "job-1") == {f"{c}:{v}:{n}" for c, v in (("s1", "baseline"), ("a1", "without"),
                                                                          ("s2", "baseline")) for n in range(3)}
    job = jobs.load(db, "job-1")
    assert (job.status, job.in_flight, job.launches) == (JobStatus.COMPLETE, None, 9)


def test_a_gated_comparison_starts_only_once_its_gates_runs_are_all_recorded() -> None:
    db = connect()
    make(db)
    seen: list[tuple[str, frozenset[str]]] = []

    class Watching(FakeExecutor):
        def run(self, request):
            seen.append((request.run.key, frozenset(jobs_recorded())))
            return super().run(request)

    lock = threading.Lock()
    recorded: set[str] = set()

    def jobs_recorded() -> set[str]:
        with lock:
            return set(recorded)

    original = jobs.record

    def record(db_, run_, *, complete):
        original(db_, run_, complete=complete)
        with lock:
            recorded.add(run_.run_key)

    jobs.record = record  # type: ignore[assignment]
    try:
        run(db, Watching(outcome=lambda request: "ok"), 4)
    finally:
        jobs.record = original  # type: ignore[assignment]
    for key, before in seen:
        if key.startswith("a1:"):
            assert {f"s1:baseline:{n}" for n in range(3)} <= before  # never while s1's runs were still out
    starts = [key for key, _ in seen]
    assert starts[:4] == ["s1:baseline:0", "s1:baseline:1", "s1:baseline:2", "s2:baseline:0"]  # a1 waits: s2 goes


def test_the_ceiling_counts_the_runs_out() -> None:
    db = connect()
    make(db, ceiling=4)
    fake = Overlapping(3)
    report = run(db, fake, 3)
    assert report.stopped is Stopped.CEILING and report.recorded == 4 and jobs.recorded_count(db, "job-1") == 4


def test_a_retried_run_backs_off_while_the_others_go_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "backoff", lambda attempt: 0.05)
    db = connect()
    make(db)
    failed: list[str] = []

    class Flaky(FakeExecutor):
        def run(self, request):
            if request.run.key == "s1:baseline:1" and not failed:
                failed.append(request.run.key)
                raise Retry("an API error")
            return super().run(request)

    report = run(db, Flaky(outcome=lambda request: "ok"), 2)
    assert (report.stopped, report.recorded, report.retried, report.launched) == (Stopped.COMPLETE, 9, 1, 10)


def test_an_exception_stops_the_runs_still_out_and_waits_for_them() -> None:
    db = connect()
    make(db)
    release = threading.Event()
    stopped: list[bool] = []

    class Failing(FakeExecutor):
        def run(self, request):
            if request.run.key == "s1:baseline:0":
                raise RuntimeError("the harness broke")
            release.wait(timeout=5)  # out until interrupted
            return super().run(request)

        def interrupt(self) -> None:
            stopped.append(True)
            release.set()

    with pytest.raises(RuntimeError):
        run(db, Failing(outcome=lambda request: "ok"), 3)
    assert stopped == [True]
    job = jobs.load(db, "job-1")
    assert job.status is JobStatus.OPEN and set(job.running) == {"s1:baseline:1", "s1:baseline:2"}  # interrupted
    assert jobs.recorded_count(db, "job-1") == 0  # what came back after the stop isn't recorded
    resumed = run(db, FakeExecutor(outcome=lambda request: "ok"), 1)
    assert set(resumed.interrupted) == {"s1:baseline:1", "s1:baseline:2"} and resumed.stopped is Stopped.COMPLETE
