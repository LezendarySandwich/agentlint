"""The job engine against a fake executor: order, resume, refusals, salvage, the lock, retries and the ceiling."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from agentlint.rank.plan import Phase
from agentlint.runner import engine
from agentlint.runner.engine import (ConfigChanged, Environment, EnvironmentChanged, JobClosed, RetriesExhausted,
                                     Stopped, create_job, run_job, salvage)
from agentlint.runner.fake import FakeExecutor
from agentlint.runner.lock import Process
from agentlint.runner.plan import Comparison, Plan
from agentlint.runner.status import ComparisonState
from agentlint.store import JobStatus, connect, jobs, retention

ENV = Environment("fp-1", "2.1.285", "opus")
T0 = datetime(2026, 10, 4, 1, 0, tzinfo=UTC)
ME = Process(4242, "started-now")
DEAD = Process(4141, "started-earlier")


def _plan() -> Plan:
    return Plan((Comparison("c1", Phase.ABLATION, "s1", "Fix the test", "rule-a", 3),
                 Comparison("c2", Phase.STABILITY, "s2", "Add logging", None, 3)))


def _run(db, job_id, executor, **kwargs):
    kwargs.setdefault("environment", ENV)
    kwargs.setdefault("process", ME)
    kwargs.setdefault("stale", lambda pid, started: pid == DEAD.pid)
    kwargs.setdefault("wait", lambda seconds: None)
    return run_job(db, job_id, executor, **kwargs)


def _job(db, ceiling: int = 100, plan: Plan | None = None) -> str:
    return create_job(db, project="/p", environment=ENV, plan=plan or _plan(), run_ceiling=ceiling, timeout_s=300,
                      now=T0, job_id="job-1")


class Kill(BaseException):
    """What a kill looks like from inside: nothing after it runs, not even an except Exception."""


def test_a_job_runs_its_plan_in_order_and_records_every_run_once() -> None:
    db = connect()
    job_id = _job(db)
    fake = FakeExecutor(outcome=lambda request: request.run.variant)
    report = _run(db, job_id, fake)
    assert fake.launched == ["c1:baseline:0", "c1:without:0", "c1:baseline:1", "c1:without:1", "c1:baseline:2",
                             "c1:without:2", "c2:baseline:0", "c2:baseline:1", "c2:baseline:2"]
    assert (report.stopped, report.launched, report.recorded, report.interrupted, report.messages()) == (
        Stopped.COMPLETE, 9, 9, (), [])
    job = jobs.load(db, job_id)
    assert (job.status, job.launches, job.in_flight, job.runner_pid) == (JobStatus.COMPLETE, 9, None, None)
    run = next(r for r in jobs.runs(db, job_id) if r.run_key == "c1:without:1")
    assert (run.outcome, json.loads(run.trace), run.session_id, run.harness_version) == (
        "without", [{"fake": "c1:without:1"}], "fake-job-1-c1:without:1", "fake/1")
    assert all(s.state is ComparisonState.COMPLETE for s in engine.job_statuses(db, job_id))
    with pytest.raises(JobClosed):
        _run(db, job_id, fake)


def test_a_kill_mid_run_leaves_the_key_in_flight_and_resume_reports_and_reruns_it() -> None:
    db = connect()
    job_id = _job(db)

    class Dies(FakeExecutor):
        def run(self, request):
            if request.run.key == "c1:baseline:1":
                raise Kill
            return super().run(request)

    with pytest.raises(Kill):
        _run(db, job_id, Dies(), process=DEAD)
    left = jobs.load(db, job_id)
    assert (left.in_flight, left.launches, left.runner_pid) == ("c1:baseline:1", 3, None)  # released in finally here
    db.execute("UPDATE jobs SET runner_pid = ?, runner_started = ?", (DEAD.pid, DEAD.started))  # as SIGKILL leaves it
    fake = FakeExecutor()
    report = _run(db, job_id, fake)
    assert report.interrupted == ("c1:baseline:1",) and report.messages() == ["1 run was interrupted"]
    assert fake.launched[0] == "c1:baseline:1" and "c1:baseline:0" not in fake.launched  # completed keys skipped
    assert jobs.recorded_count(db, job_id) == 9 and report.recorded == 7
    assert jobs.load(db, job_id).launches == 10  # the interrupted launch counts


class DiesAt(FakeExecutor):
    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key

    def run(self, request):
        if request.run.key == self.key:
            raise Kill
        return super().run(request)


def test_resume_refuses_another_harness_or_model_or_configuration() -> None:
    db = connect()
    job_id = _job(db)
    with pytest.raises(Kill):
        _run(db, job_id, DiesAt("c1:without:0"))
    with pytest.raises(EnvironmentChanged):
        _run(db, job_id, FakeExecutor(), environment=Environment("fp-2", "2.1.290", "opus"))
    with pytest.raises(EnvironmentChanged):
        _run(db, job_id, FakeExecutor(), environment=Environment("fp-2", "2.1.285", "sonnet"))
    with pytest.raises(ConfigChanged):
        _run(db, job_id, FakeExecutor(), environment=Environment("fp-2", "2.1.285", "opus"))
    assert jobs.load(db, job_id).launches == 2  # nothing launched by a refused resume
    assert jobs.load(db, job_id).in_flight == "c1:without:0"  # nor the interrupted key cleared


def test_salvage_closes_the_job_keeping_what_completed_and_nothing_appends_after() -> None:
    db = connect()
    job_id = _job(db)
    with pytest.raises(Kill):
        _run(db, job_id, DiesAt("c2:baseline:1"))
    found = salvage(db, job_id, process=ME, stale=lambda pid, started: True)
    assert [(s.comparison, s.state) for s in found] == [("c1", ComparisonState.COMPLETE),
                                                        ("c2", ComparisonState.INCOMPLETE)]
    assert jobs.load(db, job_id).status is JobStatus.SALVAGED
    with pytest.raises(JobClosed):
        _run(db, job_id, FakeExecutor())
    assert jobs.recorded_count(db, job_id) == 7


def test_a_live_runner_keeps_the_job_and_a_dead_ones_lock_is_taken_over() -> None:
    db = connect()
    job_id = _job(db)
    db.execute("UPDATE jobs SET runner_pid = 99, runner_started = 'alive'")
    with pytest.raises(jobs.JobLocked):
        _run(db, job_id, FakeExecutor(), stale=lambda pid, started: False)
    with pytest.raises(jobs.JobLocked):
        salvage(db, job_id, process=ME, stale=lambda pid, started: False)
    assert jobs.load(db, job_id).launches == 0
    report = _run(db, job_id, FakeExecutor(), stale=lambda pid, started: (pid, started) == (99, "alive"))
    assert report.stopped is Stopped.COMPLETE and jobs.load(db, job_id).runner_pid is None


def test_the_launches_counter_counts_retries_too() -> None:
    db = connect()
    job_id = _job(db, ceiling=5)
    waits = []
    fake = FakeExecutor(retry=lambda request, attempt: request.run.key == "c1:without:0" and attempt == 1)
    report = _run(db, job_id, fake, wait=waits.append, counter=engine.launches)
    assert fake.launched == ["c1:baseline:0", "c1:without:0", "c1:without:0", "c1:baseline:1", "c1:without:1"]
    assert (report.stopped, report.launched, report.recorded, report.retried) == (Stopped.CEILING, 5, 4, 1)
    assert waits == [engine.backoff(1)] and jobs.load(db, job_id).in_flight is None
    assert report.messages() == ["stopped at the run ceiling with 5 runs of the plan not run"]
    assert jobs.load(db, job_id).status is JobStatus.CEILING  # nothing raises a ceiling: it could never launch again
    with pytest.raises(JobClosed, match="stopped at its run ceiling"):
        _run(db, job_id, FakeExecutor())
    assert [s.state for s in engine.job_statuses(db, job_id)] == [ComparisonState.INCOMPLETE,
                                                                  ComparisonState.NOT_ATTEMPTED]


def test_a_kill_on_the_last_launch_the_ceiling_allows_is_reported_once_and_closes_the_job() -> None:
    db = connect()
    job_id = _job(db, ceiling=3)
    with pytest.raises(Kill):
        _run(db, job_id, DiesAt("c1:baseline:1"), counter=engine.launches)  # the 3rd launch
    assert jobs.load(db, job_id).in_flight == "c1:baseline:1"
    fake = FakeExecutor()
    report = _run(db, job_id, fake, counter=engine.launches)
    assert fake.launched == [] and (report.stopped, report.interrupted, report.not_rerun) == (
        Stopped.CEILING, ("c1:baseline:1",), ("c1:baseline:1",))
    assert report.messages() == ["1 run was interrupted and not run again: the run ceiling was reached",
                                 "stopped at the run ceiling with 7 runs of the plan not run"]
    job = jobs.load(db, job_id)
    assert (job.status, job.in_flight, job.launches, job.runner_pid) == (JobStatus.CEILING, None, 3, None)
    with pytest.raises(JobClosed):  # not reported again by every later resume
        _run(db, job_id, fake)


def test_the_ceiling_counts_recorded_runs_by_default_so_retries_launch_past_it() -> None:
    db = connect()
    job_id = _job(db, ceiling=5)
    waits = []
    fake = FakeExecutor(retry=lambda request, attempt: request.run.key == "c1:without:0" and attempt == 1)
    report = _run(db, job_id, fake, wait=waits.append)
    assert fake.launched == ["c1:baseline:0", "c1:without:0", "c1:without:0", "c1:baseline:1", "c1:without:1",
                             "c1:baseline:2"]
    assert (report.stopped, report.launched, report.recorded, report.retried) == (Stopped.CEILING, 6, 5, 1)
    assert waits == [engine.backoff(1)] and jobs.recorded_count(db, job_id) == 5
    assert report.messages() == ["stopped at the run ceiling with 4 runs of the plan not run"]


def test_a_kill_before_the_ceiling_under_recorded_runs_reruns_its_key_and_still_records_the_ceiling() -> None:
    db = connect()
    job_id = _job(db, ceiling=3)
    with pytest.raises(Kill):
        _run(db, job_id, DiesAt("c1:baseline:1"))  # the 3rd launch, 2 recorded
    fake = FakeExecutor()
    report = _run(db, job_id, fake)
    assert fake.launched == ["c1:baseline:1"] and (report.stopped, report.interrupted, report.not_rerun) == (
        Stopped.CEILING, ("c1:baseline:1",), ())
    job = jobs.load(db, job_id)
    assert (job.status, job.launches, jobs.recorded_count(db, job_id)) == (JobStatus.CEILING, 4, 3)


def test_one_key_is_retried_at_most_three_times_then_the_job_pauses() -> None:
    db = connect()
    job_id = _job(db)
    fake = FakeExecutor(retry=lambda request, attempt: request.run.key == "c1:baseline:0")
    with pytest.raises(RetriesExhausted, match="4 times in a row") as paused:
        _run(db, job_id, fake)
    assert isinstance(paused.value, engine.Paused)
    assert fake.launched == ["c1:baseline:0"] * 4  # the first try and three retries
    assert jobs.load(db, job_id).status is JobStatus.OPEN


def test_one_resume_launches_at_most_its_retry_cap_of_runs_that_record_nothing() -> None:
    db = connect()
    job_id = _job(db, ceiling=100)
    fake = FakeExecutor(retry=lambda request, attempt: attempt == 1)  # every key fails once, then works
    with pytest.raises(RetriesExhausted, match="3 launches recorded nothing"):
        _run(db, job_id, fake, retry_cap=3)
    assert jobs.recorded_count(db, job_id) == 2 and jobs.load(db, job_id).launches == 5
    assert engine.default_retry_cap(100) == 10 and engine.default_retry_cap(5) == 4


@pytest.mark.parametrize("ceiling", [6, 20, 39])
def test_with_a_small_ceiling_one_key_still_gets_its_three_retries(ceiling: int) -> None:
    """The default per-resume cap never trips before one key's own limit: the fourth failure pauses with the per-key
    message, not after the third with the per-resume one."""
    db = connect()
    job_id = _job(db, ceiling=ceiling)
    fake = FakeExecutor(retry=lambda request, attempt: True)
    with pytest.raises(RetriesExhausted, match="failed outside the agent 4 times in a row"):
        _run(db, job_id, fake)
    assert fake.launched == ["c1:baseline:0"] * 4


def test_a_key_that_keeps_failing_outside_the_agent_stops_the_run_and_keeps_the_job() -> None:
    db = connect()
    job_id = _job(db)
    with pytest.raises(RetriesExhausted):
        _run(db, job_id, FakeExecutor(retry=lambda request, attempt: request.run.key == "c1:without:0"))
    job = jobs.load(db, job_id)
    assert (job.status, job.in_flight, job.launches, job.runner_pid) == (JobStatus.OPEN, None, 5, None)
    report = _run(db, job_id, FakeExecutor())
    assert report.interrupted == () and report.recorded == 8


def test_an_executor_error_propagates_with_nothing_left_in_flight() -> None:
    db = connect()
    job_id = _job(db)
    with pytest.raises(RuntimeError):
        _run(db, job_id, Broken())
    job = jobs.load(db, job_id)
    assert (job.in_flight, job.launches, job.runner_pid) == (None, 1, None)


class Broken(FakeExecutor):
    """Raises on ``key`` (every key when None), as an executor does on an error that isn't a retry."""

    def __init__(self, key: str | None = None) -> None:
        super().__init__()
        self.key = key

    def run(self, request):
        if self.key in (None, request.run.key):
            raise RuntimeError("auth failed")
        return super().run(request)


def _killed_at_third_launch(db) -> str:
    job_id = _job(db)
    with pytest.raises(Kill):
        _run(db, job_id, DiesAt("c1:baseline:1"))
    assert jobs.load(db, job_id).in_flight == "c1:baseline:1"
    return job_id


def test_a_resume_ending_in_retries_exhausted_carries_the_interruption_it_found() -> None:
    db = connect()
    job_id = _killed_at_third_launch(db)
    with pytest.raises(RetriesExhausted) as ended:
        _run(db, job_id, FakeExecutor(retry=lambda request, attempt: True))
    assert engine.interrupted_before(ended.value) == ("c1:baseline:1",) == ended.value.interrupted
    assert "1 run was interrupted before this resume: c1:baseline:1" in ended.value.__notes__
    assert jobs.load(db, job_id).in_flight is None
    report = _run(db, job_id, FakeExecutor())  # reported once, on the exception: not again here
    assert (report.interrupted, report.messages(), report.stopped) == ((), [], Stopped.COMPLETE)


@pytest.mark.parametrize("fails_on", ["c1:baseline:1", "c2:baseline:0"])  # the interrupted key, or one after it
def test_a_resume_ending_in_an_executor_error_carries_the_interruption_it_found(fails_on: str) -> None:
    db = connect()
    job_id = _killed_at_third_launch(db)
    with pytest.raises(RuntimeError, match="auth failed") as ended:
        _run(db, job_id, Broken(fails_on))
    assert engine.interrupted_before(ended.value) == ("c1:baseline:1",)
    assert ended.value.__notes__ == ["1 run was interrupted before this resume: c1:baseline:1"]
    assert jobs.load(db, job_id).in_flight is None
    assert _run(db, job_id, FakeExecutor()).interrupted == ()


def test_an_exception_from_a_resume_that_found_nothing_interrupted_carries_nothing() -> None:
    db = connect()
    job_id = _job(db)
    with pytest.raises(RuntimeError) as ended:
        _run(db, job_id, Broken())
    assert engine.interrupted_before(ended.value) == () and not getattr(ended.value, "__notes__", [])


def _ctrl_c(seconds: float) -> None:
    raise KeyboardInterrupt


def test_ctrl_c_in_a_retry_backoff_carries_the_interruption_the_resume_found() -> None:
    db = connect()
    job_id = _killed_at_third_launch(db)
    with pytest.raises(KeyboardInterrupt) as ended:  # the retry cleared the key: nothing is in flight while it waits
        _run(db, job_id, FakeExecutor(retry=lambda request, attempt: True), wait=_ctrl_c)
    assert engine.interrupted_before(ended.value) == ("c1:baseline:1",)
    assert ended.value.__notes__ == ["1 run was interrupted before this resume: c1:baseline:1"]
    assert jobs.load(db, job_id).in_flight is None
    report = _run(db, job_id, FakeExecutor())  # reported once, on the KeyboardInterrupt
    assert (report.interrupted, report.messages()) == ((), [])


class CtrlCAt(FakeExecutor):
    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key

    def run(self, request):
        if request.run.key == self.key:
            raise KeyboardInterrupt
        return super().run(request)


@pytest.mark.parametrize("at", ["c1:without:1", "c1:baseline:1"])  # a key after the interrupted one re-ran, or it
def test_ctrl_c_mid_run_carries_the_interruption_found_and_leaves_its_own_key_to_the_next_resume(at: str) -> None:
    db = connect()
    job_id = _killed_at_third_launch(db)
    with pytest.raises(KeyboardInterrupt) as ended:
        _run(db, job_id, CtrlCAt(at))
    assert engine.interrupted_before(ended.value) == ("c1:baseline:1",)
    assert jobs.load(db, job_id).in_flight == at
    report = _run(db, job_id, FakeExecutor())  # a new interruption, even of the key the last resume re-ran
    assert (report.interrupted, report.stopped) == ((at,), Stopped.COMPLETE)


def test_keep_days_keeps_an_open_job_at_its_ceiling_that_its_counter_still_lets_resume() -> None:
    db = connect()
    job_id = _job(db, ceiling=3)
    with pytest.raises(RetriesExhausted):
        _run(db, job_id, FakeExecutor(retry=lambda request, attempt: True))  # one key's 4 attempts
    job = jobs.load(db, job_id)
    assert (job.status, job.launches, jobs.recorded_count(db, job_id)) == (JobStatus.OPEN, 4, 0)
    retention.prune(db, datetime.now(UTC) + timedelta(days=100), keep_days=30)
    report = _run(db, job_id, FakeExecutor())  # 0 recorded of 3: still resumable
    assert (report.stopped, report.recorded, jobs.load(db, job_id).status) == (Stopped.CEILING, 3, JobStatus.CEILING)


def test_a_job_forgotten_while_running_stops_at_its_next_write() -> None:
    db = connect()
    job_id = _job(db)

    class Forgets(FakeExecutor):
        def run(self, request):
            jobs.delete_all(db)
            return super().run(request)

    with pytest.raises(jobs.JobGone):
        _run(db, job_id, Forgets())
    assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


def test_an_empty_plan_completes_at_once() -> None:
    db = connect()
    job_id = _job(db, plan=Plan(()))
    report = _run(db, job_id, FakeExecutor())
    assert (report.stopped, report.launched) == (Stopped.COMPLETE, 0)
    assert jobs.load(db, job_id).status is JobStatus.COMPLETE


def test_a_job_needs_a_known_environment() -> None:
    with pytest.raises(ValueError):
        Environment("fp", "", "opus")


def test_prepare_runs_once_holding_the_lock_before_the_first_launch_and_not_when_nothing_launches() -> None:
    db = connect()
    job_id = _job(db, plan=Plan((Comparison("c1", Phase.STABILITY, "s1", "Fix it", None, 3),)))
    seen = []
    fake = FakeExecutor()
    _run(db, job_id, fake, prepare=lambda: seen.append((jobs.load(db, job_id).runner_pid, list(fake.launched))))
    assert seen == [(ME.pid, [])]
    with pytest.raises(JobClosed):
        _run(db, job_id, fake, prepare=lambda: seen.append("again"))
    assert seen == [(ME.pid, [])]


def test_a_failing_prepare_launches_nothing_and_carries_the_interruption_once() -> None:
    db = connect()
    job_id = _killed_at_third_launch(db)

    def refuse() -> None:
        raise RuntimeError("baseline differs")

    fake = FakeExecutor()
    with pytest.raises(RuntimeError) as stopped:
        _run(db, job_id, fake, prepare=refuse)
    assert engine.interrupted_before(stopped.value) == ("c1:baseline:1",) and fake.launched == []
    job = jobs.load(db, job_id)
    assert (job.in_flight, job.launches, job.runner_pid) == (None, 3, None)
    report = _run(db, job_id, fake)
    assert report.interrupted == () and report.stopped is Stopped.COMPLETE


def test_finish_runs_once_holding_the_lock_however_the_resume_ends_and_never_hides_what_propagates() -> None:
    db = connect()
    job_id = _job(db, plan=Plan((Comparison("c1", Phase.STABILITY, "s1", "Fix it", None, 2),)))
    seen = []
    fake = FakeExecutor()

    def refuse() -> None:
        raise RuntimeError("baseline differs")

    def broken() -> None:
        seen.append("finished")
        raise OSError("deletion failed")

    with pytest.raises(RuntimeError, match="baseline differs"):
        _run(db, job_id, fake, prepare=refuse, finish=broken)
    assert seen == ["finished"] and jobs.load(db, job_id).runner_pid is None
    _run(db, job_id, fake, finish=lambda: seen.append((jobs.load(db, job_id).runner_pid, len(fake.launched))))
    assert seen == ["finished", (ME.pid, 2)] and jobs.load(db, job_id).runner_pid is None
