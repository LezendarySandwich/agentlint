"""The daily sync, if you ask for one (docs/tech_impl/08-recorder.md, "Sync").

A job in your system's scheduler — launchd on macOS, cron elsewhere — not in the harness, that runs `agentlint
sync` once a day, so transcripts are archived before the harness deletes them even when agentlint isn't
otherwise run. `agentlint sync --daily` installs it; `--off` removes it. Nothing else about the system changes.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

LABEL = "dev.agentlint.sync"
MARK = "# agentlint daily sync"
HOUR, MINUTE = 13, 7  # midday, when a laptop is likely awake; launchd runs a missed one on wake

Run = Callable[..., subprocess.CompletedProcess]


class ScheduleError(Exception):
    """The scheduler refused: the message says what it said."""


@dataclass(frozen=True)
class Job:
    scheduler: str  # "launchd" or "cron"
    where: str  # the job's file, or "your crontab"


def command(store: Path) -> list[str]:
    """What the job runs: this Python and this agentlint, with this store."""
    return [sys.executable, "-m", "agentlint.cli", "sync", "--quiet"]


def _environment(store: Path) -> dict[str, str]:
    return {"AGENTLINT_HOME": str(store)}  # always, so the job uses this store whatever its own environment says


def _plist(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def installed(*, home: Path | None = None, platform: str = sys.platform, run: Run = subprocess.run) -> Job | None:
    home = home or Path.home()
    if platform == "darwin":
        path = _plist(home)
        return Job("launchd", str(path)) if path.exists() else None
    try:
        listing = run(["crontab", "-l"], capture_output=True, text=True)
    except OSError:  # no crontab on this system: no job either
        return None
    return Job("cron", "your crontab") if listing.returncode == 0 and MARK in listing.stdout else None


def install(store: Path, *, home: Path | None = None, platform: str = sys.platform, run: Run = subprocess.run) -> Job:
    home = home or Path.home()
    store = store.resolve()  # launchd starts a job in /, cron in your home: a relative path would be another store
    log = store / "sync.log"
    if platform == "darwin":
        path = _plist(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        job = {"Label": LABEL, "ProgramArguments": command(store), "RunAtLoad": False,
               "StartCalendarInterval": {"Hour": HOUR, "Minute": MINUTE},
               "StandardOutPath": str(log), "StandardErrorPath": str(log)}
        job["EnvironmentVariables"] = _environment(store)
        domain = f"gui/{os.getuid()}"
        if path.exists():
            run(["launchctl", "bootout", domain, str(path)], capture_output=True, text=True)
        path.write_bytes(plistlib.dumps(job))
        loaded = run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True)
        if loaded.returncode != 0:
            path.unlink(missing_ok=True)
            raise ScheduleError(f"launchctl wouldn't load {path}: {loaded.stderr.strip() or loaded.returncode}")
        return Job("launchd", str(path))
    env = " ".join(f"{k}={shlex.quote(v)}" for k, v in _environment(store).items())
    line = f"{MINUTE} {HOUR} * * * {env} {shlex.join(command(store))} >> {shlex.quote(str(log))} 2>&1 {MARK}"
    _write_crontab(run, [*(_crontab(run)), line])
    return Job("cron", "your crontab")


def remove(*, home: Path | None = None, platform: str = sys.platform, run: Run = subprocess.run) -> bool:
    """Take the job out; whether there was one."""
    home = home or Path.home()
    if platform == "darwin":
        path = _plist(home)
        if not path.exists():
            return False
        run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True, text=True)
        path.unlink(missing_ok=True)
        return True
    lines = _crontab(run, keep_ours=True)
    ours = [line for line in lines if MARK in line]
    if ours:
        _write_crontab(run, [line for line in lines if MARK not in line])
    return bool(ours)


def _crontab(run: Run, keep_ours: bool = False) -> list[str]:
    listing = run(["crontab", "-l"], capture_output=True, text=True)
    lines = listing.stdout.splitlines() if listing.returncode == 0 else []  # no crontab yet: empty
    return lines if keep_ours else [line for line in lines if MARK not in line]


def _write_crontab(run: Run, lines: Sequence[str]) -> None:
    written = run(["crontab", "-"], input="\n".join(lines) + "\n", capture_output=True, text=True)
    if written.returncode != 0:
        raise ScheduleError(f"crontab refused: {written.stderr.strip() or written.returncode}")
