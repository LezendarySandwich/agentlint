"""Your settings: rows in the agentlint database, `agentlint config` to edit them, flags over both."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentlint import cli, settings
from agentlint.harness import read_config as real_read_config
from agentlint.store import connect, home
from agentlint.store import state as state_store
from agentlint.store._time import encode

from .checks.test_findings_and_cli import PROJECT, FakeAdapter


def test_a_fresh_database_holds_no_setting_so_every_default_is_in_effect() -> None:
    db = connect()
    assert settings.read(db) == (settings.DEFAULTS, {})
    assert db.execute("SELECT count(*) FROM settings").fetchone()[0] == 0  # a default can still improve


def test_set_get_unset_like_git_config() -> None:
    db = connect()
    settings.set_value(db, "harnesses", "codex, opencode")
    settings.set_value(db, "sessions", "3")
    loaded = settings.load(db)
    assert loaded.harnesses == ("codex", "opencode") and loaded.sessions == 3
    assert loaded.configured == {"harnesses", "sessions"}
    stored = dict(db.execute("SELECT key, value FROM settings").fetchall())
    assert json.loads(stored["harnesses"]) == ["codex", "opencode"] and json.loads(stored["sessions"]) == 3
    settings.unset(db, "sessions")
    assert settings.load(db).sessions == 10 and "sessions" not in settings.load(db).configured


@pytest.mark.parametrize("key,raw,complaint", [
    ("sessions", "lots", "whole number"), ("keep_days", "0", "from 1"), ("nope", "1", "no setting called nope"),
    ("harnesses", "Claude Code", "harness names"), ("runs_per_situation", "2", "from 3 to 20"),
    ("run_timeout_s", "10", "from 30 to 3600"), ("model", "", "a model's name"), ("model", "a model", "a model's name"),
    ("model", "--danger", "a model's name"), ("remove_tools", "--model,x", "tool names"),
    ("keep_tools", "mcp__a__b, rm -rf", "tool names"),
])
def test_a_bad_value_is_refused_and_nothing_written(key: str, raw: str, complaint: str) -> None:
    db = connect()
    with pytest.raises(settings.SettingsError, match=complaint):
        settings.set_value(db, key, raw)
    assert db.execute("SELECT count(*) FROM settings").fetchone()[0] == 0


@pytest.mark.parametrize("value", [True, 10.0, "10", None, [10]])
def test_a_value_as_json_gives_it_is_held_to_its_type_and_range(value) -> None:
    db = connect()
    with pytest.raises(settings.SettingsError, match="sessions must be a whole number from 0 to 1000"):
        settings.store(db, "sessions", value)
    with pytest.raises(settings.SettingsError, match="harnesses must be a list"):
        settings.store(db, "harnesses", "claude-code")
    assert settings.store(db, "harnesses", ["claude-code", "claude-code"]) == ("claude-code",)


def test_a_stored_value_that_cannot_be_used_counts_as_its_default_and_is_named(capsys: pytest.CaptureFixture[str]) -> None:
    db = connect()
    db.execute("INSERT INTO settings (key, value, updated) VALUES ('sessions', '5000', 'x'), ('situations_per_target', 'not json', 'x'), "
               "('a_setting_from_another_version', '1', 'x')")
    loaded, problems = settings.read(db)
    assert loaded.sessions == 10 and loaded.situations_per_target == 3 and loaded.configured == frozenset()
    assert set(problems) == {"sessions", "situations_per_target"} and "from 0 to 1000" in problems["sessions"]
    with pytest.raises(settings.SettingsError):
        settings.load(db)
    assert cli.main(["check", "--cwd", str(PROJECT)]) == cli.EXIT_TOOL_ERROR  # refused, as a broken file was
    assert "agentlint config unset sessions" in capsys.readouterr().err  # the first by name
    assert cli.main(["config", "list"]) == cli.EXIT_OK  # and `config` still runs, to fix it
    listed = capsys.readouterr()
    assert "default — the stored value can't be used" in listed.out and "from 0 to 1000" in listed.err
    assert cli.main(["config", "unset", "situations_per_target"]) == cli.EXIT_OK and cli.main(["config", "set", "sessions", "4"]) == cli.EXIT_OK
    assert settings.read(db) == (settings.Settings(sessions=4), {})


def test_a_stored_value_that_cannot_be_used_names_its_key_once_in_the_words_the_page_shows() -> None:
    db = connect()
    db.execute("""INSERT INTO settings (key, value, updated) VALUES ('sessions', '5000', 'x'), ('keep_days', '"lots"', 'x'),
               ('harnesses', '"claude-code"', 'x')""")
    assert settings.read(db)[1] == {
        "harnesses": "the stored harnesses must be a list of harness names: lowercase, words joined by -",
        "keep_days": "the stored keep_days must be a whole number from 1 to 36500",
        "sessions": "the stored sessions must be a whole number from 0 to 1000",
    }
    page = (Path(__file__).parents[1] / "ui" / "src" / "Settings.test.tsx").read_text(encoding="utf-8")
    assert '"the stored sessions must be a whole number from 0 to 1000"' in page  # its fixture is the server's text
    with pytest.raises(settings.SettingsError, match="^sessions on the command line must be a whole number from 0 to 1000$"):
        settings.Options.resolve(settings.DEFAULTS, sessions=5000)


def test_config_path_is_gone(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["config", "path"])
    assert "invalid choice: 'path'" in capsys.readouterr().err


def test_config_list_lines_up_its_columns_for_every_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["config", "set", "situations_per_target", "4"]) == cli.EXIT_OK
    long_value = "WebFetch, WebSearch, NotebookEdit"  # longer than the 27 characters the value column once was
    assert cli.main(["config", "set", "keep_tools", "WebFetch,WebSearch,NotebookEdit"]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["config", "list"]) == cli.EXIT_OK
    rows = capsys.readouterr().out.splitlines()
    assert [row.split()[0] for row in rows] == list(settings.KEYS)
    width = max(len(key) for key in settings.KEYS)
    assert width > 18  # situations_per_target is longer than the column once was
    assert len(long_value) > 27 and long_value in rows[list(settings.KEYS).index("keep_tools")]
    # Every row's value starts one space after the longest key, and its source one space after the longest value.
    assert {row.index(row.split()[1], len(row.split()[0])) for row in rows} == {width + 1}
    sources = {len(row) - len(row.rstrip().rsplit(" ", 1)[-1]) for row in rows if row.endswith(("set", "default"))}
    assert sources == {width + 1 + len(long_value) + 1}


def test_a_config_toml_is_imported_once_and_kept_renamed(capsys: pytest.CaptureFixture[str]) -> None:
    home().mkdir(parents=True, exist_ok=True)
    (home() / "config.toml").write_text("# a comment\nsessions = 4\nharnesses = ['claude-code']\nsurprise = 1\n")
    assert cli.main(["config", "list"]) == cli.EXIT_OK
    err = capsys.readouterr().err
    assert "imported sessions, harnesses" in err and "left out what is no setting: surprise" in err
    assert "config.toml.migrated" in err and "first run" not in err  # writing config.toml was the first run
    assert not (home() / "config.toml").exists() and "sessions = 4" in (home() / "config.toml.migrated").read_text()
    db = connect()
    assert settings.load(db) == settings.Settings(harnesses=("claude-code",), sessions=4)
    assert cli.main(["config", "list"]) == cli.EXIT_OK
    assert "moved" not in capsys.readouterr().err  # once
    (home() / "config.toml").write_text("sessions = 9\nsituations_per_target = 7\n")  # another, later: what the database holds wins
    assert cli.main(["config", "get", "sessions"]) == cli.EXIT_OK
    out = capsys.readouterr()
    assert out.out.strip() == "4" and "config.toml.migrated.2" in out.err
    assert "imported situations_per_target; kept the database's sessions, set there already" in out.err  # never "imported sessions"
    assert settings.load(db).situations_per_target == 7 and (home() / "config.toml.migrated").exists()  # never deleted


def test_two_processes_importing_one_config_toml_at_once_import_it_once_and_the_second_says_nothing(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The scheduled sync starting while `agentlint ui` starts: before the lock, the second read the file before the
    first renamed it, then told you to rename a file already gone, calling the file's own value the database's."""
    import threading
    import time

    home().mkdir(parents=True, exist_ok=True)
    (home() / "config.toml").write_text("situations_per_target = 5\n")
    reading, go, reads = threading.Event(), threading.Event(), []
    real = Path.read_text

    def slow(self: Path, *args, **kwargs) -> str:
        if self.name == "config.toml":
            reads.append(threading.current_thread().name)
            reading.set()
            go.wait(5)  # the first stays between its read and its rename until the second has started
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", slow)
    results: dict[str, settings.Migration | None] = {}

    def run() -> None:
        db = connect()
        try:
            results[threading.current_thread().name] = settings.migrate(db)
        finally:
            db.close()

    first = threading.Thread(target=run, name="first")
    first.start()
    assert reading.wait(5)
    second = threading.Thread(target=run, name="second")
    second.start()
    time.sleep(0.3)  # long enough for the second to have looked for the file and read it, had it not waited
    assert reads == ["first"]
    go.set()
    first.join(10)
    second.join(10)
    assert "imported situations_per_target; the file is kept as config.toml.migrated." in results["first"].note
    assert results["second"] is None and reads == ["first"]  # it waited, then found no file: nothing to say
    assert sorted(path.name for path in home().glob("config.toml*")) == ["config.toml.migrated"]
    assert settings.load(connect()).situations_per_target == 5


def test_a_config_toml_gone_by_its_rename_is_never_a_note_to_rename_it(monkeypatch: pytest.MonkeyPatch) -> None:
    home().mkdir(parents=True, exist_ok=True)

    def vanished(self: Path, target: Path) -> None:
        self.unlink()  # someone else moved it while it was read
        raise FileNotFoundError(2, "No such file or directory", str(self))

    monkeypatch.setattr(Path, "rename", vanished)
    (home() / "config.toml").write_text("situations_per_target = 5\n")
    note = settings.migrate(connect()).note
    assert "imported situations_per_target; the file is gone already." in note and "yourself" not in note
    (home() / "config.toml").write_text("situations_per_target = 6\n")  # everything it sets, the database holds: nothing to say
    assert settings.migrate(connect()) is None and settings.load(connect()).situations_per_target == 5


def test_a_config_toml_of_comments_or_unknown_keys_says_which() -> None:
    home().mkdir(parents=True, exist_ok=True)
    (home() / "config.toml").write_text("# only a comment\n")
    assert "imported no settings (it set none)" in settings.migrate(connect()).note
    (home() / "config.toml").write_text("x = 1\n")
    note = settings.migrate(connect()).note
    assert "imported no settings; left out what is no setting: x" in note and "set none" not in note


def test_a_value_the_file_sets_that_cannot_be_used_is_stored_and_refused_never_dropped_for_its_default(
        capsys: pytest.CaptureFixture[str]) -> None:
    """keep_days = 99999 dropped for 183 would delete what you asked to keep: it is refused like any stored value."""
    home().mkdir(parents=True, exist_ok=True)
    (home() / "config.toml").write_text("keep_days = 99999\nsituations_per_target = 0\nusage_days = 7\n")
    assert cli.main(["config", "list"]) == cli.EXIT_OK
    err = capsys.readouterr().err
    assert "imported usage_days; imported, but can't be used until you change it" in err and ": keep_days, situations_per_target" in err
    current, problems = settings.read(connect())
    assert set(problems) == {"keep_days", "situations_per_target"} and current.usage_days == 7 and current.hold == settings.HOLD
    assert cli.main(["check", "--cwd", str(PROJECT)]) == cli.EXIT_TOOL_ERROR  # refused until fixed
    assert cli.main(["config", "set", "keep_days", "3650"]) == cli.EXIT_OK and cli.main(["config", "unset", "situations_per_target"]) == 0
    assert settings.read(connect()) == (settings.Settings(usage_days=7, keep_days=3650), {})


def _old_observation() -> None:
    db = connect()
    db.execute("INSERT INTO observations (session_id, turn, at, project, harness_version, model, interactive, sidechain, "
               "entities) VALUES ('s', 0, ?, '/p', '1', 'm', 1, 0, '[]')",
               (encode(datetime.now(UTC) - timedelta(days=300)),))
    db.close()


def _observations() -> int:
    return connect().execute("SELECT count(*) FROM observations").fetchone()[0]


def test_a_config_toml_that_wont_parse_is_left_in_place_and_nothing_is_deleted_by_age(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """At 1420dc1 a broken file made every command refuse; it must never make one prune at the default instead."""
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    _old_observation()
    home().mkdir(parents=True, exist_ok=True)
    (home() / "config.toml").write_text("keep_days = 3650\nsituations_per_target = 6x\n")
    for command in (["check", "--cwd", str(PROJECT)], ["usage"], ["sync", "--quiet"], ["show", "--cwd", str(PROJECT)]):
        assert cli.main(command) == cli.EXIT_TOOL_ERROR, command
        err = capsys.readouterr().err
        assert err.count("couldn't be read") == 1 and "nothing is deleted by age" in err and "first run" not in err
    assert _observations() == 1 and (home() / "config.toml").exists()  # kept where it is, unrenamed
    assert not (home() / "config.toml.migrated").exists() and settings.read(connect()) == (settings.DEFAULTS, {})
    assert cli.main(["config", "list"]) == cli.EXIT_OK  # `config` still runs
    (home() / "config.toml").write_text("keep_days = 3650\nsituations_per_target = 6\n")  # fixed: imported, then commands run
    assert cli.main(["check", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    assert settings.load(connect()).keep_days == 3650 and _observations() == 1  # 300 days is within 3650
    assert cli.main(["config", "set", "keep_days", "183"]) == cli.EXIT_OK
    assert cli.main(["check", "--cwd", str(PROJECT)]) == cli.EXIT_OK and _observations() == 0  # a real prune deletes it


def test_no_command_prunes_on_a_fallback_keep_days(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentlint.store import retention

    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    pruned: list[int] = []
    monkeypatch.setattr(retention, "prune", lambda db, now, keep_days: pruned.append(keep_days) or 0)
    held = settings.Settings(hold=settings.HOLD)
    cli._prune(held)
    db = connect()
    cli._import(db, FakeAdapter(), datetime.now(UTC), held)
    assert pruned == []
    cli._import(db, FakeAdapter(), datetime.now(UTC), settings.DEFAULTS)
    assert pruned == [183]


def test_config_get_never_prints_the_default_as_the_stored_value(capsys: pytest.CaptureFixture[str]) -> None:
    db = connect()
    db.execute("""INSERT INTO settings (key, value, updated) VALUES ('keep_days', '"400"', 'x')""")
    assert cli.main(["config", "get", "keep_days"]) == cli.EXIT_TOOL_ERROR
    out = capsys.readouterr()
    assert out.out == "" and "keep_days must be a whole number from 1 to 36500" in out.err
    assert "config unset keep_days` puts back its default (183)" in out.err
    assert cli.main(["config", "get", "situations_per_target"]) == cli.EXIT_OK and capsys.readouterr().out.strip() == "3"


def test_forget_never_deletes_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    assert cli.main(["config", "set", "keep_days", "30"]) == cli.EXIT_OK
    assert cli.main(["forget", "--yes"]) == cli.EXIT_OK
    assert settings.load(connect()).keep_days == 30


def test_the_file_beats_the_default_and_a_flag_beats_the_file(monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    read: list[int] = []

    def spy(adapter, cwd, *, sessions, forgotten_before=None):
        read.append(sessions)
        return real_read_config(adapter, cwd, sessions=sessions, forgotten_before=forgotten_before)

    monkeypatch.setattr(cli, "read_config", spy)
    assert cli.main(["config", "set", "sessions", "4"]) == cli.EXIT_OK
    assert cli.main(["show", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    assert cli.main(["show", "--sessions", "2", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    assert read == [4, 2]
    capsys.readouterr()
    with pytest.raises(SystemExit) as stopped:  # its old name, --history, went 2026-10-04
        cli.main(["show", "--history", "3", "--cwd", str(PROJECT)])
    assert stopped.value.code == cli.EXIT_TOOL_ERROR and "unrecognized arguments: --history" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["show", "--help"])
    shown = capsys.readouterr().out
    assert "--sessions N" in shown and "--history" not in shown
    assert cli.main(["config", "list"]) == cli.EXIT_OK
    listing = capsys.readouterr().out
    width = max(len(key) for key in settings.KEYS)
    assert f"{'sessions':<{width}} 4 " in listing and " set\n" in listing and f"{'keep_days':<{width}} 183 " in listing


def test_harnesses_agentlint_cannot_read_are_said_plainly(monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    assert cli.main(["config", "set", "harnesses", "codex"]) == cli.EXIT_OK
    assert "can't read codex yet" in capsys.readouterr().err
    assert cli.main(["check", "--cwd", str(PROJECT)]) == cli.EXIT_TOOL_ERROR
    assert "names none agentlint can read" in capsys.readouterr().err
    # Deleting what agentlint keeps is never turned away, and it asks.
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert cli.main(["forget"]) == cli.EXIT_OK
    assert "Nothing deleted." in capsys.readouterr().out
    assert cli.main(["config", "unset", "harnesses"]) == cli.EXIT_OK
    assert cli.main(["check", "--cwd", str(PROJECT)]) == cli.EXIT_OK


def test_the_first_run_says_once_what_is_read_and_kept(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    assert cli.main(["config", "list"]) == cli.EXIT_OK
    notice = capsys.readouterr().err
    assert "first run" in notice and "Nothing leaves this machine" in notice and "agentlint forget" in notice
    assert state_store.get(connect(), state_store.WELCOMED)[0] == {"by": "cli"}
    assert cli.main(["config", "list"]) == cli.EXIT_OK
    assert "first run" not in capsys.readouterr().err  # once


def test_agentlint_ui_leaves_the_first_run_to_the_page(monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "cmd_ui", lambda args, options: cli.EXIT_OK)
    assert cli.main(["ui", "--no-open"]) == cli.EXIT_OK
    assert "first run" not in capsys.readouterr().err
    assert state_store.get(connect(), state_store.WELCOMED) is None  # the page's first-run panel says it, once


def test_rank_and_usage_read_the_sessions_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    read: list[int] = []

    def spy(adapter, cwd, *, sessions, forgotten_before=None):
        read.append(sessions)
        return real_read_config(adapter, cwd, sessions=sessions, forgotten_before=forgotten_before)

    monkeypatch.setattr(cli, "read_config", spy)
    assert cli.main(["config", "set", "sessions", "0"]) == cli.EXIT_OK
    assert cli.main(["rank", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    assert cli.main(["usage", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    assert read == [0, 0]


def test_rank_phase_stability_plans_at_the_runs_per_situation_setting_as_tests_does(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    assert cli.main(["config", "set", "runs_per_situation", "5"]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["rank", "--phase", "stability", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    head = capsys.readouterr().out.splitlines()[0]
    assert "phase 1, stability" in head and "(15 each: 3 situations × 5 runs)" in head, head
    assert "budget 60 runs" in head  # rank's own default budget: no setting holds one (2026-10-05)
    assert cli.main(["config", "set", "situations_per_target", "2"]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["rank", "--phase", "stability", "--runs", "7", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    head = capsys.readouterr().out.splitlines()[0]
    assert "budget 7 runs" in head and "(10 each: 2 situations × 5 runs)" in head, head
    assert cli.main(["config", "get", "runs"]) == cli.EXIT_TOOL_ERROR  # max runs per test is gone


@pytest.mark.parametrize("command,flag,value,complaint", [
    (["check"], "--sessions", "-1", "from 0 to 1000"), (["show"], "--sessions", "1001", "from 0 to 1000"),
    (["ui", "--no-open"], "--sessions", "-1", "from 0 to 1000"),
    (["rank"], "--runs", "0", "from 1 to 100000"), (["rank"], "--runs", "100001", "from 1 to 100000"),
    (["usage"], "--days", "0", "from 1 to 36500"), (["usage"], "--days", "1000000", "from 1 to 36500"),
])
def test_a_flag_outside_its_settings_range_is_a_usage_error(command: list[str], flag: str, value: str, complaint: str,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:  # argparse refuses it, before anything is read
        cli.main([*command, flag, value, "--cwd", str(PROJECT)])
    err = capsys.readouterr().err
    assert stopped.value.code == cli.EXIT_TOOL_ERROR and flag in err and complaint in err


@pytest.mark.parametrize("key", ["sessions", "usage_days", "keep_days", "situations_per_target", "runs_per_situation", "run_timeout_s"])
def test_each_range_is_written_once_on_its_field_and_checked_there(key: str) -> None:
    low, high = settings.range_of(key)
    assert settings.Settings(**{key: low}) and settings.Settings(**{key: high})
    for wrong in (low - 1, high + 1, True):
        with pytest.raises(settings.SettingsError, match=f"{key} must be a whole number from {low} to {high}"):
            settings.Settings(**{key: wrong})
    assert settings.parse(key, str(high)) == high
    with pytest.raises(settings.SettingsError, match=f"from {low} to {high}"):
        settings.parse(key, str(high + 1))


def test_options_resolve_flags_over_settings_once_and_say_whether_a_reading_is_comparable() -> None:
    mine = settings.Settings(sessions=4, usage_days=7)
    plain = settings.Options.resolve(mine, sessions=None, usage_days=None)
    assert (plain.sessions, plain.days, plain.comparable) == (4, 7, True)
    flagged = settings.Options.resolve(mine, sessions=2, usage_days=None)
    assert (flagged.sessions, flagged.days, flagged.comparable) == (2, 7, False)
    assert settings.Options.resolve(mine, sessions=4).comparable  # the setting's number, given as a flag
    newer = flagged.reread(settings.Settings(sessions=2, usage_days=30))  # the UI, after `config set`
    assert (newer.sessions, newer.days, newer.comparable) == (2, 30, True)
    with pytest.raises(settings.SettingsError, match="no flag overrides runs"):  # rank's --runs is rank's own
        settings.Options.resolve(mine, runs=900)
    with pytest.raises(settings.SettingsError, match="from 0 to 1000"):
        settings.Options.resolve(mine, sessions=-1)
    with pytest.raises(settings.SettingsError, match="no flag overrides keep_days"):
        settings.Options.resolve(mine, keep_days=1)


def test_the_library_has_no_second_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The settings are the only defaults: the library calls take the value they would otherwise duplicate."""
    import inspect

    from agentlint.rank.plan import plan
    from agentlint.report.document import report_data

    for function, name in ((real_read_config, "sessions"), (plan, "budget"), (report_data, "usage_days")):
        parameter = inspect.signature(function).parameters[name]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY and parameter.default is inspect.Parameter.empty


def test_an_unexpected_error_is_a_tool_error_not_findings(monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    def broken():
        raise RuntimeError("something agentlint did not expect")

    monkeypatch.setattr(cli, "_adapter", broken)
    assert cli.main(["check", "--cwd", str(PROJECT)]) == cli.EXIT_TOOL_ERROR
    assert "something agentlint did not expect" in capsys.readouterr().err  # the traceback, for a bug report


def test_a_tests_settings_have_their_defaults_and_take_a_model_and_lists_of_tools(capsys: pytest.CaptureFixture[str]) -> None:
    db = connect()
    loaded = settings.load(db)
    assert (loaded.situations_per_target, loaded.runs_per_situation, loaded.run_timeout_s, loaded.model) == (3, 3, 300, "")
    assert loaded.keep_tools == loaded.remove_tools == ()
    assert settings.set_value(db, "model", "claude-opus-5-5[1m]") == "claude-opus-5-5[1m]"
    assert settings.set_value(db, "remove_tools", "WebFetch, mcp__mail__send_message, WebFetch") == (
        "WebFetch", "mcp__mail__send_message")
    assert settings.store(db, "keep_tools", ["mcp__wiki__wiki_upsert"]) == ("mcp__wiki__wiki_upsert",)
    loaded = settings.load(db)
    assert loaded.model == "claude-opus-5-5[1m]" and loaded.remove_tools == ("WebFetch", "mcp__mail__send_message")
    with pytest.raises(settings.SettingsError, match="model must be a model's name"):
        settings.store(db, "model", 5)
    assert cli.main(["config", "list"]) == cli.EXIT_OK
    listed = capsys.readouterr().out
    assert "runs_per_situation" in listed and "WebFetch, mcp__mail__send_message" in listed
    assert cli.main(["config", "unset", "model"]) == cli.EXIT_OK
    assert "back to its default: the model your sessions use" in capsys.readouterr().out
    assert cli.main(["config", "unset", "keep_tools"]) == cli.EXIT_OK
    assert "back to its default: none" in capsys.readouterr().out
