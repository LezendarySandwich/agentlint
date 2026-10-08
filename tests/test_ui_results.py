"""Tests › Results from the server's side (ui/testing.py): a job's results — each situation's screen, the target it was
written for by name, what matters in it, and each run with its actions on one line — and every job's summary in
GET /api/jobs: where it stands, its targets, its situations and its screens counted by state, fast for many jobs.
Built on jobs written straight into the database: nothing here runs a harness or reads a project."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentlint.adapters.claude_code.terms import action_terms
from agentlint.runner.execute import run_dir_name, runs_root
from agentlint.runner.plan import Plan, Planned, stability_plan, two_step_plan
from agentlint.runner.worker import KeepAwake
from agentlint.store import JobStatus, connect, jobs, probes, transaction
from agentlint.store import home as store_home
from agentlint.ui.testing import ACTIONS_SHOWN, WHAT_MAX, Testing, rendered, run_places, situation_results, standing

AT = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
PROJECT = "/work/project"
JOB = "2026-10-05T090000Z-aaaaaa"


class Plain:
    """A harness with nothing to read: GET /api/jobs and a job's results need only the database."""

    name, label = "plain", "Plain"

    def nested(self, environ: Mapping[str, str]) -> str | None:
        return None


def plain_testing(parts: Callable[[str], object] = lambda harness: None) -> Testing:
    def unread(adapter: object, path: Path) -> object:
        raise AssertionError("nothing here reads a project")

    return Testing(connect, scope=lambda params: (Plain(), Path(PROJECT)), read=unread, parts=parts,  # type: ignore[arg-type]
                   command=lambda job_id: ["true"], environ={}, keep_awake=KeepAwake(platform="linux"))


def action(seq: int, tool: str, given: dict, *uses: str) -> dict:
    return {"seq": seq, "tool": tool, "input": given, "message_id": f"m{seq}", "call_id": f"c{seq}",
            "uses": [f"tools/{tool}", *uses], "answered": True, "error": False, "denied": False}


def trace(*actions: dict, version: str = "2.1.289") -> str:
    return json.dumps({"session_id": "s", "harness_version": version, "models": ["model-x"], "headless": True,
                       "actions": list(actions), "fired": []})


def bash(command: str = "ls") -> str:
    return trace(action(0, "Bash", {"command": command}))


def record(db, job_id: str, key: str, text: str, *, at: datetime = AT, outcome: str = "ok", ms: int = 53_000,
           complete: bool = False) -> None:
    """One run launched and recorded, as the engine writes it, finishing at ``at``."""
    jobs.launch(db, job_id, key, at - timedelta(milliseconds=ms))
    jobs.record(db, jobs.Run(job_id, key, "s", outcome, text, "2.1.289", "model-x", ms, None,
                             at - timedelta(milliseconds=ms), at), complete=complete)


def make_job(db, job_id: str, plan: Plan, runs: Mapping[str, str], *, created: datetime = AT) -> jobs.Job:
    """A job of ``plan`` with ``runs`` (run key -> trace) recorded a minute apart, in one transaction."""
    with transaction(db):
        made = jobs.create(db, job_id=job_id, project=PROJECT, fingerprint="fp", harness_version="2.1.289",
                           model="model-x", run_ceiling=plan.size, timeout_s=300, plan=plan.to_json(), now=created,
                           harness="plain")
        for n, (key, text) in enumerate(runs.items(), 1):
            record(db, job_id, key, text, at=created + timedelta(minutes=n))
    return made


FIVE = stability_plan([("p1", "Tidy the imports.", "rules#a"), ("p2", "Rename a function.", "rules#a"),
                       ("p3", "Explain the rules.", "rules#b"), ("p4", "Start a session.", "rules#b"),
                       ("p5", "Write the notes.", "rules#b")], runs=3)


def five_runs() -> dict[str, str]:
    """One situation of each screen state: agreed, disagreed, no action, incomplete (two runs), mixed environment."""
    return {
        **{f"cmp-0001:baseline:{n}": bash() for n in range(3)},
        "cmp-0002:baseline:0": bash(), "cmp-0002:baseline:1": trace(action(0, "Read", {"file_path": "/a"})),
        "cmp-0002:baseline:2": bash(),
        **{f"cmp-0003:baseline:{n}": trace() for n in range(3)},
        **{f"cmp-0004:baseline:{n}": bash() for n in range(2)},
        "cmp-0005:baseline:0": bash(), "cmp-0005:baseline:1": trace(action(0, "Bash", {"command": "ls"}),
                                                                    version="2.1.290"),
        "cmp-0005:baseline:2": bash(),
    }


def test_every_job_says_where_it_stands_its_targets_by_name_its_situations_and_its_screens() -> None:
    db = connect()
    make_job(db, JOB, FIVE, five_runs())
    testing = plain_testing()
    answer = testing.jobs({})
    assert answer["project"] == PROJECT  # the project the request names, as jobs record theirs: the page keeps its jobs
    (entry,) = answer["jobs"]
    assert (entry["standing"], entry["situations"], entry["runs"]) == (
        "paused: Resume goes on where it stopped", 5, {"recorded": 14, "planned": 15})
    assert entry["targets"] == [{"id": "rules#a", "name": "rules#a"}, {"id": "rules#b", "name": "rules#b"}]  # no reading
    assert entry["screens"] == {"agreed": 1, "disagreed": 1, "no action": 1, "incomplete": 1, "mixed environment": 1}
    assert list(entry["screens"]) == ["agreed", "disagreed", "no action", "incomplete", "mixed environment"]
    with testing._lock:  # what the last reading of the project named its targets (GET /api/plan's names)
        testing._names[("plain", PROJECT)] = {"rules#a": 'RULES.md § "A"'}
    assert testing.jobs({})["jobs"][0]["targets"] == [{"id": "rules#a", "name": 'RULES.md § "A"'},
                                                      {"id": "rules#b", "name": "rules#b"}]  # no name held: its id
    results = testing.results(JOB)
    assert [(s["about"], s["name"]) for s in results["situations"]][1:3] == [
        ("rules#a", 'RULES.md § "A"'), ("rules#b", "rules#b")]
    assert (results["project"], results["created"], results["status"]) == (PROJECT, AT.isoformat(), "open")
    jobs.close(db, JOB, JobStatus.SALVAGED)
    assert testing.jobs({})["jobs"][0]["standing"] == "closed: what completed is kept"


def test_where_a_job_stands_is_said_in_words() -> None:
    assert [standing(status, False, False) for status in JobStatus] == [
        "paused: Resume goes on where it stopped", "complete", "closed: what completed is kept",
        "stopped at its run ceiling"]
    assert (standing(JobStatus.OPEN, True, False), standing(JobStatus.OPEN, True, True)) == ("running", "stopping")


def test_a_jobs_screens_are_counted_again_only_once_it_records_another_run(monkeypatch: pytest.MonkeyPatch) -> None:
    db = connect()
    make_job(db, JOB, FIVE, five_runs())
    read = []
    every_run = jobs.runs
    monkeypatch.setattr(jobs, "runs", lambda db, job_id: read.append(job_id) or every_run(db, job_id))
    testing = plain_testing()
    first = testing.jobs({})["jobs"][0]["screens"]
    assert read == [JOB]
    assert testing.jobs({})["jobs"][0]["screens"] == first and read == [JOB]  # no trace read again
    record(db, JOB, "cmp-0004:baseline:2", bash(), complete=True)  # the incomplete one gets its third run
    again = testing.jobs({})["jobs"][0]
    assert read == [JOB, JOB] and again["screens"]["incomplete"] == 0 and again["screens"]["agreed"] == 2
    assert again["standing"] == "complete"
    jobs.delete_all(db)  # forgotten: its screens go too
    assert testing.jobs({})["jobs"] == [] and testing._screens == {}


def test_fifty_jobs_of_twenty_runs_are_listed_in_under_200_ms() -> None:
    """The history lists every test of the project (Tests › Results), asked again every 3 s while one runs."""
    db = connect()
    plan = stability_plan([(f"p{i}", f"Situation {i}.", f"rules#{i % 2}") for i in range(5)], runs=4)
    with transaction(db):
        for j in range(50):
            job_id = f"2026-10-05T09{j:04d}Z-aaaaaa"
            runs = {}
            for k, planned in enumerate(plan.runs()):
                work = runs_root() / job_id / run_dir_name(planned.key) / "work"
                runs[planned.key] = trace(*(action(i, tool, {"command": f"cd {work} && grep -rn TODO . | head -{i}",
                                                             "description": "Find every TODO in the project"})
                                            for i, tool in enumerate(["Bash", "Read", "Grep"] * (1 + k % 3))))
            make_job(db, job_id, plan, runs, created=AT + timedelta(minutes=j))
    testing = plain_testing()
    started = time.perf_counter()
    first = testing.jobs({})
    cold = time.perf_counter() - started
    assert len(first["jobs"]) == 50 and all(sum(job["screens"].values()) == 5 for job in first["jobs"])
    started = time.perf_counter()
    testing.jobs({})
    warm = time.perf_counter() - started
    assert warm < 0.2, f"GET /api/jobs took {warm * 1000:.0f} ms for 50 jobs of 20 runs (cold: {cold * 1000:.0f} ms)"


def test_each_run_lists_its_actions_on_one_line_with_the_runs_own_directories_shown_short() -> None:
    place = runs_root() / JOB / run_dir_name("cmp-0001:baseline:0")
    work, home = place / "work", place / "home"
    shown = [
        action(0, "Bash", {"command": f"cd {work} &&\n  ls -la   {work}/src", "description": "List the files"}),
        action(1, "Read", {"file_path": f"{home}/.agent/RULES.md", "limit": 20, "offset": 0}),
        action(2, "Skill", {"skill": "tdd"}, "skills/tdd"),
        action(3, "Agent", {"description": "Review it", "prompt": "Look at\tthe notes", "subagent_type": "reviewer"},
               "agents/reviewer"),
        action(4, "Write", {"file_path": f"{work}/notes.md", "content": "word " * 100}),
        action(5, "TodoWrite", {"todos": [{"content": "One", "status": "pending"}, {"content": "Two", "status": "done"}]}),
        action(6, "Read", {"file_path": "/etc/hosts"}),  # outside the run: as it is
        action(7, "Glob", {"pattern": "*.md", "path": str(work)}),
    ]
    many = [action(8 + i, "Bash", {"command": f"echo {i}"}) for i in range(ACTIONS_SHOWN)]
    plan = stability_plan([("p1", "Set up the notes.", "rules#style")], runs=3)
    rows = [jobs.Run(JOB, f"cmp-0001:baseline:{n}", "s", "ok", trace(*shown, *many) if n == 0 else bash(), "2.1.289",
                     "model-x", 41_234, None, AT, AT) for n in range(3)]
    (found,) = situation_results(plan, rows, "fp", names={"rules#style": 'RULES.md § "Style"'},
                                 matters={"p1": "which_tools"})
    assert (found["name"], found["matters"]) == ('RULES.md § "Style"', "which_tools")
    assert found["matters_meaning"].startswith("which tool, skill or agent")
    first = found["runs"][0]
    assert (first["run"], first["outcome"], first["seconds"]) == (0, "ok", 41.2)
    assert len(first["actions"]) == ACTIONS_SHOWN and first["more_actions"] == len(shown)  # 60 listed, the rest counted
    whats = [a["what"] for a in first["actions"][:8]]
    assert whats[:4] == ["cd . && ls -la ./src · List the files", "~/.agent/RULES.md", "tdd",
                         "Review it · Look at the notes · reviewer"]
    assert whats[4].startswith("./notes.md · word word") and len(whats[4]) == WHAT_MAX and whats[4].endswith("…")
    assert whats[5:] == ["One · pending · Two · done", "/etc/hosts", "*.md · ."]
    assert [a["uses"] for a in first["actions"][:4]] == [{}, {}, {"skills": ["tdd"]}, {"agents": ["reviewer"]}]
    assert [a["tool"] for a in first["actions"][:3]] == ["Bash", "Read", "Skill"]
    assert situation_results(plan, rows, "fp")[0]["name"] == "rules#style"  # no name known: its id


def test_a_path_is_shown_short_only_where_it_is_the_runs_own_directory() -> None:
    places = run_places(JOB, "cmp-0002:baseline:1")
    place = runs_root() / JOB / "cmp-0002.baseline.1"
    other = runs_root() / JOB / "cmp-0002.baseline.2"
    def cut(text: str) -> str:
        return text if len(text) <= WHAT_MAX else text[:WHAT_MAX - 1] + "…"

    assert rendered({"a": f"{place}/work", "b": f"{place}/home/x", "c": "  "}, places) == ". · ~/x"
    assert rendered({"path": f"{place}/workshop"}, places) == cut(f"{place}/workshop")  # not its directory: as it is
    assert rendered({"path": f"{other}/work/y"}, places) == cut(f"{other}/work/y")  # another run's: as it is
    assert rendered({"n": 3, "t": True, "e": "", "s": "  "}) == ""  # no string worth a word: nothing


def test_results_say_what_matters_from_the_store_and_none_once_it_is_regenerated_away() -> None:
    db = connect()
    probes.replace(db, "sha256:t", [probes.Probe("p1", "rules#a", "sha256:t", "Tidy the imports.", "which_tools",
                                                 "m", 2, AT)])
    make_job(db, JOB, FIVE, five_runs())
    found = plain_testing().results(JOB)["situations"]
    assert [(s["situation"], s["matters"]) for s in found[:2]] == [("p1", "which_tools"), ("p2", None)]
    assert found[1]["matters_meaning"] is None
    assert [s["state"] for s in found] == ["agreed", "disagreed", "no action", "incomplete", "mixed environment"]
    assert found[3]["runs"][1]["actions"] == [{"tool": "Bash", "what": "ls", "uses": {}}]


# The fixture the UI is tested on (ui/src/fixtures/results.ts): GET /api/jobs and a job's results as this code writes
# them, each a literal declared with its contract.ts type, so the UI's build fails on a field Python writes that
# contract.ts doesn't describe, or one contract.ts promises that Python doesn't write. (A declared type, not
# `satisfies`: the literal's own type would make `{}` and `{skills: [...]}` one union no Record accepts.) After a
# deliberate change: AGENTLINT_WRITE_GOLDEN=1 uv run pytest tests/test_ui_results.py, then npx vitest run && npm run
# build in ui/.

GOLDEN = Path(__file__).parents[1] / "ui" / "src" / "fixtures" / "results.ts"
DONE, PAUSED, BOTH = "2026-10-05T090000Z-aaaaaa", "2026-10-05T100000Z-bbbbbb", "2026-10-05T110000Z-cccccc"
EXPECT = "2026-10-05T120000Z-dddddd"
# Both steps (D9): p1's runs agreed and removing its target changed them (the agent stopped running acme); p2's runs
# disagreed, so its with/without comparison was skipped; p3's agreed and removing its target changed nothing.
BOTH_PLAN = two_step_plan([Planned("rules#a", (("p1", "Find the metrics."), ("p2", "Plan the pipeline.")), True),
                           Planned("rules#b", (("p3", "Start a session."),), True)], runs=3, runs_per_side=5)
# The expectation's strength (2026-10-07): each situation expects a thing. e1's runs ran acme every time with its target
# and never without (works); e2's never invoked the skill it expects (not followed: never run without); e3's ran pwd in
# 2 of 3 with — the third read a file instead: inconsistent — and 1 of 3 without (partial); e4's read the notes every
# run, with and without (adds nothing).
EXPECTED = {"e1": {"kind": "targets", "uses": "program:acme"}, "e2": {"kind": "tool_present", "uses": "skill:tdd"},
            "e3": {"kind": "targets", "uses": "program:pwd"}, "e4": {"kind": "tool_present", "uses": "tool:Read"}}
EXPECT_PLAN = two_step_plan([Planned("rules#a", (("e1", "Find the metrics for the revenue table.", EXPECTED["e1"]),
                                                 ("e2", "Write the parser, test first.", EXPECTED["e2"])), True),
                             Planned("rules#b", (("e3", "Start in a new repository.", EXPECTED["e3"]),
                                                 ("e4", "Answer from the notes.", EXPECTED["e4"])), True)],
                            runs=3, runs_per_side=3)
HOME_SHOWN = "/Users/u/.agentlint"  # agentlint's home as the fixture shows it; a test's own is a throwaway directory


def golden_runs() -> dict[str, str]:
    """The complete job's runs: five_runs, its agreed situation's runs acting the way real runs do — a shell command
    in the run's own directory, a skill, a delegation to an agent, a long write cut, a file in the run's home, and one
    run with more actions than are listed — and its incomplete one's third run a timeout with no readable trace."""
    runs = five_runs()

    def acting(n: int) -> str:
        place = runs_root() / DONE / run_dir_name(f"cmp-0001:baseline:{n}")
        work, home = place / "work", place / "home"
        shown = [action(0, "Bash", {"command": f"cd {work} && ls -la", "description": "List the files"}),
                 action(1, "Read", {"file_path": f"{home}/.agent/RULES.md"}),
                 action(2, "Skill", {"skill": "tdd"}, "skills/tdd"),
                 action(3, "Agent", {"description": "Review the notes", "prompt": "Read notes.md and say what is "
                                     "missing.", "subagent_type": "reviewer"}, "agents/reviewer"),
                 action(4, "Write", {"file_path": f"{work}/notes.md", "content": "# Notes\n\n" + "A line of notes. " * 20})]
        more = [action(5 + i, "Bash", {"command": f"wc -l {work}/notes.md"}) for i in range(ACTIONS_SHOWN - 4)]
        return trace(*shown, *(more if n == 2 else []))

    runs.update({f"cmp-0001:baseline:{n}": acting(n) for n in range(3)})
    runs["cmp-0004:baseline:2"] = "null"  # a timeout before the harness wrote anything
    return runs


def golden() -> str:
    db = connect()
    probes.replace(db, "sha256:a", [probes.Probe("p1", "rules#a", "sha256:a", "Tidy the imports.", "which_tools",
                                                 "haiku", 2, AT),
                                    probes.Probe("p2", "rules#a", "sha256:a", "Rename a function.", "targets", "haiku",
                                                 2, AT)])
    make_job(db, DONE, FIVE, {})
    every = list(golden_runs().items())
    with transaction(db):
        for n, (key, text) in enumerate(every):
            record(db, DONE, key, text, at=AT + timedelta(minutes=1 + n), outcome="timeout" if text == "null" else "ok",
                   ms=300_000 if text == "null" else 41_234 + 1_000 * n, complete=n == len(every) - 1)
    two = stability_plan([("p1", "Tidy the imports.", "rules#a"), ("p2", "Rename a function.", "rules#a")], runs=3)
    make_job(db, PAUSED, two, {"cmp-0001:baseline:0": bash(), "cmp-0001:baseline:1": bash("ls -a")},
             created=AT + timedelta(hours=1))
    meta = bash("ls; acme data.catalog fetch --table=events | head -40")
    both = [*((f"cmp-0001:baseline:{n}", meta) for n in range(3)),
            ("cmp-0002:baseline:0", bash()), ("cmp-0002:baseline:1", trace(action(0, "Read", {"file_path": "/a"}))),
            ("cmp-0002:baseline:2", bash()),
            *((f"cmp-0003:{side}:{n}", meta if side == "baseline" else bash("ls -la")) for n in range(5)
              for side in ("baseline", "without")),
            *((f"cmp-0005:baseline:{n}", bash("pwd")) for n in range(3)),
            *((f"cmp-0006:{side}:{n}", bash("pwd")) for n in range(5) for side in ("baseline", "without"))]
    make_job(db, BOTH, BOTH_PLAN, {}, created=AT + timedelta(hours=2))
    with transaction(db):
        for n, (key, text) in enumerate(both):
            record(db, BOTH, key, text, at=AT + timedelta(hours=2, minutes=1 + n), ms=50_000 + 1_000 * n,
                   complete=n == len(both) - 1)
    notes = trace(action(0, "Read", {"file_path": "/notes.md"}))
    probes.replace(db, "sha256:e", [probes.Probe(situation, "rules#a" if situation < "e3" else "rules#b", "sha256:e",
                                                 text, json.dumps(EXPECTED[situation]), "haiku", 3, AT)
                                    for situation, text in (("e1", "Find the metrics for the revenue table."),
                                                            ("e2", "Write the parser, test first."),
                                                            ("e3", "Start in a new repository."),
                                                            ("e4", "Answer from the notes."))])
    expect = [*((f"cmp-0001:baseline:{n}", meta) for n in range(3)), *((f"cmp-0003:without:{n}", bash("ls -la")) for n in range(3)),
              *((f"cmp-0002:baseline:{n}", bash()) for n in range(3)),
              ("cmp-0005:baseline:0", bash("pwd")), ("cmp-0005:baseline:1", bash("pwd")), ("cmp-0005:baseline:2", notes),
              *((f"cmp-0006:baseline:{n}", notes) for n in range(3)),
              ("cmp-0007:without:0", bash("pwd")), ("cmp-0007:without:1", bash()), ("cmp-0007:without:2", bash()),
              *((f"cmp-0008:without:{n}", notes) for n in range(3))]
    make_job(db, EXPECT, EXPECT_PLAN, {}, created=AT + timedelta(hours=3))
    with transaction(db):
        for n, (key, text) in enumerate(expect):
            record(db, EXPECT, key, text, at=AT + timedelta(hours=3, minutes=1 + n), ms=50_000 + 1_000 * n,
                   complete=n == len(expect) - 1)
    testing = plain_testing(lambda harness: SimpleNamespace(adapter=SimpleNamespace(action_terms=action_terms)))
    with testing._lock:  # what the last reading of the project named its targets (GET /api/plan's names)
        testing._names[("plain", PROJECT)] = {"rules#a": 'RULES.md § "Style"', "rules#b": "hook: SessionStart · one"}
    listed, results, effects = testing.jobs({}), testing.results(DONE), testing.results(BOTH)
    strengths = testing.results(EXPECT)
    assert [job["standing"] for job in listed["jobs"]] == ["complete", "complete",
                                                           "paused: Resume goes on where it stopped", "complete"]
    assert [(s["effect"]["verdict"], s["effect"]["with"]["met"], (s["effect"]["without"] or {}).get("met"))
            for s in strengths["situations"]] == [("works", 3, 0), ("not followed", 0, None), ("partial", 2, 1),
                                                  ("adds nothing", 3, 3)]
    assert [(t["effect"]["verdict"], round(t["effect"]["strength"], 3)) for t in strengths["targets"]] == [
        ("not followed", 0.5), ("adds nothing", 0.167)]  # the weakest leads; mean strength, not followed counted 0
    assert listed["jobs"][0]["runs"] == {"recorded": 21, "planned": 21}  # e2 was never run without
    assert [(s["effect"] or {}).get("measured", (s["effect"] or {}).get("state")) for s in effects["situations"]] == [
        "changes", "skipped", "no-change"]
    assert listed["jobs"][1]["runs"] == {"recorded": 29, "planned": 29}  # the skipped comparison's ten left out
    assert {s["state"] for s in results["situations"]} == {"agreed", "disagreed", "no action", "incomplete",
                                                          "mixed environment"}
    assert results["situations"][0]["runs"][2]["more_actions"] > 0 and results["situations"][3]["unscreened"]

    def literal(value: object) -> str:
        text = json.dumps(value, indent=2, ensure_ascii=True)
        for home in dict.fromkeys([os.path.realpath(store_home()), str(store_home())]):
            text = text.replace(home, HOME_SHOWN)
        return text

    return (
        "// Written by tests/test_ui_results.py from ui/testing.py: what Python really writes. Don't edit it by hand;\n"
        "// AGENTLINT_WRITE_GOLDEN=1 uv run pytest tests/test_ui_results.py rewrites it.\n"
        'import type { JobsAnswer, ResultsAnswer } from "../contract";\n\n'
        "/** GET /api/jobs: a complete test of both steps, a paused job, and a complete one with a situation of every\n"
        " * screen state. */\n"
        f"export const jobsAnswer: JobsAnswer = {literal(listed)};\n\n"
        "/** GET /api/jobs/<id>/results of the complete one: each run's actions, the run's own directories shown short,\n"
        " * a long write cut, a run with more actions than are listed, and a timeout with no readable trace. */\n"
        f"export const resultsAnswer: ResultsAnswer = {literal(results)};\n\n"
        "/** GET /api/jobs/<id>/results of the test of both steps: a situation whose with/without runs changed, one skipped\n"
        " * (its runs disagreed), one where removing its target changed nothing, and each target's verdict. */\n"
        f"export const effectResultsAnswer: ResultsAnswer = {literal(effects)};\n\n"
        "/** GET /api/jobs/<id>/results of a test of what each situation expects: one that works, one not followed (never\n"
        " * run without), one partial whose runs disagreed, one that adds nothing, and each target led by its weakest. */\n"
        f"export const strengthResultsAnswer: ResultsAnswer = {literal(strengths)};\n"
    )


def test_the_ui_fixture_is_what_python_writes() -> None:
    text = golden()
    assert "agentlint-home" not in text and "/runs.noindex/" not in text and "/runs/" not in text  # every run's own one shown short
    if os.environ.get("AGENTLINT_WRITE_GOLDEN"):
        GOLDEN.write_text(text, encoding="utf-8")
    assert GOLDEN.read_text(encoding="utf-8") == text, (
        "ui/src/fixtures/results.ts is not what ui/testing.py writes now; if the change is meant, regenerate it with "
        "AGENTLINT_WRITE_GOLDEN=1 uv run pytest tests/test_ui_results.py and run the UI's tests and build")
