"""The daily sync: a scheduler job outside the harness, installed and removed on request — never for real here."""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from agentlint import cli, schedule

from .fakes import Scheduler


def test_launchd_gets_a_daily_job_and_loses_it(tmp_path: Path) -> None:
    run = Scheduler()
    job = schedule.install(tmp_path / "store", home=tmp_path, platform="darwin", run=run)
    plist = plistlib.loads(Path(job.where).read_bytes())
    assert plist["Label"] == schedule.LABEL and plist["ProgramArguments"][-2:] == ["sync", "--quiet"]
    assert plist["StartCalendarInterval"] == {"Hour": schedule.HOUR, "Minute": schedule.MINUTE}
    assert run.calls[-1][:2] == ["launchctl", "bootstrap"]
    assert schedule.installed(home=tmp_path, platform="darwin", run=run) == job
    assert schedule.remove(home=tmp_path, platform="darwin", run=run) and not Path(job.where).exists()
    assert not schedule.remove(home=tmp_path, platform="darwin", run=run)


def test_a_refusing_launchd_leaves_nothing_behind(tmp_path: Path) -> None:
    run = Scheduler()
    run.refuse = True
    with pytest.raises(schedule.ScheduleError, match="denied"):
        schedule.install(tmp_path / "store", home=tmp_path, platform="darwin", run=run)
    assert not list((tmp_path / "Library" / "LaunchAgents").glob("*"))


def test_cron_gets_one_marked_line_and_keeps_the_rest(tmp_path: Path) -> None:
    run = Scheduler()
    run.crontab = "0 9 * * * backup\n"
    schedule.install(tmp_path / "store", home=tmp_path, platform="linux", run=run)
    schedule.install(tmp_path / "store", home=tmp_path, platform="linux", run=run)  # again: still one line
    lines = run.crontab.splitlines()
    assert lines[0] == "0 9 * * * backup" and sum(schedule.MARK in line for line in lines) == 1
    assert schedule.installed(home=tmp_path, platform="linux", run=run) is not None
    assert schedule.remove(home=tmp_path, platform="linux", run=run) and run.crontab == "0 9 * * * backup\n"


def test_cli_sync_and_forget(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from .checks.test_findings_and_cli import FakeAdapter

    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    monkeypatch.setattr(schedule, "installed", lambda **kwargs: None)
    assert cli.main(["sync"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "The archive keeps 0 transcripts" in out and "Daily sync: off" in out
    assert cli.main(["sync", "--quiet"]) == cli.EXIT_OK and capsys.readouterr().out == ""
    assert cli.main(["forget", "--yes"]) == cli.EXIT_OK
    assert "every archived transcript" in capsys.readouterr().out


def test_after_forget_a_check_reads_no_session_from_before_and_says_so(monkeypatch: pytest.MonkeyPatch,
                                                                      capsys: pytest.CaptureFixture[str]) -> None:
    """The fake harness's one session started 2026-10-01: forgotten after it, nothing of it is read back in — not into
    the reading stored, not into the findings — and the checks that need a session say why they said nothing."""
    import json

    from agentlint.store import connect
    from agentlint.store.snapshots import load, stored

    from .checks.test_findings_and_cli import PROJECT, FakeAdapter

    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    assert cli.main(["check", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    db = connect()
    assert load(db, str(PROJECT), stored(db, str(PROJECT))[0].fingerprint).sessions != ()
    db.close()
    assert cli.main(["forget", "--yes"]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["check", "--cwd", str(PROJECT), "--json"]) == cli.EXIT_OK
    said = json.loads(capsys.readouterr().out)["not_evaluated"]
    assert said["untraced-injection"].startswith("no session in this project since you forgot (")
    db = connect()
    [only] = stored(db, str(PROJECT))
    assert load(db, str(PROJECT), only.fingerprint).sessions == ()


def test_the_job_names_its_store_by_absolute_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # launchd starts a job in /, cron in $HOME: a relative store would be another one
    monkeypatch.delenv("AGENTLINT_HOME")  # and the job names its store even when the default is in use
    run = Scheduler()
    job = schedule.install(Path("al-home"), home=tmp_path, platform="darwin", run=run)
    plist = plistlib.loads(Path(job.where).read_bytes())
    store = str(tmp_path.resolve() / "al-home")
    assert plist["EnvironmentVariables"] == {"AGENTLINT_HOME": store}
    assert plist["StandardOutPath"] == plist["StandardErrorPath"] == f"{store}/sync.log"
    schedule.install(Path("al-home"), home=tmp_path, platform="linux", run=run)
    assert f"AGENTLINT_HOME={store} " in run.crontab and f">> {store}/sync.log" in run.crontab


def test_no_crontab_binary_means_no_daily_sync(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from .checks.test_findings_and_cli import FakeAdapter

    def missing(args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", args[0])

    assert schedule.installed(platform="linux", run=missing) is None
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    monkeypatch.setattr(schedule.installed, "__kwdefaults__", {"home": None, "platform": "linux", "run": missing})
    assert cli.main(["sync"]) == cli.EXIT_OK
    assert "Daily sync: off" in capsys.readouterr().out


def test_forget_without_a_terminal_deletes_nothing_and_says_how(monkeypatch: pytest.MonkeyPatch,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    def no_answer(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", no_answer)
    assert cli.main(["forget"]) == cli.EXIT_TOOL_ERROR
    err = capsys.readouterr().err
    assert "nothing deleted" in err and "agentlint forget --yes" in err
