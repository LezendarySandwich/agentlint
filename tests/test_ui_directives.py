"""The UI's directives endpoints (ui/testing.py): every source you can have read, with how much of it is read; a reading
of the sources you choose, as a job in a worker process of its own — planned at once from the last reading, each
section once — listed with the other jobs, opened to its sections, stopped and resumed; and the refusals."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from agentlint.ui.server import Refused
from tests.runner.fake_claude import fake_machine, write
from tests.test_ui_testing import make_testing

SCRIPT = Path(__file__).parent / "directives" / "read_script.py"


def wait_for(check, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


@pytest.fixture
def ui(tmp_path: Path):
    adapter, project = fake_machine(tmp_path)
    sections = "".join(f"\n## Part {n}\n\nRead the notes, part {n}.\n" for n in range(7))  # more than 4 at once
    write(project / "AGENTS.md", "# Project\n\nRead the notes first.\n" + sections)
    environ = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}  # as from a plain terminal
    testing = make_testing(adapter, project, None, environ,
                           read_command=lambda read_id: [sys.executable, str(SCRIPT), read_id])
    return testing, environ


def agents_md(listed: dict) -> dict:
    return next(s for s in listed["sources"] if "AGENTS.md" in s["name"])


def done(ui, read_id: str) -> dict:
    return wait_for(lambda: (lambda d: d if not d["runner"]["alive"] and d["status"] != "open" else None)(
        ui.read_detail(read_id)))


def test_a_reading_is_a_job_planned_at_once_its_sections_listed_and_each_read_once(ui) -> None:
    testing, _ = ui
    listed = testing.directives({})
    agents = agents_md(listed)
    assert (agents["state"], agents["read"], listed["reading"]) == ("not read", 0, None)
    started = testing.read_directives({"source_ids": [agents["id"]]})["read"]
    assert started["kind"] == "reading" and started["sections"]["planned"] == agents["sections"]
    finished = done(testing, started["read_id"])
    assert finished["standing"] == "complete" and finished["sections"]["read"] == agents["sections"]
    assert [(s["status"], s["directives"]) for s in finished["sections_list"]][0] == ("read", 1)
    assert "complete:" in finished["log_tail"][-1]
    assert [r["read_id"] for r in testing.reads({})["reads"]] == [started["read_id"]]
    again = agents_md(testing.directives({}))
    assert (again["state"], again["tokens"]) == ("read", 0)
    assert testing.read_directives({"source_ids": [agents["id"]]}) == {"read": None, "project": listed["project"]}


def test_a_reading_is_blocked_while_one_runs_and_stops_and_resumes(ui) -> None:
    testing, environ = ui
    environ["FAKE_READ_SLEEP"] = "1.5"
    agents = agents_md(testing.directives({}))
    started = testing.read_directives({"source_ids": [agents["id"]]})["read"]
    wait_for(lambda: testing.read_detail(started["read_id"])["runner"]["alive"])
    with pytest.raises(Refused) as caught:
        testing.read_directives({"source_ids": [agents["id"]]})
    assert caught.value.status == 409 and started["read_id"] in str(caught.value)  # the page links to it
    assert testing.directives({})["reading"]["read_id"] == started["read_id"]
    wait_for(lambda: testing.read_detail(started["read_id"])["sections"]["reading"])  # a call under way
    testing.stop_read(started["read_id"], {})
    stopped = done(testing, started["read_id"])
    assert stopped["standing"] == "stopped: Resume reads the rest" and stopped["sections"]["reading"] == 0
    assert stopped["sections"]["pending"] > 0 and "stopped:" in stopped["log_tail"][-1]  # the calls under way finished
    environ["FAKE_READ_SLEEP"] = "0"
    testing.resume_read(started["read_id"], {})
    resumed = done(testing, started["read_id"])
    assert resumed["standing"] == "complete" and resumed["sections"]["read"] == resumed["sections"]["planned"]
    with pytest.raises(Refused) as caught:
        testing.resume_read(started["read_id"], {})
    assert caught.value.status == 409


def test_a_reading_is_refused_what_it_cant_read(ui) -> None:
    testing, _ = ui
    for body in ({}, {"source_ids": []}, {"source_ids": "all"}, {"source_ids": ["x"], "extra": 1}):
        with pytest.raises(Refused) as caught:
            testing.read_directives(body)
        assert caught.value.status == 400
    testing.directives({})
    with pytest.raises(Refused) as caught:
        testing.read_directives({"source_ids": ["no-such-source"]})
    assert caught.value.status == 400 and "no source no-such-source" in str(caught.value)
    for call in (testing.read_detail, lambda r: testing.stop_read(r, {}), lambda r: testing.resume_read(r, {})):
        with pytest.raises(Refused) as caught:
            call("2026-01-01T000000Z-aaaaaa")
        assert caught.value.status == 404
