"""Behaviour findings from tests (checks/tested.py), on the evidence the reading takes from the store (runner/tested.py):
each target's latest decided test, while its text is the text its situations were written for."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentlint.adapters.claude_code.terms import action_terms
from agentlint.checks import NotEvaluated, run_checks
from agentlint.checks.tested import CONSISTENCY, EXPECTATION
from agentlint.config.reading import Reading
from agentlint.harness import read_config
from agentlint.probes.generate import probe_id, target_hash
from agentlint.rank.plan import targets
from agentlint.report.finding import Confidence, FindingKind, Severity
from agentlint.runner import tested
from agentlint.runner.plan import Planned, two_step_plan
from agentlint.store import JobStatus, connect, jobs, probes, transaction
from tests.runner.fake_claude import fake_machine, write

AT = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)


def trace(*commands: str, read: bool = False) -> str:
    actions = [{"seq": i, "tool": "Bash", "input": {"command": c}, "message_id": f"m{i}", "call_id": f"c{i}",
                "uses": ["tools/Bash"], "answered": True, "error": False, "denied": False} for i, c in enumerate(commands)]
    if read:
        actions.append({"seq": len(actions), "tool": "Read", "input": {"file_path": "/n"}, "message_id": "r",
                        "call_id": "r", "uses": ["tools/Read"], "answered": True, "error": False, "denied": False})
    return json.dumps({"session_id": "s", "harness_version": "9.9.9", "models": ["model-x"], "headless": True,
                       "actions": actions, "fired": []})


@pytest.fixture
def machine(tmp_path: Path):
    adapter, project = fake_machine(tmp_path)
    write(project / "AGENTS.md", "# Project\nProject rules.\n\n## Metrics\n\nFetch metrics with acme before answering.\n")
    snapshot = read_config(adapter, project, sessions=0).snapshot
    target = next(t for t in targets(snapshot) if t.id.endswith("#metrics") or "Metrics" in t.id)
    return adapter, project, snapshot, target


def situations(target) -> list[tuple[str, str, dict]]:
    current = target_hash(target)
    texts = (("Find the revenue metrics.", "program:acme"), ("Summarise last week's metrics.", "program:acme"))
    return [(probe_id(current, text), text, {"kind": "targets", "uses": thing}) for text, thing in texts]


def make_test(db, adapter, project, target, runs: dict[str, str], *, job_id: str = "2026-10-07T090000Z-aaaaaa",
              created: datetime = AT, complete: bool = True) -> None:
    given = situations(target)
    probes.replace(db, target_hash(target), [probes.Probe(pid, target.id, target_hash(target), text, json.dumps(m),
                                                          "haiku", 3, AT) for pid, text, m in given])
    plan = two_step_plan([Planned(target.id, tuple(given), True)], runs=3, runs_per_side=3)
    with transaction(db):
        jobs.create(db, job_id=job_id, project=os.path.realpath(project), fingerprint="fp", harness_version="9.9.9",
                    model="model-x", run_ceiling=plan.size, timeout_s=300, plan=plan.to_json(), now=created,
                    harness=adapter.name)
        for n, (key, text) in enumerate(runs.items(), 1):
            at = created + timedelta(minutes=n)
            jobs.launch(db, job_id, key, at - timedelta(seconds=50))
            jobs.record(db, jobs.Run(job_id, key, "s", "ok", text, "9.9.9", "model-x", 50_000, None,
                                     at - timedelta(seconds=50), at), complete=False)
    if complete:
        jobs.close(db, job_id, JobStatus.COMPLETE)


META = trace("acme data.catalog fetch x")
# cmp-0001 / cmp-0003: situation 1, with and without; cmp-0002 / cmp-0004: situation 2.
WORKS = {**{f"cmp-0001:baseline:{n}": META for n in range(3)}, **{f"cmp-0003:without:{n}": trace("ls") for n in range(3)},
         **{f"cmp-0002:baseline:{n}": META for n in range(3)}, **{f"cmp-0004:without:{n}": trace("ls") for n in range(3)}}
SHORT = {**{f"cmp-0001:baseline:{n}": trace("ls") for n in range(3)},  # never ran acme: not followed
         "cmp-0002:baseline:0": META, "cmp-0002:baseline:1": META, "cmp-0002:baseline:2": trace("ls", read=True),
         "cmp-0004:without:0": META, "cmp-0004:without:1": trace("ls"), "cmp-0004:without:2": trace("ls")}


def evidence(db, adapter, snapshot):
    return tested.load(db, snapshot, adapter.name, action_terms)


def test_a_target_that_fell_short_is_a_warning_naming_each_situation_and_one_that_worked_is_no_finding(machine) -> None:
    adapter, project, snapshot, target = machine
    db = connect()
    make_test(db, adapter, project, target, SHORT)
    found = evidence(db, adapter, snapshot)
    (result,) = found.targets
    assert (result.target, result.verdict, round(result.strength, 3)) == (target.id, "not followed", 0.167)
    assert [(s.verdict, s.with_met, s.without_met, s.consistent) for s in result.situations] == [
        ("not followed", 0, None, True), ("partial", 2, 1, False)]
    results = run_checks(Reading(snapshot, tests=found), [EXPECTATION, CONSISTENCY])
    short, differed = results.findings
    assert (short.check, short.kind, short.confidence, short.severity) == (
        "tested-expectation", FindingKind.BEHAVIOUR, Confidence.MEASURED, Severity.WARN)
    assert short.title.endswith(": the agent didn't do what it is for") and short.discriminator == target.id
    assert '"Find the revenue metrics." expects program:acme: 0 of 3 runs with it, so never run without (what they ' \
           'did instead, by runs: shell:ls in 3) — not followed' in short.detail
    assert '2 of 3 runs with it, 1 of 3 without, strength +0.33 — partial' in short.detail
    assert short.locations[0].source_id == target.source.id and short.locations[0].line is not None
    assert differed.check == "tested-consistency" and "differed in 1 of 2 tested situations" in differed.title

    db2 = connect()
    make_test(db2, adapter, project, target, WORKS, job_id="2026-10-07T100000Z-bbbbbb", created=AT + timedelta(hours=1))
    newest = evidence(db2, adapter, snapshot)
    assert [t.verdict for t in newest.targets] == ["works"]  # the newest decided test is the one read
    assert run_checks(Reading(snapshot, tests=newest), [EXPECTATION, CONSISTENCY]).findings == []


def test_a_test_still_running_is_passed_over_for_the_older_decided_one(machine) -> None:
    adapter, project, snapshot, target = machine
    db = connect()
    make_test(db, adapter, project, target, SHORT)
    make_test(db, adapter, project, target, {"cmp-0001:baseline:0": META}, job_id="2026-10-07T100000Z-bbbbbb",
              created=AT + timedelta(hours=1), complete=False)
    assert [t.job_id for t in evidence(db, adapter, snapshot).targets] == ["2026-10-07T090000Z-aaaaaa"]


def test_a_target_whose_text_changed_since_its_test_has_none(machine, tmp_path: Path) -> None:
    adapter, project, snapshot, target = machine
    db = connect()
    make_test(db, adapter, project, target, SHORT)
    write(project / "AGENTS.md", "# Project\nProject rules.\n\n## Metrics\n\nFetch metrics with acme, always.\n")
    edited = read_config(adapter, project, sessions=0).snapshot
    assert evidence(db, adapter, edited).targets == ()


def test_without_test_results_the_checks_say_so_and_close_nothing(machine) -> None:
    _, _, snapshot, _ = machine
    results = run_checks(Reading(snapshot), [EXPECTATION, CONSISTENCY])
    assert results.findings == [] and set(results.not_evaluated) == {"tested-expectation", "tested-consistency"}
    assert tested.load(connect(), snapshot, "x", None) is None  # no action terms: nothing is read
    with pytest.raises(NotEvaluated):
        EXPECTATION.run(replace(Reading(snapshot), tests=None))


def test_a_situation_regenerated_away_still_counts_while_the_target_text_is_the_same(machine) -> None:
    adapter, project, snapshot, target = machine
    db = connect()
    make_test(db, adapter, project, target, SHORT)
    with transaction(db):
        probes.replace(db, target_hash(target), [])  # Write again: the tested situations are gone from the store
    assert [t.verdict for t in evidence(db, adapter, snapshot).targets] == ["not followed"]  # its id says the text
