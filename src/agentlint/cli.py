"""Command-line entry point — the composition root: the one place that wires an adapter in.

Exit codes follow docs/tech_impl/10-reporting.md: 0 clean, 1 findings at or above the
--fail-on threshold, 2 tool error.

main() resolves the flags and your settings once into a frozen ``Options`` (settings.py), and every ``cmd_*``
receives it with the parsed arguments, which are never changed. A flag over a setting is held to that setting's
range by argparse (``bounded``), so a value out of range is a usage error (exit 2) before anything is read.
Each command's parser names its function (``set_defaults(func=...)``). A command that can't go on raises
``ToolError``, which main() prints and exits 2 for; ``store()`` opens the database for a block and turns its
failures into one.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from agentlint import __version__
from agentlint.harness import Adapter, read_config
from agentlint.settings import Options, Settings, range_of

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TOOL_ERROR = 2


def _claude_code() -> Adapter:
    from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter

    return ClaudeCodeAdapter()


# Every harness agentlint has an adapter for, by name, in the order they are offered: adding a harness is one entry.
ADAPTERS: dict[str, Callable[[], Adapter]] = {"claude-code": _claude_code}


def _claude_code_tests():
    """Claude Code's parts for testing: the adapter, the launcher that runs one situation, and the generator that writes
    situations with the model it asks for (D14)."""
    from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
    from agentlint.adapters.claude_code.generator import MODEL, ClaudeCodeGenerator
    from agentlint.adapters.claude_code.launch import ClaudeCodeLauncher
    from agentlint.ui.testing import Parts

    return Parts(ClaudeCodeAdapter(), ClaudeCodeLauncher(), ClaudeCodeGenerator(), MODEL)


# The harnesses whose tests agentlint can run: a harness with a launcher and a generator is one entry.
TESTS: dict[str, Callable[[], object]] = {"claude-code": _claude_code_tests}


def worker_command(job_id: str) -> list[str]:
    """How the UI launches a job's worker: this Python, ``-m agentlint.runner <job id>`` (internal, not a command)."""
    return [sys.executable, "-m", "agentlint.runner", job_id]


def read_worker_command(read_id: str) -> list[str]:
    """How the UI launches a reading's worker: ``-m agentlint.directives <read id>`` (internal, not a command)."""
    return [sys.executable, "-m", "agentlint.directives", read_id]


def run_read_worker(argv: list[str]) -> int:
    """``python -m agentlint.directives <read_id>``: read a reading's sections with its harness's generator."""
    import os

    from agentlint.directives import worker
    from agentlint.store import connect

    if len(argv) != 1:
        print("usage: python -m agentlint.directives <read_id> (the UI starts this; it isn't a command to type)",
              file=sys.stderr)
        return worker.EXIT_REFUSED
    try:
        return worker.work(argv[0], connect=connect, parts=lambda h: TESTS[h]() if h in TESTS else None,
                           environ=os.environ)
    except ValueError as error:  # not a reading id
        print(f"agentlint: {error}", file=sys.stderr)
        return worker.EXIT_REFUSED


def run_worker(argv: list[str]) -> int:
    """``python -m agentlint.runner <job_id>``: run or resume the job with its harness's parts (``runner/worker.py``)."""
    import os

    from agentlint.runner import worker
    from agentlint.store import connect

    if len(argv) != 1:
        print("usage: python -m agentlint.runner <job_id> (the UI starts this; it isn't a command to type)",
              file=sys.stderr)
        return worker.EXIT_REFUSED

    def parts(harness: str):
        made = TESTS[harness]() if harness in TESTS else None
        return (made.adapter, made.launcher) if made is not None else None  # type: ignore[attr-defined]

    try:
        return worker.work(argv[0], connect=connect, parts=parts, environ=os.environ)
    except ValueError as error:  # not a job id
        print(f"agentlint: {error}", file=sys.stderr)
        return worker.EXIT_REFUSED


def _adapter() -> Adapter:
    """The adapter the commands other than `ui` read through."""
    return _claude_code()


class ToolError(Exception):
    """A command can't go on — the database can't be opened or failed in use, a check or a reading couldn't run: the
    message says why. main() prints it and exits 2 (EXIT_TOOL_ERROR), never 1, which reads as findings."""


@contextmanager
def store() -> Iterator[sqlite3.Connection]:
    """The agentlint database, open for the block and closed after it however the block ends. One that can't be
    opened (StoreError) or fails in use (sqlite3.Error, or OSError: the block may read transcripts to import them) is
    a ToolError, said as main() says every tool error."""
    from agentlint.store import StoreError, connect

    try:
        db = connect()
    except StoreError as error:
        raise ToolError(str(error)) from error
    try:
        yield db
    except StoreError as error:
        raise ToolError(str(error)) from error
    except (sqlite3.Error, OSError) as error:
        raise ToolError(f"the agentlint database: {error}") from error
    finally:
        db.close()


def _read(adapter: Adapter, cwd: Path, sessions: int):
    """``read_config`` with the store's forget cutoff: no session from before you last forgot is read back in — and
    with the targets' latest tests from the store (``Reading.tests``, the behaviour checks' evidence). A ToolError when
    the store can't be read, as the cutoff can't be honoured then."""
    from agentlint.store import usage as usage_store

    with store() as db:
        forgotten = usage_store.cutoff(db)
    reading = read_config(adapter, cwd, sessions=sessions, forgotten_before=forgotten)
    with store() as db:
        return _with_tests(db, adapter, reading)


def _with_tests(db: sqlite3.Connection, adapter: Adapter, reading):
    """The reading with what tests said of its targets (``runner/tested.py``; unchanged when this adapter's runs can't
    be read here) and the directives stored for its sections (``_directives``)."""
    from dataclasses import replace

    from agentlint.runner import tested

    found = tested.load(db, reading.snapshot, adapter.name, getattr(adapter, "action_terms", None))
    if found is not None:
        reading = replace(reading, tests=found)
    return replace(reading, directives=_directives(db, reading.snapshot))


def _directives(db: sqlite3.Connection, snapshot):
    """The directives stored for the snapshot's sections (``directives/extract.py``): read when you chose to, never
    here."""
    from agentlint.config.reading import DirectiveEvidence
    from agentlint.directives.extract import occurrences

    found, read, total = occurrences(db, snapshot)
    return DirectiveEvidence(tuple(found), read, total)


def _adapters(settings: Settings) -> list[Adapter]:
    """Every harness agentlint reads that is installed here: in the harnesses setting's order when it names any
    (a name agentlint can't read is skipped), else every one agentlint has an adapter for."""
    names = [name for name in settings.harnesses if name in ADAPTERS] if settings.harnesses else list(ADAPTERS)
    made = [ADAPTERS[name]() for name in dict.fromkeys(names)]
    return [adapter for adapter in made if adapter.installed()]


def _port(text: str) -> int:
    """--port: a TCP port, or 0 for any free one. Anything else is a usage error (exit 2), not a traceback."""
    try:
        port = int(text)
    except ValueError:
        port = -1
    if not 0 <= port <= 65_535:
        raise argparse.ArgumentTypeError(f"{text!r} isn't a port: a whole number from 0 to 65535 (0: any free one)")
    return port


# `agentlint rank`'s own budget (a developer's view of the ranker's planning, 03-ranker.md). It is no setting: a test
# from the UI runs what you tick (11-ui.md "Tests"), and budget-driven planning returns as the overnight layer.
RANK_RUNS = 60
RANK_RUNS_RANGE = (1, 100_000)


def _rank_runs(text: str) -> int:
    """`rank --runs`: a whole number from 1 to 100,000; anything else is a usage error (exit 2)."""
    low, high = RANK_RUNS_RANGE
    try:
        value = int(text)
    except ValueError:
        value = None
    if value is None or not low <= value <= high:
        raise argparse.ArgumentTypeError(f"{text!r} isn't a whole number from {low} to {high}")
    return value


def bounded(key: str):
    """The argparse ``type=`` for a flag over the setting ``key``: a whole number in that setting's range (read
    from its field in settings.py). Anything else is a usage error (exit 2) naming the flag and the range."""
    low, high = range_of(key)

    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            value = None
        if value is None or not low <= value <= high:
            raise argparse.ArgumentTypeError(f"{text!r} isn't a whole number from {low} to {high} (the range of the "
                                             f"{key} setting)")
        return value

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentlint",
        description="Check an agent's configuration: what contradicts itself, what does "
        "nothing, and where the agent can't make up its mind.",
    )
    parser.add_argument("--version", action="version", version=f"agentlint {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    check = commands.add_parser("check", help="run the static checks and print findings")
    check.set_defaults(func=cmd_check)
    check.add_argument("--only", metavar="IDS", help="comma-separated check ids to run")
    check.add_argument("--json", action="store_true", help="print findings as JSON")
    check.add_argument("--fail-on", choices=["info", "warn", "error"], help="exit 1 if any finding is at or above this")
    check.add_argument("--list", action="store_true", help="list the available checks")
    check.add_argument("--detail", action="store_true", help="print info findings in full, not one line each")
    check.add_argument("--cwd", type=Path, default=Path.cwd(), help="project directory (default: here)")
    _sessions_flag(check)

    suppress = commands.add_parser("suppress", help="hide a finding until what it rests on changes")
    suppress.set_defaults(func=cmd_suppress)
    suppress.add_argument("finding", nargs="?", help="finding id, or any unique prefix of at least 4 characters")
    suppress.add_argument("--reason", help="why you are dismissing it (required)")
    suppress.add_argument("--list", action="store_true", help="list suppressions, and where they have lapsed")
    suppress.add_argument("--cwd", type=Path, default=Path.cwd(),
                          help="the project you saw it in: checked first, so it is armed there against it now (default: here)")
    unsuppress = commands.add_parser("unsuppress", help="show a suppressed finding again")
    unsuppress.set_defaults(func=cmd_suppress)
    unsuppress.add_argument("finding", help="finding id, or any unique prefix of at least 4 characters")

    sync = commands.add_parser("sync", help="archive new transcripts and derive usage now; --daily to keep doing it")
    sync.set_defaults(func=cmd_sync)
    when = sync.add_mutually_exclusive_group()
    when.add_argument("--daily", action="store_true", help="install a daily job in your system's scheduler (not the harness)")
    when.add_argument("--off", action="store_true", help="remove the daily job")
    sync.add_argument("--quiet", action="store_true", help="print nothing unless something goes wrong (for the job)")

    forget = commands.add_parser("forget", help="delete everything agentlint kept from transcripts: archive, usage, stored readings, findings and test runs")
    forget.set_defaults(func=cmd_forget)
    forget.add_argument("--yes", action="store_true", help="don't ask")

    config = commands.add_parser("config", help="your settings, in the agentlint database — the same as Settings in agentlint ui")
    config.set_defaults(func=cmd_config)
    config.add_argument("action", choices=["list", "get", "set", "unset"], help="what to do")
    config.add_argument("key", nargs="?", help="a setting, as `agentlint config list` names them: sessions, usage_days, "
                                               "keep_days, runs_per_situation, …")
    config.add_argument("value", nargs="?", help="for set: a number, or a list like codex,opencode")

    rank = commands.add_parser("rank", help="the testing plan: what would be tested, in what order — spends nothing")
    rank.set_defaults(func=cmd_rank)
    rank.add_argument("--runs", type=_rank_runs, default=RANK_RUNS, metavar="N",
                      help=f"the budget, in agent runs (default: {RANK_RUNS}). Tests in agentlint ui use no budget: a test "
                           "runs the targets you tick")
    rank.add_argument("--phase", choices=["stability", "ablation"], default="ablation",
                      help="stability: does the agent agree with itself (what Tests in agentlint ui plans and runs, at your "
                           "runs_per_situation); ablation: does removing each target change what it does")
    rank.add_argument("--explain", metavar="TARGET",
                      help="why one target (a rule, or a whole skill or hook) is where it is, or why it was skipped")
    rank.add_argument("--all", action="store_true", help="list every target, planned, deferred and skipped")
    rank.add_argument("--cwd", type=Path, default=Path.cwd(), help="project directory (default: here)")

    ui = commands.add_parser("ui", help="the report in your browser, read fresh on every load (this machine only)")
    ui.set_defaults(func=cmd_ui)
    ui.add_argument("--cwd", type=Path, default=Path.cwd(), help="project directory (default: here)")
    ui.add_argument("--port", type=_port, default=0, help="port on 127.0.0.1 (default: any free one)")
    ui.add_argument("--no-open", action="store_true", help="print the address; don't open a browser")
    _sessions_flag(ui)

    usage = commands.add_parser("usage", help="which skills and agents your turns actually used")
    usage.set_defaults(func=cmd_usage)
    usage.add_argument("--days", type=bounded("usage_days"), default=None, metavar="N", help="the window, in days (default: the usage_days setting)")
    usage.add_argument("--unused", action="store_true", help="list the skills and agents never used in the window")
    usage.add_argument("--cwd", type=Path, default=Path.cwd(), help="project directory (default: here)")

    show = commands.add_parser("show", help="the assembled configuration, ranked by token cost")
    show.set_defaults(func=cmd_show)
    show.add_argument("--json", action="store_true", help="print the full snapshot as JSON")
    show.add_argument("--always-only", action="store_true", help="only what is loaded every session")
    show.add_argument("--rules", metavar="SOURCE_ID", help="list the rules inside one source")
    show.add_argument("--all", action="store_true", help="list every source, not just the largest")
    show.add_argument("--diff", nargs="?", const="", metavar="FINGERPRINT",
                      help="what changed since the previous configuration of this project (or a given fingerprint)")
    show.add_argument("--text", action="store_true", help="with --diff: print what changed in each text")
    show.add_argument("--snapshots", action="store_true", help="list the stored snapshots of this project")
    show.add_argument("--cwd", type=Path, default=Path.cwd(), help="project directory (default: here)")
    _sessions_flag(show)
    return parser


def _sessions_flag(command: argparse.ArgumentParser) -> None:
    """--sessions N, the sessions setting for one command."""
    command.add_argument("--sessions", type=bounded("sessions"), default=None, metavar="N",
                         help="recent sessions to read for what disk cannot show (default: the sessions setting)")


def _incomparable(options: Options) -> str:
    """Why this reading isn't comparable (``options.comparable`` is false), for the note saying it wasn't kept."""
    return f"--sessions {options.sessions} isn't your sessions setting ({options.settings.sessions})"


def _ui_unrecorded(options: Options) -> str:
    """What the UI says, on Findings and What changed, about a reading it read but didn't record (and `agentlint ui`
    once as it starts): why, and what that leaves out."""
    return (f"Not recorded: agentlint ui was started with --sessions {options.sessions}, which isn't your sessions "
            f"setting ({options.settings.sessions}), so no reading here is stored or compared with stored ones — "
            "nothing is added to What changed or to the findings' history.")


def _run_checks(reading, checks):
    """``run_checks``, a check that couldn't run (bad input it was given) a ToolError."""
    from agentlint.checks import CheckError, run_checks

    try:
        return run_checks(reading, checks)
    except CheckError as error:
        raise ToolError(str(error)) from error


def cmd_check(args: argparse.Namespace, options: Options) -> int:
    from datetime import UTC, datetime

    from agentlint.checks import UnknownCheck, select
    from agentlint.checks.registry import CORE_CHECKS
    from agentlint.config.serialize import SNAPSHOT_FORMAT_VERSION, to_data
    from agentlint.report.finding import Severity
    from agentlint.report.terminal import render
    from agentlint.store import transaction
    from agentlint.store.findings import record
    from agentlint.store.snapshots import save

    adapter = _adapter()
    available = [*CORE_CHECKS, *adapter.checks()]
    if args.list:
        summaries: dict[str, str] = {}
        for check in available:
            summaries.setdefault(check.id, check.summary)  # an id with several contributors is listed once
        for check_id, summary in summaries.items():
            print(f"{check_id:<24} {summary}")
        return EXIT_OK
    try:
        chosen = select(available, args.only.split(",") if args.only else None)
    except UnknownCheck as error:
        raise ToolError(str(error)) from error
    recording = options.comparable
    reading = _read(adapter, args.cwd.resolve(), options.sessions)
    snapshot = reading.snapshot
    results = _run_checks(reading, chosen)
    now = datetime.now(UTC)
    with store() as db:
        _import(db, adapter, now, options.settings)  # each transcript its own transaction: an interruption keeps progress
        with transaction(db):  # the snapshot and the lifecycle land together or not at all
            if recording:
                save(db, snapshot, now)
            lifecycle = record(
                db, project=str(snapshot.cwd), results=results, snapshot=snapshot, at=now, write=recording,
                note=None if recording else f"Not recorded: {_incomparable(options)}, so this reading isn't compared with stored ones.")
    if args.json:
        data = {"schema_version": SNAPSHOT_FORMAT_VERSION, "fingerprint": snapshot.fingerprint, "recorded": recording,
                "checks": list(dict.fromkeys(c.id for c in chosen)), "not_evaluated": results.not_evaluated,
                "findings": [{"id": t.finding.id, "suppressed": t.suppressed, "suppression_lapsed": t.suppression_lapsed,
                              **to_data(t.finding)} for t in lifecycle.tracked]}
        json.dump(data, sys.stdout, indent=1)  # ASCII: valid JSON whatever the locale
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render(snapshot, results, lifecycle, detail=args.detail))
    if args.fail_on:
        threshold = Severity(args.fail_on).rank
        if any(t.finding.severity.rank >= threshold for t in lifecycle.shown):  # suppressed never fail a build
            return EXIT_FINDINGS
    return EXIT_OK


def cmd_suppress(args: argparse.Namespace, options: Options) -> int:
    from datetime import UTC, datetime

    from agentlint.store.suppressions import (NoMatch, SuppressionState, UnknownFinding, latest, load, open_in, resolve,
                                              status, suppress, unsuppress)

    with store() as db:
        try:
            if args.command == "suppress" and args.list:
                suppressions = load(db)
                if not suppressions:
                    print("No suppressions.")
                for item in sorted(suppressions.values(), key=lambda s: s.at):
                    seen = latest(db, item.id)
                    check, title = (seen[0], seen[1]) if seen else ("?", "(no longer recorded in any project)")
                    states = status(db, item)
                    active = sum(1 for s in states.values() if s is SuppressionState.ACTIVE)
                    lapsed = [project for project, s in states.items() if s is SuppressionState.LAPSED]
                    moot = [project for project, s in states.items() if s is SuppressionState.MOOT]
                    state = " · ".join([
                        *([f"active in {active} project{'s' if active != 1 else ''}"] if active or not states else []),
                        *([f"inputs changed since, shown again in: {', '.join(lapsed)}"] if lapsed else []),
                        *([f"no longer found in: {', '.join(moot)}"] if moot else []),
                    ])
                    print(f"{item.id[:12]}  {check:<22} {title}\n{'':14}{item.at.astimezone():%Y-%m-%d}  "
                          f"\"{item.reason}\"  [{state}]")
                return EXIT_OK
            if not args.finding:
                print("agentlint: name a finding id (or use --list)", file=sys.stderr)
                return EXIT_TOOL_ERROR
            if args.command == "unsuppress":
                finding_id = resolve(db, args.finding)
                print(f"Unsuppressed {finding_id[:12]}." if unsuppress(db, finding_id) else f"{finding_id[:12]} was not suppressed.")
                return EXIT_OK
            if not args.reason or not args.reason.strip():
                print("agentlint: say why with --reason, so the dismissal can be revisited", file=sys.stderr)
                return EXIT_TOOL_ERROR
            reason = args.reason.strip().encode("utf-8", "replace").decode("utf-8")  # undecodable argv bytes
            # Check the project first, the way `check` would, so the finding is recorded there (the UI records it only
            # when a page loads, and never with another --sessions) and armed against what it rests on
            # there now — even when a check recorded it elsewhere,
            # or long ago here: the project the user saw it in must be able to lapse it, and must not pair an old row with
            # newer configuration.
            try:
                resolve(db, args.finding)  # a malformed or ambiguous id fails before anything is read
            except NoMatch:
                pass
            cwd = args.cwd.resolve()
            project = _check_quietly(db, cwd, options.settings)
            try:
                finding_id = resolve(db, args.finding)
            except NoMatch:
                finding_id = None
            if finding_id is None or latest(db, finding_id) is None:
                print(f"agentlint: no finding id starts with {args.finding.strip().lower()}, recorded or found in {cwd} "
                      "now; `agentlint check` lists current ids", file=sys.stderr)
                return EXIT_TOOL_ERROR
            item = suppress(db, finding_id, reason, datetime.now(UTC))
            where = open_in(db, finding_id)
            check, title, _ = latest(db, finding_id, project if project in where else None)  # as checked just now
            armed = len(where)
            if not armed:
                watch = ("No check finds it in any project now; it is armed against what it rests on the first time a "
                         "check sees it.")
            elif not any(item.baselines.get(open_there) for open_there in where):
                watch = ("It rests on no source agentlint can watch yet, so it stays hidden unless a newly configured "
                         "source joins it, or until you unsuppress it.")
            else:
                watch = (f"It stays hidden in each project until what it rests on there changes — armed now in the {armed} "
                         f"project{'s' if armed != 1 else ''} where it is open, and anywhere else the first time a check "
                         "sees it there.")
            print(f"Suppressed {finding_id[:12]} in every project — {check}: {title}\n{watch}")
            return EXIT_OK
        except UnknownFinding as error:
            raise ToolError(str(error)) from error


def _check_quietly(db, cwd: Path, settings: Settings, adapter: Adapter | None = None) -> str:
    """Check a project as `check` does by default — every check, a comparable reading, stored and recorded — and
    print nothing: with the harness found here, or ``adapter`` (the UI's scope). Returns the project as the store names
    it; a ToolError if a check couldn't run, or if the harnesses setting leaves out the harness it would read: then
    nothing is read or stored, as every reading command refuses."""
    from datetime import UTC, datetime

    from agentlint.checks.registry import CORE_CHECKS
    from agentlint.store import transaction
    from agentlint.store.findings import record
    from agentlint.store.snapshots import save

    adapter = adapter or _adapter()
    if settings.harnesses and adapter.name not in settings.harnesses:
        raise ToolError(f"suppress checks the project first, and your harnesses setting ({', '.join(settings.harnesses)}) "
                        f"leaves out {adapter.name}, the harness it would read, so nothing was read or suppressed; "
                        "`agentlint config unset harnesses` reads every one found (`suppress --list` and `unsuppress` "
                        "still run)")
    from agentlint.store import usage as usage_store

    reading = _with_tests(db, adapter, read_config(adapter, cwd, sessions=settings.sessions,
                                                   forgotten_before=usage_store.cutoff(db)))
    snapshot = reading.snapshot
    results = _run_checks(reading, [*CORE_CHECKS, *adapter.checks()])
    now = datetime.now(UTC)
    with transaction(db):
        save(db, snapshot, now)
        record(db, project=str(snapshot.cwd), results=results, snapshot=snapshot, at=now, write=True, note=None)
    return str(snapshot.cwd)


def _report(adapter: Adapter, cwd: Path, options: Options, *, prune: bool = True) -> tuple[dict, dict]:
    """Read the configuration, run every check, record the lifecycle and store the snapshot exactly as `check` does
    (a reading at another number of sessions is neither: it reads the lifecycle, and the report's ``not_recorded``
    says why), count usage: the report the UI shows, served fresh by `ui`. Reading an unchanged configuration again adds nothing: its snapshot
    and its analysis are replaced, and no finding opens or closes. A ToolError when it can't be read."""
    from datetime import UTC, datetime, timedelta

    from agentlint.checks.registry import CORE_CHECKS
    from agentlint.report.document import report_data
    from agentlint.store import transaction
    from agentlint.store import usage as usage_store
    from agentlint.store.findings import record
    from agentlint.store.snapshots import save

    checks = [*CORE_CHECKS, *adapter.checks()]
    summaries: dict[str, str] = {}
    for check in checks:
        summaries.setdefault(check.id, check.summary)  # an id with several contributors: its first summary
    recording = options.comparable
    reading = _read(adapter, cwd, options.sessions)
    snapshot = reading.snapshot
    results = _run_checks(reading, checks)
    now = datetime.now(UTC)
    with store() as db:
        _import(db, adapter, now, options.settings, prune=prune)
        usage = usage_store.index(db, since=now - timedelta(days=options.days))
        with transaction(db):  # the snapshot and the lifecycle land together or not at all, as in `check`
            if recording:
                save(db, snapshot, now)
            lifecycle = record(
                db, project=str(snapshot.cwd), results=results, snapshot=snapshot, at=now, write=recording,
                note=None if recording else _ui_unrecorded(options))
    return report_data(snapshot, results, lifecycle, summaries=summaries, generated_at=now, version=__version__,
                       usage=usage, usage_days=options.days, harness_label=adapter.label,
                       package_label=adapter.package_label, home_dir=Path.home())


def cmd_sync(args: argparse.Namespace, options: Options) -> int:
    from datetime import UTC, datetime

    from agentlint import schedule
    from agentlint.store import archive, home

    try:
        if args.off:
            print("Daily sync removed." if schedule.remove() else "There was no daily sync to remove.")
            return EXIT_OK
        if args.daily:
            job = schedule.install(home())
            print(f"Daily sync on: {job.scheduler} runs `agentlint sync` each day at "
                  f"{schedule.HOUR:02d}:{schedule.MINUTE:02d} ({job.where}). `agentlint sync --off` removes it.")
            return EXIT_OK
    except (schedule.ScheduleError, OSError) as error:
        print(f"agentlint: {error}", file=sys.stderr)
        return EXIT_TOOL_ERROR
    from agentlint.store import state as state_store

    try:
        adapter = _adapter()  # inside: no harness here is a failed sync too
        with store() as db:
            changed, turns = _import(db, adapter, datetime.now(UTC), options.settings)
            files, size, stored = archive.totals(db)
            state_store.put(db, state_store.LAST_SYNC, {"ok": True, "changed": changed, "turns": turns}, datetime.now(UTC))
    except Exception as error:  # Settings shows the last sync's result, a failure's too
        _sync_failed(error)
        raise
    if not args.quiet:
        job = schedule.installed()
        print(f"Archived {changed:,} transcript{'s' if changed != 1 else ''} that changed; {turns:,} turns of usage derived.\n"
              f"The archive keeps {files:,} transcripts — {size / 1e6:,.0f} MB as written, {stored / 1e6:,.0f} MB stored — "
              f"for {options.settings.keep_days} days.\n"
              + (f"Daily sync: on ({job.where})." if job else "Daily sync: off — `agentlint sync --daily` keeps the archive "
                 "current even when agentlint isn't run."))
    return EXIT_OK


def _sync_failed(error: Exception) -> None:
    """Record that a sync failed, and why, if the database can still be written; else nothing more can be done."""
    from datetime import UTC, datetime

    from agentlint.store import StoreError, connect
    from agentlint.store import state as state_store

    try:
        db = connect()
        try:
            state_store.put(db, state_store.LAST_SYNC, {"ok": False, "error": str(error) or type(error).__name__},
                            datetime.now(UTC))
        finally:
            db.close()
    except (StoreError, sqlite3.Error, OSError):
        pass


def cmd_forget(args: argparse.Namespace, options: Options) -> int:
    """Delete everything agentlint kept from transcripts, asked first unless --yes. The UI's Forget everything calls
    the same ``forget_everything``."""
    from datetime import UTC, datetime

    from agentlint.forget import JobsRunning, forget_everything

    if not args.yes:
        try:
            answer = input("Delete every archived transcript, all usage history, and every stored reading, finding, test run, generated situation and diagnostic agentlint keeps "
                           "(your settings and suppressions stay)? [y/N] ")
        except (EOFError, OSError):
            print("agentlint: no answer (nothing to ask on stdin), so nothing deleted; "
                  "`agentlint forget --yes` deletes without asking", file=sys.stderr)
            return EXIT_TOOL_ERROR
        if answer.strip().lower() not in ("y", "yes"):
            print("Nothing deleted.")
            return EXIT_OK
    with store() as db:
        try:  # the test runs' directories go too: whole variant homes, each holding its run's transcript
            gone = forget_everything(db, datetime.now(UTC), [_adapter()])
        except JobsRunning as error:
            raise ToolError(f"nothing deleted: {error}") from error
    for failure in gone.undeleted:
        print(f"agentlint: could not delete a test run's directory ({failure}); the next forget or test run tries "
              "again", file=sys.stderr)
    print(f"Forgot {gone.turns:,} turns, every archived transcript, and every stored reading, finding, test run, generated situation and diagnostic (your settings and suppressions "
          "stay). Of each transcript agentlint keeps only where it is, its session, how far it had got, when it last "
          "changed and a hash of its last bytes, so what it held is not read back in. No reading of your "
          "configuration uses a session that started before now; usage counts only turns taken after now, including "
          "those of a session that carries on. `agentlint ui`'s transcript reader still shows the sessions your "
          "harness keeps when you open them, storing nothing.")
    return EXIT_OK


def cmd_config(args: argparse.Namespace, options: Options) -> int:
    from agentlint import settings

    try:
        if args.action == "list":
            with store() as db:
                current, problems = settings.read(db)
            shown = {key: settings.shown(key, getattr(current, key)) for key in settings.KEYS}
            # The longest key and the longest value shown size their columns, so every row's columns line up.
            width = max(len(key) for key in settings.KEYS)
            value_width = max(len(text) for text in shown.values())
            for key in settings.KEYS:
                where = ("default — the stored value can't be used" if key in problems
                         else "set" if key in current.configured else "default")
                print(f"{key:<{width}} {shown[key]:<{value_width}} {where}")
            for problem in problems.values():
                print(f"\n{problem}", file=sys.stderr)
            return EXIT_OK
        if not args.key:
            print(f"agentlint: config {args.action} needs a setting: {', '.join(settings.KEYS)}", file=sys.stderr)
            return EXIT_TOOL_ERROR
        if args.key not in settings.KEYS:
            print(f"agentlint: no setting called {args.key} (settings: {', '.join(settings.KEYS)})", file=sys.stderr)
            return EXIT_TOOL_ERROR
        if args.action == "get":
            with store() as db:
                current, problems = settings.read(db)
            if args.key in problems:  # never the default printed as if it were the value
                default = getattr(settings.DEFAULTS, args.key)
                print(f"agentlint: {problems[args.key]}; `agentlint config set {args.key} <value>` sets one, `agentlint "
                      f"config unset {args.key}` puts back its default "
                      f"({settings.shown(args.key, default)})",
                      file=sys.stderr)
                return EXIT_TOOL_ERROR
            value = getattr(current, args.key)
            print(", ".join(value) if isinstance(value, tuple) else value)
        elif args.action == "set":
            if args.value is None:
                print(f"agentlint: config set {args.key} needs a value", file=sys.stderr)
                return EXIT_TOOL_ERROR
            with store() as db:
                value = settings.set_value(db, args.key, args.value)
            print(f"{args.key} = {', '.join(value) if isinstance(value, tuple) else value}")
            unknown = [h for h in value if h not in ADAPTERS] if args.key == "harnesses" else []
            if unknown:
                print(f"(agentlint can't read {', '.join(unknown)} yet; it reads {', '.join(ADAPTERS)}.)", file=sys.stderr)
        else:  # unset
            with store() as db:
                settings.unset(db, args.key)
            default = getattr(settings.DEFAULTS, args.key)
            print(f"{args.key} is back to its default: {settings.shown(args.key, default)}")
    except settings.SettingsError as error:
        print(f"agentlint: {error}", file=sys.stderr)
        return EXIT_TOOL_ERROR
    return EXIT_OK


def cmd_rank(args: argparse.Namespace, options: Options) -> int:
    from datetime import UTC, datetime

    from agentlint.checks.registry import CORE_CHECKS
    from agentlint.rank.plan import Phase, RunCounts, plan
    from agentlint.report.rank import explain, render
    from agentlint.store import usage as usage_store
    from agentlint.store.verdicts import statuses

    adapter = _adapter()
    reading = _read(adapter, args.cwd.resolve(), options.sessions)
    snapshot = reading.snapshot
    results = _run_checks(reading, [*CORE_CHECKS, *adapter.checks()])  # what a static check settles isn't run
    with store() as db:
        _import(db, adapter, datetime.now(UTC), options.settings)
        usage = usage_store.index(db)  # all kept history: evidence of use, however old
        measured = statuses(db, str(snapshot.cwd))
    # Situations per target and runs per situation are your settings, as Tests › Plan has them: `rank --phase stability`
    # ranks the targets the UI lists, in the same order. Its budget is rank's own (--runs); a test from the UI has none.
    counts = RunCounts(situations=options.settings.situations_per_target, stability=options.settings.runs_per_situation)
    result = plan(snapshot, findings=results.findings, usage=usage, statuses=measured, budget=args.runs,
                  phase=Phase(args.phase), counts=counts)
    names = snapshot.entities_by_source
    if args.explain:
        text = explain(result, args.explain, names)
        if text is None:
            print(f"agentlint: no target matches {args.explain!r}; `agentlint rank --all` lists them", file=sys.stderr)
            return EXIT_TOOL_ERROR
        sys.stdout.write(text)
        return EXIT_OK
    sys.stdout.write(render(result, names, everything=args.all))
    return EXIT_OK


def cmd_ui(args: argparse.Namespace, options: Options) -> int:
    import webbrowser

    from agentlint.settings import HOLD
    from agentlint.store import connect, home
    from agentlint.ui.catalog import Catalogs, SessionCatalog
    from agentlint.ui.control import StoreControl, SystemScheduler
    from agentlint.ui.records import StoredRecords
    from agentlint.ui.server import NoSessions, Refused, UIServer, Unreadable
    from agentlint.ui.shell import ShellError, shell

    adapters = _adapters(options.settings)  # the setting as it starts
    attention: list[str] = []  # what the page opens Settings for
    if not adapters:
        wanted = [name for name in options.settings.harnesses if name in ADAPTERS] or list(ADAPTERS)
        if not options.settings.harnesses:  # nothing to show, and no setting to change: there is no harness here
            print(f"agentlint: no harness agentlint reads is installed on this machine (it looked for {', '.join(wanted)}), "
                  "so there is nothing to show", file=sys.stderr)
            return EXIT_TOOL_ERROR
        attention.append(f"Your harnesses setting ({', '.join(options.settings.harnesses)}) names no harness agentlint "
                         f"can read on this machine (it reads {', '.join(ADAPTERS)}, if installed). Change it here, then "
                         "start agentlint ui again.")
    catalogs = Catalogs([SessionCatalog(adapter, connect) for adapter in adapters], args.cwd.resolve()) if adapters else None

    def scope(params: dict):
        """The harness and project a request names; with no harness to read, why (Settings says so too)."""
        if catalogs is None:
            raise Unreadable(attention[0])
        return catalogs.scope(params)

    def build(params: dict):
        from agentlint import settings

        adapter, project = scope(params)
        try:
            with store() as db:  # the settings as they are now: Settings and `config set` reach an open UI
                current = options.reread(settings.read(db)[0])  # one that can't be used counts as its default
            return _report(adapter, project, current, prune=False)  # pruned as it started, not per load
        except ToolError as error:
            raise Unreadable(str(error)) from error

    def check(params: dict) -> str:
        """Dismissing from the page checks the scope's project first, as `suppress --cwd` does (StoreControl.suppress,
        which has already refused the settings `suppress` would be turned away for)."""
        from agentlint import settings

        if catalogs is None:
            raise Refused(409, attention[0])
        adapter, project = catalogs.scope(params)  # only a harness and a project the server lists
        try:
            with store() as db:
                current = settings.read(db)[0]  # as they are now
                if current.harnesses and adapter.name not in current.harnesses:
                    raise Refused(409, f"your harnesses setting ({', '.join(current.harnesses)}) leaves out "
                                       f"{adapter.name}, so its project can't be checked and nothing was dismissed; "
                                       "change it in Settings")
                return _check_quietly(db, project, current, adapter)
        except ToolError as error:
            raise Unreadable(str(error)) from error

    def read_for_tests(adapter: Adapter, project: Path):
        """A project read for the Tests screens as `rank` reads it: at your sessions setting, its transcripts archived
        first (usage tells the ranker what is used), and every check run (what one proves broken isn't tested)."""
        from datetime import UTC, datetime

        from agentlint import settings
        from agentlint.checks.registry import CORE_CHECKS

        try:
            with store() as db:
                current = settings.read(db)[0]
                _import(db, adapter, datetime.now(UTC), current, prune=False)
            reading = _read(adapter, project, current.sessions)
            return reading, _run_checks(reading, [*CORE_CHECKS, *adapter.checks()]).findings
        except ToolError as error:
            raise Unreadable(str(error)) from error

    def test_parts(harness: str):
        made = TESTS[harness]() if harness in TESTS else None
        return made

    def tests_scope(params: dict):
        if catalogs is None:
            raise Refused(409, attention[0])
        return catalogs.scope(params)

    import os

    from agentlint.ui.testing import Testing

    testing = Testing(connect, scope=tests_scope, read=read_for_tests, parts=test_parts, command=worker_command,
                      read_command=read_worker_command,
                      environ=os.environ)

    # Why nothing is deleted by age, said in Settings while it is so: a stored keep_days that can't be used is read
    # again on every request (StoreControl adds settings.HOLD while it lasts); a config.toml that couldn't be read, while
    # the file is still there — each request tries importing it again, so a fixed one is imported at once.
    hold = options.settings.hold
    control = StoreControl(connect, harnesses=list(ADAPTERS), sources=adapters, scheduler=SystemScheduler(home()),
                           attention=attention, blocked=hold if hold and hold != HOLD else None, check=check,
                           sessions=dict(options.flags).get("sessions"))
    _prune(options.settings)  # once, as it starts — skipped on hold: a page load never deletes
    try:
        page = shell()
        server = UIServer(build, page, port=args.port, catalog=catalogs or NoSessions(), control=control,
                          records=StoredRecords(scope, connect), tests=testing)
    except (ShellError, OSError) as error:
        raise ToolError(str(error)) from error
    print(f"agentlint ui — {server.url}\nOnly this machine can reach it, and only with that address. Ctrl-C stops it."
          + ("" if options.comparable else f"\n{_ui_unrecorded(options)}"), flush=True)
    if not args.no_open:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return EXIT_OK


def _prune(settings: Settings) -> None:
    """Delete what is older than keep_days, without importing anything first — nothing while keep_days isn't what you
    set (``settings.hold``)."""
    from datetime import UTC, datetime

    from agentlint.store import retention

    if settings.hold:
        return
    with store() as db:
        retention.prune(db, datetime.now(UTC), settings.keep_days)


def _import(db, adapter: Adapter, now, settings: Settings, *, prune: bool = True) -> tuple[int, int]:
    """Archive what changed in the harness's transcripts — sessions and subagents — derive usage from the
    archive, and delete what is older than keep_days. Runs on every command that reads; each transcript is its own
    transaction. The UI imports on every page load but prunes only as it starts (prune=False). (transcripts archived
    or extended, turns derived)"""
    from agentlint.store import archive, retention
    from agentlint.store import usage as usage_store

    changed = archive.update(db, adapter.name, adapter.transcripts(), adapter.read_transcript,
                             forgotten_before=usage_store.cutoff(db))
    turns = usage_store.derive(db, adapter.name, adapter.usage_reader_version, adapter.turns_from)
    if prune and not settings.hold:  # never on a fallback keep_days: it would delete what you asked to keep
        retention.prune(db, now, settings.keep_days)
    return len(changed), turns


def cmd_usage(args: argparse.Namespace, options: Options) -> int:
    from datetime import UTC, datetime, timedelta

    from agentlint.config.usage import summarise
    from agentlint.report.usage import render
    from agentlint.store import usage as usage_store

    adapter = _adapter()
    now = datetime.now(UTC)
    with store() as db:
        _import(db, adapter, now, options.settings)
        index = usage_store.index(db, since=now - timedelta(days=options.days))
        forgotten = usage_store.cutoff(db)
    snapshot = read_config(adapter, args.cwd.resolve(), sessions=options.sessions, forgotten_before=forgotten).snapshot
    package_of = {}
    for entity in snapshot.entities:
        try:
            package_of[entity.id] = snapshot.source(entity.provided_by).provenance.package or "your own and built-in"
        except KeyError:
            pass
    sys.stdout.write(render(summarise(snapshot.entities, index), index, days=options.days, unused=args.unused,
                            package_of=package_of))
    return EXIT_OK


def cmd_show(args: argparse.Namespace, options: Options) -> int:
    from datetime import UTC, datetime

    from agentlint.config.serialize import snapshot_data
    from agentlint.report.show import render, render_rules
    from agentlint.store.snapshots import save

    snapshots_wanted = args.snapshots or args.diff is not None
    if snapshots_wanted and not options.comparable:
        print(f"agentlint: --diff and --snapshots compare readings taken with your sessions setting "
              f"({options.settings.sessions})", file=sys.stderr)
        return EXIT_TOOL_ERROR
    adapter = _adapter()
    snapshot = _read(adapter, args.cwd.resolve(), options.sessions).snapshot
    if options.comparable:  # every comparable reading is a baseline for the next --diff
        with store() as db:
            _import(db, adapter, datetime.now(UTC), options.settings)
            save(db, snapshot, datetime.now(UTC))  # one transaction
            if snapshots_wanted:
                return _show_snapshots(db, snapshot, args)
    else:
        print(f"(Not stored: {_incomparable(options)}, so this reading isn't kept for --diff.)", file=sys.stderr)
    if args.json:
        json.dump(snapshot_data(snapshot), sys.stdout, indent=1)  # ASCII: valid JSON whatever the locale
        sys.stdout.write("\n")
    elif args.rules:
        try:
            sys.stdout.write(render_rules(snapshot, args.rules))
        except KeyError:
            print(f"agentlint: no source {args.rules!r} (see `agentlint show --json` for ids)", file=sys.stderr)
            return EXIT_TOOL_ERROR
    else:
        sys.stdout.write(render(snapshot, always_only=args.always_only, limit=None if args.all else 25))
    return EXIT_OK


def _show_snapshots(db, snapshot, args: argparse.Namespace) -> int:
    from agentlint.report.diff import always_on, diff, render
    from agentlint.store.snapshots import UnknownSnapshot, UnreadableSnapshot, load, previous, resolve, stored, text

    project = str(snapshot.cwd)
    if args.snapshots:
        for item in stored(db, project):
            current = "  (now)" if item.fingerprint == snapshot.fingerprint else ""
            try:
                kept = load(db, project, item.fingerprint)
                about = f"harness {kept.harness_version}  ≈{always_on(kept):,} always-on tokens"
            except UnreadableSnapshot:
                about = "stored by an agentlint whose format this one can't read back"
            print(f"{item.taken_at.astimezone():%Y-%m-%d %H:%M}  {item.fingerprint.removeprefix('sha256:')[:12]}  "
                  f"{about}{current}")
        return EXIT_OK
    try:
        earlier = resolve(db, project, args.diff) if args.diff else previous(db, project, snapshot.fingerprint)
    except UnknownSnapshot as error:
        print(f"agentlint: {error}", file=sys.stderr)
        return EXIT_TOOL_ERROR
    if earlier is None:
        print("No earlier configuration of this project is stored yet; this one is saved, so the next change "
              "will have something to compare with.")
        return EXIT_OK
    try:
        result = diff(load(db, project, earlier.fingerprint), snapshot)
    except UnreadableSnapshot as error:
        print(f"agentlint: {error}; they can't be compared", file=sys.stderr)
        return EXIT_TOOL_ERROR
    sys.stdout.write(render(result, earlier.taken_at, text_of=(lambda ref: text(db, ref)) if args.text else None))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # configuration text can hold lone surrogates: print them escaped
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_OK
    from dataclasses import replace
    from datetime import UTC, datetime

    from agentlint import settings
    from agentlint.store import home
    from agentlint.store import state as state_store

    syncing = args.command == "sync" and not (args.daily or args.off)

    def refuse(why: str) -> int:
        """A command turned away before it runs. A sync records it, so Settings shows the failure — the scheduler's
        daily job would otherwise fail every day while Settings shows the last good sync."""
        print(f"agentlint: {why}", file=sys.stderr)
        if syncing:
            _sync_failed(why)
        return EXIT_TOOL_ERROR

    now = datetime.now(UTC)
    try:
        with store() as db:
            migration = settings.migrate(db, now)  # a config.toml from before 2026-10-04, imported once
            loaded, problems = settings.read(db)
            # The first-run notice and the UI's first-run panel share one flag: whichever meets you first says it, once.
            # `ui` leaves it to the page, which sets it when you close the panel.
            first = args.command != "ui" and state_store.claim(db, state_store.WELCOMED, {"by": "cli"}, now)
    except ToolError as error:
        return refuse(str(error))
    deleting = args.command == "forget"
    fixing = args.command in ("config", "ui") or deleting
    if migration and (fixing or not migration.blocked):  # a command a broken file turns away says it once, below
        print(migration.note, file=sys.stderr)
    if first:
        print(f"agentlint — first run. It reads your harness's configuration and transcripts on this machine, and keeps\n"
              f"a copy of every transcript (subagents' too) and the usage derived from them in {home()} — readable by\n"
              f"you only — for {loaded.keep_days} days. Nothing leaves this machine.\n"
              f"Settings: Settings in `agentlint ui`, or `agentlint config list`. `agentlint forget` deletes what it keeps;\n"
              f"`agentlint sync --daily` keeps it current even when agentlint isn't run.\n", file=sys.stderr)
    # A stored setting that can't be used, or a config.toml that can't be read: `config` fixes it, `forget` needs
    # none, and `ui` starts anyway and opens on Settings, which says what is wrong — deleting
    # nothing by age while keep_days isn't what you set (Settings.hold). Every other command refuses.
    if migration and migration.blocked:
        loaded = replace(loaded, hold=migration.blocked)
        if not fixing:
            return refuse(migration.blocked)
    if problems and not fixing:
        key, why = next(iter(problems.items()))
        return refuse(f"{why}; `agentlint config unset {key}` puts back its default (or Settings in `agentlint ui`)")
    # Once, for every command: a flag beats the stored setting, the stored setting beats the default. argparse has
    # already held each flag to its setting's range (bounded); `args` is never changed after parsing.
    options = Options.resolve(loaded, sessions=getattr(args, "sessions", None), usage_days=getattr(args, "days", None))
    wanted = loaded.harnesses
    # Fixing the setting, a suppression, or deleting what agentlint keeps still runs, and `ui` starts on Settings to
    # fix it there; `suppress <id>` checks the project first, so it refuses a harness the setting leaves out
    # (_check_quietly).
    exempt = args.command in ("config", "suppress", "unsuppress", "ui") or deleting
    if wanted and not set(wanted) & set(ADAPTERS) and not exempt:
        return refuse(f"your harnesses setting ({', '.join(wanted)}) names none agentlint can read yet "
                      f"(it reads {', '.join(ADAPTERS)}); `agentlint config unset harnesses` reads every one found")
    if isinstance(getattr(args, "cwd", None), Path):
        try:
            str(args.cwd.resolve()).encode("utf-8")
        except UnicodeEncodeError:  # undecodable bytes in the path: nothing could store or show it faithfully
            print("agentlint: the project's path isn't valid UTF-8, so agentlint can't record it", file=sys.stderr)
            return EXIT_TOOL_ERROR
    try:
        return args.func(args, options)
    except ToolError as error:
        print(f"agentlint: {error}", file=sys.stderr)
        return EXIT_TOOL_ERROR
    except KeyboardInterrupt:
        return 130
    except Exception:  # anything unexpected is a tool error (2), never "findings" (1)
        import traceback

        traceback.print_exc()
        print("agentlint: an unexpected error (a tool error, exit 2); the traceback above says where", file=sys.stderr)
        return EXIT_TOOL_ERROR


if __name__ == "__main__":
    sys.exit(main())
