"""`agentlint ui`: three answers, this machine only, the run's token on every request (docs/tech_impl/11-ui.md)."""

from __future__ import annotations

import gzip
import http.client
import json
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentlint import cli
from agentlint.config.sessions import SessionHeader
from agentlint.settings import Options, Settings
from agentlint.ui.catalog import KEPT_SUBAGENTS
from agentlint.ui.server import HOST, TOKEN_HEADER, NoSessions, UIServer, Unreadable

from .checks.test_findings_and_cli import PROJECT, FakeAdapter


def header(session_id: str) -> SessionHeader:
    return SessionHeader(session_id, Path("/w"), None, None, datetime(2026, 10, 1, tzinfo=UTC))


@pytest.fixture
def server() -> Iterator[UIServer]:
    calls = []

    def build(params):
        calls.append(params)
        scope = {"harness": params.get("harness") or "h-a", "cwd": params.get("project") or "/here"}
        return ({"schema_version": 1, "readings": len(calls), "snapshot": scope},
                {"ref": f"text of {scope['cwd']} \ud800 with a lone surrogate"})

    running = UIServer(build, "<html>shell</html>")
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    yield running
    running.shutdown()
    running.server_close()


def get(server: UIServer, path: str, *, token: str | None = None, host: str | None = None) -> tuple[int, bytes, dict]:
    connection = http.client.HTTPConnection(HOST, server.server_port, timeout=5)
    headers = {"Host": host or f"{HOST}:{server.server_port}"}
    if token:
        headers[TOKEN_HEADER] = token
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    body = response.read()
    if response.getheader("Content-Encoding") == "gzip":
        body = gzip.decompress(body)
    return response.status, body, dict(response.getheaders())


def test_it_listens_on_this_machine_only(server: UIServer) -> None:
    assert server.server_address[0] == "127.0.0.1"
    assert server.url == f"http://127.0.0.1:{server.server_port}/?token={server.token}"


def test_every_request_needs_the_token(server: UIServer) -> None:
    assert get(server, "/")[0] == 403
    assert get(server, "/?token=wrong")[0] == 403
    assert get(server, "/api/report", token="wrong")[0] == 403
    status, body, headers = get(server, f"/?token={server.token}")
    assert status == 200 and body == b"<html>shell</html>"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"] and headers["Cache-Control"] == "no-store"


def test_a_page_elsewhere_cannot_reach_it_under_its_own_name(server: UIServer) -> None:
    assert get(server, f"/?token={server.token}", host="evil.example:80")[0] == 403  # DNS rebinding
    assert get(server, f"/?token={server.token}", host=f"localhost:{server.server_port}")[0] == 200


def test_it_answers_the_report_and_its_texts_and_nothing_else(server: UIServer) -> None:
    status, body, _ = get(server, "/api/report", token=server.token)
    assert status == 200 and json.loads(body)["readings"] == 1
    assert server.build.__name__ == "build" and get(server, "/api/report?project=/w", token=server.token)[0] == 200
    assert json.loads(get(server, "/api/report", token=server.token)[1])["readings"] == 3  # read fresh each time
    status, body, _ = get(server, "/api/texts?harness=h-a&project=/here", token=server.token)
    assert status == 200 and json.loads(body) == {"ref": "text of /here \ud800 with a lone surrogate"}
    for path in ("/api/transcripts", "/../../etc/passwd", "/api/report/../texts/x", "/favicon.ico"):
        assert get(server, path, token=server.token)[0] == 404


def test_a_failed_reading_says_why_and_the_server_carries_on() -> None:
    def build(params):
        raise Unreadable("the agentlint database: disk I/O error")

    running = UIServer(build, "<html></html>")
    threading.Thread(target=running.serve_forever, daemon=True).start()
    try:
        status, body, _ = get(running, "/api/report", token=running.token)
        assert status == 500 and "disk I/O error" in json.loads(body)["error"]
        assert get(running, f"/?token={running.token}")[0] == 200
    finally:
        running.shutdown()
        running.server_close()


def test_the_cli_builds_the_report_the_ui_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    data, texts = cli._report(FakeAdapter(), PROJECT, Options(Settings()))
    assert data["snapshot"]["cwd"] == str(PROJECT) and data["check"]["findings"] and texts
    assert set(data) >= {"labels", "contexts", "usage", "check", "snapshot"}
    assert data["home_dir"] == str(Path.home())  # the user's own home: the UI's `~` stands for it and nothing else


COUNTED = ("SELECT (SELECT count(*) FROM snapshots), (SELECT count(*) FROM analyses), "
           "(SELECT count(*) FROM findings WHERE status = 'open')")


def test_the_report_records_what_check_records_and_reading_it_again_adds_nothing() -> None:
    """Someone who only uses the UI gets the findings' lifecycle and the snapshots What changed compares, as `check`
    records them; a reload of an unchanged configuration stores no second snapshot, piles up no analyses and opens or
    closes nothing."""
    from agentlint.store import connect

    def counts() -> tuple:
        db = connect()
        try:
            return (tuple(db.execute(COUNTED).fetchone()),
                    db.execute("SELECT id, first_seen FROM findings ORDER BY id").fetchall())
        finally:
            db.close()

    data, _ = cli._report(FakeAdapter(), PROJECT, Options(Settings()))
    (snapshots, analyses, opened), seen = counts()
    assert (snapshots, analyses) == (1, 1) and opened == len(data["check"]["findings"]) > 0
    again, _ = cli._report(FakeAdapter(), PROJECT, Options(Settings()))
    assert counts() == ((1, 1, opened), seen)  # a reload: nothing new, nothing reopened, first_seen kept
    assert again["check"]["findings"] == data["check"]["findings"]

    cli._report(Edited(), PROJECT, Options(Settings()))
    assert counts()[0][:2] == (2, 2)  # a changed configuration: a snapshot to compare with the one before
    assert data["not_recorded"] is None and again["not_recorded"] is None
    unrecorded, _ = cli._report(Edited(), PROJECT, Options.resolve(Settings(), sessions=7))
    assert counts()[0][:2] == (2, 2)  # another number of sessions sees other evidence: stored and recorded neither
    note = unrecorded["not_recorded"]  # and the page is told why (Findings, What changed)
    assert note.startswith("Not recorded:") and "--sessions 7" in note and f"({Settings().sessions})" in note


def test_a_ui_started_at_another_number_of_sessions_says_once_that_it_records_nothing(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: FakeAdapter})
    started(monkeypatch)
    assert "Not recorded" not in capsys.readouterr().out  # at your sessions setting: every page load is recorded
    started(monkeypatch, "--sessions", "7")
    printed = capsys.readouterr().out
    assert printed.count("Not recorded: agentlint ui was started with --sessions 7") == 1


def test_a_reading_the_ui_records_still_keeps_its_texts_per_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentlint.store import connect

    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: FakeAdapter})
    captured = started(monkeypatch)
    running = UIServer(captured.build, "<html></html>", catalog=captured.catalog)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    try:
        for _ in range(2):
            status, body, _ = get(running, f"/api/report?project={PROJECT.resolve()}", token=running.token)
            assert status == 200
        report = json.loads(body)
        status, body, _ = get(running, f"/api/texts?harness={FakeAdapter.name}&project={PROJECT.resolve()}",
                              token=running.token)
        contents = [source.get("content") or {} for source in report["snapshot"]["sources"]]
        refs = {content["text_ref"] for content in contents if "text_ref" in content}
        assert status == 200 and refs and refs <= set(json.loads(body))
        db = connect()
        assert tuple(db.execute(COUNTED).fetchone())[:2] == (1, 1)  # two page loads, one reading stored
        db.close()
    finally:
        running.shutdown()
        running.server_close()


class Edited(FakeAdapter):
    """The test harness after its notes were edited."""

    def read_declared(self, cwd: Path):
        from agentlint.config.model import Declared

        from .checks.test_findings_and_cli import declared
        from .config.helpers import file_source

        kept = declared()
        return Declared(kept.harness, kept.cwd, kept.harness_version, kept.model,
                        (file_source("notes", "## A\nother words, and more of them than before\n"), *kept.sources[1:]),
                        kept.entities)


def test_what_changed_lists_the_projects_snapshots_and_compares_two(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentlint.ui.server import ScopeError

    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: FakeAdapter, "h-b": harness("h-b", [])})
    records = started(monkeypatch).records
    here = {"project": str(PROJECT.resolve())}
    assert records.snapshots(here) == []
    cli._report(FakeAdapter(), PROJECT.resolve(), Options(Settings()))
    cli._report(Edited(), PROJECT.resolve(), Options(Settings()))
    newer, older = records.snapshots(here)
    assert newer["taken_at"] > older["taken_at"] and newer["fingerprint"] != older["fingerprint"]
    assert newer.keys() == {"fingerprint", "taken_at", "harness_version", "readable", "sources", "tokens"}
    assert newer["sources"] == 4 and newer["readable"] and newer["harness_version"] == "2.0"
    assert records.snapshots({**here, "harness": "h-b"}) == []  # read through another harness: not this one's
    result = records.diff({**here, "from": older["fingerprint"].removeprefix("sha256:")[:10]})  # any unique prefix
    assert result["from"]["fingerprint"] == older["fingerprint"] and result["to"]["fingerprint"] == newer["fingerprint"]
    [changed] = [c for c in result["changes"] if c["kind"] == "changed"]
    assert changed["source_id"] == "notes" and changed["load"] == "always" and changed["delta"] == changed["after"] - changed["before"] > 0
    assert result["always_after"] - result["always_before"] == changed["delta"] + result["unexplained"]
    assert records.diff({**here, "from": newer["fingerprint"], "to": older["fingerprint"]})["changes"][0]["delta"] == -changed["delta"]
    for wrong in ({}, {"from": "ffffffff"}, {"from": "sha256:"}):  # none named, none matching, every one matching
        with pytest.raises(ScopeError):
            records.diff({**here, **wrong})
    with pytest.raises(ScopeError):
        records.snapshots({"project": "/etc"})  # only a project agentlint lists, as everywhere


def test_a_snapshot_this_agentlint_cannot_read_back_is_listed_as_such_and_never_compared(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import zlib

    from agentlint.store import connect

    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: FakeAdapter})
    cli._report(FakeAdapter(), PROJECT.resolve(), Options(Settings()))
    cli._report(Edited(), PROJECT.resolve(), Options(Settings()))
    db = connect()
    oldest = db.execute("SELECT fingerprint, data FROM snapshots ORDER BY taken_at LIMIT 1").fetchone()
    data = json.loads(zlib.decompress(oldest["data"])) | {"schema_version": 0}
    db.execute("UPDATE snapshots SET data = ? WHERE fingerprint = ?", (zlib.compress(json.dumps(data).encode()), oldest["fingerprint"]))
    db.close()
    records = started(monkeypatch).records
    here = {"project": str(PROJECT.resolve())}
    newer, older = records.snapshots(here)
    assert newer["readable"] and older == {**older, "readable": False, "sources": None, "tokens": None}
    with pytest.raises(Unreadable, match="can't be compared"):
        records.diff({**here, "from": older["fingerprint"]})


def test_diagnostics_are_listed_apart_from_findings_whatever_the_project(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentlint.store import connect
    from agentlint.store.diagnostics import Diagnostic, add
    from agentlint.ui.server import ScopeError

    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: FakeAdapter})
    records = started(monkeypatch).records
    here = {"project": str(PROJECT.resolve())}
    assert records.diagnostics(here) == []
    db = connect()
    add(db, Diagnostic("generator_empty", "rules#2", "The generator wrote nothing for this target.", {"reply": "x"}),
        datetime(2026, 10, 4, 9, 0, tzinfo=UTC))
    add(db, Diagnostic("situation_contaminated", "rules#3", "A situation named its own target and was dropped."),
        datetime(2026, 10, 4, 10, 0, tzinfo=UTC))
    db.close()
    data, _ = cli._report(FakeAdapter(), PROJECT.resolve(), Options(Settings()))
    assert [d["kind"] for d in records.diagnostics(here)] == ["situation_contaminated", "generator_empty"]  # newest first
    assert records.diagnostics(here)[1] == {"id": 1, "kind": "generator_empty", "subject": "rules#2",
                                            "message": "The generator wrote nothing for this target.",
                                            "created": "2026-10-04T09:00:00+00:00"}  # its details stay in the store
    assert not any(f["check"] in ("generator_empty", "situation_contaminated") for f in data["check"]["findings"])
    with pytest.raises(ScopeError):
        records.diagnostics({"project": "/etc"})


def test_what_changed_is_answered_over_http_and_only_by_a_server_that_records(server: UIServer) -> None:
    for path in ("/api/snapshots", "/api/diff", "/api/diagnostics"):
        assert get(server, path, token=server.token)[0] == 404  # a server of reports only

    class Kept:
        def snapshots(self, params):
            return [{"fingerprint": "sha256:ab", "asked": params}]

        def diff(self, params):
            from agentlint.ui.server import ScopeError

            raise ScopeError("name the snapshot to compare from (from=<fingerprint>)")

        def diagnostics(self, params):
            return [{"kind": "generator_empty"}]

    running = UIServer(lambda params: ({}, {}), "<html></html>", records=Kept())
    threading.Thread(target=running.serve_forever, daemon=True).start()
    try:
        status, body, _ = get(running, "/api/snapshots?harness=h&project=/w", token=running.token)
        assert status == 200 and json.loads(body) == [{"fingerprint": "sha256:ab", "asked": {"harness": "h", "project": "/w"}}]
        status, body, _ = get(running, "/api/diff?project=/w", token=running.token)
        assert status == 400 and "from=" in json.loads(body)["error"]
        assert json.loads(get(running, "/api/diagnostics", token=running.token)[1]) == [{"kind": "generator_empty"}]
        assert get(running, "/api/snapshots")[0] == 403  # the token, as everywhere
    finally:
        running.shutdown()
        running.server_close()


def test_sessions_are_listed_and_read_by_id_and_nothing_else() -> None:
    kept = {"s1": {"title": None, "items": [{"type": "prompt", "text": "hello \ud800"}]}}
    class Kept(NoSessions):
        def sessions(self, params):
            return [{"session_id": "s1"}]

        def session(self, params, session_id):
            return kept.get(session_id)

    running = UIServer(lambda params: ({}, {}), "<html></html>", catalog=Kept())
    threading.Thread(target=running.serve_forever, daemon=True).start()
    try:
        assert json.loads(get(running, "/api/sessions", token=running.token)[1]) == [{"session_id": "s1"}]
        status, body, _ = get(running, "/api/sessions/s1", token=running.token)
        assert status == 200 and json.loads(body)["items"][0]["text"] == "hello \ud800"
        assert get(running, "/api/sessions/nope", token=running.token)[0] == 404
        assert get(running, "/api/sessions/s1/../../x", token=running.token)[0] == 404
        assert get(running, "/api/sessions/s1")[0] == 403  # the token, as everywhere
    finally:
        running.shutdown()
        running.server_close()


def test_the_cli_serves_a_session_the_harness_deleted_from_the_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    """agentlint ui lists and reads a session from its archive once the harness has deleted the file."""
    from agentlint.config.conversation import Conversation, SessionListing, TranscriptFile
    from agentlint.store import archive, connect

    class Kept(FakeAdapter):
        def listing_from(self, handle: str, data: bytes) -> SessionListing:
            return SessionListing(header(handle), data.decode(), datetime(2026, 10, 1, tzinfo=UTC), 1, len(data))

        def conversation_from(self, handle: str, data: bytes, *, parts: bool = True) -> Conversation:
            return Conversation(data.decode(), ())

    db = connect()
    archive.update(db, FakeAdapter.name, [TranscriptFile("gone-1", 5, time.time(), "gone-1")], lambda k, o=0, n=None: b"title"[o:])
    db.close()
    servers: list = []
    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: Kept})

    import agentlint.ui.server as served

    class Capture(served.UIServer):
        def serve_forever(self, poll_interval: float = 0.5) -> None:
            servers.append(self)

    monkeypatch.setattr(served, "UIServer", Capture)
    assert cli.main(["ui", "--no-open", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    [server] = servers
    listed = server.catalog.sessions({"project": "/w"})
    assert listed == [{**listed[0], "title": "title", "archived_only": True}]
    assert listed[0]["header"]["session_id"] == "gone-1" and listed[0]["header"]["project"] == "/w"  # its header, embedded
    assert server.catalog.session({}, "gone-1")["title"] == "title"
    scope = server.catalog.scopes({})
    assert scope["harnesses"] == [{"name": FakeAdapter.name, "label": "Test harness", "version": None}]
    assert scope["harness"] == FakeAdapter.name and scope["home_dir"] == str(Path.home())
    assert [p["path"] for p in scope["projects"]] == [str(PROJECT.resolve()), "/w"]  # this directory first
    from agentlint.ui.server import ScopeError
    with pytest.raises(ScopeError):
        server.build({"project": "/etc"})  # only a project agentlint lists is ever read


def started(monkeypatch: pytest.MonkeyPatch, *argv: str):
    """`agentlint ui` as main() builds it, captured instead of served."""
    import agentlint.ui.server as served

    servers: list = []

    class Capture(served.UIServer):
        def serve_forever(self, poll_interval: float = 0.5) -> None:
            servers.append(self)

    monkeypatch.setattr(served, "UIServer", Capture)
    assert cli.main(["ui", "--no-open", "--cwd", str(PROJECT), *argv]) == cli.EXIT_OK
    [server] = servers
    server.server_close()
    return server


def test_a_running_ui_reads_your_settings_on_every_request_and_never_prunes_there(monkeypatch: pytest.MonkeyPatch) -> None:
    """keep_days deletes for good: an open UI must neither prune on a page load nor prune with yesterday's value."""
    from agentlint.store import retention

    pruned, given = [], []
    monkeypatch.setattr(retention, "prune", lambda db, now, keep_days: pruned.append(keep_days) or 0)
    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: FakeAdapter})
    report = cli._report
    monkeypatch.setattr(cli, "_report", lambda adapter, cwd, options, **kw: given.append((options.sessions, options.settings))
                        or report(adapter, cwd, options, **kw))
    server = started(monkeypatch)
    assert pruned == [183]  # once, as it starts
    assert cli.main(["config", "set", "keep_days", "365"]) == cli.EXIT_OK
    assert cli.main(["config", "set", "sessions", "4"]) == cli.EXIT_OK
    server.build({})
    assert pruned == [183]  # a page load deletes nothing
    assert given[-1][0] == 4 and given[-1][1].keep_days == 365  # what the file says now
    flagged = started(monkeypatch, "--sessions", "7")
    flagged.build({})
    assert given[-1][0] == 7  # a flag still beats the file


def test_agentlint_ui_starts_but_prunes_nothing_while_keep_days_is_not_what_you_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored keep_days that can't be used, or a config.toml that can't be read, counts as no keep_days at all:
    pruning at the default would delete what you asked to keep. The UI starts on Settings and says so."""
    from agentlint import settings
    from agentlint.store import connect, home, retention

    pruned: list[int] = []
    monkeypatch.setattr(retention, "prune", lambda db, now, keep_days: pruned.append(keep_days) or 0)
    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: FakeAdapter})
    monkeypatch.setattr("agentlint.ui.control.SystemScheduler.installed", lambda self: None)
    connect().execute("INSERT INTO settings (key, value, updated) VALUES ('keep_days', '40000', 'x')")
    server = started(monkeypatch)
    attention = server.control.settings()["attention"]
    assert pruned == [] and settings.HOLD in attention and any("keep_days must be" in why for why in attention)
    server.build({})  # a page load reads with the default in effect, and still deletes nothing
    assert pruned == []
    connect().execute("DELETE FROM settings")
    (home() / "config.toml").write_text("keep_days = 3650\nruns = 600x\n")
    server = started(monkeypatch)
    [why] = server.control.settings()["attention"]
    assert pruned == [] and "couldn't be read" in why and "nothing is deleted by age" in why
    assert (home() / "config.toml").exists()
    (home() / "config.toml").unlink()
    started(monkeypatch)
    assert pruned == [183]  # nothing held: it prunes as it starts


def test_what_settings_is_told_to_fix_goes_once_it_is_fixed_without_a_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hold was added once, as the server started, and outlived the fix: every page load opened on Settings."""
    from agentlint import settings
    from agentlint.store import connect, home, retention

    monkeypatch.setattr(retention, "prune", lambda db, now, keep_days: 0)
    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: FakeAdapter})
    monkeypatch.setattr("agentlint.ui.control.SystemScheduler.installed", lambda self: None)
    connect().execute("""INSERT INTO settings (key, value, updated) VALUES ('keep_days', '"lots"', 'x')""")
    server = started(monkeypatch)
    assert server.control.settings()["attention"] == ["the stored keep_days must be a whole number from 1 to 36500",
                                                      settings.HOLD]
    answer = server.control.set_setting({"key": "keep_days", "value": 90})
    assert answer["attention"] == [] and server.control.settings()["attention"] == []
    connect().execute("""UPDATE settings SET value = '"lots"' WHERE key = 'keep_days'""")
    answer = server.control.set_setting({"key": "keep_days", "reset": True})  # Settings' Reset on a row that can't be used
    [entry] = [e for e in answer["settings"] if e["key"] == "keep_days"]
    assert answer["attention"] == [] and (entry["value"], entry["set"], entry["problem"]) == (183, False, None)
    connect().execute("DELETE FROM settings")
    (home() / "config.toml").write_text("keep_days = 3650\nruns = 600x\n")
    server = started(monkeypatch)
    [why] = server.control.settings()["attention"]
    assert "couldn't be read" in why
    (home() / "config.toml").unlink()  # removed, as the notice says: it goes
    assert server.control.settings()["attention"] == []


def test_a_running_ui_lists_nothing_you_forgot(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentlint.config.conversation import SessionListing, TranscriptFile
    from agentlint.store import archive, connect

    class Kept(FakeAdapter):
        def listing_from(self, handle: str, data: bytes) -> SessionListing:
            return SessionListing(header(handle), data.decode(), datetime(2026, 10, 1, tzinfo=UTC), 1, len(data))

    db = connect()
    archive.update(db, FakeAdapter.name, [TranscriptFile("gone-1", 5, time.time(), "gone-1")], lambda k, o=0, n=None: b"title"[o:])
    db.close()
    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: Kept})
    server = started(monkeypatch)
    assert [s["header"]["session_id"] for s in server.catalog.sessions({"project": "/w"})] == ["gone-1"]
    assert cli.main(["forget", "--yes"]) == cli.EXIT_OK
    assert [p["path"] for p in server.catalog.scopes({})["projects"]] == [str(PROJECT.resolve())]  # its project, gone with it
    assert server.catalog.session({}, "gone-1") is None


@pytest.mark.parametrize("port", ["70000", "-1", "http"])
def test_a_port_out_of_range_is_a_tool_error_not_a_traceback(port: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.main(["ui", "--no-open", "--port", port])
    assert stopped.value.code == cli.EXIT_TOOL_ERROR and "--port" in capsys.readouterr().err


def test_a_subagent_reference_is_handed_back_as_is_and_judged_by_the_adapter() -> None:
    asked: list[str] = []

    class Subagents(NoSessions):
        def subagents(self, params, session_id, ref):  # the harness's side: its adapter says which references it writes
            asked.append(ref)
            return [] if ref.startswith("agent:") else None

    running = UIServer(lambda params: ({}, {}), "<html></html>", catalog=Subagents())
    threading.Thread(target=running.serve_forever, daemon=True).start()
    try:
        assert get(running, "/api/sessions/s1/subagents?ref=agent:a1", token=running.token)[0] == 200
        assert get(running, "/api/sessions/s1/subagents?ref=run:x", token=running.token)[0] == 400  # the adapter said no
        for ref in ("", "agent:" + "x" * 200):  # nothing, or too long for any reference: never handed on
            assert get(running, f"/api/sessions/s1/subagents?ref={ref}", token=running.token)[0] == 400
        assert get(running, "/api/sessions/s1/subagents", token=running.token)[0] == 400
        assert asked == ["agent:a1", "run:x"]
    finally:
        running.shutdown()
        running.server_close()


def harness(name: str, calls: list, *, installed: bool = True) -> type:
    """A test harness called ``name`` that records what was read through it."""

    class Named(FakeAdapter):
        label = f"Harness {name.upper()}"

        def installed(self) -> bool:
            return installed

        def version(self) -> str | None:
            return f"{name}-1.0"

        def read_declared(self, cwd):
            calls.append((name, "configuration"))
            return super().read_declared(cwd)

        def list_conversations(self):
            calls.append((name, "sessions"))
            return []

        def read_conversation(self, session_id: str):
            calls.append((name, session_id))
            return None

        def transcripts(self):
            calls.append((name, "transcripts"))
            return []

    Named.name = name
    return Named


def test_the_ui_lists_every_installed_harness_in_your_order_and_reads_through_the_one_chosen(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from agentlint.ui.server import ScopeError

    calls: list = []
    monkeypatch.setattr(cli, "ADAPTERS", {"h-a": harness("h-a", calls), "h-b": harness("h-b", calls),
                                          "h-c": harness("h-c", calls, installed=False)})
    server = started(monkeypatch)
    scope = server.catalog.scopes({})
    assert [h["name"] for h in scope["harnesses"]] == ["h-a", "h-b"]  # the one not installed is not listed
    assert scope["harnesses"][0] == {"name": "h-a", "label": "Harness H-A", "version": "h-a-1.0"} and scope["harness"] == "h-a"
    assert server.catalog.scopes({"harness": "h-b"})["harness"] == "h-b"
    calls.clear()
    server.build({"harness": "h-b"})
    assert ("h-b", "configuration") in calls and not [c for c in calls if c[0] != "h-b"]
    calls.clear()
    assert server.catalog.session({"harness": "h-b"}, "s1") is None and calls[0] == ("h-b", "s1")
    assert server.catalog.session({}, "s1") is None and calls[-1] == ("h-a", "s1")  # no harness named: the first listed
    calls.clear()
    assert server.catalog.subagents({"harness": "h-b"}, "s1", "agent:x") == [] and server.catalog.subagent({"harness": "h-b"}, "s1", "x") is None
    assert {c[0] for c in calls} == {"h-b"}
    for unlisted in ("nope", "h-c"):  # never one agentlint doesn't list here, installed or not
        for call in (lambda p: server.catalog.scopes(p), server.build, server.catalog.sessions, lambda p: server.catalog.session(p, "s1"),
                     lambda p: server.catalog.subagents(p, "s1", "agent:x"), lambda p: server.catalog.subagent(p, "s1", "x")):
            with pytest.raises(ScopeError):
                call({"harness": unlisted})
    assert cli.main(["config", "set", "harnesses", "h-c,h-b,h-a"]) == cli.EXIT_OK
    assert [h["name"] for h in started(monkeypatch).catalog.scopes({})["harnesses"]] == ["h-b", "h-a"]  # your order


def test_with_no_harness_installed_the_ui_says_so_and_stops(monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "ADAPTERS", {"h-a": harness("h-a", [], installed=False)})
    assert cli.main(["ui", "--no-open", "--cwd", str(PROJECT)]) == cli.EXIT_TOOL_ERROR
    assert "no harness agentlint reads is installed on this machine (it looked for h-a)" in capsys.readouterr().err


def test_a_scope_the_server_does_not_list_is_refused_with_400() -> None:
    from agentlint.ui.server import ScopeError

    def refuse(params, *rest):
        if params.get("harness") == "nope":
            raise ScopeError("not a harness agentlint lists here: nope")
        return {"harness": "h-a"} if not rest else None

    class Refusing(NoSessions):
        scopes = session = staticmethod(refuse)

        def sessions(self, params):
            return refuse(params) and []

    running = UIServer(lambda params: refuse(params) and ({}, {}), "<html></html>", catalog=Refusing())
    threading.Thread(target=running.serve_forever, daemon=True).start()
    try:
        for path in ("/api/scope", "/api/report", "/api/sessions", "/api/sessions/s1"):
            status, body, _ = get(running, f"{path}?harness=nope", token=running.token)
            assert status == 400 and "nope" in json.loads(body)["error"], path
        assert get(running, "/api/scope?harness=h-a", token=running.token)[0] == 200
    finally:
        running.shutdown()
        running.server_close()


def test_texts_are_kept_per_harness_and_project_and_asking_never_reads(server: UIServer) -> None:
    def texts(query: str) -> tuple[int, dict]:
        status, body, _ = get(server, f"/api/texts?{query}", token=server.token)
        return status, json.loads(body)

    assert texts("harness=h-a&project=/here")[0] == 404  # never read: no hidden reading either
    assert get(server, "/api/report?project=/a", token=server.token)[0] == 200
    assert get(server, "/api/report?harness=h-b&project=/a", token=server.token)[0] == 200
    assert texts("harness=h-a&project=/a") == (200, {"ref": "text of /a \ud800 with a lone surrogate"})
    assert texts("harness=h-b&project=/a")[0] == 200  # the same project in another harness: its own entry
    assert texts("harness=h-a&project=/b")[0] == 404 and "aren't kept" in texts("harness=h-a&project=/b")[1]["error"]
    assert texts("project=/a")[0] == 400 and texts("harness=h-a")[0] == 400  # a scope is both, never a guess
    assert json.loads(get(server, "/api/report", token=server.token)[1])["readings"] == 3  # none of that read anything
    for project in ("/b", "/c", "/d"):  # six scopes read now (the third, /here): the two oldest are let go
        get(server, f"/api/report?project={project}", token=server.token)
    kept = [texts(f"harness={h}&project={p}")[0] for h, p in (("h-a", "/a"), ("h-b", "/a"), ("h-a", "/here"), ("h-a", "/d"))]
    assert kept == [404, 404, 200, 200]
    assert len(server.texts) == 4
    get(server, "/api/report?project=/b", token=server.token)  # read again: the freshest, kept longest
    assert list(server.texts)[-1] == ("h-a", "/b")


def test_the_default_scope_and_the_same_one_named_keep_one_entry_of_texts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: FakeAdapter})
    server = started(monkeypatch)
    for params in ({}, {"harness": FakeAdapter.name, "project": str(PROJECT.resolve())}):
        server.keep_texts(*server.build(params))
    assert list(server.texts) == [(FakeAdapter.name, str(PROJECT.resolve()))]  # what the UI asks with: its snapshot's
    assert server.kept_texts(FakeAdapter.name, str(PROJECT.resolve()))


def test_a_running_ui_keeps_one_parse_per_subagent_and_only_so_many(monkeypatch: pytest.MonkeyPatch) -> None:
    """A subagent still running grows between views: the parse of its older size is let go, never kept beside the
    new one, and only the most recently viewed KEPT_SUBAGENTS stay at all — an open UI's memory stays bounded."""
    import gc
    import weakref

    from agentlint.config.conversation import Conversation, SessionListing, TranscriptFile

    sizes = {f"agent-{n}": 1 for n in range(KEPT_SUBAGENTS + 1)}
    parses: list[str] = []

    class Running(FakeAdapter):
        def transcripts(self) -> list[TranscriptFile]:
            return [TranscriptFile(handle, size, 0.0, "s1", subagent=True) for handle, size in sizes.items()]

        def read_transcript(self, handle: str, offset: int = 0, length: int | None = None) -> bytes:
            return b"x" * sizes[handle]

        def conversation_from(self, handle: str, data: bytes, *, parts: bool = True) -> Conversation:
            parses.append(handle)
            return Conversation(handle, ())

    from agentlint.ui import catalog

    captured: list = []
    monkeypatch.setattr(catalog, "to_data", lambda c: captured.append(weakref.ref(c)) or {"id": c.title})
    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: Running})
    server = started(monkeypatch)

    assert server.catalog.subagent({}, "s1", "agent-0") == {"id": "agent-0"}
    server.catalog.subagent({}, "s1", "agent-0")
    assert parses == ["agent-0"]  # unchanged: served from memory
    first = captured[0]
    sizes["agent-0"] = 2  # it ran on
    server.catalog.subagent({}, "s1", "agent-0")
    gc.collect()
    assert parses == ["agent-0", "agent-0"] and first() is None  # the older size's parse is gone
    for n in range(1, KEPT_SUBAGENTS + 1):
        server.catalog.subagent({}, "s1", f"agent-{n}")
    gc.collect()
    assert captured[2]() is None  # agent-0, the least recently viewed, let go past the cap
    server.catalog.subagent({}, "s1", f"agent-{KEPT_SUBAGENTS}")
    assert parses.count(f"agent-{KEPT_SUBAGENTS}") == 1  # the newest still kept


def test_listing_subagents_parses_each_without_its_briefings_parts_once_per_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """Listing a workflow's subagents reads only a title and a count: no briefing parts, so no discovery and no
    counting — and a parse already kept for viewing is used as it is."""
    from agentlint.config.conversation import Conversation, Prompt, TranscriptFile

    sizes = {"agent-0": 1, "agent-1": 1}
    parses: list[tuple[str, bool]] = []

    class Running(FakeAdapter):
        def transcripts(self) -> list[TranscriptFile]:
            return [TranscriptFile(handle, size, 0.0, "s1", subagent=True) for handle, size in sizes.items()]

        def read_transcript(self, handle: str, offset: int = 0, length: int | None = None) -> bytes:
            return b"x" * sizes[handle]

        def belongs(self, ref: str, handle: str, read) -> bool:
            return True

        def conversation_from(self, handle: str, data: bytes, *, parts: bool = True) -> Conversation:
            parses.append((handle, parts))
            return Conversation(None, (Prompt(None, f"asked {handle}\nmore"),) * 2)

    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: Running})
    server = started(monkeypatch)
    server.catalog.subagent({}, "s1", "agent-1")  # viewed: its full parse is kept
    listed = server.catalog.subagents({}, "s1", "agent:x")
    assert listed == [{"id": "agent-0", "title": "asked agent-0", "entries": 2},
                      {"id": "agent-1", "title": "asked agent-1", "entries": 2}]
    assert parses == [("agent-1", True), ("agent-0", False)]
    server.catalog.subagents({}, "s1", "agent:x")
    assert len(parses) == 2  # both listed from memory
    sizes["agent-0"] = 2  # it ran on
    server.catalog.subagents({}, "s1", "agent:x")
    assert parses[2:] == [("agent-0", False)]
    server.catalog.subagent({}, "s1", "agent-0")
    assert parses[3:] == [("agent-0", True)]  # a list entry is never served as the transcript


def test_listing_a_projects_sessions_gets_its_adapter_ready_to_read_them(monkeypatch: pytest.MonkeyPatch) -> None:
    prepared: list[Path] = []

    class Ready(FakeAdapter):
        def prepare(self, project: Path) -> None:
            prepared.append(project)

    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: Ready})
    server = started(monkeypatch)
    server.catalog.sessions({})
    assert prepared == [PROJECT]


def test_an_unexpected_error_prints_its_traceback_and_answers_500_and_an_expected_one_only_says_why(
        capsys: pytest.CaptureFixture[str]) -> None:
    def build(params):
        if params.get("project") == "/bug":
            raise KeyError("a bug")
        raise Unreadable("the agentlint database: disk I/O error")

    running = UIServer(build, "<html></html>")
    threading.Thread(target=running.serve_forever, daemon=True).start()
    try:
        status, body, _ = get(running, f"/api/report?project=/bug&token={running.token}")
        assert status == 500 and "KeyError: 'a bug'" in json.loads(body)["error"]
        err = capsys.readouterr().err
        assert "Traceback (most recent call last)" in err and "raise KeyError" in err
        assert running.token not in err  # the request carried it in its query: never printed
        status, body, _ = get(running, "/api/report", token=running.token)
        assert status == 500 and json.loads(body) == {"error": "the agentlint database: disk I/O error"}
        assert "Traceback" not in capsys.readouterr().err  # expected: said, not a bug
        assert get(running, f"/?token={running.token}")[0] == 200  # still serving
    finally:
        running.shutdown()
        running.server_close()


def test_the_catalog_lists_once_per_request_and_is_built_without_the_cli() -> None:
    """/api/sessions checks the project against the same listing it filters: one scan of the harness and the store."""
    from agentlint.config.conversation import SessionListing
    from agentlint.store import connect
    from agentlint.ui.catalog import Catalogs, SessionCatalog
    from agentlint.ui.server import ScopeError

    scans: list[str] = []

    class Listing(FakeAdapter):
        def list_conversations(self):
            scans.append("listed")
            return [SessionListing(header("s1"), "hello", datetime(2026, 10, 2, tzinfo=UTC), 1, 10)]

    catalogs = Catalogs([SessionCatalog(Listing(), connect)], PROJECT)
    assert [s["header"]["session_id"] for s in catalogs.sessions({"project": "/w"})] == ["s1"] and scans == ["listed"]
    assert [p["path"] for p in catalogs.scopes({})["projects"]] == [str(PROJECT), "/w"]
    with pytest.raises(ScopeError):
        catalogs.sessions({"project": "/elsewhere"})


class Recorder:
    """A control that records each write and answers it, refusing a value of "no"."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, dict]] = []

    def settings(self) -> dict:
        return {"settings": [], "first_run": True}

    def _take(self, name: str, body: dict) -> dict:
        from agentlint.ui.server import Refused

        if body.get("value") == "no":
            raise Refused(400, "sessions must be a whole number from 0 to 1000")
        self.writes.append((name, body))
        return {"ok": name}

    def set_setting(self, body: dict) -> dict:
        return self._take("settings", body)

    def schedule(self, body: dict) -> dict:
        return self._take("schedule", body)

    def forget(self, body: dict) -> dict:
        return self._take("forget", body)

    def welcomed(self, body: dict) -> dict:
        return self._take("welcomed", body)

    def suppress(self, body: dict) -> dict:
        return self._take("suppress", body)

    def unsuppress(self, body: dict) -> dict:
        return self._take("unsuppress", body)

    def suppressions(self) -> dict:
        return {"suppressions": [], "home_dir": "/home/me"}


@pytest.fixture
def writable() -> Iterator[tuple[UIServer, Recorder]]:
    control = Recorder()
    running = UIServer(lambda params: ({}, {}), "<html></html>", control=control)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    yield running, control
    running.shutdown()
    running.server_close()


def post(server: UIServer, path: str, body: bytes | object = b"{}", *, token: str | None = "", host: str | None = None,
         headers: dict[str, str] | None = None, method: str = "POST") -> tuple[int, dict, dict]:
    """A request as a page would send it: the token in its header, JSON, this server's Origin — each overridable."""
    connection = http.client.HTTPConnection(HOST, server.server_port, timeout=5)
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    sent = {"Host": host or f"{HOST}:{server.server_port}", "Content-Type": "application/json",
            "Origin": f"http://{HOST}:{server.server_port}", "Content-Length": str(len(data))}
    if token is not None:
        sent[TOKEN_HEADER] = token or server.token
    sent.update(headers or {})
    connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for name, value in sent.items():
        if value is not None:
            connection.putheader(name, value)
    connection.endheaders()
    connection.send(data)
    response = connection.getresponse()
    raw = response.read()
    if response.getheader("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    try:
        answer = json.loads(raw)
    except ValueError:
        answer = {"raw": raw.decode(errors="replace")}
    return response.status, answer, {k.lower(): v for k, v in response.getheaders()}


def test_a_write_from_the_page_is_done(writable: tuple[UIServer, Recorder]) -> None:
    server, control = writable
    status, answer, _ = post(server, "/api/settings", {"key": "sessions", "value": 4})
    assert (status, answer) == (200, {"ok": "settings"}) and control.writes == [("settings", {"key": "sessions", "value": 4})]
    for path, name in (("/api/schedule", "schedule"), ("/api/forget", "forget"), ("/api/welcomed", "welcomed"),
                       ("/api/suppress", "suppress"), ("/api/unsuppress", "unsuppress")):
        assert post(server, path, {"on": True})[:2] == (200, {"ok": name})
    assert post(server, "/api/settings", headers={"Origin": None})[0] == 200  # a request with no Origin: not a browser's cross-site one
    assert post(server, "/api/settings", headers={"Content-Type": "application/json; charset=utf-8"})[0] == 200
    status, body, _ = get(server, "/api/settings", token=server.token)
    assert status == 200 and json.loads(body) == {"settings": [], "first_run": True}


@pytest.mark.parametrize("why,kwargs,status", [
    ("no token", {"token": None}, 403),
    ("a wrong token", {"token": "wrong"}, 403),
    ("a foreign host", {"host": "evil.example:80"}, 403),
    ("another origin", {"headers": {"Origin": "http://evil.example"}}, 403),
    ("this host on another port", {"headers": {"Origin": "http://127.0.0.1:1"}}, 403),
    ("a sandboxed page", {"headers": {"Origin": "null"}}, 403),
    ("a form's body", {"headers": {"Content-Type": "application/x-www-form-urlencoded"}}, 415),
    ("plain text", {"headers": {"Content-Type": "text/plain"}}, 415),
    ("no type", {"headers": {"Content-Type": None}}, 415),
    ("no length", {"headers": {"Content-Length": None}}, 411),
    ("chunked", {"headers": {"Transfer-Encoding": "chunked"}}, 411),
    ("a nonsense length", {"headers": {"Content-Length": "lots"}}, 400),
    ("not JSON", {"body": b"{nope"}, 400),
    ("not an object", {"body": b"[1, 2]"}, 400),
    ("NaN", {"body": b'{"value": NaN}'}, 400),
    ("bytes that aren't UTF-8", {"body": b'{"value": "\xff"}'}, 400),
    ("a value the control refuses", {"body": {"key": "sessions", "value": "no"}}, 400),
])
@pytest.mark.parametrize("path", ["/api/settings", "/api/schedule", "/api/forget", "/api/welcomed", "/api/suppress",
                                  "/api/unsuppress"])
def test_every_write_that_breaks_a_rule_is_refused_with_why(writable: tuple[UIServer, Recorder], why: str, kwargs: dict,
                                                           status: int, path: str) -> None:
    server, control = writable
    answered, answer, headers = post(server, path, **kwargs)
    assert answered == status, why
    assert headers["content-type"] == "application/json" and answer["error"]  # a reason, in JSON
    assert server.token not in json.dumps(answer)
    assert control.writes == []


def test_a_token_in_the_address_is_refused_for_a_write(writable: tuple[UIServer, Recorder]) -> None:
    server, control = writable
    for query in (f"?token={server.token}", "?token="):
        for path in ("/api/settings", "/api/suppress", "/api/unsuppress"):
            status, answer, _ = post(server, f"{path}{query}")
            assert status == 403 and "header only" in answer["error"]
    assert control.writes == []


def test_a_body_over_the_cap_is_refused_unread(writable: tuple[UIServer, Recorder]) -> None:
    from agentlint.ui.server import MAX_BODY

    server, control = writable
    status, answer, _ = post(server, "/api/settings", {"key": "x" * MAX_BODY})
    assert status == 413 and f"{MAX_BODY:,}" in answer["error"] and control.writes == []
    big = b'{"key": "' + b"x" * (MAX_BODY - 11) + b'"}'
    assert len(big) == MAX_BODY and post(server, "/api/settings", big)[0] == 200  # the cap itself is allowed


def test_there_is_no_preflight_so_a_cross_origin_page_cannot_send_json(writable: tuple[UIServer, Recorder]) -> None:
    server, control = writable
    status, _, headers = post(server, "/api/settings", b"", method="OPTIONS",
                              headers={"Access-Control-Request-Method": "POST", "Origin": "http://evil.example"})
    assert status == 501 and not any(name.startswith("access-control-") for name in headers)
    for method in ("PUT", "DELETE", "PATCH"):
        assert post(server, "/api/settings", method=method)[0] == 501
    assert control.writes == []


def test_a_write_nowhere_and_a_server_without_a_control_answer_404(writable: tuple[UIServer, Recorder],
                                                                   server: UIServer) -> None:
    running, control = writable
    assert post(running, "/api/report")[0] == 404 and post(running, "/api/settings/../forget")[0] == 404
    assert control.writes == []
    assert post(server, "/api/settings", {"key": "sessions", "value": 4})[0] == 404  # reports only: nothing to change
    assert get(server, "/api/settings", token=server.token)[0] == 404
    assert post(server, "/api/suppress", {"finding_id": "abcd", "project": "/p", "reason": "x"})[0] == 404
    assert post(server, "/api/unsuppress", {"finding_id": "abcd"})[0] == 404
    assert get(server, "/api/suppressions", token=server.token)[0] == 404


def test_a_write_that_fails_unexpectedly_is_a_500_that_never_says_the_token(capsys: pytest.CaptureFixture[str]) -> None:
    class Broken(Recorder):
        def forget(self, body: dict) -> dict:
            raise RuntimeError("the disk went away")

    running = UIServer(lambda params: ({}, {}), "<html></html>", control=Broken())
    threading.Thread(target=running.serve_forever, daemon=True).start()
    try:
        status, answer, _ = post(running, "/api/forget", {"confirm": "forget"})
        assert status == 500 and "the disk went away" in answer["error"]
        assert running.token not in capsys.readouterr().err  # the traceback is printed; the token never
        assert post(running, "/api/settings", {"key": "sessions", "value": 4})[0] == 200  # and it carries on
    finally:
        running.shutdown()
        running.server_close()
