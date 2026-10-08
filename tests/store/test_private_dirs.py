"""Every directory agentlint makes under its home is 0700 at every level it makes (00-overview.md "State on disk",
05-runner.md "The executor"): ``Path.mkdir(parents=True, mode=0o700)`` gives the mode to the last level only, which
left ``runs/`` 0755 on the user's machine (2026-10-05). The umask is set to 022 here, the usual one, under which the
old calls made every level above the last 0755."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from agentlint.adapters.claude_code.generator import ClaudeCodeGenerator
from agentlint.adapters.claude_code.launch import ClaudeCodeLauncher
from agentlint.runner import worker
from agentlint.runner.execute import Launch, Launched, run_test, runs_root
from agentlint.runner.plan import stability_plan
from agentlint.store import connect, home, private_dirs
from tests.runner.fake_claude import fake_machine, write
from tests.runner.test_executor import install_fake_claude


@pytest.fixture(autouse=True)
def usual_umask():
    was = os.umask(0o022)
    yield
    os.umask(was)


@pytest.fixture
def new_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An agentlint home two levels below a directory that exists: agentlint makes both."""
    made = tmp_path / "new" / "agentlint"
    monkeypatch.setenv("AGENTLINT_HOME", str(made))
    return made


def mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def levels(top: Path, path: Path) -> dict[str, int]:
    """The mode of each directory below ``top`` down to ``path``, by its path relative to ``top``."""
    found, level = {}, path
    while level != top:
        found[str(level.relative_to(top))] = mode(level)
        level = level.parent
    return found


def test_every_level_made_is_0700_and_what_exists_is_left_as_it_is(tmp_path: Path) -> None:
    tmp_path.chmod(0o755)
    made = private_dirs(tmp_path / "a" / "b" / "c")
    assert levels(tmp_path, made) == {"a": 0o700, "a/b": 0o700, "a/b/c": 0o700}
    assert mode(tmp_path) == 0o755  # it existed: not agentlint's to change
    (tmp_path / "a").chmod(0o750)
    assert private_dirs(made) == made and mode(tmp_path / "a") == 0o750  # exists: nothing made, nothing changed
    with pytest.raises(FileExistsError):
        private_dirs(made, exist_ok=False)
    write(tmp_path / "file", "x")
    with pytest.raises(OSError):
        private_dirs(tmp_path / "file" / "below")  # a file in the way is never taken for a directory


def test_the_home_the_database_makes_is_0700_at_every_level(tmp_path: Path, new_home: Path) -> None:
    connect().close()
    assert levels(tmp_path, new_home) == {"new": 0o700, "new/agentlint": 0o700}
    assert mode(new_home / "agentlint.db") == 0o600


def test_the_generators_directory_and_a_jobs_log_are_0700_at_every_level(tmp_path: Path, new_home: Path) -> None:
    generator = ClaudeCodeGenerator(executable="no-such-claude")
    assert generator._empty_directory() == new_home / "generate"
    assert levels(tmp_path, new_home / "generate") == {"new": 0o700, "new/agentlint": 0o700,
                                                        "new/agentlint/generate": 0o700}
    log = worker.log_path("2026-10-05T000000Z-aaaaaa")
    launched = worker.launch("2026-10-05T000000Z-aaaaaa", ["true"], environ=dict(os.environ))
    launched.wait()
    assert log.parent == new_home / "jobs" and mode(log.parent) == 0o700


class Watching:
    """A launcher that notes, as each run starts, the mode of every directory from the home's parent down to the run's
    working directory and its variant home."""

    def __init__(self, launcher: ClaudeCodeLauncher, top: Path) -> None:
        self.launcher, self.top, self.seen = launcher, top, []

    def launch(self, launch: Launch) -> Launched:
        self.seen.append({**levels(self.top, launch.workdir), **levels(self.top, launch.home)})
        return self.launcher.launch(launch)


def test_a_jobs_run_directories_are_0700_at_every_level(tmp_path: Path, new_home: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, project = fake_machine(tmp_path)
    write(project / ".claude" / "settings.json", json.dumps({"permissions": {"allow": ["Bash(make)"]}}))
    install_fake_claude(tmp_path / "bin", monkeypatch)
    monkeypatch.setenv("FAKE_CLAUDE_QUEUE", str(tmp_path / "queue.json"))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "calls.jsonl"))
    top = Path(os.path.realpath(tmp_path))
    watching = Watching(ClaudeCodeLauncher(grace_s=1), top)
    tested = run_test(connect(), adapter, watching, project=project, model="model-x",
                      plan=stability_plan([("s1", "Fix the failing test")], runs=2), run_ceiling=10, timeout_s=30,
                      wait=lambda seconds: None)
    assert tested.report.recorded == 2 and len(watching.seen) == 2
    run = f"new/agentlint/runs.noindex/{tested.job_id}/cmp-0001.baseline.0"
    assert watching.seen[0] == {"new": 0o700, "new/agentlint": 0o700, "new/agentlint/runs.noindex": 0o700,
                                f"new/agentlint/runs.noindex/{tested.job_id}": 0o700, run: 0o700, f"{run}/work": 0o700,
                                f"{run}/home": 0o700}
    assert all(set(seen.values()) == {0o700} for seen in watching.seen)
    assert runs_root() == Path(os.path.realpath(home())) / "runs.noindex" and mode(runs_root()) == 0o700  # what stays
