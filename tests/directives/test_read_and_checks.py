"""Reading the sources you select for directives — each section once, by its fingerprint — and the two checks on what
was stored: contradiction candidates and repeats, with the exact quotes and where they are."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

import pytest

from agentlint.checks import NotEvaluated, run_checks
from agentlint.checks.directives import CONFLICT, REPEAT
from agentlint.config.reading import DirectiveEvidence, Reading
from agentlint.directives import extract
from agentlint.harness import read_config
from agentlint.probes.generator import GenerationFailed, Reply
from agentlint.report.finding import Confidence, Severity
from agentlint.store import connect, reads
from agentlint.store import diagnostics as diagnostic_store
from tests.runner.fake_claude import fake_machine, write

AGENTS = """# Project

Project rules.

## Exploring

Never delegate to subagents unless the user asks for one.
Always read the notes before answering.
"""
HOOK_SAYS = "Always use the reviewer agent for every code review."


class ByText:
    """A generator answering each section by what its text holds; every call counted."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def generate(self, prompt, schema, model) -> Reply:
        with self.lock:
            self.calls.append(prompt)
        section = prompt.split("---\n", 2)[1]
        found = []
        if "Never delegate to subagents" in section:
            found.append({"polarity": "must_not", "thing": "agent:*", "quote": "Never delegate to subagents",
                          "when": "unless the user asks for one"})
        if "read the notes" in section:
            found.append({"polarity": "must", "thing": "tool:Read", "quote": "Always read the notes", "when": None})
        if HOOK_SAYS in section:
            found.append({"polarity": "must", "thing": "agent:reviewer", "quote": "Always use the reviewer agent",
                          "when": "for every code review"})
        if "Project rules." in section and "Exploring" not in section:
            raise GenerationFailed("the model timed out")
        if "mine body" in section:
            found.append({"polarity": "must", "thing": "skill:made-up", "quote": "mine body", "when": None})
        return Reply({"directives": found}, "fake-model", "{}")


@pytest.fixture
def machine(tmp_path: Path):
    adapter, project = fake_machine(tmp_path)
    write(project / "AGENTS.md", AGENTS)
    write(tmp_path / "real-home" / ".claude" / "CLAUDE.md", f"# Mine\n\n{HOOK_SAYS}\n")  # another source
    return adapter, project, read_config(adapter, project, sessions=0).snapshot


def read_sources(snapshot, ids, model, *, stop=None) -> str:
    """Plan a reading of these sources and run it here, as its worker would; its id."""
    from datetime import UTC, datetime

    read_id = f"read-{len(reads.listed(connect()))}"
    planned = extract.plan_read(connect(), snapshot, ids, read_id=read_id, harness="claude-code", model="haiku",
                                now=datetime.now(UTC))
    if planned:
        extract.run_read(connect, read_id, model, stop=stop)
    return read_id


def test_the_sources_you_select_are_read_once_each_section_by_its_fingerprint(machine) -> None:
    adapter, project, snapshot = machine
    statuses = {s.source.id: s for s in extract.statuses(connect(), snapshot)}
    agents = next(s for s in statuses.values() if s.source.provenance.file and s.source.provenance.file.name == "AGENTS.md")
    reviewer = next(s for s in statuses.values() if "reviewer" in s.name)
    assert agents.read == 0 and agents.sections >= 2 and agents.tokens > 0
    model = ByText()
    read_id = read_sources(snapshot, [agents.source.id, reviewer.source.id], model)
    found = reads.counts(connect(), read_id)
    assert sum(found.values()) == agents.sections + reviewer.sections and found["failed"] == 1  # the timed-out one
    failed = reads.sections(connect(), read_id, statuses=(reads.Section.FAILED,))
    assert failed[0].message == "the model timed out"
    assert all('copied exactly' in call for call in model.calls)
    again = {s.source.id: s for s in extract.statuses(connect(), snapshot)}
    assert again[agents.source.id].read == agents.sections - 1  # the failed one is still to read
    assert again[reviewer.source.id].read == reviewer.sections
    before = len(model.calls)
    read_sources(snapshot, [agents.source.id, reviewer.source.id], model)
    assert len(model.calls) - before == 1  # only the section that failed: the rest are read, by their fingerprint
    extract.run_read(connect, read_id, model)  # a resume of the first reading tries its failed section again, only it
    assert reads.counts(connect(), read_id)["failed"] == 1 and len(model.calls) - before == 2
    assert [d.kind for d in diagnostic_store.listed(connect(), kind=extract.FAILED)] == [extract.FAILED] * 3  # each try


def test_contradictions_and_repeats_quote_each_side_where_it_is(machine) -> None:
    adapter, project, snapshot = machine
    statuses = extract.statuses(connect(), snapshot)
    read_sources(snapshot, [s.source.id for s in statuses], ByText())
    found, read, total = extract.occurrences(connect(), snapshot)
    reading = Reading(snapshot, directives=DirectiveEvidence(tuple(found), read, total))
    results = run_checks(reading, [CONFLICT, REPEAT])
    (conflict,) = [f for f in results.findings if f.check == "conflicting-directives"]
    assert "CLAUDE.md" in conflict.title and conflict.title.endswith("says not to use agent:*")
    assert "says to use agent:reviewer; AGENTS.md" in conflict.title
    assert conflict.confidence is Confidence.SUGGESTIVE and conflict.severity is Severity.WARN  # AGENTS.md: every session
    assert '"Never delegate to subagents" — must not agent:* (when: unless the user asks for one)' in conflict.detail
    assert '"Always use the reviewer agent" — must agent:reviewer (when: for every code review)' in conflict.detail
    assert {loc.line for loc in conflict.locations} >= {7}  # AGENTS.md's line
    assert [f for f in results.findings if f.check == "repeated-directive"] == []  # Read said once in every session
    (rejected,) = diagnostic_store.listed(connect(), kind=extract.REJECTED)  # the skill nobody has: a diagnostic only
    assert rejected.data["rejected"][0]["reason"] == "UNKNOWN_THING"


def test_nothing_read_is_not_evaluated(machine) -> None:
    _, _, snapshot = machine
    results = run_checks(Reading(snapshot), [CONFLICT, REPEAT])
    assert set(results.not_evaluated) == {"conflicting-directives", "repeated-directive"}
    with pytest.raises(NotEvaluated):
        CONFLICT.run(replace(Reading(snapshot), directives=DirectiveEvidence((), 0, 10)))


def test_a_stop_ends_the_reading_after_the_calls_under_way(machine) -> None:
    _, _, snapshot = machine
    statuses = extract.statuses(connect(), snapshot)
    stop = threading.Event()
    stop.set()
    read_id = read_sources(snapshot, [s.source.id for s in statuses], ByText(), stop=stop)
    found = reads.counts(connect(), read_id)
    assert found["read"] == 0 and found["pending"] > 0  # nothing started: all still to read on a resume


def test_raising_the_reader_version_makes_every_read_section_stale(machine, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, snapshot = machine
    statuses = extract.statuses(connect(), snapshot)
    read_sources(snapshot, [s.source.id for s in statuses], ByText())
    assert all(s.stale == 0 for s in extract.statuses(connect(), snapshot))
    monkeypatch.setattr(extract, "READER_VERSION", extract.READER_VERSION + 1)  # the version raised by hand
    after = extract.statuses(connect(), snapshot)
    assert all(s.read == 0 for s in after) and sum(s.stale for s in after) > 0  # read before: stale now
    assert extract.occurrences(connect(), snapshot)[1] == 0  # no check uses a stale directive
