"""Dismissing a finding from the page, showing it again, and the Dismissed screen's list (docs/tech_impl/11-ui.md,
"Views › 3. Findings" and "Views › 8. Dismissed"): the same suppressions `agentlint suppress` makes, through the guarded POSTs."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from agentlint import cli, settings
from agentlint.checks import Check
from agentlint.report.finding import Severity
from agentlint.store import connect
from agentlint.store.suppressions import load
from agentlint.ui.control import REASON_MAX, SystemScheduler
from agentlint.ui.server import Refused, UIServer

from .checks.test_findings_and_cli import PROJECT, FakeAdapter, finding
from .store.helpers import check_at, configuration, resting_on, rows
from .test_ui_server import get, post, started

T0 = datetime(2026, 10, 1, 9, tzinfo=UTC)
SHOWN = finding(severity=Severity.ERROR)  # what FakeAdapter's own check finds in PROJECT


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Iterator[UIServer]:
    """`agentlint ui --cwd PROJECT` as main() builds it — its control, its catalog, its quiet check — served."""
    monkeypatch.setattr(cli, "ADAPTERS", {FakeAdapter.name: FakeAdapter})
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)  # `check` below reads the fake harness, never this machine's
    monkeypatch.setattr(SystemScheduler, "installed", lambda self: None)  # never the real launchd or crontab
    built = started(monkeypatch)
    running = UIServer(built.build, "<html></html>", catalog=built.catalog, control=built.control)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    yield running
    running.shutdown()
    running.server_close()


def listed(server: UIServer) -> dict:
    status, body, _ = get(server, "/api/suppressions", token=server.token)
    assert status == 200
    return json.loads(body)


def shown_in_report(server: UIServer) -> dict:
    data, _ = server.build({})
    return {f["id"]: f for f in data["check"]["findings"]}


def test_a_finding_dismissed_from_the_page_is_suppressed_as_the_cli_suppresses_it(served: UIServer) -> None:
    assert not shown_in_report(served)[SHOWN.id]["suppressed"]
    assert rows(connect(), "SELECT count(*) FROM findings")[0][0] > 0  # the page's reading records, as `check` does
    status, answer, _ = post(served, "/api/suppress", {"finding_id": SHOWN.id, "project": str(PROJECT),
                                                       "harness": FakeAdapter.name, "reason": "  known, on purpose  "})
    assert status == 200, answer
    entry = answer["suppression"]
    assert entry["id"] == SHOWN.id and entry["reason"] == "known, on purpose" and entry["check"] == "demo-check"
    assert entry["title"] == SHOWN.title and entry["projects"] == [{"project": str(PROJECT), "state": "active"}]
    assert "armed now in the 1 project where it is found" in answer["note"]
    # the quiet check recorded the project, as `suppress --cwd` does, and the suppression is armed there
    assert set(load(connect())[SHOWN.id].baselines) == {str(PROJECT)}
    assert shown_in_report(served)[SHOWN.id]["suppressed"]
    assert cli.main(["check", "--cwd", str(PROJECT), "--fail-on", "error"]) == cli.EXIT_OK  # suppressed never fail
    assert listed(served)["suppressions"] == [entry]
    assert listed(served)["home_dir"]
    status, answer, _ = post(served, "/api/unsuppress", {"finding_id": SHOWN.id})
    assert (status, answer) == (200, {"finding_id": SHOWN.id, "was_dismissed": True})
    assert post(served, "/api/unsuppress", {"finding_id": SHOWN.id})[1]["was_dismissed"] is False  # already shown
    assert listed(served)["suppressions"] == [] and not shown_in_report(served)[SHOWN.id]["suppressed"]
    assert rows(connect(), "SELECT count(*) FROM suppression_baselines") == [(0,)]


def test_a_dismissal_names_its_harness_or_takes_the_first_listed(served: UIServer) -> None:
    status, answer, _ = post(served, "/api/suppress", {"finding_id": SHOWN.id, "project": str(PROJECT), "reason": "x"})
    assert status == 200 and answer["suppression"]["projects"][0]["state"] == "active"
    status, answer, _ = post(served, "/api/suppress", {"finding_id": SHOWN.id, "project": str(PROJECT), "reason": "x",
                                                       "harness": "nosuch"})
    assert status == 400 and "not a harness agentlint lists here: nosuch" in answer["error"]


@pytest.mark.parametrize("body,status,reason", [
    ({"finding_id": SHOWN.id, "project": str(PROJECT)}, 400, "a reason is required"),
    ({"finding_id": SHOWN.id, "project": str(PROJECT), "reason": "   \n "}, 400, "a reason is required"),
    ({"finding_id": SHOWN.id, "project": str(PROJECT), "reason": 7}, 400, "a reason is required"),
    ({"finding_id": SHOWN.id, "project": str(PROJECT), "reason": "x" * (REASON_MAX + 1)}, 400, "at most 1,000 characters"),
    ({"project": str(PROJECT), "reason": "x"}, 400, "name the finding by its id"),
    ({"finding_id": "not hex!", "project": str(PROJECT), "reason": "x"}, 400, "name the finding by its id"),
    ({"finding_id": SHOWN.id.upper(), "project": str(PROJECT), "reason": "x"}, 400, "name the finding by its id"),
    ({"finding_id": SHOWN.id, "reason": "x"}, 400, "name the project"),
    ({"finding_id": SHOWN.id, "project": "", "reason": "x"}, 400, "name the project"),
    ({"finding_id": SHOWN.id, "project": str(PROJECT), "reason": "x", "harness": 3}, 400, "harness must be"),
    ({"finding_id": SHOWN.id, "project": str(PROJECT), "reason": "x", "also": 1}, 400, "unexpected field: also"),
    ({"finding_id": SHOWN.id, "project": "/not/listed", "reason": "x"}, 400, "not a project agentlint knows"),
    ({"finding_id": "abcdef1234567890", "project": str(PROJECT), "reason": "x"}, 404, "no finding abcdef1234567890"),
])
def test_a_dismissal_the_server_wont_make_is_refused_with_why(served: UIServer, body: dict, status: int,
                                                              reason: str) -> None:
    answered, answer, _ = post(served, "/api/suppress", body)
    assert answered == status and reason in answer["error"], answer
    assert load(connect()) == {}  # nothing dismissed


def test_a_prefix_is_refused_once_it_names_a_finding(served: UIServer) -> None:
    assert post(served, "/api/suppress", {"finding_id": SHOWN.id, "project": str(PROJECT), "reason": "x"})[0] == 200
    assert post(served, "/api/unsuppress", {"finding_id": SHOWN.id})[0] == 200
    answered, answer, _ = post(served, "/api/suppress", {"finding_id": SHOWN.id[:8], "project": str(PROJECT),
                                                         "reason": "x"})
    assert answered == 400 and "only the start of a finding id" in answer["error"]
    assert post(served, "/api/unsuppress", {"finding_id": SHOWN.id[:8]})[0] == 400
    assert load(connect()) == {}


def test_a_finding_found_only_in_another_project_is_not_dismissed_from_this_one(served: UIServer) -> None:
    other = finding(discriminator="elsewhere only")
    check_at(connect(), T0, [resting_on(other, ["a"])], {"a": "A"}, project="/elsewhere")
    answered, answer, _ = post(served, "/api/suppress", {"finding_id": other.id, "project": str(PROJECT), "reason": "x"})
    assert answered == 409 and "isn't found in /work/project now (it is found in 1 other project)" in answer["error"]
    assert load(connect()) == {}


@pytest.mark.parametrize("body,status,reason", [
    ({}, 400, "name the finding by its id"),
    ({"finding_id": 12}, 400, "name the finding by its id"),
    ({"finding_id": SHOWN.id, "reason": "x"}, 400, "unexpected field: reason"),
    ({"finding_id": "abcdef1234567890"}, 404, "no finding abcdef1234567890 is recorded or dismissed"),
])
def test_showing_again_what_the_server_doesnt_know_is_refused(served: UIServer, body: dict, status: int,
                                                               reason: str) -> None:
    answered, answer, _ = post(served, "/api/unsuppress", body)
    assert answered == status and reason in answer["error"], answer


def test_a_dismissal_is_refused_when_the_harnesses_setting_leaves_its_harness_out(served: UIServer,
                                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    read: list = []
    monkeypatch.setattr(cli, "read_config", lambda *a, **k: read.append(a))
    settings.store(connect(), "harnesses", ["codex"])  # since the UI started
    answered, answer, _ = post(served, "/api/suppress", {"finding_id": SHOWN.id, "project": str(PROJECT), "reason": "x"})
    assert answered == 409 and "leaves out" in answer["error"] and "change it in Settings" in answer["error"]
    assert read == [] and load(connect()) == {}  # nothing read, nothing dismissed


def test_the_dismissed_list_says_per_project_where_it_hides_where_it_lapsed_and_where_it_is_no_longer_found(
        served: UIServer) -> None:
    from agentlint.store.snapshots import save
    from agentlint.store.suppressions import suppress

    db = connect()
    budget = finding(discriminator="always-on", title="The always-on budget is large")

    def checked(at: datetime, found: list, files: dict, project: str) -> None:  # stored, as `check` stores it
        save(db, configuration(files, project=project), at)
        check_at(db, at, [resting_on(f, files) for f in found], files, project=project)

    for project, files in (("/a", {"a/CLAUDE.md": "A"}), ("/b", {"b/CLAUDE.md": "B"}), ("/c", {"c/CLAUDE.md": "C"})):
        checked(T0, [budget], files, project)
    suppress(db, budget.id, "known", T0 + timedelta(minutes=1))
    checked(T0 + timedelta(hours=1), [budget], {"b/CLAUDE.md": "B, edited"}, "/b")
    checked(T0 + timedelta(hours=1), [], {"c/CLAUDE.md": "C, fixed"}, "/c")
    db.execute("INSERT INTO suppressions (id, reason, at) VALUES ('0123456789abcdef', 'gone since', ?)",
               ((T0 - timedelta(days=1)).isoformat(),))  # its finding no longer recorded anywhere
    [entry, gone] = listed(served)["suppressions"]  # the most recent first
    assert entry["id"] == budget.id and entry["title"] == "The always-on budget is large" and entry["reason"] == "known"
    assert entry["at"] == (T0 + timedelta(minutes=1)).isoformat()
    assert entry["projects"] == [{"project": "/a", "state": "active"}, {"project": "/b", "state": "lapsed"},
                                 {"project": "/c", "state": "moot"}]
    assert gone == {"id": "0123456789abcdef", "check": None, "title": None, "at": (T0 - timedelta(days=1)).isoformat(),
                    "reason": "gone since", "projects": []}
    assert post(served, "/api/unsuppress", {"finding_id": "0123456789abcdef"})[1]["was_dismissed"] is True
    assert [e["id"] for e in listed(served)["suppressions"]] == [budget.id]


def _nothing_checked_or_dismissed() -> None:
    assert rows(connect(), "SELECT count(*) FROM snapshots") == [(0,)]  # no reading stored
    assert rows(connect(), "SELECT count(*) FROM findings") == [(0,)]  # nothing recorded
    assert load(connect()) == {}


def test_a_dismissal_is_refused_while_a_stored_setting_cant_be_used_as_suppress_refuses_it(served: UIServer) -> None:
    """The quiet check would read with the default in its place and store that reading as comparable."""
    connect().execute("""INSERT INTO settings (key, value, updated) VALUES ('sessions', '"lots"', 'x')""")
    assert cli.main(["suppress", SHOWN.id, "--reason", "x", "--cwd", str(PROJECT)]) == cli.EXIT_TOOL_ERROR
    answered, answer, _ = post(served, "/api/suppress", {"finding_id": SHOWN.id, "project": str(PROJECT), "reason": "x"})
    assert answered == 409, answer
    assert "sessions must be a whole number" in answer["error"] and "change sessions in Settings" in answer["error"]
    _nothing_checked_or_dismissed()
    settings.unset(connect(), "sessions")  # fixed in Settings: the next dismissal goes through
    assert post(served, "/api/suppress", {"finding_id": SHOWN.id, "project": str(PROJECT), "reason": "x"})[0] == 200


def test_a_dismissal_is_refused_while_a_config_toml_cant_be_read_and_goes_through_once_it_is_fixed(
        served: UIServer) -> None:
    """No GET /api/settings in between: the dismissal tries the import itself, as every request does."""
    from agentlint.store import home

    legacy = home() / "config.toml"
    legacy.write_text("sessions = 3x\n")  # since the UI started
    assert cli.main(["suppress", SHOWN.id, "--reason", "x", "--cwd", str(PROJECT)]) == cli.EXIT_TOOL_ERROR
    answered, answer, _ = post(served, "/api/suppress", {"finding_id": SHOWN.id, "project": str(PROJECT), "reason": "x"})
    assert answered == 409 and "couldn't be read" in answer["error"] and "Nothing was dismissed" in answer["error"]
    assert legacy.exists()
    _nothing_checked_or_dismissed()
    legacy.write_text("sessions = 3\n")  # fixed: imported by the dismissal's own request, then the check reads with it
    answered, answer, _ = post(served, "/api/suppress", {"finding_id": SHOWN.id, "project": str(PROJECT), "reason": "x"})
    assert answered == 200, answer
    assert not legacy.exists() and settings.load(connect()).sessions == 3


DEEP = finding(discriminator="only in sessions", title="Seen only in sessions")


class SessionsOnly(FakeAdapter):
    """Finds DEEP only in a reading that read sessions."""

    def checks(self):
        return [Check("demo-check", "seen in sessions", lambda s: [DEEP] if s.snapshot.sessions else [])]


def test_under_a_sessions_flag_a_dismissal_that_cant_find_the_finding_says_why_the_page_shows_it(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "ADAPTERS", {SessionsOnly.name: SessionsOnly})
    monkeypatch.setattr(cli, "_adapter", SessionsOnly)
    monkeypatch.setattr(SystemScheduler, "installed", lambda self: None)
    assert cli.main(["config", "set", "sessions", "0"]) == cli.EXIT_OK
    built = started(monkeypatch, "--sessions", "5")
    assert DEEP.id in {f["id"] for f in built.build({})[0]["check"]["findings"]}  # the page offers Dismiss on it
    with pytest.raises(Refused) as refused:
        built.control.suppress({"finding_id": DEEP.id, "project": str(PROJECT), "reason": "x"})
    assert refused.value.status == 404
    assert ("The page reads 5 recent sessions (agentlint ui --sessions 5), while a dismissal checks the project with "
            "your sessions setting (0)") in str(refused.value)
    assert "once the setting is 5" in str(refused.value) and load(connect()) == {}
    plain = started(monkeypatch)  # no flag: the page and the dismissal read alike, and the refusal says nothing more
    with pytest.raises(Refused) as refused:
        plain.control.suppress({"finding_id": "abcdef1234567890", "project": str(PROJECT), "reason": "x"})
    assert "sessions" not in str(refused.value)
    assert cli.main(["config", "set", "sessions", "5"]) == cli.EXIT_OK  # as the refusal says: then it is dismissed
    assert built.control.suppress({"finding_id": DEEP.id, "project": str(PROJECT), "reason": "x"})["suppression"]
