"""What the UI reads about agentlint itself and changes (docs/tech_impl/11-ui.md, "Settings", "Findings" and "The
Dismissed screen"): ``ui/server.py``'s ``Control``, over the agentlint database and the system's scheduler — the
Settings screen, and dismissing a finding or showing it again (``store/suppression_store.py``, as `agentlint suppress` does).

Every write checks every value it is given and refuses — ``Refused``, with a status and a reason a person can act on —
anything else: a field it doesn't take, a setting out of range, Forget without the typed word or while a test job
runs. The server has already checked who is asking (``ui/server.py``, "Writes"). What the UI shows comes from here,
never from a copy in the page: each setting's default, range and meaning are ``settings.py``'s.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentlint import __version__, schedule, settings
from agentlint.forget import JobsRunning, Transcripts, forget_everything, running
from agentlint.store import StoreError, archive, home
from agentlint.store import state as state_store
from agentlint.store import suppressions as suppression_store
from agentlint.ui.server import Params, Refused, Unreadable

CONFIRM = "forget"  # what Forget everything's dialog must be typed, and the server requires
REASON_MAX = 1000  # characters of a dismissal's reason: a sentence or two, said again wherever it is listed
_FINDING_ID = re.compile(r"[0-9a-f]{4,64}")
Check = Callable[[Params], str]  # checks a scope's project quietly, as `suppress` does; the project as the store names it


class SystemScheduler:
    """The daily sync's job in the system's scheduler (``schedule.py``). ``home``, ``platform`` and ``run`` are the
    module's own seams: tests pass a fake ``run`` and never reach launchd or crontab."""

    def __init__(self, store: Path, *, home: Path | None = None, platform: str = sys.platform,
                 run: Callable[..., Any] | None = None) -> None:
        self.store = store
        self._where: dict[str, Any] = {"home": home, "platform": platform}
        if run is not None:
            self._where["run"] = run
        self.name = "launchd" if platform == "darwin" else "cron"

    def installed(self) -> schedule.Job | None:
        return schedule.installed(**self._where)

    def install(self) -> schedule.Job:
        return schedule.install(self.store, **self._where)

    def remove(self) -> bool:
        return schedule.remove(**self._where)


def _fields(body: dict[str, Any], *allowed: str) -> None:
    """Refuse a body with a field the write doesn't take."""
    extra = sorted(set(body) - set(allowed))
    if extra:
        raise Refused(400, f"unexpected field{'s' if len(extra) > 1 else ''}: {', '.join(extra)}")


def _finding_id(body: dict[str, Any]) -> str:
    """The finding a write names: its whole id, as the page has it."""
    finding_id = body.get("finding_id")
    if not isinstance(finding_id, str) or not _FINDING_ID.fullmatch(finding_id):
        raise Refused(400, "name the finding by its id: finding_id, its hex characters")
    return finding_id


def _whole(db: sqlite3.Connection, finding_id: str) -> str | None:
    """The finding ever seen or dismissed with this whole id; None when there is none. A prefix is refused: the CLI
    takes one for typing, a page always has the whole id."""
    try:
        found = suppression_store.resolve(db, finding_id)
    except suppression_store.NoMatch:
        return None
    except suppression_store.UnknownFinding as error:
        raise Refused(400, str(error)) from error
    if found != finding_id:
        raise Refused(400, f"{finding_id} is only the start of a finding id: name the whole id")
    return found


def _entry(db: sqlite3.Connection, item: suppression_store.Suppression) -> dict[str, Any]:
    """A suppression as the page lists it: the finding as last recorded (None when no project records it any more),
    when and why, and per project with a baseline its state — active, lapsed (shown again: what it rests on there
    changed) or moot (no longer found there) — as `suppress --list` says it."""
    seen = suppression_store.latest(db, item.id)
    states = suppression_store.status(db, item)
    return {"id": item.id, "check": seen[0] if seen else None, "title": seen[1] if seen else None,
            "at": item.at.isoformat(), "reason": item.reason,
            "projects": [{"project": project, "state": state.value} for project, state in sorted(states.items())]}



class StoreControl:
    """The Settings screen's reads and writes. ``connect`` opens the database for one request; ``harnesses`` are the
    names agentlint has an adapter for (from the composition root); ``sources`` the harnesses whose transcripts are
    forgotten as they are on disk now; ``check`` how a dismissal checks the project it names first, as
    `agentlint suppress` does (from the composition root; None, nothing can be dismissed); ``sessions`` the server's
    --sessions flag, if it was given, so a dismissal refused for want of the finding says why the page shows it;
    ``attention`` what the server found wrong as it started and stays wrong until it
    starts again (a harnesses setting naming none it reads), which the page opens Settings for; ``blocked`` why a
    config.toml couldn't be read as it started, said while that file is still there — and the import is tried again on
    every request while there is a file, so a fixed one is imported then and there (``_retry_import``).

    Everything else the page is told to fix is read again on every request, so it goes once it is fixed: each stored
    value that can't be used, and the notice that nothing is deleted by age while that value is keep_days
    (``settings.HOLD``)."""

    def __init__(self, connect: Callable[[], sqlite3.Connection], *, harnesses: Sequence[str],
                 sources: Sequence[Transcripts], scheduler: SystemScheduler, attention: Sequence[str] = (),
                 blocked: str | None = None, stale: Callable[[int, str], bool] | None = None,
                 check: Check | None = None, sessions: int | None = None) -> None:
        self._connect = connect
        self.check = check
        self.sessions = sessions  # `agentlint ui --sessions N`'s N: the page reads that many, a dismissal the setting's
        self.harnesses = list(harnesses)
        self.sources = list(sources)
        self.scheduler = scheduler
        self.attention = list(attention)
        self.blocked = blocked
        self.stale = stale

    def _db(self) -> sqlite3.Connection:
        try:
            return self._connect()
        except (StoreError, sqlite3.Error, OSError) as error:
            raise Unreadable(f"the agentlint database: {error}") from error

    def _retry_import(self, db: sqlite3.Connection) -> None:
        """A config.toml, tried again on every request while there is one — as every command tries it: fixed (or new
        and readable), it is imported and renamed (``settings.migrate``, under the database's write lock as at the
        start) and the notice goes at once, with no command or restart needed; still broken, or broken anew, the notice
        says why as it is now. What an import did is printed to the terminal running the server, as a command prints
        it. No file, nothing is written: a request writes only when there is a file to import."""
        if not settings.legacy_file().is_file():
            return
        migration = settings.migrate(db)
        self.blocked = migration.blocked if migration else None
        if migration and not migration.blocked:
            print(migration.note, file=sys.stderr, flush=True)

    def settings(self) -> dict[str, Any]:
        db = self._db()
        try:
            self._retry_import(db)
            current, problems = settings.read(db)
            files, written, stored = archive.totals(db)
            last = state_store.get(db, state_store.LAST_SYNC)
            welcomed = state_store.get(db, state_store.WELCOMED)
            busy = running(db, self.stale)
        except sqlite3.Error as error:
            raise Unreadable(f"the agentlint database: {error}") from error
        finally:
            db.close()
        try:
            job, sync_error = self.scheduler.installed(), None
        except (schedule.ScheduleError, OSError) as error:
            job, sync_error = None, str(error)
        unknown = [name for name in current.harnesses if name not in self.harnesses]
        notes = {"harnesses": f"agentlint can't read {', '.join(unknown)} yet; it reads {', '.join(self.harnesses)}"} \
            if unknown else {}
        described = settings.describe(current, problems, self.harnesses)
        for entry in described:
            entry["note"] = notes.get(entry["key"])
        return {
            # Reading and keeping, as the Settings screen lists them; a test's settings apart, for the Tests screens
            # and onboarding (each entry says its group).
            "settings": [entry for entry in described if entry["group"] == "reading"],
            "test_settings": [entry for entry in described if entry["group"] == "tests"],
            "attention": [*self.attention, *([self.blocked] if self.blocked and settings.legacy_file().is_file() else []),
                          *problems.values(), *([settings.HOLD] if "keep_days" in problems else [])],
            "sync": {
                "installed": job is not None, "scheduler": job.scheduler if job else self.scheduler.name,
                "where": job.where if job else None, "time": f"{schedule.HOUR:02d}:{schedule.MINUTE:02d}",
                "error": sync_error,
                "last": {**last[0], "at": last[1]} if last and isinstance(last[0], dict) else None,
            },
            "archive": {"transcripts": files, "bytes_written": written, "bytes_stored": stored},
            "store": str(home()),  # where agentlint keeps it all: AGENTLINT_HOME, else ~/.agentlint
            "home_dir": str(Path.home()),  # so the page shows that, and the job's file, in the ~ form (format.ts home)
            "version": __version__,
            "first_run": welcomed is None,
            "jobs_running": [{"job_id": held.job_id, "pid": held.runner_pid} for held in busy],
        }

    def set_setting(self, body: dict[str, Any]) -> dict[str, Any]:
        key = body.get("key")
        if not isinstance(key, str) or key not in settings.KEYS:
            raise Refused(400, f"no setting called {key!r} (settings: {', '.join(settings.KEYS)})"
                          if isinstance(key, str) else "name the setting: {key, value} or {key, reset: true}")
        if "reset" in body:
            _fields(body, "key", "reset")
            if body["reset"] is not True:
                raise Refused(400, "reset must be true")
        else:
            _fields(body, "key", "value")
            if "value" not in body:
                raise Refused(400, "give the value: {key, value} or {key, reset: true}")
        try:
            value = None if "reset" in body else settings.validate(key, body["value"])
        except settings.SettingsError as error:
            raise Refused(400, str(error)) from error
        unknown = [name for name in value if name not in self.harnesses] if key == "harnesses" and value else []
        if unknown:  # only the choices GET offers: a name agentlint can't read would turn every sync away
            raise Refused(400, f"agentlint can't read {', '.join(map(repr, unknown))}; it reads {', '.join(self.harnesses)}")
        db = self._db()
        try:
            if "reset" in body:
                settings.unset(db, key)
            else:
                settings.store(db, key, body["value"])
        except settings.SettingsError as error:
            raise Refused(400, str(error)) from error
        finally:
            db.close()
        return self.settings()

    def schedule(self, body: dict[str, Any]) -> dict[str, Any]:
        _fields(body, "on")
        on = body.get("on")
        if not isinstance(on, bool):
            raise Refused(400, "on must be true or false")
        try:
            if on:
                self.scheduler.install()
            else:
                self.scheduler.remove()
        except (schedule.ScheduleError, OSError) as error:
            raise Refused(500, f"the scheduler refused: {error}") from error
        return self.settings()

    def forget(self, body: dict[str, Any]) -> dict[str, Any]:
        _fields(body, "confirm")
        if body.get("confirm") != CONFIRM:
            raise Refused(400, f"type {CONFIRM} to confirm: nothing was deleted")
        db = self._db()
        try:
            gone = forget_everything(db, datetime.now(UTC), self.sources, stale=self.stale)
        except JobsRunning as error:
            raise Refused(409, f"nothing deleted: {error}") from error
        except (StoreError, sqlite3.Error, OSError) as error:
            raise Unreadable(f"the agentlint database: {error}") from error
        finally:
            db.close()
        return {"turns": gone.turns, "transcripts": gone.transcripts, "undeleted": list(gone.undeleted),
                "settings": self.settings()}

    def welcomed(self, body: dict[str, Any]) -> dict[str, Any]:
        _fields(body)
        db = self._db()
        try:
            state_store.claim(db, state_store.WELCOMED, {"by": "ui"}, datetime.now(UTC))
        finally:
            db.close()
        return {"first_run": False}

    # Dismissals: a finding dismissed, shown again, and every dismissal listed.

    def suppressions(self) -> dict[str, Any]:
        """GET /api/suppressions: every suppression across projects, the most recent first."""
        db = self._db()
        try:
            listed = [_entry(db, item) for item in sorted(suppression_store.load(db).values(), key=lambda s: s.at, reverse=True)]
        except sqlite3.Error as error:
            raise Unreadable(f"the agentlint database: {error}") from error
        finally:
            db.close()
        return {"suppressions": listed, "home_dir": str(Path.home())}

    def suppress(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/suppress ``{finding_id, project, reason}`` (and ``harness``, the scope's, when the page names one):
        as `agentlint suppress <id> --cwd <project> --reason …` — the project checked quietly first, as `check` would,
        so the finding is recorded there; then, if it is found there, dismissed everywhere and armed in every project
        where it is open against what it rests on there now."""
        _fields(body, "finding_id", "project", "reason", "harness")
        finding_id = _finding_id(body)
        project, harness, reason = body.get("project"), body.get("harness"), body.get("reason")
        if not isinstance(project, str) or not project:
            raise Refused(400, "name the project the finding was seen in: project, its path")
        if harness is not None and (not isinstance(harness, str) or not harness):
            raise Refused(400, "harness must be the name of a harness agentlint lists")
        if not isinstance(reason, str) or not reason.strip():
            raise Refused(400, "say why you are dismissing it, so the dismissal can be revisited: a reason is required")
        reason = reason.strip().encode("utf-8", "replace").decode("utf-8")  # a lone surrogate JSON can carry: as `?`
        if len(reason) > REASON_MAX:
            raise Refused(400, f"a reason is at most {REASON_MAX:,} characters (this one is {len(reason):,})")
        if self.check is None:
            raise Refused(404, "this server can't check a project, so it can't dismiss a finding")
        db = self._db()
        try:
            _whole(db, finding_id)  # a malformed id fails before anything is read
            used = self._usable(db)
            checked = self.check({"project": project, **({"harness": harness} if harness else {})})
            if _whole(db, finding_id) is None:
                raise Refused(404, f"no finding {finding_id} is recorded, or found in {project} now; nothing was "
                                   f"dismissed{self._flagged(used)}")
            where = suppression_store.open_in(db, finding_id)
            if checked not in where:
                elsewhere = f" (it is found in {len(where)} other project{'s' if len(where) != 1 else ''})" if where else ""
                raise Refused(409, f"{finding_id} isn't found in {project} now{elsewhere}, so nothing was dismissed: "
                                   f"dismiss it from a project where it is found{self._flagged(used)}")
            item = suppression_store.suppress(db, finding_id, reason, datetime.now(UTC))
            entry = _entry(db, item)
        except suppression_store.UnknownFinding as error:
            raise Refused(404, str(error)) from error
        except sqlite3.Error as error:
            raise Unreadable(f"the agentlint database: {error}") from error
        finally:
            db.close()
        armed = sum(1 for open_there in where if item.baselines.get(open_there))
        note = (f"Dismissed in every project. It stays hidden in each until what it rests on there changes — armed now "
                f"in the {len(where)} project{'s' if len(where) != 1 else ''} where it is found, and anywhere else the "
                "first time a check sees it there." if armed else
                "Dismissed in every project. It rests on no source agentlint can watch yet, so it stays hidden unless a "
                "newly configured source joins it, or until you show it again.")
        return {"suppression": entry, "note": note}

    def _usable(self, db: sqlite3.Connection) -> settings.Settings:
        """The settings a dismissal's quiet check reads with, refused where `agentlint suppress` refuses them (main()):
        a config.toml that can't be read (tried again first, as every request does) or a stored value that can't be
        used — the check would read with a default you never set and store that reading as comparable."""
        self._retry_import(db)
        if self.blocked and settings.legacy_file().is_file():
            raise Refused(409, f"{self.blocked}. Nothing was dismissed")
        current, problems = settings.read(db)
        if problems:
            key, why = next(iter(problems.items()))
            raise Refused(409, f"{why}, so the project can't be checked and nothing was dismissed; change {key} in "
                               "Settings")
        return current

    def _flagged(self, used: settings.Settings) -> str:
        """Why a finding the page shows isn't found by a dismissal's check: the page reads `agentlint ui --sessions N`'s
        N sessions, the check (as `suppress` does) your sessions setting's — a reading comparable with the stored ones."""
        if self.sessions is None or self.sessions == used.sessions:
            return ""
        return (f". The page reads {self.sessions} recent sessions (agentlint ui --sessions {self.sessions}), while a "
                f"dismissal checks the project with your sessions setting ({used.sessions}), as `agentlint suppress` "
                f"does: a finding only that many sessions show can be dismissed once the setting is {self.sessions}")

    def unsuppress(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/unsuppress ``{finding_id}``: shown again everywhere, its baselines gone, as `agentlint unsuppress`."""
        _fields(body, "finding_id")
        finding_id = _finding_id(body)
        db = self._db()
        try:
            if _whole(db, finding_id) is None:
                raise Refused(404, f"no finding {finding_id} is recorded or dismissed")
            was = suppression_store.unsuppress(db, finding_id)
        except sqlite3.Error as error:
            raise Unreadable(f"the agentlint database: {error}") from error
        finally:
            db.close()
        return {"finding_id": finding_id, "was_dismissed": was}
