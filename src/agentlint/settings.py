"""Your settings, in the agentlint database's ``settings`` table (docs/tech_impl/00-overview.md, "Settings").

A setting you never changed has no row, so its default can still improve; a row overrides the default, and a
command-line flag overrides the row for one run. `agentlint config set/get/unset/list` and the UI's Settings screen
read and write the same rows. Until 2026-10-04 they lived in ``~/.agentlint/config.toml``: ``migrate`` imports that
file once and renames it ``config.toml.migrated``, never deleting it.

Each number's range is written once, on its field: `Settings` checks it on construction, every stored value and
every value written is checked against it, and the CLI builds its flags' validators from it (`range_of`). The flags
and the settings are resolved once per command into an `Options`, which is what every command reads.
"""

from __future__ import annotations

import json
import re
import sqlite3
import tomllib
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentlint.store import home, transaction
from agentlint.store import state as state_store
from agentlint.store._time import encode

_HARNESS = re.compile(r"[a-z][a-z0-9-]*")
# A tool as the harness names it. Never starting with '-': the list is the last thing on a run's command line, after a
# flag that takes every word that follows, so a name like '--model' would be read as a flag.
_TOOL = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:@/-]{0,199}")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/\[\]-]{0,99}")  # a model name or alias, as the harness's --model takes


class SettingsError(Exception):
    """A stored setting, or a value given for one, can't be used: the message says which and why."""


def _ranged(default: int, low: int, high: int) -> Any:
    """A whole-number setting, with the range it accepts: the one place that range is written."""
    return field(default=default, metadata={"range": (low, high)})


@dataclass(frozen=True)
class Settings:
    harnesses: tuple[str, ...] = ()  # which harnesses to read; empty: every one agentlint finds
    sessions: int = _ranged(10, 0, 1000)  # recent sessions read for what disk can't show
    usage_days: int = _ranged(30, 1, 36_500)  # the usage window `agentlint usage` and the UI count
    keep_days: int = _ranged(183, 1, 36_500)  # everything older is deleted, except what is current
    # A test's size (11-ui.md "Tests"; decided 2026-10-05, both steps D9 2026-10-06): the targets you tick × situations
    # per target × runs per situation × 3 (on your configuration, then with and without, where a variant can remove the
    # target) — one run count for every step (the user, 2026-10-07). That is the most it can run, and the job's run
    # ceiling (05-runner.md "Budget"): there is no ceiling setting.
    situations_per_target: int = _ranged(3, 1, 10)  # written for each target; a job uses at most this many kept
    # Each situation's runs on your configuration, and with and without its target, each side. Under 3 is no screen;
    # at 3 a side the with/without step can never reach p < 0.05 (07-analysis "Effect of a rule"), at 5 it can.
    runs_per_situation: int = _ranged(3, 3, 20)
    # Runs a test keeps out at once (2026-10-07). Each run starts the harness with every MCP server and hook it
    # configures, so the machine's load grows with it; a run that times out is behaviour (D7), so too many at once
    # make timeouts that aren't the configuration's. Read as each resume starts, never frozen in a job.
    runs_at_once: int = _ranged(1, 1, 8)
    # One run's time limit. E1's run took 53 s, 28 s of it one SessionStart hook; a timeout is an outcome (behaviour,
    # D7), so the limit must sit well clear of a normal run, and a stuck run costs at most this.
    run_timeout_s: int = _ranged(300, 30, 3600)
    model: str = ""  # the model every run of a new job asks for; "": the one your own sessions use (the adapter reads it)
    keep_tools: tuple[str, ...] = ()  # tools the adapter would remove from test runs that you keep offering (D6)
    remove_tools: tuple[str, ...] = ()  # tools removed from every test run besides those the adapter removes (D6)
    configured: frozenset[str] = field(default=frozenset(), compare=False)  # keys the database sets
    # Why nothing may be deleted by age now: keep_days isn't a value you set — its stored value can't be used, or a
    # config.toml that may set it couldn't be read. Pruning on the fallback default would delete what you asked to
    # keep, so every pruning path checks this first (cli._prune, cli._import). None: prune at keep_days.
    hold: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        for each in fields(self):
            if "range" in each.metadata:
                checked(each.name, getattr(self, each.name))


def range_of(key: str) -> tuple[int, int]:
    """The whole numbers a setting accepts, read from its field."""
    for each in fields(Settings):
        if each.name == key and "range" in each.metadata:
            return each.metadata["range"]
    raise KeyError(f"{key} is not a whole-number setting")


def checked(key: str, value: Any, subject: str = "") -> int:
    """The value, if it is a whole number in the setting's range; else a SettingsError saying the range. ``subject``
    is what the reason opens with — "the stored keep_days", "sessions on the command line" — and names the key itself,
    so the reason names it once; empty: the key."""
    low, high = range_of(key)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise SettingsError(f"{subject or key} must be a whole number from {low} to {high}")
    return value


MEANING = {  # one line each: what the setting decides, as Settings and `config list` say it
    "harnesses": "Which harnesses to read. Empty: every harness agentlint finds on this machine.",
    "sessions": "Recent sessions read for what disk can't show: hook output, the harness's own prompt and listings.",
    "usage_days": "The usage window `agentlint usage` and the UI count, in days.",
    "keep_days": "Everything older than this many days is deleted — usage, readings, findings, archived transcripts — "
                 "except what is current.",
    "situations_per_target": "How many situations the generator writes for each target, and the most kept ones a "
                             "test runs of each target you tick.",
    "runs_per_situation": "How many times a test runs each situation: on your configuration as it is, then — where "
                          "those runs agree — as many times with the target and as many without. Three runs that "
                          "disagree show the agent is inconsistent; with and without needs five or more to show a "
                          "change.",
    "run_timeout_s": "One test run's time limit, in seconds. A run that reaches it is recorded as timed out: an "
                     "outcome, not an error.",
    "runs_at_once": "How many test runs go at once. Each run starts its own harness, with every MCP server and hook "
                    "it configures: more at once finishes sooner but loads the machine, and a run that times out "
                    "under load is recorded as timed out.",
    "model": "The model every run of a new test asks for. Empty: the model your own sessions use, as agentlint reads "
             "it from your harness's settings.",
    "keep_tools": "Tools agentlint would remove from test runs, as acting outside your machine, that runs keep "
                  "offering anyway.",
    "remove_tools": "Tools removed from every test run besides those agentlint removes as acting outside your machine.",
}
GROUPS = {"reading": ("harnesses", "sessions", "usage_days", "keep_days"),  # Settings › Reading and keeping
          "tests": ("situations_per_target", "runs_per_situation", "runs_at_once", "run_timeout_s", "model", "keep_tools",
                    "remove_tools")}
_LISTS = {"harnesses": (_HARNESS, "a list of harness names: lowercase, words joined by -"),
          "keep_tools": (_TOOL, "a list of tool names, as the harness names them (none starting with -)"),
          "remove_tools": (_TOOL, "a list of tool names, as the harness names them (none starting with -)")}
EMPTY = {"harnesses": "every harness found", "keep_tools": "none", "remove_tools": "none",
         "model": "the model your sessions use"}  # how a setting's empty value reads
KEYS = tuple(f.name for f in fields(Settings) if f.name not in ("configured", "hold"))
DEFAULTS = Settings()


def _known(key: str) -> None:
    if key not in KEYS:
        raise SettingsError(f"no setting called {key} (settings: {', '.join(KEYS)})")


def _check(key: str, value: Any, subject: str = "") -> Any:
    """The value as the setting holds it, or a SettingsError saying what is wrong with it (``subject``: as ``checked``)."""
    if key in _LISTS:
        pattern, what = _LISTS[key]
        if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) and pattern.fullmatch(item)
                                                            for item in value):
            raise SettingsError(f"{subject or key} must be {what}")
        return tuple(dict.fromkeys(value))
    if key == "model":
        if not isinstance(value, str) or not _MODEL.fullmatch(value):
            raise SettingsError(f"{subject or key} must be a model's name, as the harness's --model takes it (letters, "
                                "digits and . _ : @ / [ ] -); unset it to use the model your sessions use")
        return value
    return checked(key, value, subject)


def validate(key: str, value: Any) -> Any:
    """A value as JSON gives it, as the setting holds it — or a SettingsError saying what is wrong. Nothing written."""
    _known(key)
    return _check(key, value)


def read(db: sqlite3.Connection) -> tuple[Settings, dict[str, str]]:
    """What is in effect, and each stored value that can't be used, by key, with why. An unusable value counts as
    unset — its default is in effect — so whoever asks can still start, and say what to fix. A key this agentlint has
    no setting for is ignored: another version wrote it."""
    values: dict[str, Any] = {}
    problems: dict[str, str] = {}
    for row in db.execute("SELECT key, value FROM settings ORDER BY key"):
        key = row["key"]
        if key not in KEYS:
            continue
        try:
            values[key] = _check(key, json.loads(row["value"]), f"the stored {key}")
        except ValueError:  # not JSON
            problems[key] = f"the stored {key} isn't readable: {row['value'][:40]!r}"
        except SettingsError as error:
            problems[key] = str(error)
    hold = HOLD if "keep_days" in problems else None
    return Settings(**values, configured=frozenset(values), hold=hold), problems


HOLD = ("Nothing is deleted by age until keep_days can be used: its default might delete what you asked agentlint "
        "to keep.")


def load(db: sqlite3.Connection) -> Settings:
    """What is in effect; a SettingsError naming the first stored value that can't be used."""
    loaded, problems = read(db)
    if problems:
        raise SettingsError(next(iter(problems.values())))
    return loaded


def store(db: sqlite3.Connection, key: str, value: Any, now: datetime | None = None) -> Any:
    """Set a setting to a value as JSON gives it (a list of names, a whole number), checked first: nothing is written
    unless it is in range. The value as the setting holds it."""
    _known(key)
    value = _check(key, value)
    with transaction(db):
        db.execute("INSERT INTO settings (key, value, updated) VALUES (?, ?, ?) "
                   "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated = excluded.updated",
                   (key, json.dumps(list(value) if isinstance(value, tuple) else value), encode(now or datetime.now(UTC))))
    return value


def parse(key: str, raw: str) -> Any:
    """A value typed on the command line, as the setting holds it: comma-separated for a list."""
    _known(key)
    if key in _LISTS:
        return _check(key, [item.strip() for item in raw.split(",") if item.strip()])
    if key == "model":
        return _check(key, raw.strip())
    try:
        return _check(key, int(raw))
    except ValueError as error:
        raise SettingsError(f"{key} must be a whole number, not {raw!r}") from error


def set_value(db: sqlite3.Connection, key: str, raw: str) -> Any:
    """`config set`: the value typed, parsed and stored."""
    return store(db, key, parse(key, raw))


def unset(db: sqlite3.Connection, key: str) -> None:
    """Back to the default: the row goes, so the default is in effect — and can still improve."""
    _known(key)
    with transaction(db):
        db.execute("DELETE FROM settings WHERE key = ?", (key,))


def legacy_file() -> Path:
    """Where settings lived until 2026-10-04."""
    return home() / "config.toml"


@dataclass(frozen=True)
class Migration:
    """What ``migrate`` did with a config.toml: ``note``, one line for stderr; ``blocked``, set when the file couldn't
    be read — it stays where it is, imported next time it reads, and until then only the commands that need no setting
    run, and nothing is deleted by age (it may set keep_days)."""

    note: str
    blocked: str | None = None


def migrate(db: sqlite3.Connection, now: datetime | None = None) -> Migration | None:
    """Import a ``config.toml`` once, then rename it ``config.toml.migrated`` — never deleting it — and count the
    first-run notice as shown, since writing that file was the first run. None when there is no file.

    Each setting the file sets is stored as written unless the database already holds it (the database's is kept, and
    named): a value that can't be used is stored too, so it is refused, shown and fixed like any stored value that
    can't be used (``read``) — never dropped for a default, which for keep_days would delete what you asked to keep. A
    key that is no setting is left out and named. A file that can't be read is left in place, unrenamed, with nothing
    imported, and ``blocked`` says so: it may set keep_days.

    Two agentlint processes can start at once — the scheduled sync while `agentlint ui` starts — so the whole of it,
    from looking for the file to renaming it, holds the database's write lock: the second waits, then finds no file
    and says nothing (None). A file gone by the rename was moved by someone else: nothing to say about it either."""
    file = legacy_file()
    now = now or datetime.now(UTC)
    with transaction(db):  # BEGIN IMMEDIATE (or the caller's, which is one): one migrating process at a time
        if not file.is_file():
            return None
        try:
            data: dict[str, Any] = tomllib.loads(file.read_text(encoding="utf-8"))
        except FileNotFoundError:  # removed between the look and the read, by you: nothing to import
            return None
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
            state_store.claim(db, state_store.WELCOMED, {"by": "config.toml"}, now)  # the file was the first run
            why = (f"your settings file {file} couldn't be read ({error}), so nothing was imported from it and nothing "
                   "is deleted by age (it may set keep_days); fix it, or remove it to use the database's settings — it "
                   "is imported, and renamed, the next time it reads")
            return Migration(f"agentlint: {why}.", blocked=why)
        imported, unusable, kept, unknown = [], [], [], []
        for key, value in data.items():
            if key not in KEYS:
                unknown.append(key)
                continue
            try:
                value, usable = _check(key, value), True
            except SettingsError:
                usable = False
            raw = json.dumps(list(value) if isinstance(value, tuple) else value, default=str)
            wrote = db.execute("INSERT OR IGNORE INTO settings (key, value, updated) VALUES (?, ?, ?)",
                               (key, raw, encode(now))).rowcount == 1
            (kept if not wrote else imported if usable else unusable).append(key)
        state_store.claim(db, state_store.WELCOMED, {"by": "config.toml"}, now)
        target = file.with_name("config.toml.migrated")
        n = 2
        while target.exists():  # an earlier migration's copy is kept too
            target, n = file.with_name(f"config.toml.migrated.{n}"), n + 1
        try:
            file.rename(target)
            where = f"the file is kept as {target.name}"
        except FileNotFoundError:  # moved while it was read, by you: what it set is imported all the same
            if not imported and not unusable:
                return None
            where = "the file is gone already"
        except OSError as error:
            where = f"the file couldn't be renamed ({error}); rename or remove it yourself"
    said = [f"imported {', '.join(imported)}" if imported else "imported no settings"
            + (" (it set none)" if not data else "")]
    if unusable:
        said.append(f"imported, but can't be used until you change it (Settings in `agentlint ui`, or `agentlint config "
                    f"set`): {', '.join(unusable)}")
    if kept:
        said.append(f"kept the database's {', '.join(kept)}, set there already")
    if unknown:
        said.append(f"left out what is no setting: {', '.join(unknown)}")
    return Migration(f"agentlint: your settings moved from {file} into the agentlint database — {'; '.join(said)}; "
                     f"{where}.")


def shown(key: str, value: Any) -> str:
    """A setting's value as a line of text says it: a list joined by commas, an empty one or an empty model in words."""
    if isinstance(value, tuple | list):
        return ", ".join(value) or EMPTY.get(key, "none")
    if value == "" and key in EMPTY:
        return EMPTY[key]
    return str(value)


def describe(current: Settings, problems: dict[str, str], harnesses: list[str]) -> list[dict[str, Any]]:
    """Each setting for a person to read and change: its group (``GROUPS``), its value in effect, its default, whether
    you set it, what it accepts (a range, the harnesses agentlint reads, or its kind: a model's name, a list of
    tools), its one-line meaning, and why its stored value can't be used, if it can't. ``harnesses``: the names
    agentlint has an adapter for, from the composition root."""
    described = []
    for key in KEYS:
        value, default = getattr(current, key), getattr(DEFAULTS, key)
        entry: dict[str, Any] = {
            "key": key, "group": next(group for group, keys in GROUPS.items() if key in keys),
            "value": list(value) if isinstance(value, tuple) else value,
            "default": list(default) if isinstance(default, tuple) else default,
            "set": key in current.configured, "meaning": MEANING[key], "problem": problems.get(key),
        }
        if key == "harnesses":
            entry["choices"] = harnesses
        elif key in _LISTS:
            entry["kind"] = "tools"
        elif key == "model":
            entry["kind"] = "model"
        else:
            entry["range"] = list(range_of(key))
        described.append(entry)
    return described


OVERRIDABLE = ("sessions", "usage_days")  # the settings a command-line flag can override for one run


@dataclass(frozen=True)
class Options:
    """What one command uses: each overridable setting from its flag if one was given, else from the settings.

    Resolved once, before any command runs, and never changed after. `reread` takes newer settings and keeps the
    flags: an open UI reads the settings again on every request, and a flag still beats them."""

    settings: Settings
    flags: tuple[tuple[str, int], ...] = ()  # (setting, value) for each flag given on the command line

    def __post_init__(self) -> None:
        for key, value in self.flags:
            if key not in OVERRIDABLE:
                raise SettingsError(f"no flag overrides {key} (flags override: {', '.join(OVERRIDABLE)})")
            checked(key, value, f"{key} on the command line")

    @classmethod
    def resolve(cls, settings: Settings, **flags: int | None) -> Options:
        """The settings, with each flag given (not None) over its setting."""
        return cls(settings, tuple((key, value) for key, value in flags.items() if value is not None))

    def reread(self, settings: Settings) -> Options:
        return replace(self, settings=settings)

    def _value(self, key: str) -> int:
        return dict(self.flags).get(key, getattr(self.settings, key))

    @property
    def sessions(self) -> int:
        """Recent sessions to read for what disk can't show."""
        return self._value("sessions")

    @property
    def days(self) -> int:
        """The usage window, in days."""
        return self._value("usage_days")

    @property
    def comparable(self) -> bool:
        """A reading of your `sessions` setting's number of sessions: comparable with stored readings, so it is
        stored and recorded. Any other number sees other evidence, so it is neither."""
        return self.sessions == self.settings.sessions
