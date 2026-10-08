"""The database's shape: one built fresh and one upgraded from the first release are the same database.

``_V1`` is the schema agentlint 70a0307 created, verbatim — the first database anyone has. A fresh database comes
from ``store._SCHEMA``, an upgraded one from ``_V1`` plus every step in ``store._MIGRATIONS``; the two are compared
table by table: columns (name, type, NOT NULL, default, primary key), STRICT, CHECK clauses and indexes.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from agentlint.store import DB_SCHEMA_VERSION, connect
from agentlint.store.verdicts import statuses

_V1 = """
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
    status TEXT NOT NULL CHECK (status IN ('open', 'fixed')),
    first_seen TEXT NOT NULL,       -- when it last opened
    last_seen TEXT NOT NULL,
    fingerprint TEXT NOT NULL,      -- of the configuration it was last seen in
    input_hashes TEXT NOT NULL,     -- JSON: {source id: declared hash} of what it rests on
    data TEXT NOT NULL,             -- the finding, as JSON
    PRIMARY KEY (project, id)
) STRICT;

CREATE TABLE history (              -- append-only: one row per transition
    project TEXT NOT NULL,
    id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('new', 'fixed')),
    at TEXT NOT NULL,
    fingerprint TEXT NOT NULL
) STRICT;
CREATE INDEX history_by_finding ON history (project, id);

CREATE TABLE suppressions (         -- global: a finding id means the same thing in every project
    id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    at TEXT NOT NULL,
    input_hashes TEXT NOT NULL      -- JSON: what the finding rested on when it was dismissed
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


def _checks(sql: str) -> list[str]:
    """Each CHECK clause in a CREATE TABLE, whitespace collapsed (its parentheses balanced by hand)."""
    found = []
    for match in re.finditer(r"\bCHECK\s*\(", sql, re.IGNORECASE):
        depth, end = 1, match.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(sql[end], 0)
            end += 1
        found.append(" ".join(sql[match.end():end - 1].split()))
    return sorted(found)


def shape(db: sqlite3.Connection) -> dict[str, dict]:
    tables = {}
    for table in db.execute("PRAGMA table_list").fetchall():
        name = table["name"]
        if table["schema"] != "main" or name.startswith("sqlite_"):
            continue
        (sql,) = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
        indexes = sorted(
            (index["name"] if index["origin"] == "c" else index["origin"], index["unique"], index["partial"],
             tuple(column["name"] for column in db.execute(f"PRAGMA index_info({index['name']!r})")))
            for index in db.execute(f"PRAGMA index_list({name!r})"))
        tables[name] = {
            "strict": table["strict"],
            "without_rowid": table["wr"],
            "columns": [(c["name"], c["type"], c["notnull"], c["dflt_value"], c["pk"], c["hidden"])
                        for c in db.execute(f"PRAGMA table_xinfo({name!r})")],
            "checks": _checks(sql),
            "indexes": indexes,
        }
    return tables


def test_a_database_upgraded_from_the_first_release_is_the_same_as_a_fresh_one(tmp_path: Path) -> None:
    old = sqlite3.connect(tmp_path / "old.db")
    old.executescript(_V1 + "PRAGMA user_version = 1;")
    old.close()
    upgraded, fresh = connect(tmp_path / "old.db"), connect(tmp_path / "fresh.db")
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == DB_SCHEMA_VERSION
    assert shape(upgraded) == shape(fresh)
    assert all(table["strict"] for table in shape(fresh).values())
    assert shape(fresh)["findings"]["checks"] == ["status IN ('open', 'closed')"]  # the comparison sees CHECKs


def test_the_comparison_notices_a_difference(tmp_path: Path) -> None:
    fresh = connect(tmp_path / "fresh.db")
    before = shape(fresh)
    fresh.executescript("""
        CREATE TABLE loose (a TEXT);
        DROP TABLE usage_meta;
        CREATE TABLE usage_meta (key TEXT PRIMARY KEY, value TEXT) STRICT;""")
    after = shape(fresh)
    assert after != before and "loose" in after and not after["loose"]["strict"]
    assert after["usage_meta"]["columns"] != before["usage_meta"]["columns"]  # NOT NULL dropped


def test_a_schema_8_verdict_is_kept_under_its_target_id(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    connect(path).close()
    old = sqlite3.connect(path)
    old.executescript("""
        ALTER TABLE verdicts RENAME COLUMN target_id TO unit_id;
        ALTER TABLE archive_files RENAME COLUMN bytes_read TO size;
        ALTER TABLE archive_files RENAME COLUMN mtime_epoch_s TO mtime;
        ALTER TABLE archive_files RENAME COLUMN forgotten_bytes TO start;
        ALTER TABLE archive_files RENAME COLUMN tail_sha256 TO tail;
        INSERT INTO verdicts (project, unit_id, last_fingerprint, verdict, situations_tried)
            VALUES ('/p', 'hook', 'fp', '{"measured": "effect"}', 3);
        DROP TABLE runs;
        DROP TABLE jobs;
        DROP TABLE probes;
        DROP TABLE diagnostics;
        DROP TABLE settings;
        DROP TABLE state;
        PRAGMA user_version = 8;""")
    old.close()
    db = connect(path)
    (kept,) = statuses(db, "/p").values()
    assert (kept.target_id, kept.situations_tried, kept.current("fp")) == ("hook", 3, True)


def test_a_schema_9_archive_row_is_kept_under_the_names_of_what_it_holds(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    connect(path).close()
    old = sqlite3.connect(path)
    old.executescript("""
        ALTER TABLE archive_files RENAME COLUMN bytes_read TO size;
        ALTER TABLE archive_files RENAME COLUMN mtime_epoch_s TO mtime;
        ALTER TABLE archive_files RENAME COLUMN forgotten_bytes TO start;
        ALTER TABLE archive_files RENAME COLUMN tail_sha256 TO tail;
        INSERT INTO archive_files (key, harness, session_id, subagent, size, mtime, start, tail, derived)
            VALUES ('s1', 'h', 's1', 0, 120, 1759568400.25, 20, x'00ff', 1);
        INSERT INTO archive_chunks (key, seq, data) VALUES ('s1', 0, x'');
        DROP TABLE runs;
        DROP TABLE jobs;
        DROP TABLE probes;
        DROP TABLE diagnostics;
        DROP TABLE settings;
        DROP TABLE state;
        PRAGMA user_version = 9;""")
    old.close()
    db = connect(path)
    row = db.execute("SELECT bytes_read, mtime_epoch_s, forgotten_bytes, tail_sha256, derived FROM archive_files").fetchone()
    assert tuple(row) == (120, 1759568400.25, 20, b"\x00\xff", 1)
    assert db.execute("SELECT count(*) FROM archive_chunks").fetchone()[0] == 1


def test_a_schema_13_database_gains_settings_and_state_and_keeps_everything(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    connect(path).close()
    old = sqlite3.connect(path)
    old.executescript("""
        DROP TABLE settings;
        DROP TABLE state;
        ALTER TABLE jobs DROP COLUMN harness;
        INSERT INTO usage_meta (key, value) VALUES ('forgotten_before', '2026-10-01T00:00:00.000000+00:00');
        PRAGMA user_version = 13;""")
    old.close()
    db = connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == DB_SCHEMA_VERSION == 17
    assert shape(db) == shape(connect(tmp_path / "fresh.db"))
    assert db.execute("SELECT count(*) FROM settings").fetchone()[0] == 0  # every setting at its default
    assert db.execute("SELECT value FROM usage_meta").fetchone()[0] == "2026-10-01T00:00:00.000000+00:00"


def test_a_schema_14_database_gains_a_jobs_harness_and_keeps_its_jobs(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    connect(path).close()
    old = sqlite3.connect(path)
    old.executescript("""
        ALTER TABLE jobs DROP COLUMN harness;
        INSERT INTO jobs (job_id, project, fingerprint, harness_version, model, run_ceiling, timeout_s, plan, status,
                          created, last_progress)
            VALUES ('j1', '/p', 'fp', '1.0', 'm', 10, 300, '{}', 'open', '2026-10-04T00:00:00.000000+00:00',
                    '2026-10-04T00:00:00.000000+00:00');
        PRAGMA user_version = 14;""")
    old.close()
    db = connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == DB_SCHEMA_VERSION == 17
    assert shape(db) == shape(connect(tmp_path / "fresh.db"))
    from agentlint.store import jobs

    job = jobs.load(db, "j1")
    assert job is not None and job.harness is None and job.model == "m"  # made before: no harness, the worker refuses it


def test_a_schema_15_databases_pending_situations_become_kept_and_nothing_else_changes(tmp_path: Path) -> None:
    """Situations start kept (decided 2026-10-05): a pending one becomes kept, still unreviewed, and 'pending' leaves
    the CHECK; the others keep their review and its time, and every row keeps its place in the order written. Then
    (17) every situation that expects nothing — its what matters a bare menu value — goes."""
    path = tmp_path / "old.db"
    connect(path).close()
    old = sqlite3.connect(path)
    old.executescript("""
        DROP TABLE probes;
        CREATE TABLE probes (
            probe_id TEXT PRIMARY KEY, target_id TEXT NOT NULL, target_hash TEXT NOT NULL, situation TEXT NOT NULL,
            matters TEXT NOT NULL, model TEXT, prompt_version INTEGER NOT NULL, created TEXT NOT NULL,
            review TEXT NOT NULL CHECK (review IN ('pending', 'accepted', 'rejected', 'edited')),
            edited_text TEXT, reviewed TEXT, CHECK ((review = 'edited') = (edited_text IS NOT NULL))
        ) STRICT;
        CREATE INDEX probes_by_target ON probes (target_hash, created);
        INSERT INTO probes VALUES ('p3', 't', 'sha256:t', 'Third.', '{"kind": "which_tools", "uses": "skill:*"}', 'm', 1, 'c', 'pending', NULL, NULL);
        INSERT INTO probes VALUES ('p1', 't', 'sha256:t', 'First.', '{"kind": "which_tools", "uses": "skill:*"}', 'm', 1, 'c', 'rejected', NULL, 'r1');
        INSERT INTO probes VALUES ('p2', 't', 'sha256:t', 'Second.', '{"kind": "which_tools", "uses": "skill:*"}', 'm', 1, 'c', 'edited', 'Two.', 'r2');
        INSERT INTO probes VALUES ('p0', 't', 'sha256:t', 'Zero.', '{"kind": "which_tools", "uses": "skill:*"}', 'm', 1, 'c', 'accepted', NULL, 'r0');
        INSERT INTO probes VALUES ('px', 't', 'sha256:t', 'Expects nothing.', 'order', 'm', 1, 'c', 'accepted', NULL, NULL);
        PRAGMA user_version = 15;""")
    old.close()
    db = connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == DB_SCHEMA_VERSION == 17
    assert shape(db) == shape(connect(tmp_path / "fresh.db"))
    assert shape(db)["probes"]["checks"][-1] == "review IN ('accepted', 'rejected', 'edited')"
    rows = db.execute("SELECT probe_id, review, edited_text, reviewed FROM probes WHERE target_hash = 'sha256:t' "
                      "ORDER BY created, rowid").fetchall()
    assert [tuple(row) for row in rows] == [("p3", "accepted", None, None), ("p1", "rejected", None, "r1"),
                                            ("p2", "edited", "Two.", "r2"), ("p0", "accepted", None, "r0")]
    # schema 17 (2026-10-07): a situation written before expectations expects nothing and can't be tested: gone
    assert db.execute("SELECT count(*) FROM probes WHERE probe_id = 'px'").fetchone()[0] == 0
