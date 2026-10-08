"""Cutting a user-level target out of a variant home with the content's own excision; what no copy holds is refused,
saying why (project targets with a project copied, and packages' hooks: test_variant_project.py)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from agentlint.rank.plan import targets
from agentlint.runner import variant as V
from agentlint.runner.variant import SourceChanged, TargetNotSupported, build_variant
from tests.runner.fake_claude import USER_MD, fake_machine, reading, target, write


@pytest.fixture
def no_copy(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """build_home replaced by one that records it was called and copies nothing: a refusal must come first."""
    calls: list[Path] = []

    def recorded(source: Path, plan: V.CopyPlan, dest: Path) -> V.BuiltHome:
        calls.append(dest)
        raise AssertionError("a refused target got as far as copying the home")

    monkeypatch.setattr(V, "build_home", recorded)
    return calls


def test_a_user_instruction_section_is_cut_from_the_copy_only(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    t = target(reading(adapter, project), "/.claude/CLAUDE.md", "section:/Testing")
    variant = build_variant(adapter, agentlint_home / "v", t)
    assert (variant.home.root / ".claude" / "CLAUDE.md").read_text() == "# Memory\nRemember things.\n\n"
    assert (adapter.home_dir() / ".claude" / "CLAUDE.md").read_text() == USER_MD
    assert variant.cut.rewritten == (".claude/CLAUDE.md",) and variant.cut.target_id == t.id


def test_a_user_settings_key_is_cut_and_the_json_stays_valid(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    t = target(reading(adapter, project), "/.claude/settings.json", "key:/model")
    variant = build_variant(adapter, agentlint_home / "v", t)
    cut = json.loads((variant.home.root / ".claude" / "settings.json").read_text())
    assert "model" not in cut and cut["permissions"] == {"allow": ["Bash(ls)"]}
    assert json.loads((adapter.home_dir() / ".claude" / "settings.json").read_text())["model"] == "model-x"


def test_a_user_skill_goes_with_its_directory(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    variant = build_variant(adapter, agentlint_home / "v", target(reading(adapter, project), "skill:user:mine"))
    assert not (variant.home.root / ".claude" / "skills" / "mine").exists()
    assert variant.cut.removed == (".claude/skills/mine",)
    assert (adapter.home_dir() / ".claude" / "skills" / "mine" / "SKILL.md").is_file()


def test_a_linked_user_skill_loses_its_link_not_what_it_leads_to(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    variant = build_variant(adapter, agentlint_home / "v", target(reading(adapter, project), "skill:user:linked"))
    root = variant.home.root / ".claude"
    assert not (root / "skills" / "linked").exists() and (root / "shared" / "linked" / "SKILL.md").is_file()
    assert (adapter.home_dir() / ".claude" / "skills" / "linked").is_symlink()


def test_a_user_agent_goes_with_its_file(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    variant = build_variant(adapter, agentlint_home / "v", target(reading(adapter, project), "agent:user:reviewer"))
    assert not (variant.home.root / ".claude" / "agents" / "reviewer.md").exists()
    assert (variant.home.root / ".claude" / "agents").is_dir()


@pytest.mark.parametrize(("source_end", "key", "says"), [
    ("/project/AGENTS.md", "section:/Project", "no project is copied"),
    ("/memory/MEMORY.md", "section:/Index", "history a run doesn't get"),
    ("skill:pk:helper", None, "D3"),
    ("#claudeMd", None, "can.t be removed"),
])
def test_project_package_and_managed_targets_are_refused_before_anything_is_copied(
        tmp_path: Path, agentlint_home: Path, no_copy: list[Path], source_end: str, key: str | None,
        says: str) -> None:
    adapter, project = fake_machine(tmp_path, package=True)
    t = target(reading(adapter, project), source_end, key)
    with pytest.raises(TargetNotSupported, match=says):
        build_variant(adapter, agentlint_home / "v", t)
    assert no_copy == [] and not (agentlint_home / "v").exists()


def test_a_user_hook_is_refused_before_anything_is_copied(tmp_path: Path, agentlint_home: Path,
                                                          no_copy: list[Path]) -> None:
    adapter, project = fake_machine(tmp_path)
    hook = next(t for t in targets(reading(adapter, project))
                if t.source.provenance.point.name == "session_start_hook")
    with pytest.raises(TargetNotSupported, match="entry"):
        build_variant(adapter, agentlint_home / "v", hook)
    assert no_copy == [] and not (agentlint_home / "v").exists()


def test_a_whole_source_the_adapter_cant_name_files_for_is_refused_before_copying(
        tmp_path: Path, agentlint_home: Path, no_copy: list[Path], monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, project = fake_machine(tmp_path)
    t = target(reading(adapter, project), "agent:user:reviewer")
    monkeypatch.setattr(adapter, "removal", lambda source: ())
    with pytest.raises(TargetNotSupported, match="which files"):
        build_variant(adapter, agentlint_home / "v", t)
    assert no_copy == []


def test_a_user_file_outside_the_copy_is_refused_as_outside_it_not_as_project_history(
        tmp_path: Path, agentlint_home: Path, no_copy: list[Path]) -> None:
    adapter, project = fake_machine(tmp_path)
    home = adapter.home_dir()
    settings = json.loads((home / ".claude" / "settings.json").read_text())
    write(home / ".claude" / "settings.json", json.dumps({**settings, "permissions": {
        "allow": ["Bash(ls)"], "additionalDirectories": ["~/extra"]}}))
    write(home / "extra" / ".claude" / "settings.json", json.dumps({"model": "model-y"}))
    found = [t for t in targets(reading(adapter, project)) if t.source.provenance.file is not None
             and t.source.provenance.file.is_relative_to(home / "extra") and t.id.endswith("#key:/model")]
    assert len(found) == 1, [t.id for t in targets(reading(adapter, project))]
    with pytest.raises(TargetNotSupported, match="outside what this variant copies") as refused:
        build_variant(adapter, agentlint_home / "v", found[0])
    assert "D1" not in str(refused.value) and "history" not in str(refused.value)
    assert no_copy == []


def test_a_file_edited_since_it_was_read_is_not_cut(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    t = target(reading(adapter, project), "/.claude/CLAUDE.md", "section:/Testing")
    (adapter.home_dir() / ".claude" / "CLAUDE.md").write_text(USER_MD + "# New\nmore\n")
    with pytest.raises(SourceChanged):
        build_variant(adapter, agentlint_home / "v", t)
    assert not (agentlint_home / "v").exists()


def test_a_user_file_reached_through_a_link_is_rewritten_as_the_variants_own(tmp_path: Path,
                                                                           agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    config = adapter.home_dir() / ".claude"
    (config / "CLAUDE.md").rename(config / "shared" / "CLAUDE.md")
    (config / "CLAUDE.md").symlink_to(config / "shared" / "CLAUDE.md")
    t = target(reading(adapter, project), "/.claude/CLAUDE.md", "section:/Testing")
    variant = build_variant(adapter, agentlint_home / "v", t)
    copy = variant.home.root / ".claude"
    assert not (copy / "CLAUDE.md").is_symlink() and "Testing" not in (copy / "CLAUDE.md").read_text()
    assert (copy / "shared" / "CLAUDE.md").read_text() == USER_MD  # what the link led to is untouched


def test_a_read_only_user_file_is_cut_and_keeps_its_mode(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    original = adapter.home_dir() / ".claude" / "CLAUDE.md"
    os.chmod(original, 0o444)
    t = target(reading(adapter, project), "/.claude/CLAUDE.md", "section:/Testing")
    variant = build_variant(adapter, agentlint_home / "v", t)
    copy = variant.home.root / ".claude" / "CLAUDE.md"
    assert copy.read_text() == "# Memory\nRemember things.\n\n" and stat.S_IMODE(os.stat(copy).st_mode) == 0o444
    assert original.read_text() == USER_MD and stat.S_IMODE(os.stat(original).st_mode) == 0o444
    assert not [name for name in os.listdir(copy.parent) if "agentlint" in name]  # no scratch file left behind
    V.remove_home(variant.home.root)


@pytest.mark.parametrize("skills_mode", [0o755, 0o555])
def test_a_user_skill_with_a_read_only_directory_inside_goes_whole(tmp_path: Path, agentlint_home: Path,
                                                                   skills_mode: int) -> None:
    adapter, project = fake_machine(tmp_path)
    skills = adapter.home_dir() / ".claude" / "skills"
    write(skills / "mine" / "assets" / "logo.txt", "x")
    os.chmod(skills / "mine" / "assets", 0o555)
    os.chmod(skills, skills_mode)
    try:
        variant = build_variant(adapter, agentlint_home / "v", target(reading(adapter, project), "skill:user:mine"))
        copied = variant.home.root / ".claude" / "skills"
        assert not os.path.lexists(copied / "mine") and stat.S_IMODE(os.stat(copied).st_mode) == skills_mode
        assert (skills / "mine" / "assets" / "logo.txt").is_file()
        V.remove_home(variant.home.root)
    finally:
        os.chmod(skills, 0o755)
        os.chmod(skills / "mine" / "assets", 0o755)


def test_a_user_file_linked_out_of_the_home_is_refused_before_copying(tmp_path: Path, agentlint_home: Path,
                                                                     no_copy: list[Path]) -> None:
    adapter, project = fake_machine(tmp_path)
    config = adapter.home_dir() / ".claude"
    kept = tmp_path / "dotfiles" / "CLAUDE.md"
    kept.parent.mkdir()
    (config / "CLAUDE.md").rename(kept)
    (config / "CLAUDE.md").symlink_to(kept)  # a dotfile manager's layout
    t = target(reading(adapter, project), "/.claude/CLAUDE.md", "section:/Testing")
    with pytest.raises(TargetNotSupported, match=r"CLAUDE\.md is a link to .*dotfiles.*outside the copy"):
        build_variant(adapter, agentlint_home / "v", t)
    assert no_copy == []


def test_a_user_skill_linked_out_of_the_home_is_refused_before_copying(tmp_path: Path, agentlint_home: Path,
                                                                      no_copy: list[Path]) -> None:
    adapter, project = fake_machine(tmp_path)
    write(tmp_path / "dots" / "SKILL.md", "---\nname: dots\ndescription: dotfile skill\n---\nbody\n")
    (adapter.home_dir() / ".claude" / "skills" / "dots").symlink_to(tmp_path / "dots")
    with pytest.raises(TargetNotSupported, match=r"skills/dots is a link to .*outside the copy") as refused:
        build_variant(adapter, agentlint_home / "v", target(reading(adapter, project), "skill:user:dots"))
    assert "changed" not in str(refused.value)
    assert no_copy == []


@pytest.mark.skipif(not hasattr(os, "lchflags"), reason="this system has no file flags")
def test_a_locked_user_file_is_cut_and_the_variant_deletes(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    original = adapter.home_dir() / ".claude" / "CLAUDE.md"
    os.chflags(original, stat.UF_IMMUTABLE)  # Finder's "Locked"
    try:
        t = target(reading(adapter, project), "/.claude/CLAUDE.md", "section:/Testing")
        variant = build_variant(adapter, agentlint_home / "v", t)
        assert (variant.home.root / ".claude" / "CLAUDE.md").read_text() == "# Memory\nRemember things.\n\n"
        assert original.read_text() == USER_MD and os.lstat(original).st_flags & stat.UF_IMMUTABLE
        V.remove_home(variant.home.root)
        assert not os.path.lexists(variant.home.root)
    finally:
        os.chflags(original, 0)
