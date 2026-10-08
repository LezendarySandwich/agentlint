"""Runs live in ``runs.noindex`` (2026-10-07: Spotlight indexed every variant home a run built, and leaves a .noindex
directory alone); what a run left in ``runs``, where they lived before, is still swept, and an older job's traces,
which spell paths under ``runs``, are still read against their own directories."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from agentlint.runner import execute
from agentlint.store import connect, jobs
from agentlint.ui.testing import effect_places, rendered, run_places


def test_runs_live_in_a_noindex_directory_and_the_old_one_is_still_swept() -> None:
    assert execute.runs_root().name == "runs.noindex"
    left = execute.legacy_runs_root() / "job-gone" / "cmp-0001.baseline.0" / "home"
    left.mkdir(parents=True)
    (left / "transcript.jsonl").write_text("{}\n")
    cleaning = execute.clean_runs(connect())
    assert cleaning.swept == 1 and cleaning.close(connect(), wait=True) == ()
    assert not (execute.legacy_runs_root() / "job-gone" / "cmp-0001.baseline.0").exists()


def test_an_older_jobs_run_is_read_against_the_directories_its_trace_names() -> None:
    at = datetime(2026, 10, 6, tzinfo=UTC)
    old = execute.legacy_runs_root() / "job-old" / "cmp-0001.baseline.0"
    trace = json.dumps({"actions": [{"input": {"file_path": f"{old}/work/notes.md"}}]})
    row = jobs.Run("job-old", "cmp-0001:baseline:0", "s", "ok", trace, None, None, 1, None, at, at)
    assert effect_places(row).work == str(old / "work")
    new = jobs.Run("job-new", "cmp-0001:baseline:0", "s", "ok", "{}", None, None, 1, None, at, at)
    assert effect_places(new).work == str(execute.runs_root() / "job-new" / "cmp-0001.baseline.0" / "work")
    assert rendered({"file_path": f"{old}/work/notes.md"}, run_places("job-old", "cmp-0001:baseline:0")) == "./notes.md"
