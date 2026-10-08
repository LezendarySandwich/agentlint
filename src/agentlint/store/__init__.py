"""The agentlint database: everything agentlint accumulates between runs, in one SQLite file.

``~/.agentlint/agentlint.db`` — ``AGENTLINT_HOME`` moves the directory. Your settings live in it too (the
``settings`` table, ``settings.py``); ``emphasis.toml``, your marker overrides, lives beside it and stays TOML;
exports stay JSON. Each command
writes inside one transaction, so a kill never leaves half a write. The schema version is the
database's ``user_version``: a database written by a newer agentlint is refused, not misread.
Every statement names its columns, and rows come back as ``sqlite3.Row``, read by column name — never by position.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from pathlib import Path

DB_SCHEMA_VERSION = 17


class FindingStatus(Enum):
    """``findings.status``: whether a finding is open in a project. Bookkeeping only — never shown as new or fixed."""

    OPEN = "open"  # the latest recorded check that could judge it found it
    CLOSED = "closed"  # a recorded check that could have seen it didn't (``store/findings.py``)


class JobStatus(Enum):
    """``jobs.status``: whether a job can take more runs. How far it got is never stored here: that is its runs."""

    OPEN = "open"  # running, or resumable
    COMPLETE = "complete"  # every run of its plan is recorded; set in the transaction that records the last one
    SALVAGED = "salvaged"  # closed by --salvage: what completed is kept, nothing more is ever added
    CEILING = "ceiling"  # stopped at its run ceiling, which nothing raises: it can never launch again


class ReviewStatus(Enum):
    """``probes.review``: where a generated situation stands. It is written kept (decided 2026-10-05: review is the
    exception, not a gate), and ``probes.reviewed`` stays unset until a person reviews it, so "kept as written" and
    "kept by you" stay apart in the data. Regenerating a target's situations starts them all again kept and
    unreviewed: a review never carries across (04-probe-generator.md)."""

    ACCEPTED = "accepted"  # kept: run as written — as every situation is written, until a person drops or edits it
    REJECTED = "rejected"  # dropped: never run
    EDITED = "edited"  # kept as the reviewer rewrote it (``edited_text``)

_SCHEMA = """
CREATE TABLE analyses (
    project TEXT NOT NULL,          -- the directory the configuration was read for
    at TEXT NOT NULL,               -- ISO 8601, UTC
    fingerprint TEXT NOT NULL,
    checks TEXT NOT NULL,           -- JSON: the check ids that were evaluated
    PRIMARY KEY (project, at)
) STRICT;

CREATE TABLE findings (
    project TEXT NOT NULL,
    id TEXT NOT NULL,
    check_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed')),  -- FindingStatus
    first_seen TEXT NOT NULL,       -- when it last opened
    last_seen TEXT NOT NULL,
    fingerprint TEXT NOT NULL,      -- of the configuration it was last seen in
    input_hashes TEXT NOT NULL,     -- JSON: {source id: declared hash} of what it rests on
    data TEXT NOT NULL,             -- the finding, as JSON
    PRIMARY KEY (project, id)
) STRICT;

CREATE TABLE suppressions (         -- global: dismissing a finding id dismisses it in every project
    id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    at TEXT NOT NULL
) STRICT;

CREATE TABLE suppression_baselines (  -- per project: what the finding rested on there when first hidden
    id TEXT NOT NULL,
    project TEXT NOT NULL,
    input_hashes TEXT NOT NULL,     -- JSON: {source id: declared hash}
    known TEXT,                     -- JSON: every source configured there then; a source not in it is new
    PRIMARY KEY (id, project)
) STRICT;

CREATE TABLE observations (         -- usage read from transcripts: one row per turn, never prompt text
    session_id TEXT NOT NULL,
    turn INTEGER NOT NULL,
    at TEXT NOT NULL,
    project TEXT NOT NULL,
    harness_version TEXT NOT NULL,
    model TEXT NOT NULL,
    interactive INTEGER NOT NULL,
    sidechain INTEGER NOT NULL,
    entities TEXT NOT NULL,         -- JSON: namespace/name of each entity the turn used
    PRIMARY KEY (session_id, turn)
) STRICT;
CREATE INDEX observations_by_time ON observations (at);

CREATE TABLE archive_files (        -- every transcript read, sessions and subagents: how far, and what it was
    key TEXT PRIMARY KEY,           -- the adapter's handle for it
    harness TEXT NOT NULL,
    session_id TEXT NOT NULL,       -- its own session, or the session a subagent ran in
    subagent INTEGER NOT NULL,
    bytes_read INTEGER NOT NULL,    -- how far it was read: the file's length when last read, not its size now
    mtime_epoch_s REAL NOT NULL,    -- the file's modification time when last read, in Unix seconds: keep_days
                                    -- measures it, and it is compared with the file's stat, so it isn't ISO text
    forgotten_bytes INTEGER NOT NULL DEFAULT 0,  -- the first this many bytes were forgotten, or outlived keep_days
    tail_sha256 BLOB NOT NULL,      -- SHA-256 of the last 64 bytes read, never the bytes: a file that no longer ends
                                    -- with them was rewritten
    derived INTEGER NOT NULL DEFAULT 0  -- 1 once usage was derived from what is kept now
) STRICT;
CREATE INDEX archive_by_session ON archive_files (harness, session_id);

CREATE TABLE archive_chunks (       -- the bytes, zlib-compressed, in order
    key TEXT NOT NULL,
    seq INTEGER NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (key, seq)
) STRICT;

CREATE TABLE usage_meta (           -- e.g. forgotten_before: turns older than this are never read back in
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE verdicts (             -- what has been measured about each ablation target: the ranker's staleness record
    project TEXT NOT NULL,
    target_id TEXT NOT NULL,        -- a rule's id, or a whole skill's or hook's source id
    last_measured TEXT,
    last_fingerprint TEXT,          -- the configuration it was measured under
    last_job_id TEXT,
    verdict TEXT,                   -- JSON RuleVerdict, written by the runner
    situations_tried INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project, target_id)
) STRICT;

CREATE TABLE jobs (                 -- one per test you start: its plan, frozen when it was created and replayed on resume
    job_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,          -- the directory the configuration was read for
    fingerprint TEXT NOT NULL,      -- of the configuration the plan was made for: resume refuses another
    harness_version TEXT NOT NULL,  -- the harness and model the job runs under: resume refuses others
    model TEXT NOT NULL,
    run_ceiling INTEGER NOT NULL CHECK (run_ceiling > 0),  -- the most runs you agreed to (what counts: runner/engine.py)
    timeout_s INTEGER NOT NULL CHECK (timeout_s > 0),  -- one run's limit
    plan TEXT NOT NULL,             -- JSON: the comparisons in the order they run, never recomputed
    status TEXT NOT NULL CHECK (status IN ('open', 'complete', 'salvaged', 'ceiling')),  -- JobStatus
    launches INTEGER NOT NULL DEFAULT 0,  -- every invocation started, retries and interrupted ones included
    in_flight TEXT,                 -- the run key launched and not yet recorded; set while a run is out
    runner_pid INTEGER,             -- the process running the job (its lock), and that process's start time, so a
    runner_started TEXT,            -- dead runner's lock, or one whose pid was reused, is seen to be stale
    created TEXT NOT NULL,
    last_progress TEXT NOT NULL,    -- the last launch or recorded run
    harness TEXT                    -- the harness whose adapter runs it; NULL for a job made before schema 15
) STRICT;
CREATE INDEX jobs_by_project ON jobs (project, created);

CREATE TABLE runs (                 -- one per run recorded: the only record of a job's progress
    job_id TEXT NOT NULL,
    run_key TEXT NOT NULL,          -- <comparison>:<variant>:<n>
    session_id TEXT,                -- the harness's id for the run's transcript, when it gave one
    outcome TEXT NOT NULL,          -- what the run came to, as the executor named it
    trace TEXT NOT NULL,            -- JSON: the raw trace read from its transcript
    harness_version TEXT,           -- the harness and model the run was observed under
    model TEXT,
    duration_ms INTEGER NOT NULL,
    cost_usd REAL,                  -- the harness's estimate, when it gave one: shown only to someone billed
    started TEXT NOT NULL,
    finished TEXT NOT NULL,
    PRIMARY KEY (job_id, run_key)
) STRICT;

CREATE TABLE probes (               -- generated situations, kept until their target's text changes (D15)
    probe_id TEXT PRIMARY KEY,      -- hash of the target's text hash and the situation's text
    target_id TEXT NOT NULL,        -- the ablation target whose generation wrote it (the latest, when two share a text)
    target_hash TEXT NOT NULL,      -- content hash of the target's text: the only cache key
    situation TEXT NOT NULL,        -- verbatim, as the generator wrote it
    matters TEXT NOT NULL,          -- its expectation: the menu choice naming its thing, as JSON (probes/matters.py)
    model TEXT,                     -- the model that answered, as the call reported it
    prompt_version INTEGER NOT NULL,
    created TEXT NOT NULL,
    review TEXT NOT NULL CHECK (review IN ('accepted', 'rejected', 'edited')),  -- ReviewStatus: written 'accepted'
    edited_text TEXT,               -- the reviewer's text: set exactly when review is 'edited'
    reviewed TEXT,                  -- when a person last reviewed it; NULL: kept as written, nobody reviewed it
    CHECK ((review = 'edited') = (edited_text IS NOT NULL))
) STRICT;
CREATE INDEX probes_by_target ON probes (target_hash, created);

CREATE TABLE diagnostics (          -- agentlint's findings about itself, never mixed with findings (D16)
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,             -- what happened, as its producer names it (store/diagnostics.py)
    subject TEXT NOT NULL,          -- what it is about: a target id, a job id
    message TEXT NOT NULL,          -- one sentence for a person
    data TEXT NOT NULL,             -- JSON: the details
    created TEXT NOT NULL
) STRICT;
CREATE INDEX diagnostics_by_time ON diagnostics (created);

CREATE TABLE settings (             -- your settings: one row per setting you changed; a setting at its default has none
    key TEXT PRIMARY KEY,           -- a field of settings.Settings
    value TEXT NOT NULL,            -- JSON, checked against the setting's range or choices when written and when read
    updated TEXT NOT NULL
) STRICT;

CREATE TABLE state (                -- agentlint's own bookkeeping, never a setting: the first-run notice, the last sync
    key TEXT PRIMARY KEY,           -- store/state.py names each
    value TEXT NOT NULL,            -- JSON
    updated TEXT NOT NULL
) STRICT;

CREATE TABLE blobs (                -- each distinct source text, once
    hash TEXT PRIMARY KEY,          -- the content hash snapshots refer to
    text BLOB NOT NULL              -- zlib-compressed UTF-8
) STRICT;

CREATE TABLE snapshots (
    project TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    data BLOB NOT NULL,             -- zlib-compressed snapshot JSON; source text replaced by blob hashes
    PRIMARY KEY (project, fingerprint)
) STRICT;
"""


def _statements(script: str) -> Iterator[str]:
    """One SQL statement at a time, split by SQLite's own tokenizer (comments may hold semicolons)."""
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer
            buffer = ""


# Each entry upgrades a database from that version to the next, inside the opening transaction.
_MIGRATIONS = {
    # 1 -> 2: a suppression's inputs move to per-project baselines. Version 1 armed them against
    # whichever project last saw the finding, so they are dropped and re-armed on first sight.
    1: """
CREATE TABLE suppression_baselines (
    id TEXT NOT NULL,
    project TEXT NOT NULL,
    input_hashes TEXT NOT NULL,
    PRIMARY KEY (id, project)
) STRICT;
CREATE TABLE suppressions_new (id TEXT PRIMARY KEY, reason TEXT NOT NULL, at TEXT NOT NULL) STRICT;
INSERT INTO suppressions_new (id, reason, at) SELECT id, reason, at FROM suppressions;
DROP TABLE suppressions;
ALTER TABLE suppressions_new RENAME TO suppressions;
""",
    # 2 -> 3: usage history read from transcripts (the recorder).
    2: """
CREATE TABLE observations (         -- usage read from transcripts: one row per turn, never prompt text
    session_id TEXT NOT NULL,
    turn INTEGER NOT NULL,
    at TEXT NOT NULL,
    project TEXT NOT NULL,
    harness_version TEXT NOT NULL,
    model TEXT NOT NULL,
    interactive INTEGER NOT NULL,
    sidechain INTEGER NOT NULL,
    entities TEXT NOT NULL,         -- JSON: namespace/name of each entity the turn used
    PRIMARY KEY (session_id, turn)
) STRICT;
CREATE INDEX observations_by_time ON observations (at);

CREATE TABLE usage_read (           -- each transcript as last read, so only what changed is read again
    key TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL
) STRICT;

CREATE TABLE usage_meta (           -- e.g. forgotten_before: turns older than this are never read back in
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
""",
    # 3 -> 4: the ranker's record of what has been measured.
    3: """
CREATE TABLE verdicts (             -- what has been measured about each unit: the ranker's staleness record
    project TEXT NOT NULL,
    unit_id TEXT NOT NULL,          -- a rule's id, or a whole skill's or hook's source id
    last_measured TEXT,
    last_fingerprint TEXT,          -- the configuration it was measured under
    last_job_id TEXT,
    verdict TEXT,                   -- JSON RuleVerdict, written by the runner
    situations_tried INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project, unit_id)
) STRICT;
""",
    # 4 -> 5: a suppression's baseline also records what was configured, so only a newly configured source lapses it.
    4: """
ALTER TABLE suppression_baselines ADD COLUMN known TEXT;
""",
    # 5 -> 6: every transcript is archived; what was read is tracked there, so usage_read goes.
    5: """
CREATE TABLE archive_files (        -- every transcript read, sessions and subagents: how far, and what it was
    key TEXT PRIMARY KEY,           -- the adapter's handle for it
    harness TEXT NOT NULL,
    session_id TEXT NOT NULL,       -- its own session, or the session a subagent ran in
    subagent INTEGER NOT NULL,
    size INTEGER NOT NULL,          -- bytes read so far
    mtime REAL NOT NULL,            -- the file's time when last read: what keep_days measures
    start INTEGER NOT NULL DEFAULT 0,  -- bytes before this were forgotten
    tail BLOB NOT NULL              -- a hash of the last bytes read: a file that no longer ends with them was rewritten
) STRICT;
CREATE INDEX archive_by_session ON archive_files (harness, session_id);

CREATE TABLE archive_chunks (       -- the bytes, zlib-compressed, in order
    key TEXT NOT NULL,
    seq INTEGER NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (key, seq)
) STRICT;
DROP TABLE usage_read;
""",
    # 6 -> 7: whether usage was derived from each transcript is kept, so an interrupted import is finished next time.
    # Every transcript starts underived: usage is derived again once, which also recovers any an interruption lost.
    6: """
ALTER TABLE archive_files ADD COLUMN derived INTEGER NOT NULL DEFAULT 0;
""",
    # 7 -> 8: a finding no check finds is 'closed', not 'fixed' — the product claims no fixes — and the history of
    # transitions goes: nothing read it once "new" and "fixed" stopped being shown.
    7: """
CREATE TABLE findings_new (
    project TEXT NOT NULL,
    id TEXT NOT NULL,
    check_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed')),  -- FindingStatus
    first_seen TEXT NOT NULL,       -- when it last opened
    last_seen TEXT NOT NULL,
    fingerprint TEXT NOT NULL,      -- of the configuration it was last seen in
    input_hashes TEXT NOT NULL,     -- JSON: {source id: declared hash} of what it rests on
    data TEXT NOT NULL,             -- the finding, as JSON
    PRIMARY KEY (project, id)
) STRICT;
INSERT INTO findings_new (project, id, check_id, status, first_seen, last_seen, fingerprint, input_hashes, data)
    SELECT project, id, check_id, CASE status WHEN 'fixed' THEN 'closed' ELSE status END,
    first_seen, last_seen, fingerprint, input_hashes, data FROM findings;
DROP TABLE findings;
ALTER TABLE findings_new RENAME TO findings;
DROP TABLE IF EXISTS history;
""",
    # 8 -> 9: what one variant removes is an ablation target, not a "unit" (decided 2026-10-04).
    8: """
ALTER TABLE verdicts RENAME COLUMN unit_id TO target_id;
""",
    # 9 -> 10: archive_files' columns are named for what they hold (decided 2026-10-04): size was bytes read so far,
    # not the file's size; start, how many leading bytes were forgotten; tail, a hash of the last bytes, not the
    # bytes; mtime, the one time the store keeps as epoch seconds rather than ISO text. Every row is kept as it was.
    9: """
ALTER TABLE archive_files RENAME COLUMN size TO bytes_read;
ALTER TABLE archive_files RENAME COLUMN mtime TO mtime_epoch_s;
ALTER TABLE archive_files RENAME COLUMN start TO forgotten_bytes;
ALTER TABLE archive_files RENAME COLUMN tail TO tail_sha256;
""",
    # 10 -> 11: a finding's location names a source by id, no longer a path or label (decided 2026-10-04). A stored
    # location can't be mapped to a source without the reading it came from, and nothing reads a stored finding's
    # locations back, so they are dropped: every other field is kept, and the next check rewrites each open finding whole.
    10: """
UPDATE findings SET data = json_set(data, '$.locations', json('[]'));
""",
    # 11 -> 12: the runner's jobs and runs (docs/tech_impl/05-runner.md). New tables: nothing earlier is touched.
    11: """
CREATE TABLE jobs (                 -- one per test you start: its plan, frozen when it was created and replayed on resume
    job_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,          -- the directory the configuration was read for
    fingerprint TEXT NOT NULL,      -- of the configuration the plan was made for: resume refuses another
    harness_version TEXT NOT NULL,  -- the harness and model the job runs under: resume refuses others
    model TEXT NOT NULL,
    run_ceiling INTEGER NOT NULL CHECK (run_ceiling > 0),  -- the most runs you agreed to (what counts: runner/engine.py)
    timeout_s INTEGER NOT NULL CHECK (timeout_s > 0),  -- one run's limit
    plan TEXT NOT NULL,             -- JSON: the comparisons in the order they run, never recomputed
    status TEXT NOT NULL CHECK (status IN ('open', 'complete', 'salvaged', 'ceiling')),  -- JobStatus
    launches INTEGER NOT NULL DEFAULT 0,  -- every invocation started, retries and interrupted ones included
    in_flight TEXT,                 -- the run key launched and not yet recorded; set while a run is out
    runner_pid INTEGER,             -- the process running the job (its lock), and that process's start time, so a
    runner_started TEXT,            -- dead runner's lock, or one whose pid was reused, is seen to be stale
    created TEXT NOT NULL,
    last_progress TEXT NOT NULL     -- the last launch or recorded run
) STRICT;
CREATE INDEX jobs_by_project ON jobs (project, created);

CREATE TABLE runs (                 -- one per run recorded: the only record of a job's progress
    job_id TEXT NOT NULL,
    run_key TEXT NOT NULL,          -- <comparison>:<variant>:<n>
    session_id TEXT,                -- the harness's id for the run's transcript, when it gave one
    outcome TEXT NOT NULL,          -- what the run came to, as the executor named it
    trace TEXT NOT NULL,            -- JSON: the raw trace read from its transcript
    harness_version TEXT,           -- the harness and model the run was observed under
    model TEXT,
    duration_ms INTEGER NOT NULL,
    cost_usd REAL,                  -- the harness's estimate, when it gave one: shown only to someone billed
    started TEXT NOT NULL,
    finished TEXT NOT NULL,
    PRIMARY KEY (job_id, run_key)
) STRICT;
""",
    # 12 -> 13: generated situations and agentlint's diagnostics about itself (04-probe-generator.md, decided
    # 2026-10-04 as D15 and D16). New tables: nothing earlier is touched.
    12: """
CREATE TABLE probes (               -- generated situations, kept until their target's text changes (D15)
    probe_id TEXT PRIMARY KEY,      -- hash of the target's text hash and the situation's text
    target_id TEXT NOT NULL,        -- the ablation target whose generation wrote it (the latest, when two share a text)
    target_hash TEXT NOT NULL,      -- content hash of the target's text: the only cache key
    situation TEXT NOT NULL,        -- verbatim, as the generator wrote it
    matters TEXT NOT NULL,          -- what matters in it, from the fixed menu; used for no verdict
    model TEXT,                     -- the model that answered, as the call reported it
    prompt_version INTEGER NOT NULL,
    created TEXT NOT NULL,
    review TEXT NOT NULL CHECK (review IN ('pending', 'accepted', 'rejected', 'edited')),  -- ReviewStatus
    edited_text TEXT,               -- the reviewer's text: set exactly when review is 'edited'
    reviewed TEXT,                  -- when it was last reviewed
    CHECK ((review = 'edited') = (edited_text IS NOT NULL))
) STRICT;
CREATE INDEX probes_by_target ON probes (target_hash, created);

CREATE TABLE diagnostics (          -- agentlint's findings about itself, never mixed with findings (D16)
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,             -- what happened, as its producer names it (store/diagnostics.py)
    subject TEXT NOT NULL,          -- what it is about: a target id, a job id
    message TEXT NOT NULL,          -- one sentence for a person
    data TEXT NOT NULL,             -- JSON: the details
    created TEXT NOT NULL
) STRICT;
CREATE INDEX diagnostics_by_time ON diagnostics (created);
""",
    # 13 -> 14: settings move from config.toml into the database (decided 2026-10-04), with agentlint's own bookkeeping
    # beside them. New tables: nothing earlier is touched; settings.migrate imports a config.toml once.
    13: """
CREATE TABLE settings (             -- your settings: one row per setting you changed; a setting at its default has none
    key TEXT PRIMARY KEY,           -- a field of settings.Settings
    value TEXT NOT NULL,            -- JSON, checked against the setting's range or choices when written and when read
    updated TEXT NOT NULL
) STRICT;

CREATE TABLE state (                -- agentlint's own bookkeeping, never a setting: the first-run notice, the last sync
    key TEXT PRIMARY KEY,           -- store/state.py names each
    value TEXT NOT NULL,            -- JSON
    updated TEXT NOT NULL
) STRICT;
""",
    # 14 -> 15: a job names the harness that runs it, so the worker the UI launches (python -m agentlint.runner <job>)
    # builds that harness's adapter. A job made before has none: the worker refuses it.
    14: """
ALTER TABLE jobs ADD COLUMN harness TEXT;
""",
    # 15 -> 16: a generated situation starts kept, and 'pending' is no state any more (decided 2026-10-05): a pending
    # one becomes kept, its `reviewed` left as it was (unset: nobody reviewed it), and the CHECK loses 'pending', which
    # SQLite changes only by building the table again. Rows are copied in their order, which `for_hash` lists by.
    15: """
CREATE TABLE probes_new (           -- generated situations, kept until their target's text changes (D15)
    probe_id TEXT PRIMARY KEY,      -- hash of the target's text hash and the situation's text
    target_id TEXT NOT NULL,        -- the ablation target whose generation wrote it (the latest, when two share a text)
    target_hash TEXT NOT NULL,      -- content hash of the target's text: the only cache key
    situation TEXT NOT NULL,        -- verbatim, as the generator wrote it
    matters TEXT NOT NULL,          -- what matters in it, from the fixed menu; used for no verdict
    model TEXT,                     -- the model that answered, as the call reported it
    prompt_version INTEGER NOT NULL,
    created TEXT NOT NULL,
    review TEXT NOT NULL CHECK (review IN ('accepted', 'rejected', 'edited')),  -- ReviewStatus: written 'accepted'
    edited_text TEXT,               -- the reviewer's text: set exactly when review is 'edited'
    reviewed TEXT,                  -- when a person last reviewed it; NULL: kept as written, nobody reviewed it
    CHECK ((review = 'edited') = (edited_text IS NOT NULL))
) STRICT;
INSERT INTO probes_new (probe_id, target_id, target_hash, situation, matters, model, prompt_version, created, review,
                        edited_text, reviewed)
    SELECT probe_id, target_id, target_hash, situation, matters, model, prompt_version, created,
    CASE review WHEN 'pending' THEN 'accepted' ELSE review END, edited_text, reviewed FROM probes ORDER BY rowid;
DROP TABLE probes;
ALTER TABLE probes_new RENAME TO probes;
CREATE INDEX probes_by_target ON probes (target_hash, created);
""",
    # 16 -> 17: a situation carries its expectation — its what matters naming a thing, stored as JSON — and a test counts
    # the runs that meet it (decided with the user 2026-10-07). One written before records the bare menu value and
    # expects nothing, so it can't be tested: the user asked for every such situation to go, to write them again.
    16: """
DELETE FROM probes WHERE matters NOT LIKE '{%';
""",
}


class StoreError(Exception):
    """The database can't be used: written by a newer agentlint, or not an agentlint database."""


def home() -> Path:
    return Path(os.environ.get("AGENTLINT_HOME") or Path.home() / ".agentlint")


def private_dirs(path: Path, *, exist_ok: bool = True) -> Path:
    """The directory ``path``, made with every missing directory above it, each one mode 0700 whatever the umask says:
    readable by you only, like the transcripts agentlint keeps copies of. ``Path.mkdir(parents=True, mode=0o700)`` gives
    the mode to the last level alone (``runs/`` was made 0755 so). A directory that exists is left as it is; ``path``
    existing is FileExistsError unless ``exist_ok``. Every directory agentlint makes under its home is made here."""
    path = Path(path)
    missing = [path]
    for parent in path.parents:
        if os.path.isdir(parent):
            break
        missing.append(parent)
    for level in reversed(missing):
        try:
            os.mkdir(level, 0o700)
        except FileExistsError:
            if (level == path and not exist_ok) or not os.path.isdir(level):
                raise
            continue  # made meanwhile, by another agentlint process: its maker set its mode
        os.chmod(level, 0o700)  # the umask may have taken some of it
    return path


def _private(path: Path) -> None:
    """Readable by you only, like the transcripts it copies from: the directory 0700, the files 0600."""
    private_dirs(path.parent)
    for target, mode in ((path.parent, 0o700), (path, 0o600), (Path(f"{path}-wal"), 0o600), (Path(f"{path}-shm"), 0o600)):
        try:
            if target.exists() and target.stat().st_uid == os.getuid() and target.stat().st_mode & 0o077:
                target.chmod(mode)
        except OSError:
            pass  # not ours to change: leave it


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or home() / "agentlint.db"
    try:
        _private(path)
        if not path.exists():
            os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))  # created private, never briefly world-readable
        db = sqlite3.connect(path, timeout=10, isolation_level=None)  # transactions are explicit
    except (OSError, sqlite3.Error) as error:
        raise StoreError(f"{path}: {error}") from error
    try:
        db.execute("PRAGMA journal_mode=WAL")  # the recorder may write while a command reads
        db.execute("PRAGMA secure_delete=ON")  # what is deleted is overwritten, not left readable in the file
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > DB_SCHEMA_VERSION:
            raise StoreError(f"{path} was written by a newer agentlint (schema {version}, this one reads "
                             f"{DB_SCHEMA_VERSION}); upgrade agentlint or set AGENTLINT_HOME elsewhere")
        if version < DB_SCHEMA_VERSION:
            with transaction(db):
                version = db.execute("PRAGMA user_version").fetchone()[0]  # another process may have just done it
                if version == 0:
                    if db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
                        raise StoreError(f"{path} is not an agentlint database")
                    for statement in _statements(_SCHEMA):
                        db.execute(statement)
                else:
                    for step in range(version, DB_SCHEMA_VERSION):
                        for statement in _statements(_MIGRATIONS[step]):
                            db.execute(statement)
                db.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")
    except sqlite3.DatabaseError as error:
        db.close()
        raise StoreError(f"{path}: {error}") from error
    except StoreError:
        db.close()
        raise
    _private(path)  # the -wal and -shm files exist now
    db.row_factory = sqlite3.Row  # columns are read by name, never by position
    return db


@contextmanager
def transaction(db: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """All of it or none of it. Inside an open transaction, joins it: the outer one decides."""
    if db.in_transaction:
        yield db
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")
