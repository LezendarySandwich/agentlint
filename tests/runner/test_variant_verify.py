"""Variant verification compares what sources stand for, with the variant home mapped back to the real one and the
run's directory to the project: a check by id could never fire, because ids embed paths."""

from __future__ import annotations

import json
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from agentlint.adapters.claude_code.discovery import MarketplaceNotCopied
from agentlint.runner.variant import build_home, build_variant
from agentlint.runner.verify import AblationFailed, read_variant, verify
from tests.runner.fake_claude import USER_MD, fake_machine, reading, target, write

_watching: list[tuple[Path, list[str]]] = []


def _audit(event: str, args: tuple) -> None:
    if _watching and event in ("open", "os.listdir", "os.scandir") and args and isinstance(args[0], (str, bytes, Path)):
        path = str(args[0] if not isinstance(args[0], bytes) else args[0].decode(errors="replace"))
        for root, seen in _watching:
            if path.startswith(str(root)):
                seen.append(f"{event} {path}")


sys.addaudithook(_audit)


@contextmanager
def reads_under(root: Path):
    seen: list[str] = []
    _watching.append((root, seen))
    try:
        yield seen
    finally:
        _watching.pop()


def pair(adapter, project: Path, root: Path, t):
    """(baseline reading, without-target reading), each in its own home and empty run directory under ``root``."""
    readings = []
    for name, removed in (("base", None), ("without", t)):
        variant = build_variant(adapter, root / name / "home", removed)
        (root / name / "work").mkdir()
        readings.append(read_variant(adapter, variant.home.root, root / name / "work", project))
    return readings


def test_a_cut_section_is_verified_gone(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    t = target(reading(adapter, project), "/.claude/CLAUDE.md", "section:/Testing")
    baseline, without = pair(adapter, project, agentlint_home, t)
    assert verify(t, baseline, without).changed == ()
    assert baseline.escapes == () and without.escapes == ()


def test_a_variant_that_failed_to_cut_is_refused(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    t = target(reading(adapter, project), "/.claude/CLAUDE.md", "section:/Testing")
    baseline, _ = pair(adapter, project, agentlint_home, t)
    silent = build_home(adapter.home_dir(), adapter.copy_plan(), agentlint_home / "silent" / "home")  # nothing cut
    (agentlint_home / "silent" / "work").mkdir()
    without = read_variant(adapter, silent.root, agentlint_home / "silent" / "work", project)
    assert t.source.id not in {s.id for s in without.snapshot.sources}  # the old check by id passes: it can't fire
    with pytest.raises(AblationFailed, match="still in"):
        verify(t, baseline, without)


def test_a_section_cut_from_its_file_but_read_from_another_is_not_gone(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    t = target(reading(adapter, project), "/.claude/CLAUDE.md", "section:/Testing")
    baseline, without = pair(adapter, project, agentlint_home, t)
    rules = without.snapshot.sources
    (agentlint_home / "without" / "work" / "CLAUDE.md").write_text("# Testing\nRun the tests first.\n")
    without = read_variant(adapter, agentlint_home / "without" / "home", agentlint_home / "without" / "work", project)
    assert len(without.snapshot.sources) == len(rules) + 1
    with pytest.raises(AblationFailed, match="still read, from .*work/CLAUDE.md"):
        verify(t, baseline, without)


def test_a_settings_key_and_a_skill_are_verified_gone(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    real = reading(adapter, project)
    for i, t in enumerate([target(real, "/.claude/settings.json", "key:/model"), target(real, "skill:user:mine"),
                           target(real, "skill:user:linked"), target(real, "agent:user:reviewer")]):
        baseline, without = pair(adapter, project, agentlint_home / str(i), t)
        assert verify(t, baseline, without).changed == (), t.id


def test_removing_a_settings_key_says_what_else_it_took(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    t = target(reading(adapter, project), "/.claude/settings.json", "key:/hooks")
    baseline, without = pair(adapter, project, agentlint_home, t)
    assert verify(t, baseline, without).changed == ()  # the hook entries were in the same file
    adapter, project = fake_machine(tmp_path / "p", package=True)
    t = target(reading(adapter, project), "/.claude/settings.json", "key:/enabledPlugins")
    baseline, without = pair(adapter, project, agentlint_home / "p", t)
    changed = verify(t, baseline, without).changed
    assert any(place.endswith("skills/helper/SKILL.md") for place in changed)


def test_a_whole_source_still_read_is_not_gone(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    t = target(reading(adapter, project), "skill:user:mine")
    baseline, _ = pair(adapter, project, agentlint_home, t)
    with pytest.raises(AblationFailed, match="still read"):
        verify(t, baseline, baseline)


def test_a_target_the_baseline_lacks_too_measures_nothing(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    t = target(reading(adapter, project), "/.claude/CLAUDE.md", "section:/Testing")
    baseline, without = pair(adapter, project, agentlint_home, t)
    (agentlint_home / "base" / "home" / ".claude" / "CLAUDE.md").write_text("# Memory\nRemember things.\n\n")
    baseline = read_variant(adapter, agentlint_home / "base" / "home", agentlint_home / "base" / "work", project)
    with pytest.raises(AblationFailed, match="baseline variant doesn't have"):
        verify(t, baseline, without)


def test_the_real_users_file_found_above_the_run_directory_is_caught(tmp_path: Path, monkeypatch) -> None:
    """With agentlint's home inside the real home, the harness walks up from the run's directory into the real home
    and reads its ~/.claude/CLAUDE.md as a project file — so cutting the variant's copy removes nothing."""
    adapter, project = fake_machine(tmp_path)
    store = adapter.home_dir() / ".agentlint"
    monkeypatch.setenv("AGENTLINT_HOME", str(store))
    t = target(reading(adapter, project), "/.claude/CLAUDE.md", "section:/Testing")
    baseline, without = pair(adapter, project, store / "v", t)
    assert adapter.home_dir() / ".claude" / "CLAUDE.md" in without.escapes
    with pytest.raises(AblationFailed, match="still"):
        verify(t, baseline, without)


def test_reading_a_variant_never_reads_the_real_home(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    variant = build_variant(adapter, agentlint_home / "v" / "home", None)
    (agentlint_home / "v" / "work").mkdir()
    with reads_under(adapter.home_dir()) as seen:
        result = read_variant(adapter, variant.home.root, agentlint_home / "v" / "work", project)
    assert seen == [] and result.escapes == ()
    assert any(s.provenance.file == variant.home.root / ".claude" / "CLAUDE.md" for s in result.snapshot.sources)


def test_a_variant_reads_its_own_copy_of_a_package(tmp_path: Path, agentlint_home: Path) -> None:
    """Installed packages are found through known_marketplaces.json, which the copy still has naming the real home's
    marketplace by absolute path; the harness reads the variant's own copy of it (Verified Facts, E2b), and so does the
    reading: nothing of the package is opened in the real home, and nothing is an escape."""
    adapter, project = fake_machine(tmp_path, package=True)
    variant = build_variant(adapter, agentlint_home / "v" / "home", None)
    (agentlint_home / "v" / "work").mkdir()
    with reads_under(adapter.home_dir()) as seen:
        result = read_variant(adapter, variant.home.root, agentlint_home / "v" / "work", project)
    assert seen == [] and result.escapes == ()
    skill = next(s for s in result.snapshot.sources if s.provenance.package == "pk@mk" and s.provenance.file)
    assert skill.provenance.file.is_relative_to(variant.home.root.resolve())
    shutil.rmtree(agentlint_home / "v")
    assert (adapter.home_dir() / ".claude" / "CLAUDE.md").read_text() == USER_MD


HOOKED = json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo pk-start"}]}]}})
CHECKOUT = Path("plugins/marketplaces/mk/pk")  # the package in its marketplace checkout, under .claude
CACHE = Path("plugins/cache/mk/pk/1.0.0")  # and its copy in the plugin cache, which the harness doesn't read


def hooked_package(tmp: Path):
    """A fake machine whose package has a SessionStart hook, in both its marketplace checkout and the plugin cache."""
    adapter, project = fake_machine(tmp, package=True)
    for place in (CHECKOUT, CACHE):
        write(adapter.home_dir() / ".claude" / place / ".claude-plugin" / "plugin.json", HOOKED)
    return adapter, project


def unhooked(adapter, project: Path, root: Path, place: Path):
    """(baseline reading, reading of a variant whose package copy at ``place`` lost its hooks), each built under
    ``root``: the edit E2 and E2b made by hand in a variant home."""
    readings = []
    for name in ("base", "without"):
        variant = build_variant(adapter, root / name / "home", None)
        if name == "without":
            write(variant.home.root / ".claude" / place / ".claude-plugin" / "plugin.json", "{}")
        (root / name / "work").mkdir()
        readings.append(read_variant(adapter, variant.home.root, root / name / "work", project))
    return readings


def _hooks(reading) -> list[str]:
    return [s.id for s in reading.snapshot.sources if s.id.startswith("claude-code:hook:pk@mk:")]


def test_a_hook_removed_from_the_variants_marketplace_checkout_is_gone(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = hooked_package(tmp_path)
    t = target(reading(adapter, project), "pk@mk:plugin.json#/hooks/SessionStart/0/hooks/0")
    baseline, without = unhooked(adapter, project, agentlint_home, CHECKOUT)
    assert _hooks(baseline) == [t.source.id] and _hooks(without) == []
    assert verify(t, baseline, without).changed == ()
    assert baseline.escapes == () and without.escapes == ()


def test_a_hook_removed_only_from_the_variants_plugin_cache_is_still_there(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = hooked_package(tmp_path)
    t = target(reading(adapter, project), "pk@mk:plugin.json#/hooks/SessionStart/0/hooks/0")
    baseline, without = unhooked(adapter, project, agentlint_home, CACHE)
    assert _hooks(without) == [t.source.id]
    with pytest.raises(AblationFailed, match="still"):
        verify(t, baseline, without)


def test_a_variant_without_its_copy_of_a_marketplace_is_refused(tmp_path: Path, agentlint_home: Path) -> None:
    """The copy's marketplace checkout gone: the package is not read from the real home instead — the reading refuses,
    saying where it looked and what the copied configuration names."""
    adapter, project = fake_machine(tmp_path, package=True)
    variant = build_variant(adapter, agentlint_home / "v" / "home", None)
    shutil.rmtree(variant.home.root / ".claude" / "plugins" / "marketplaces" / "mk")
    (agentlint_home / "v" / "work").mkdir()
    with reads_under(adapter.home_dir()) as seen, pytest.raises(MarketplaceNotCopied, match="not read from there"):
        read_variant(adapter, variant.home.root, agentlint_home / "v" / "work", project)
    assert seen == []
