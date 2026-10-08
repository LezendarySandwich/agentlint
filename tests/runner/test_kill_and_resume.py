"""v2 exit criteria, with a fake executor: a runner SIGKILLed mid-run resumes correctly, and the ceiling holds.

A real child process runs the engine with the FakeExecutor and is killed with SIGKILL while a run is out — nothing in
it gets to clean up, as when a laptop sleeps or a terminal is closed. The job is then resumed here. Every key ends up
recorded exactly once, the killed one is launched twice and reported, and the dead runner's lock is seen as stale.

The child is given the database path and its own AGENTLINT_HOME, and imports agentlint only — never the test suite.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pytest

from agentlint.rank.plan import Phase
from agentlint.runner import lock
from agentlint.runner.engine import Environment, JobClosed, Stopped, create_job, run_job
from agentlint.runner.fake import FakeExecutor
from agentlint.runner.plan import Comparison, Plan
from agentlint.store import JobStatus, connect, jobs

ENV = Environment("fp-1", "2.1.285", "opus")
CHILD = """
import sys
from pathlib import Path

from agentlint.runner.engine import Environment, run_job
from agentlint.runner.fake import FakeExecutor
from agentlint.store import connect

db_path, job_id, log, delay = sys.argv[1:5]
run_job(connect(Path(db_path)), job_id, FakeExecutor(delay_s=float(delay), log=Path(log)),
        environment=Environment("fp-1", "2.1.285", "opus"))
"""
PLAN = Plan((Comparison("c1", Phase.ABLATION, "s1", "Fix the failing test", "rule-a", 3),
             Comparison("c2", Phase.ABLATION, "s2", "Add a migration", "skill-b", 3),
             Comparison("c3", Phase.STABILITY, "s3", "Rename the module", None, 3)))


def _kill_mid_run(tmp_path: Path, home: Path, ceiling: int, after: int) -> tuple[Path, str, list[str], int]:
    """Start a runner in a child process, SIGKILL it while its ``after``-th launch is out; what it launched."""
    db_path = home / "agentlint.db"
    job_id = create_job(connect(db_path), project="/p", environment=ENV, plan=PLAN, run_ceiling=ceiling, timeout_s=300,
                        job_id="job-kill")
    script, log = tmp_path / "child.py", tmp_path / "launched.log"
    script.write_text(CHILD)
    child = subprocess.Popen([sys.executable, str(script), str(db_path), job_id, str(log), "0.4"],
                             env={**os.environ, "AGENTLINT_HOME": str(home)})
    deadline = time.monotonic() + 60
    try:
        while not (log.exists() and len(log.read_text().splitlines()) >= after):
            assert child.poll() is None, "the runner ended before it could be killed"
            assert time.monotonic() < deadline, "the runner never got that far"
            time.sleep(0.005)
        os.kill(child.pid, signal.SIGKILL)
    finally:
        child.kill()
        child.wait()
    assert child.returncode == -signal.SIGKILL
    return db_path, job_id, log.read_text().splitlines(), child.pid


def test_a_runner_killed_mid_run_resumes_running_every_key_once_and_reports_the_interrupted_one(
        tmp_path: Path, agentlint_home: Path) -> None:
    db_path, job_id, launched_before, child_pid = _kill_mid_run(tmp_path, agentlint_home, ceiling=100, after=5)
    db = connect(db_path)
    left = jobs.load(db, job_id)
    killed = launched_before[-1]
    assert launched_before == [run.key for run in PLAN.runs()][:5]
    assert (left.in_flight, left.launches, jobs.recorded_count(db, job_id)) == (killed, 5, 4)
    assert left.runner_pid == child_pid and lock.stale(left.runner_pid, left.runner_started)  # the lock outlived it

    fake = FakeExecutor()
    report = run_job(db, job_id, fake, environment=ENV)  # this process's real lock takes over the dead one's

    assert report.interrupted == (killed,) and report.messages() == ["1 run was interrupted"]
    assert report.stopped is Stopped.COMPLETE and fake.launched[0] == killed
    launched = Counter(launched_before + fake.launched)
    assert {key for key, n in launched.items() if n != 1} == {killed} and launched[killed] == 2
    assert set(launched) == {run.key for run in PLAN.runs()} == jobs.recorded_keys(db, job_id)
    assert jobs.recorded_count(db, job_id) == PLAN.size == 15
    done = jobs.load(db, job_id)
    assert (done.status, done.in_flight, done.launches, done.runner_pid) == (JobStatus.COMPLETE, None, 16, None)


def test_the_ceiling_holds_across_a_kill_and_a_resume(tmp_path: Path, agentlint_home: Path) -> None:
    db_path, job_id, launched_before, _ = _kill_mid_run(tmp_path, agentlint_home, ceiling=8, after=5)
    db = connect(db_path)
    fake = FakeExecutor()
    report = run_job(db, job_id, fake, environment=ENV)
    assert report.stopped is Stopped.CEILING and report.interrupted == (launched_before[-1],)
    assert jobs.recorded_count(db, job_id) == 8  # the ceiling counts recorded runs (D8): exactly the results asked for
    assert len(launched_before) + len(fake.launched) == jobs.load(db, job_id).launches == 9  # the kill cost one launch
    assert report.not_rerun == () and jobs.load(db, job_id).status is JobStatus.CEILING
    with pytest.raises(JobClosed):  # it can never launch again, so it is closed
        run_job(db, job_id, FakeExecutor(), environment=ENV)


SECOND = """
import sys
from pathlib import Path

from agentlint.runner.engine import Environment, run_job
from agentlint.runner.fake import FakeExecutor
from agentlint.store import connect, jobs

db_path, job_id = sys.argv[1:3]
try:
    run_job(connect(Path(db_path)), job_id, FakeExecutor(), environment=Environment("fp-1", "2.1.285", "opus"))
except jobs.JobLocked:
    print("locked")
"""


def test_a_resume_from_another_time_zone_sees_the_live_runner_and_leaves_the_job_alone(tmp_path: Path,
                                                                                    agentlint_home: Path) -> None:
    """A scheduler's runner in UTC, a resume from a terminal in Tokyo: the second must be refused, not run the job."""
    db_path = agentlint_home / "agentlint.db"
    job_id = create_job(connect(db_path), project="/p", environment=ENV, plan=PLAN, run_ceiling=100, timeout_s=300,
                        job_id="job-tz")
    first, second, log = tmp_path / "first.py", tmp_path / "second.py", tmp_path / "launched.log"
    first.write_text(CHILD)
    second.write_text(SECOND)
    env = {**os.environ, "AGENTLINT_HOME": str(agentlint_home)}
    runner = subprocess.Popen([sys.executable, str(first), str(db_path), job_id, str(log), "0.4"],
                              env={**env, "TZ": "UTC"})
    try:
        deadline = time.monotonic() + 60
        while not (log.exists() and log.read_text().strip()):
            assert runner.poll() is None and time.monotonic() < deadline, "the first runner never started a run"
        resumed = subprocess.run([sys.executable, str(second), str(db_path), job_id], env={**env, "TZ": "Asia/Tokyo"},
                                 capture_output=True, text=True, timeout=60, check=True)
        assert resumed.stdout.strip() == "locked"
        assert runner.wait(timeout=60) == 0
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait()
    launched = log.read_text().splitlines()
    assert len(launched) == len(set(launched)) == PLAN.size  # every key once: no second runner ran any
    assert jobs.load(connect(db_path), job_id).status is JobStatus.COMPLETE
