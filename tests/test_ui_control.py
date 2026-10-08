"""The Settings screen's endpoints: what they read, what they write, and every value they refuse
(docs/tech_impl/11-ui.md, "Settings"). The scheduler is always the fake one: never launchd or crontab for real."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentlint import __version__, cli, settings
from agentlint.store import connect, home, jobs
from agentlint.store import state as state_store
from agentlint.ui.control import StoreControl, SystemScheduler
from agentlint.ui.server import UIServer

from .checks.test_findings_and_cli import PROJECT, FakeAdapter
from .fakes import Scheduler
from .test_ui_server import get, post, started

NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)


class Live:
    """A lock check that calls every holder alive, or none."""

    def __init__(self, alive: bool) -> None:
        self.alive = alive

    def __call__(self, pid: int, started: str) -> bool:  # stale?
        return not self.alive


@pytest.fixture
def scheduler(tmp_path: Path) -> SystemScheduler:
    return SystemScheduler(home(), home=tmp_path, platform="darwin", run=Scheduler())


@pytest.fixture
def control(scheduler: SystemScheduler) -> StoreControl:
    return StoreControl(connect, harnesses=["claude-code", "codex"], sources=[FakeAdapter()], scheduler=scheduler,
                        stale=Live(True))


@pytest.fixture
def served(control: StoreControl) -> Iterator[UIServer]:
    running = UIServer(lambda params: ({}, {}), "<html></html>", control=control)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    yield running
    running.shutdown()
    running.server_close()


def test_it_shows_every_setting_with_its_default_range_and_meaning(control: StoreControl) -> None:
    answer = control.settings()
    by_key = {entry["key"]: entry for entry in answer["settings"]}
    tests = {entry["key"]: entry for entry in answer["test_settings"]}
    assert list(by_key) == list(settings.GROUPS["reading"]) and list(tests) == list(settings.GROUPS["tests"])
    assert [*by_key, *tests] == sorted(settings.KEYS, key=[*by_key, *tests].index) and len(settings.KEYS) == 11
    assert by_key["keep_days"] == {"key": "keep_days", "group": "reading", "value": 183, "default": 183, "set": False,
                                   "range": [1, 36_500], "meaning": settings.MEANING["keep_days"], "problem": None,
                                   "note": None}
    assert tests["situations_per_target"]["range"] == [1, 10] and tests["situations_per_target"]["value"] == 3
    assert "runs_per_side" not in tests  # one run count for every step (the user, 2026-10-07)
    assert (tests["runs_at_once"]["value"], tests["runs_at_once"]["range"]) == (1, [1, 8])  # one at a time by default
    assert "runs" not in tests  # max runs per test is gone: a test's size is what you tick (2026-10-05)
    assert tests["model"]["kind"] == "model" and tests["model"]["value"] == ""
    assert tests["keep_tools"]["kind"] == tests["remove_tools"]["kind"] == "tools"
    assert by_key["harnesses"]["choices"] == ["claude-code", "codex"] and by_key["harnesses"]["value"] == []
    assert answer["sync"] == {"installed": False, "scheduler": "launchd", "where": None, "time": "13:07", "error": None,
                              "last": None}
    assert answer["archive"] == {"transcripts": 0, "bytes_written": 0, "bytes_stored": 0}
    assert answer["store"] == str(home()) and answer["home_dir"] == str(Path.home())  # the page shows both as ~
    assert answer["version"] == __version__ and answer["first_run"] is True
    assert answer["attention"] == [] and answer["jobs_running"] == []


def test_a_tests_setting_is_saved_and_listed_with_the_tests(served: UIServer) -> None:
    status, answer, _ = post(served, "/api/settings", {"key": "remove_tools", "value": ["WebFetch", "mcp__m__send"]})
    tests = {e["key"]: e for e in answer["test_settings"]}
    assert status == 200 and tests["remove_tools"]["value"] == ["WebFetch", "mcp__m__send"] and tests["remove_tools"]["set"]
    assert post(served, "/api/settings", {"key": "model", "value": "claude-opus-5-5[1m]"})[0] == 200
    assert settings.load(connect()).model == "claude-opus-5-5[1m]"
    assert "remove_tools" not in {e["key"] for e in answer["settings"]}  # Reading and keeping lists its own only


def test_a_setting_is_saved_and_reset_and_a_bad_one_refused_with_why(served: UIServer) -> None:
    status, answer, _ = post(served, "/api/settings", {"key": "keep_days", "value": 30})
    assert status == 200 and {e["key"]: e for e in answer["settings"]}["keep_days"]["set"] is True
    assert settings.load(connect()).keep_days == 30
    for body, reason in (
        ({"key": "keep_days", "value": 0}, "keep_days must be a whole number from 1 to 36500"),
        ({"key": "keep_days", "value": "30"}, "whole number"),
        ({"key": "keep_days", "value": True}, "whole number"),
        ({"key": "harnesses", "value": ["Claude Code"]}, "harness names"),
        ({"key": "nope", "value": 1}, "no setting called 'nope'"),
        ({"value": 1}, "name the setting"),
        ({"key": "keep_days"}, "give the value"),
        ({"key": "keep_days", "value": 30, "also": 1}, "unexpected field: also"),
        ({"key": "keep_days", "reset": False}, "reset must be true"),
        ({"key": "keep_days", "reset": True, "value": 3}, "unexpected field: value"),
        ({"key": "runs_per_situation", "value": 2}, "runs_per_situation must be a whole number from 3 to 20"),
        ({"key": "run_timeout_s", "value": 5000}, "from 30 to 3600"),
        ({"key": "model", "value": ""}, "model must be a model's name"),
        ({"key": "model", "value": ["opus"]}, "model must be a model's name"),
        ({"key": "remove_tools", "value": ["--model"]}, "none starting with -"),
        ({"key": "keep_tools", "value": "mcp__a__b"}, "a list of tool names"),
    ):
        status, answer, _ = post(served, "/api/settings", body)
        assert status == 400 and reason in answer["error"], body
    assert post(served, "/api/settings", {"key": "keep_days", "value": 0})[1] == {
        "error": "keep_days must be a whole number from 1 to 36500"}  # said once, as the page shows it
    assert settings.load(connect()).keep_days == 30  # nothing refused was written
    status, answer, _ = post(served, "/api/settings", {"key": "keep_days", "reset": True})
    assert status == 200 and settings.read(connect()) == (settings.DEFAULTS, {})
    for names in (["nosuch"], ["codex", "opencode"]):  # only the choices GET offers: never one that turns syncs away
        status, answer, _ = post(served, "/api/settings", {"key": "harnesses", "value": names})
        assert status == 400 and answer["error"].startswith(f"agentlint can't read '{names[-1]}'; it reads claude-code, codex")
    assert settings.read(connect()) == (settings.DEFAULTS, {})
    assert post(served, "/api/settings", {"key": "harnesses", "value": ["codex"]})[0] == 200
    settings.store(connect(), "harnesses", ["codex", "opencode"])  # as `config set` may write it, with a warning
    assert {e["key"]: e for e in control_answer(served)["settings"]}["harnesses"]["note"].startswith(
        "agentlint can't read opencode yet")


def control_answer(served: UIServer) -> dict:
    return served.control.settings()


def test_the_daily_sync_switches_on_and_off_in_the_fake_scheduler(served: UIServer, scheduler: SystemScheduler) -> None:
    status, answer, _ = post(served, "/api/schedule", {"on": True})
    assert status == 200 and answer["sync"]["installed"] and answer["sync"]["where"].endswith("dev.agentlint.sync.plist")
    assert ["launchctl", "bootstrap"] == scheduler._where["run"].calls[-1][:2]
    assert post(served, "/api/schedule", {"on": False})[1]["sync"]["installed"] is False
    for body in ({"on": "yes"}, {}, {"on": True, "at": "09:00"}):
        assert post(served, "/api/schedule", body)[0] == 400
    scheduler._where["run"].refuse = True
    status, answer, _ = post(served, "/api/schedule", {"on": True})
    assert status == 500 and "denied" in answer["error"]


def test_the_last_sync_is_recorded_and_shown_failures_too(monkeypatch: pytest.MonkeyPatch, control: StoreControl) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    assert cli.main(["sync", "--quiet"]) == cli.EXIT_OK
    last = control.settings()["sync"]["last"]
    assert last["ok"] is True and last["changed"] == 0 and last["turns"] == 0 and last["at"]

    def broken(*args, **kwargs):
        raise OSError("the transcripts' disk is gone")

    monkeypatch.setattr(cli, "_import", broken)
    assert cli.main(["sync", "--quiet"]) == cli.EXIT_TOOL_ERROR
    last = control.settings()["sync"]["last"]
    assert last["ok"] is False and "the transcripts' disk is gone" in last["error"]


def test_a_sync_turned_away_before_it_starts_is_recorded_as_failed(monkeypatch: pytest.MonkeyPatch,
                                                                  control: StoreControl) -> None:
    """The scheduler's daily job must not fail every day while Settings shows the last good sync."""
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)

    def last() -> dict:
        return control.settings()["sync"]["last"]

    def good() -> None:
        assert cli.main(["sync", "--quiet"]) == cli.EXIT_OK and last()["ok"] is True

    good()
    connect().execute("INSERT INTO settings (key, value, updated) VALUES ('keep_days', '40000', 'x')")
    assert cli.main(["sync", "--quiet"]) == cli.EXIT_TOOL_ERROR  # a stored value that can't be used
    assert last()["ok"] is False and "keep_days must be a whole number" in last()["error"]
    connect().execute("DELETE FROM settings")
    good()
    settings.store(connect(), "harnesses", ["nosuch"])
    assert cli.main(["sync", "--quiet"]) == cli.EXIT_TOOL_ERROR  # a harnesses setting naming none it reads
    assert last()["ok"] is False and "names none agentlint can read" in last()["error"]
    connect().execute("DELETE FROM settings")
    good()
    home().joinpath("config.toml").write_text("runs = 600x\n")
    assert cli.main(["sync", "--quiet"]) == cli.EXIT_TOOL_ERROR  # a config.toml that can't be read
    assert last()["ok"] is False and "couldn't be read" in last()["error"]
    home().joinpath("config.toml").unlink()
    good()

    def none_installed():
        raise cli.ToolError("no harness agentlint reads is installed")

    monkeypatch.setattr(cli, "_adapter", none_installed)
    assert cli.main(["sync", "--quiet"]) == cli.EXIT_TOOL_ERROR
    assert last()["ok"] is False and "no harness agentlint reads is installed" in last()["error"]
    connect().execute("INSERT INTO settings (key, value, updated) VALUES ('keep_days', '40000', 'x')")
    before = last()
    assert cli.main(["sync", "--daily"]) == cli.EXIT_TOOL_ERROR and last() == before  # --daily is no sync


def _kept(db) -> None:
    db.execute("INSERT INTO observations (session_id, turn, at, project, harness_version, model, interactive, sidechain, "
               "entities) VALUES ('s', 0, '2026-10-01T00:00:00.000000+00:00', '/p', '1', 'm', 1, 0, '[]')")
    db.execute("INSERT INTO suppressions (id, reason, at) VALUES ('abcd1234abcd1234', 'mine', 't')")
    jobs.create(db, job_id="j1", project="/p", fingerprint="fp", harness_version="1", model="m", run_ceiling=5,
                timeout_s=60, plan="{}", now=NOW)
    jobs.claim(db, "j1", 4242, "ps:started", lambda pid, started: True)


def test_forget_needs_the_typed_word_and_no_live_runner(served: UIServer, control: StoreControl) -> None:
    db = connect()
    _kept(db)
    settings.store(db, "keep_days", 30)
    for body in ({}, {"confirm": "Forget"}, {"confirm": "yes"}, {"confirm": True}, {"confirm": "forget", "also": 1}):
        status, answer, _ = post(served, "/api/forget", body)
        assert status == 400, body
    assert "type forget to confirm" in post(served, "/api/forget", {"confirm": "no"})[1]["error"]
    assert control.settings()["jobs_running"] == [{"job_id": "j1", "pid": 4242}]
    status, answer, _ = post(served, "/api/forget", {"confirm": "forget"})
    assert status == 409 and "j1 (process 4242)" in answer["error"] and "stop it first" in answer["error"]
    assert db.execute("SELECT count(*) FROM observations").fetchone()[0] == 1  # nothing deleted
    control.stale = Live(False)  # the runner died: its lock is stale and blocks nothing
    status, answer, _ = post(served, "/api/forget", {"confirm": "forget"})
    assert status == 200 and answer["turns"] == 1 and answer["settings"]["jobs_running"] == []
    for table, left in (("observations", 0), ("jobs", 0), ("suppressions", 1)):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == left, table
    assert settings.load(db).keep_days == 30  # settings stay


def test_the_cli_and_the_ui_share_one_forget(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from agentlint import forget

    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    _kept(connect())
    monkeypatch.setattr(forget.lock, "stale", lambda pid, started: False)  # process 4242 is alive, as far as cli knows
    assert cli.main(["forget", "--yes"]) == cli.EXIT_TOOL_ERROR
    assert "nothing deleted: a test job is running — j1 (process 4242)" in capsys.readouterr().err
    assert connect().execute("SELECT count(*) FROM observations").fetchone()[0] == 1


def test_both_forgets_delete_the_test_runs_directories_and_name_those_they_could_not(
        served: UIServer, control: StoreControl, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    from agentlint.runner import execute

    def refuse(path: Path) -> None:
        raise PermissionError(f"refused {path}")

    def leave_a_run() -> Path:
        run = execute.runs_root() / "job-forgotten" / "cmp-0001.baseline.0"  # a whole variant home, its transcript in it
        (run / "home" / "projects" / "p").mkdir(parents=True, exist_ok=True)
        (run / "home" / "projects" / "p" / "s.jsonl").write_text("{}\n")
        return run

    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    control.stale = Live(False)
    leave_a_run()
    with monkeypatch.context() as patched:
        patched.setattr(execute, "remove_home", refuse)
        status, answer, _ = post(served, "/api/forget", {"confirm": "forget"})
        assert status == 200 and len(answer["undeleted"]) == 1 and "PermissionError" in answer["undeleted"][0]
        assert cli.main(["forget", "--yes"]) == cli.EXIT_OK  # still there: the CLI says so too
        assert "could not delete a test run's directory" in capsys.readouterr().err
    assert cli.main(["forget", "--yes"]) == cli.EXIT_OK and "could not" not in capsys.readouterr().err
    assert not execute.runs_root().exists() or list(execute.runs_root().iterdir()) == []
    leave_a_run()
    status, answer, _ = post(served, "/api/forget", {"confirm": "forget"})
    assert status == 200 and answer["undeleted"] == [] and list(execute.runs_root().iterdir()) == []


def test_the_first_run_panel_is_closed_once(served: UIServer, control: StoreControl) -> None:
    assert post(served, "/api/welcomed", {"seen": True})[0] == 400  # takes nothing
    assert control.settings()["first_run"] is True
    assert post(served, "/api/welcomed", {})[:2] == (200, {"first_run": False})
    assert control.settings()["first_run"] is False
    assert state_store.get(connect(), state_store.WELCOMED)[0] == {"by": "ui"}
    assert post(served, "/api/welcomed", {})[0] == 200  # again: nothing changes


def test_agentlint_ui_serves_settings_and_starts_when_a_setting_is_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentlint.ui.server import Unreadable

    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: FakeAdapter})
    monkeypatch.setattr(SystemScheduler, "installed", lambda self: None)  # never the real launchd or crontab
    server = started(monkeypatch)
    answer = server.control.settings()
    assert {e["key"] for e in answer["settings"] + answer["test_settings"]} == set(settings.KEYS)
    assert server.control.settings()["sync"]["scheduler"] in ("launchd", "cron")
    connect().execute("INSERT INTO settings (key, value, updated) VALUES ('sessions', '-3', 'x')")
    server = started(monkeypatch)  # a stored value that can't be used: it starts, and Settings says why
    assert any("sessions must be a whole number" in why for why in server.control.settings()["attention"])
    connect().execute("DELETE FROM settings")
    assert cli.main(["config", "set", "harnesses", "codex"]) == cli.EXIT_OK
    server = started(monkeypatch)  # a harnesses setting naming none it can read: it starts on Settings too
    [why] = server.control.settings()["attention"]
    assert "names no harness agentlint can read" in why
    with pytest.raises(Unreadable, match="names no harness"):
        server.build({})
    assert server.catalog.scopes({}) == {}
    for answer in (server.records.snapshots, server.records.diff, server.records.diagnostics):  # What changed and
        with pytest.raises(Unreadable, match="names no harness"):  # Diagnostics say why, as the report does
            answer({})


def test_a_config_toml_fixed_while_the_ui_runs_is_imported_at_the_next_request(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Wave 1 kept the old parse error, and opened every page load on Settings, until a command imported the file."""
    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: FakeAdapter})
    monkeypatch.setattr(SystemScheduler, "installed", lambda self: None)  # never the real launchd or crontab
    home().mkdir(parents=True, exist_ok=True)
    legacy = home() / "config.toml"
    legacy.write_text("keep_days = 3650\nruns = 600x\n")
    server = started(monkeypatch)
    [why] = server.control.settings()["attention"]
    assert "couldn't be read" in why and "line 2" in why
    legacy.write_text("keep_days = 3650\nsituations_per_target = [\n")  # still broken, another way: the notice says how it is now
    [why] = server.control.settings()["attention"]
    assert "couldn't be read" in why and "line 2" not in why and legacy.exists()
    capsys.readouterr()
    legacy.write_text("keep_days = 3650\nsituations_per_target = 6\n")  # fixed: imported and renamed at once, the notice gone
    answer = server.control.settings()
    assert answer["attention"] == [] and not legacy.exists() and (home() / "config.toml.migrated").exists()
    assert {e["key"]: e["value"] for e in answer["settings"]}["keep_days"] == 3650
    assert "your settings moved from" in capsys.readouterr().err  # said to the terminal, as a command says it
    legacy.write_text("situations_per_target = 6x\n")  # a new broken file: said, as the next command would refuse it
    [why] = server.control.settings()["attention"]
    assert "couldn't be read" in why and legacy.exists()
    legacy.write_text("situations_per_target = 7\n")  # and imported once readable; what the database holds already is kept
    assert server.control.settings()["attention"] == [] and settings.load(connect()).situations_per_target == 6
    assert (home() / "config.toml.migrated.2").exists()


def test_settings_over_http_need_the_token(served: UIServer) -> None:
    assert get(served, "/api/settings")[0] == 403
    status, body, _ = get(served, "/api/settings", token=served.token)
    assert status == 200 and b'"keep_days"' in body
    assert post(served, "/api/settings", {"key": "keep_days", "value": 5}, token=None)[0] == 403
    assert settings.read(connect())[0].keep_days == 183


def test_a_ui_started_in_a_project_still_works_with_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: FakeAdapter})
    server = started(monkeypatch)
    assert server.build({})[0]["snapshot"]["cwd"] == str(PROJECT)
