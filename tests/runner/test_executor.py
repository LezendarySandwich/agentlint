"""The real Claude Code executor end to end, against a fake ``claude`` on PATH that behaves as the real one was observed
to (``fake_claude_cli.py``): a whole small job — the baseline check, runs in fresh homes and empty directories deleted
behind them, the harness version the job pinned on every launch, no permission mode, the MCP tools that act outside
the machine removed and frozen with the job, API errors retried, an authentication failure pausing the job, the
ceiling, a timeout as an outcome — and a runner SIGKILLed mid-run and resumed."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
from agentlint.adapters.claude_code.instructions import project_slug
from agentlint.adapters.claude_code.launch import AuthFailed, ClaudeCodeLauncher, HarnessFailed
from agentlint.harness import read_config
from agentlint.rank.plan import Phase
from agentlint.runner.engine import ConfigChanged, EnvironmentChanged, Paused, RetriesExhausted, Stopped
from agentlint.runner import engine, execute
from agentlint.runner.engine import JobClosed
from agentlint.runner.execute import (RUNNING, TRASH, acting_tools, clean_runs, forget_runs, job_removed_tools,
                                      record_running, run_test, runs_root, salvage, stop_left_running)
from agentlint.runner.plan import Comparison, Plan, stability_plan
from agentlint.runner.verify import BaselineMismatch
from agentlint.store import JobStatus, connect, jobs
from agentlint.store import usage as usage_store
from agentlint.trace.model import Trace
from tests.runner.fake_claude import fake_machine, reading, target, write

FAKE = Path(__file__).with_name("fake_claude_cli.py")
MODEL = "model-x"
PLAN = stability_plan([("s1", "Fix the failing test"), ("s2", "Add logging to the parser")], runs=3)


@dataclass
class Machine:
    adapter: ClaudeCodeAdapter
    project: Path
    queue: Path
    log: Path

    def then(self, *behaviours: str) -> None:
        self.queue.write_text(json.dumps(list(behaviours)))

    def calls(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def real_files(self) -> dict[str, bytes]:
        root = self.adapter.home_dir()
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
                if p.is_file() and not p.is_symlink()}


def install_fake_claude(bin_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "claude"
    exe.write_text(f"#!{sys.executable}\n" + FAKE.read_text())
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    adapter, project = fake_machine(tmp_path)
    write(project / ".claude" / "settings.json", json.dumps({"permissions": {"allow": ["Bash(make)"]}}))
    write(project / "src" / "app.py", "print('code')\n")
    install_fake_claude(tmp_path / "bin", monkeypatch)
    m = Machine(adapter, project, tmp_path / "queue.json", tmp_path / "calls.jsonl")
    monkeypatch.setenv("FAKE_CLAUDE_QUEUE", str(m.queue))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(m.log))
    return m


def test(m: Machine, db=None, **options):
    options.setdefault("wait", lambda seconds: None)
    if "job_id" not in options:
        options.setdefault("plan", PLAN)
        options.setdefault("run_ceiling", 100)
        options.setdefault("timeout_s", 30)
    return run_test(db or connect(), m.adapter, ClaudeCodeLauncher(grace_s=1), project=m.project, model=MODEL,
                    **options)


test.__test__ = False  # a helper, not a test


def leftovers(job_id: str) -> list[str]:
    job_dir = runs_root() / job_id
    found = [p.name for p in job_dir.iterdir() if p.name != TRASH] if job_dir.is_dir() else []
    trash = job_dir / TRASH
    return found + ([f"{TRASH}/{p.name}" for p in trash.iterdir()] if trash.is_dir() else [])


def test_a_small_job_runs_every_situation_in_a_fresh_home_and_records_its_trace(machine: Machine) -> None:
    before = machine.real_files()
    tested = test(machine)
    assert tested.report.stopped is Stopped.COMPLETE and tested.report.recorded == 6
    assert tested.baseline is not None and tested.baseline.sources > 5
    db = connect()
    runs = jobs.runs(db, tested.job_id)
    assert [r.outcome for r in runs] == ["ok"] * 6
    assert {(r.harness_version, r.model) for r in runs} == {("9.9.9", MODEL)}
    trace = Trace.from_dict(json.loads(runs[0].trace))
    assert [a.tool for a in trace.actions] == ["Bash"] and trace.session_id == runs[0].session_id
    calls = machine.calls()
    assert len(calls) == 6 and len({c["home"] for c in calls}) == 6  # a fresh home every run
    for call, run in zip(calls, runs):
        argv = call["argv"]
        assert argv[:2] == ["-p", "Fix the failing test" if run.run_key.startswith("cmp-0001") else
                            "Add logging to the parser"]
        assert "--permission-mode" not in argv  # D6 final: the org's guardrails govern, as in your sessions
        assert "--disallowedTools" not in argv  # no session showed an MCP tool: nothing to remove
        assert argv[argv.index("--model") + 1] == MODEL and argv[argv.index("--session-id") + 1] == run.session_id
        assert argv[argv.index("--output-format") + 1] == "json"
        place = runs_root() / tested.job_id / run.run_key.replace(":", ".")
        assert (call["home"], call["cwd"]) == (str(place / "home"), str(place / "work"))
        assert call["agents_md"]  # the project's configuration beside the situation (D1)
    assert leftovers(tested.job_id) == []  # deleted in the background, waited for at the end
    assert machine.real_files() == before  # the real home only read


def test_an_api_error_is_retried_and_never_recorded(machine: Machine) -> None:
    machine.then("server_error", "ok", "rate_limit")
    tested = test(machine)
    assert (tested.report.recorded, tested.report.retried, tested.report.launched) == (6, 2, 8)
    db = connect()
    assert [r.outcome for r in jobs.runs(db, tested.job_id)] == ["ok"] * 6
    assert jobs.load(db, tested.job_id).launches == 8


def test_a_key_failing_with_api_errors_four_times_pauses_the_job_and_a_resume_finishes(machine: Machine) -> None:
    machine.then(*["server_error"] * 4)
    db = connect()
    with pytest.raises(RetriesExhausted, match="4 times in a row"):
        test(machine, db)
    (job,) = db.execute("SELECT job_id FROM jobs").fetchall()
    assert jobs.load(db, job[0]).status is JobStatus.OPEN and jobs.recorded_count(db, job[0]) == 0
    resumed = test(machine, db, job_id=job[0])
    assert resumed.report.stopped is Stopped.COMPLETE and resumed.report.recorded == 6


def test_an_authentication_failure_pauses_the_job_at_once_with_a_clear_message(machine: Machine) -> None:
    machine.then("auth")
    db = connect()
    with pytest.raises(AuthFailed, match="no credentials") as paused:
        test(machine, db)
    assert isinstance(paused.value, Paused) and len(machine.calls()) == 1  # not retried
    (job,) = db.execute("SELECT job_id, status FROM jobs").fetchall()
    assert job[1] == "open" and jobs.recorded_count(db, job[0]) == 0


@pytest.mark.parametrize("behaviour", [
    "gateway_auth",  # this machine's setup: the gateway couldn't authenticate upstream, written as invalid_request
    "api:unknown:gateway_signin_required", "api:unknown:gateway_session_expired", "api:unknown:provider_credentials",
    "api:authentication_failed:provider_credentials", "api:invalid_request:invalid_credential_header",
    "api:invalid_request:org_disabled_credential", "api:invalid_request:api_key_auth_disabled",
    "api:verification_required", "api:billing_error", "api:account_on_hold", "api:oauth_org_not_allowed"])
def test_every_authentication_failure_the_harness_writes_pauses_the_job_at_once(machine: Machine,
                                                                               behaviour: str) -> None:
    machine.then(behaviour)
    db = connect()
    with pytest.raises(AuthFailed, match="couldn't authenticate"):
        test(machine, db)
    assert len(machine.calls()) == 1  # not retried, nothing recorded
    (job,) = db.execute("SELECT job_id, status FROM jobs").fetchall()
    assert job[1] == "open" and jobs.recorded_count(db, job[0]) == 0


@pytest.mark.parametrize("behaviour", ["api:unknown", "api:cloud_credential_error", "api:never_seen_before",
                                       "api:server_error:no_response", "api:overloaded_error"])
def test_any_other_api_error_is_infrastructure_retried_never_an_outcome(machine: Machine, behaviour: str) -> None:
    machine.then(behaviour)
    tested = test(machine, plan=stability_plan([("s1", "Fix the failing test")], runs=2))
    assert (tested.report.recorded, tested.report.retried, tested.report.launched) == (2, 1, 3)
    assert [r.outcome for r in jobs.runs(connect(), tested.job_id)] == ["ok", "ok"]


def test_an_unknown_api_error_every_time_pauses_the_job_after_three_retries(machine: Machine) -> None:
    machine.then(*["api:unknown"] * 4)
    with pytest.raises(RetriesExhausted, match="4 times in a row"):
        test(machine, plan=stability_plan([("s1", "Fix the failing test")], runs=3), run_ceiling=3)
    assert len(machine.calls()) == 4


def test_a_refused_request_is_an_outcome_and_a_missing_model_pauses(machine: Machine) -> None:
    machine.then("refusal", "api:invalid_request:tool_history_mismatch")
    tested = test(machine, plan=stability_plan([("s1", "Fix the failing test")], runs=2))
    assert [r.outcome for r in jobs.runs(connect(), tested.job_id)] == ["error", "error"]
    machine.then("api:model_not_found")
    with pytest.raises(HarnessFailed, match="isn't served"):
        test(machine)


def test_every_launch_runs_the_harness_version_the_job_pinned_and_its_model(machine: Machine) -> None:
    tested = test(machine)
    db = connect()
    assert jobs.load(db, tested.job_id).harness_version == "9.9.9" == machine.adapter.version()
    calls = machine.calls()
    assert len(calls) == 6 and {c["version_override"] for c in calls} == {"9.9.9"}
    assert {c["argv"][c["argv"].index("--model") + 1] for c in calls} == {MODEL}


def launcher_updated(machine: Machine, monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    """The launcher installed another version since: the adapter reports it, and it is what runs unless pinned."""
    write(machine.adapter.paths.install_dir / "package.json",
          json.dumps({"dependencies": {"@anthropic-ai/claude-code": version}}))
    monkeypatch.setenv("FAKE_CLAUDE_VERSION", version)


def test_a_resume_after_the_launcher_updated_is_not_refused_and_runs_the_pinned_version(
        machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    machine.then("ok", *["server_error"] * 4)
    db = connect()
    with pytest.raises(RetriesExhausted):
        test(machine, db)
    (job_id,) = db.execute("SELECT job_id FROM jobs").fetchone()
    launcher_updated(machine, monkeypatch, "9.9.10")
    assert machine.adapter.version() == "9.9.10"
    resumed = test(machine, db, job_id=job_id)
    assert resumed.report.stopped is Stopped.COMPLETE and resumed.report.recorded == 5
    assert {c["version_override"] for c in machine.calls()} == {"9.9.9"}  # resumes included
    assert {r.harness_version for r in jobs.runs(db, job_id)} == {"9.9.9"}
    fresh = test(machine, db)  # a new job pins what the adapter reports now
    assert jobs.load(db, fresh.job_id).harness_version == "9.9.10"
    assert {r.harness_version for r in jobs.runs(db, fresh.job_id)} == {"9.9.10"}


def test_a_resume_still_refuses_a_changed_configuration_or_another_model(machine: Machine,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    machine.then(*["server_error"] * 4)
    db = connect()
    with pytest.raises(RetriesExhausted):
        test(machine, db)
    (job_id,) = db.execute("SELECT job_id FROM jobs").fetchone()
    launcher_updated(machine, monkeypatch, "9.9.10")
    write(machine.project / "AGENTS.md", "# Project\nOther rules.\n")
    with pytest.raises(ConfigChanged):
        test(machine, db, job_id=job_id)
    write(machine.project / "AGENTS.md", "# Project\nProject rules.\n")
    with pytest.raises(EnvironmentChanged, match="model-y"):
        run_test(db, machine.adapter, ClaudeCodeLauncher(grace_s=1), project=machine.project, model="model-y",
                 job_id=job_id, wait=lambda seconds: None)
    assert test(machine, db, job_id=job_id).report.stopped is Stopped.COMPLETE


@pytest.mark.parametrize("behaviour", ["version:9.9.10", "model:model-y"])
def test_a_run_that_recorded_another_version_or_model_is_recorded_as_it_ran(machine: Machine, behaviour: str) -> None:
    """No per-run stop (D5): the run is stored with what it recorded, and the analysis refuses to compare runs whose
    versions or models differ."""
    machine.then("ok", behaviour)
    tested = test(machine)
    assert tested.report.stopped is Stopped.COMPLETE
    runs = jobs.runs(connect(), tested.job_id)
    assert [(r.harness_version, r.model) for r in runs][:2] == [
        ("9.9.9", MODEL), ("9.9.10", MODEL) if "version" in behaviour else ("9.9.9", "model-y")]


CALENDAR, WIKI = "mcp__plugin_calendar_calendar-tools__", "mcp__plugin_agents-hub_wiki__"


def session_offering(machine: Machine, session: str, tools: list[str], version: str = "9.9.9") -> None:
    """A session of yours in the real home, on harness ``version``, whose deferred-tool listing named ``tools``."""
    def line(kind: str, **fields: object) -> dict:
        return {"type": kind, "sessionId": session, "cwd": str(machine.project), "version": version,
                "timestamp": "2026-10-03T09:00:00.000Z", "isSidechain": False, **fields}

    write(machine.adapter.home_dir() / ".claude" / "projects" / project_slug(machine.project) / f"{session}.jsonl",
          "\n".join(json.dumps(r) for r in [
              line("user", message={"role": "user", "content": "hello"}),
              line("attachment", attachment={"type": "deferred_tools_delta", "addedNames": tools, "addedLines": tools}),
              line("assistant", message={"role": "assistant", "model": MODEL, "content": [], "usage": {
                  "input_tokens": 5, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}})]) + "\n")


def test_after_a_launcher_update_a_new_job_still_removes_the_tools_older_sessions_offered(
        machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    session_offering(machine, "s-1", [CALENDAR + "calendar_book_beta", CALENDAR + "calendar_list_beta"])
    session_offering(machine, "s-0", [WIKI + "wiki_upsert"], version="9.9.8")  # an older version still counts
    launcher_updated(machine, monkeypatch, "9.9.10")  # no session has run the new version yet
    db = connect()
    removed = (WIKI + "wiki_upsert", CALENDAR + "calendar_book_beta")
    assert acting_tools(db, machine.adapter, machine.project) == removed
    assert [(v.name, v.removed) for v in machine.adapter.tool_verdicts(
        read_config(machine.adapter, machine.project, sessions=10))] == [
        (WIKI + "wiki_upsert", True), (CALENDAR + "calendar_book_beta", True), (CALENDAR + "calendar_list_beta", False)]
    tested = test(machine, db)
    assert jobs.load(db, tested.job_id).harness_version == "9.9.10"
    assert job_removed_tools(db, tested.job_id) == removed
    assert all(c["disallowed"] == list(removed) for c in machine.calls())


def test_mcp_tools_that_act_outside_the_machine_are_removed_from_every_run_and_frozen_with_the_job(
        machine: Machine) -> None:
    session_offering(machine, "s-1", [CALENDAR + "calendar_book_beta", CALENDAR + "calendar_list_beta",
                                      WIKI + "wiki_upsert", WIKI + "wiki_read", "mcp__x__frobnicate", "WebFetch"])
    removed = (WIKI + "wiki_upsert", CALENDAR + "calendar_book_beta", "mcp__x__frobnicate")  # unknown verb: removed
    db = connect()
    assert acting_tools(db, machine.adapter, machine.project) == removed  # what onboarding would show
    machine.then("ok", *["server_error"] * 4)
    with pytest.raises(RetriesExhausted):
        test(machine, db)
    (job_id,) = db.execute("SELECT job_id FROM jobs").fetchone()
    assert job_removed_tools(db, job_id) == removed
    session_offering(machine, "s-2", [CALENDAR + "calendar_accept"])  # a tool seen only after the job was created
    assert CALENDAR + "calendar_accept" in acting_tools(db, machine.adapter, machine.project)
    resumed = test(machine, db, job_id=job_id)
    assert resumed.report.stopped is Stopped.COMPLETE
    calls = machine.calls()
    assert len(calls) == 10 and all(c["disallowed"] == list(removed) for c in calls)  # resumes use the frozen list
    assert all(c["argv"][-len(removed) - 1] == "--disallowedTools" for c in calls)  # last: it takes every word after
    assert all("--permission-mode" not in c["argv"] for c in calls)


def test_a_list_given_when_the_job_is_created_overrides_the_adapters_choice(machine: Machine) -> None:
    session_offering(machine, "s-1", [CALENDAR + "calendar_book_beta"])
    tested = test(machine, removed_tools=["mcp__a__list_things", "mcp__a__list_things"])
    assert job_removed_tools(connect(), tested.job_id) == ("mcp__a__list_things",)
    assert {tuple(c["disallowed"]) for c in machine.calls()} == {("mcp__a__list_things",)}
    machine.log.unlink()
    nothing = test(machine, removed_tools=[])  # you kept every tool
    assert job_removed_tools(connect(), nothing.job_id) == () and {c["disallowed"] for c in machine.calls()} == {None}


def test_the_ceiling_counts_recorded_runs_and_a_retry_launches_past_it(machine: Machine) -> None:
    machine.then("ok", "server_error")
    tested = test(machine, run_ceiling=2)
    assert (tested.report.stopped, tested.report.recorded, tested.report.launched) == (Stopped.CEILING, 2, 3)
    assert jobs.load(connect(), tested.job_id).status is JobStatus.CEILING


def test_a_timeout_is_an_outcome_and_one_with_an_api_error_is_not(machine: Machine) -> None:
    machine.then("sleep:20", "hang:20")
    tested = test(machine, plan=stability_plan([("s1", "Fix the failing test")], runs=3), timeout_s=1)
    runs = jobs.runs(connect(), tested.job_id)
    assert [r.outcome for r in runs] == ["timeout", "timeout", "ok"]
    assert Trace.from_dict(json.loads(runs[0].trace)).actions == ()  # the prompt was written, no call yet
    assert runs[1].trace == "null"  # killed before the harness wrote anything


def test_a_process_a_finished_run_leaves_behind_neither_holds_it_up_nor_outlives_it(machine: Machine) -> None:
    machine.then("linger")
    started = time.monotonic()
    tested = test(machine, plan=stability_plan([("s1", "Fix the failing test")], runs=3), timeout_s=20)
    assert time.monotonic() - started < 15
    assert [r.outcome for r in jobs.runs(connect(), tested.job_id)] == ["ok"] * 3
    pid = int(Path(str(machine.log) + ".linger").read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:  # not our child (launchd reaps it): only its existence can be checked
            break
        time.sleep(0.05)
    else:
        os.kill(pid, signal.SIGKILL)
        raise AssertionError("the run's leftover process was not stopped")


def test_errors_crashes_and_denials_are_outcomes(machine: Machine) -> None:
    machine.then("error", "crash", "deny")
    tested = test(machine, plan=stability_plan([("s1", "Fix the failing test")], runs=3))
    runs = jobs.runs(connect(), tested.job_id)
    assert [r.outcome for r in runs] == ["error:error_max_turns", "crash", "ok"]
    (action,) = Trace.from_dict(json.loads(runs[2].trace)).actions
    assert (action.tool, action.denied, action.error) == ("Bash", True, True)  # named in permission_denials


def test_a_harness_that_writes_no_transcript_pauses_the_job(machine: Machine) -> None:
    machine.then("no-transcript")
    with pytest.raises(HarnessFailed, match="boom"):
        test(machine)


def test_a_baseline_that_reads_the_real_home_stops_the_job_before_any_run(tmp_path: Path, machine: Machine,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """agentlint's home inside the real home, which has a ~/.claude/CLAUDE.md: the run directory's ancestor walk
    would read it as a project file too. The baseline check says so, and nothing runs."""
    monkeypatch.setenv("AGENTLINT_HOME", str(machine.adapter.home_dir() / ".agentlint"))
    db = connect()
    with pytest.raises(BaselineMismatch, match="read from the real home"):
        test(machine, db)
    assert machine.calls() == []
    (job,) = db.execute("SELECT job_id, launches FROM jobs").fetchall()
    assert job[1] == 0 and leftovers(job[0]) == []


def test_a_project_hook_running_the_projects_code_stops_the_job_before_any_run(machine: Machine) -> None:
    write(machine.project / ".claude" / "settings.json", json.dumps({"hooks": {"PreToolUse": [
        {"hooks": [{"type": "command", "command": '"$CLAUDE_PROJECT_DIR"/tools/guard.sh'}]}]}}))
    write(machine.project / "tools" / "guard.sh", "#!/bin/sh\nexit 2\n")
    with pytest.raises(BaselineMismatch, match="tools/guard.sh"):
        test(machine)
    assert machine.calls() == []


def test_a_project_mcp_server_running_the_projects_code_stops_the_job_before_any_run(machine: Machine) -> None:
    write(machine.project / ".mcp.json", json.dumps({"mcpServers": {"guard": {"command": "python3",
                                                                              "args": ["tools/server.py"]}}}))
    write(machine.project / "tools" / "server.py", "print('serving')\n")
    with pytest.raises(BaselineMismatch, match="MCP server guard .* runs tools/server.py"):
        test(machine)
    assert machine.calls() == []


@pytest.mark.parametrize("where", ["mcp", "hook"])
def test_a_project_server_or_hook_running_a_bare_file_of_the_project_stops_the_job_before_any_run(
        machine: Machine, where: str) -> None:
    """``python3 server.py``: the file is in the project's root, where the server or hook starts, and not in the run's
    directory, which holds the configuration only (D1)."""
    write(machine.project / "server.py", "print('serving')\n")
    if where == "mcp":
        write(machine.project / ".mcp.json", json.dumps({"mcpServers": {"local": {"command": "python3",
                                                                                  "args": ["server.py"]}}}))
    else:
        write(machine.project / ".claude" / "settings.json", json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": "python3 server.py"}]}]}}))
    with pytest.raises(BaselineMismatch, match="not placed: .* runs server.py"):
        test(machine)
    assert machine.calls() == []


def test_an_ablation_run_is_verified_and_runs_without_its_target(machine: Machine) -> None:
    t = target(reading(machine.adapter, machine.project), "/.claude/CLAUDE.md", "section:/Testing")
    plan = Plan((Comparison("c1", Phase.ABLATION, "s1", "Fix the failing test", t.id, 1),))
    tested = test(machine, plan=plan)
    assert tested.report.recorded == 2
    baseline, without = machine.calls()
    assert "# Testing" in baseline["user_md"] and "# Testing" not in without["user_md"]


CHILD = """
import sys
from pathlib import Path

from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
from agentlint.adapters.claude_code.discovery import Paths
from agentlint.adapters.claude_code.launch import ClaudeCodeLauncher
from agentlint.runner.execute import run_test
from agentlint.runner.plan import stability_plan
from agentlint.store import connect

from agentlint.runner import engine

home, managed, install, project = map(Path, sys.argv[1:5])
ceiling, counter = int(sys.argv[5]), getattr(engine, sys.argv[6])
plan = stability_plan([("s1", "Fix the failing test"), ("s2", "Add logging to the parser")], runs=3)
run_test(connect(), ClaudeCodeAdapter(Paths(home, managed, install)), ClaudeCodeLauncher(grace_s=1), project=project,
         model="model-x", plan=plan, run_ceiling=ceiling, timeout_s=60, counter=counter)
"""


def alive(pid: int) -> bool:
    """Whether a process exists (not our child: launchd reaps it once its runner is gone)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def gone_within(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


def start_runner(tmp_path: Path, machine: Machine, ceiling: int = 100,
                 counter: str = "recorded_runs") -> subprocess.Popen:
    """A runner in a child process, stopped at its third run (``sleep:30``) once that run's harness has started."""
    machine.then("ok", "ok", "sleep:30")
    paths = machine.adapter.paths
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    child = subprocess.Popen([sys.executable, str(script), str(paths.home), str(paths.managed_settings),
                              str(paths.install_dir), str(machine.project), str(ceiling), counter],
                             env=os.environ.copy(),
                             stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 60
    while len(machine.calls()) < 3:
        if child.poll() is not None:
            raise AssertionError("the runner ended before it could be killed")
        if time.monotonic() > deadline:
            child.kill()
            raise AssertionError("the runner never got that far")
        time.sleep(0.02)
    time.sleep(0.3)  # the harness's start recorded
    return child


def test_a_runner_killed_mid_run_resumes_stopping_and_sweeping_what_it_left(tmp_path: Path, machine: Machine,
                                                                           agentlint_home: Path) -> None:
    child = start_runner(tmp_path, machine)
    orphan = machine.calls()[2]["pid"]
    try:
        os.kill(child.pid, signal.SIGKILL)  # nothing in the runner gets to clean up
        child.wait()
        assert alive(orphan)  # the harness run outlived its runner, in a session of its own
        db = connect()
        (job_id,) = db.execute("SELECT job_id FROM jobs").fetchone()
        killed = jobs.load(db, job_id).in_flight
        assert killed == "cmp-0001:baseline:2" and "cmp-0001.baseline.2" in leftovers(job_id)
        resumed = test(machine, db, job_id=job_id)
        assert resumed.stopped == 1 and gone_within(orphan, 5)  # the resume stopped it before its first launch
        assert resumed.report.interrupted == (killed,) and resumed.report.messages() == ["1 run was interrupted"]
        assert resumed.swept >= 1 and leftovers(job_id) == []
        assert resumed.report.stopped is Stopped.COMPLETE and jobs.recorded_count(db, job_id) == 6
        assert jobs.load(db, job_id).launches == 7
        assert all(c["pid"] != orphan for c in machine.calls()[3:])
    finally:
        child.kill()
        if alive(orphan):
            os.kill(orphan, signal.SIGKILL)


def test_a_resume_that_closes_the_job_at_its_ceiling_stops_and_sweeps_what_the_killed_runner_left(
        tmp_path: Path, machine: Machine, agentlint_home: Path) -> None:
    """Counting launches, the killed runner's third launch was the job's last: the resume closes the job at its ceiling
    before any launch, so no ``prepare`` runs — yet the orphan harness is stopped, its directory swept, and both
    reported."""
    child = start_runner(tmp_path, machine, ceiling=3, counter="launches")
    orphan = machine.calls()[2]["pid"]
    try:
        os.kill(child.pid, signal.SIGKILL)
        child.wait()
        assert alive(orphan)
        db = connect()
        (job_id,) = db.execute("SELECT job_id FROM jobs").fetchone()
        assert "cmp-0001.baseline.2" in leftovers(job_id)
        resumed = test(machine, db, job_id=job_id, counter=engine.launches)
        assert resumed.report.stopped is Stopped.CEILING and resumed.report.launched == 0
        assert resumed.report.not_rerun == ("cmp-0001:baseline:2",) and resumed.baseline is None
        assert resumed.stopped == 1 and gone_within(orphan, 5)
        assert resumed.swept >= 1 and resumed.undeleted == ()
        assert jobs.load(db, job_id).status is JobStatus.CEILING and not (runs_root() / job_id).exists()
        assert len(machine.calls()) == 3  # nothing launched by the resume
    finally:
        child.kill()
        if alive(orphan):
            os.kill(orphan, signal.SIGKILL)


def test_a_runner_sent_sigterm_stops_its_run_on_the_way_out(tmp_path: Path, machine: Machine,
                                                            agentlint_home: Path) -> None:
    child = start_runner(tmp_path, machine)
    orphan = machine.calls()[2]["pid"]
    try:
        os.kill(child.pid, signal.SIGTERM)
        assert child.wait(timeout=20) != 0
        assert gone_within(orphan, 5)  # stopped by the runner itself, not left for the resume
        db = connect()
        (job_id,) = db.execute("SELECT job_id FROM jobs").fetchone()
        assert jobs.load(db, job_id).in_flight == "cmp-0001:baseline:2"  # an interruption, reported by the resume
        resumed = test(machine, db, job_id=job_id)
        assert resumed.stopped == 0 and resumed.report.interrupted == ("cmp-0001:baseline:2",)
        assert resumed.report.stopped is Stopped.COMPLETE and jobs.recorded_count(db, job_id) == 6
    finally:
        child.kill()
        if alive(orphan):
            os.kill(orphan, signal.SIGKILL)


def test_a_left_running_record_whose_pid_now_names_another_process_is_left_alone(tmp_path: Path) -> None:
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        run = tmp_path / "job" / "cmp-0001.baseline.0"
        run.mkdir(parents=True)
        record_running(run / RUNNING, sleeper.pid)
        recorded = json.loads((run / RUNNING).read_text())
        assert recorded["group"] == sleeper.pid
        (run / RUNNING).write_text(json.dumps({**recorded, "started": "ps:another process"}))  # a reused pid
        assert stop_left_running(tmp_path / "job", grace_s=0.2) == 0 and sleeper.poll() is None
        (run / RUNNING).write_text(json.dumps(recorded))
        assert stop_left_running(tmp_path / "job", grace_s=2) == 1
        assert sleeper.wait(timeout=5) != 0
    finally:
        sleeper.kill()
        sleeper.wait()


def failing_deletions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every background deletion fails, as when a signal stops the runner during run_test's final wait."""
    def refuse(path: Path) -> None:
        raise PermissionError(f"refused {path}")

    monkeypatch.setattr(execute, "remove_home", refuse)


def complete_job_with_leftovers(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> str:
    with monkeypatch.context() as patched:
        failing_deletions(patched)
        tested = test(machine)
    assert tested.report.stopped is Stopped.COMPLETE and len(tested.undeleted) == 7  # 6 runs and the baseline check
    assert all("PermissionError" in failure for failure in tested.undeleted)
    assert len(leftovers(tested.job_id)) == 7 and jobs.load(connect(), tested.job_id).status is JobStatus.COMPLETE
    return tested.job_id


def test_what_a_failed_deletion_left_of_a_completed_job_is_deleted_by_the_next_run_test(
        machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    """A completed job can't be resumed, so its own resume never sweeps it: the next run_test does, any job's."""
    done = complete_job_with_leftovers(machine, monkeypatch)
    db = connect()
    with pytest.raises(JobClosed):
        test(machine, db, job_id=done)  # refused, but what it left is gone
    assert leftovers(done) == [] and not (runs_root() / done).exists()
    again = complete_job_with_leftovers(machine, monkeypatch)
    other = test(machine, db)  # a new job sweeps the other job's leftovers before its own runs
    assert other.swept >= 7 and other.undeleted == ()
    assert not (runs_root() / again).exists() and not (runs_root() / other.job_id).exists()


def test_forget_deletes_every_jobs_run_directories_and_stops_what_a_killed_run_left_running(
        machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    done = complete_job_with_leftovers(machine, monkeypatch)
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        orphan = runs_root() / done / "cmp-0002.baseline.0"  # a harness a killed runner left running
        orphan.mkdir()
        record_running(orphan / RUNNING, sleeper.pid)
        unknown = runs_root() / "job-pruned-earlier" / TRASH / "abc"  # a job no longer in the database
        write(unknown / "home" / ".claude" / "projects" / "x" / "s.jsonl", "{}\n")
        db = connect()
        removed, undeleted = forget_runs(db, lambda: usage_store.forget(db, datetime.now(UTC)))
        assert removed == 0 and undeleted == ()
        assert jobs.load(db, done) is None and list(runs_root().iterdir()) == []
        assert sleeper.wait(timeout=15) != 0
    finally:
        sleeper.kill()
        sleeper.wait()


def test_the_one_forget_the_cli_and_the_ui_share_deletes_the_test_runs_and_names_what_it_could_not(
        machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentlint.forget import JobsRunning, forget_everything

    done = complete_job_with_leftovers(machine, monkeypatch)
    db = connect()
    jobs.claim(db, done, 4242, "ps:a live runner", lambda pid, started: True)
    with pytest.raises(JobsRunning):  # refused while a runner is alive: nothing deleted, its directories included
        forget_everything(db, datetime.now(UTC), [], stale=lambda pid, started: False)
    assert len(leftovers(done)) == 7 and jobs.load(db, done) is not None
    with monkeypatch.context() as patched:
        failing_deletions(patched)
        gone = forget_everything(db, datetime.now(UTC), [], stale=lambda pid, started: True)  # the runner died
    assert jobs.load(db, done) is None and leftovers(done)
    assert gone.undeleted and all("PermissionError" in failure for failure in gone.undeleted)
    again = forget_everything(db, datetime.now(UTC), [])  # the next forget sweeps what the last one couldn't
    assert again.undeleted == () and list(runs_root().iterdir()) == []


def test_salvage_deletes_what_the_jobs_runs_left(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    machine.then(*["server_error"] * 4)
    db = connect()
    with monkeypatch.context() as patched:
        failing_deletions(patched)
        with pytest.raises(RetriesExhausted):
            test(machine, db)
    (job_id,) = db.execute("SELECT job_id FROM jobs").fetchone()
    assert leftovers(job_id)
    statuses, undeleted = salvage(db, job_id)
    assert undeleted == () and statuses and jobs.load(db, job_id).status is JobStatus.SALVAGED
    assert not (runs_root() / job_id).exists()


def test_a_job_a_live_runner_holds_is_left_to_it(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    db = connect()
    machine.then(*["server_error"] * 4)
    with monkeypatch.context() as patched:
        failing_deletions(patched)
        with pytest.raises(RetriesExhausted):
            test(machine, db)
    (job_id,) = db.execute("SELECT job_id FROM jobs").fetchone()
    before = leftovers(job_id)
    jobs.claim(db, job_id, 4242, "ps:a live runner", lambda pid, started: True)
    cleaning = clean_runs(db, stale=lambda pid, started: False)  # its holder is alive
    assert cleaning.close(db) == () and (cleaning.swept, cleaning.stopped) == (0, 0)
    assert leftovers(job_id) == before and jobs.load(db, job_id).runner_pid == 4242
