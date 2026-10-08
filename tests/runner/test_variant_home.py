"""Building a variant home: a fresh copy, under agentlint's home, of what the plan copies — and nothing written
anywhere else."""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePath

import pytest

from agentlint.runner import variant
from agentlint.runner.variant import CopyEntry, CopyPlan, UnsafePath, VariantError, build_home, remove_home

PLAN = CopyPlan((CopyEntry(".conf", frozenset({"projects", "history.jsonl"})), CopyEntry(".conf.json")))


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def fake_home(tmp: Path) -> Path:
    home = tmp / "real-home"
    write(home / ".conf" / "settings.json", '{"a": 1}')
    write(home / ".conf" / "skills" / "mine" / "SKILL.md", "---\nname: mine\n---\nbody\n")
    write(home / ".conf" / "market" / "skills" / "shared" / "SKILL.md", "---\nname: shared\n---\nshared body\n")
    write(home / ".conf" / "projects" / "-p" / "s.jsonl", "{}\n")
    write(home / ".conf" / "history.jsonl", "typed prompts\n")
    write(home / ".conf.json", '{"k": true}')
    write(home / "Documents" / "private.txt", "not configuration")
    return home


def snapshot_tree(root: Path) -> dict[str, tuple]:
    found = {}
    for directory, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(directory) / name
            info = os.lstat(path)
            found[str(path.relative_to(root))] = (info.st_mode, info.st_size, info.st_mtime_ns,
                                                  os.readlink(path) if path.is_symlink() else None)
    return found


def test_it_copies_what_the_plan_names_and_nothing_else(tmp_path: Path, agentlint_home: Path) -> None:
    home = fake_home(tmp_path)
    built = build_home(home, PLAN, agentlint_home / "v" / "home")
    root = built.root
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
    assert (root / ".conf" / "settings.json").read_text() == '{"a": 1}'
    assert (root / ".conf" / "skills" / "mine" / "SKILL.md").is_file()
    assert (root / ".conf.json").read_text() == '{"k": true}'
    assert not (root / ".conf" / "projects").exists() and not (root / ".conf" / "history.jsonl").exists()
    assert not (root / "Documents").exists()
    assert built.copied == (".conf", ".conf.json") and built.missing == ()


def test_the_copy_is_its_own_and_the_home_is_left_as_it_was(tmp_path: Path, agentlint_home: Path) -> None:
    home = fake_home(tmp_path)
    before = snapshot_tree(home)
    built = build_home(home, PLAN, agentlint_home / "v" / "home")
    (built.root / ".conf" / "settings.json").write_text("{}")
    remove_home(built.root)
    assert not built.root.exists()
    assert snapshot_tree(home) == before
    assert (home / ".conf" / "settings.json").read_text() == '{"a": 1}'


def test_a_missing_entry_is_reported_not_invented(tmp_path: Path, agentlint_home: Path) -> None:
    home = fake_home(tmp_path)
    (home / ".conf.json").unlink()
    built = build_home(home, PLAN, agentlint_home / "v" / "home")
    assert built.missing == (".conf.json",) and not (built.root / ".conf.json").exists()


def test_links_never_lead_out_of_the_copy(tmp_path: Path, agentlint_home: Path) -> None:
    home = fake_home(tmp_path)
    outside = write(tmp_path / "elsewhere" / "notes.md", "outside")
    skills = home / ".conf" / "skills"
    os.symlink(home / ".conf" / "market" / "skills" / "shared", skills / "shared")  # absolute, into the copy
    os.symlink("../market/skills/shared", skills / "relative")  # relative, into the copy
    os.symlink(outside, skills / "out")  # out of the home altogether
    os.symlink(home / ".conf" / "projects" / "-p", skills / "history")  # into what the plan leaves out
    os.symlink("../../Documents/private.txt", home / ".conf" / "doc")  # into the home, but not the copy
    built = build_home(home, PLAN, agentlint_home / "v" / "home")
    root = built.root
    for name in ("shared", "relative"):
        link = root / ".conf" / "skills" / name
        assert link.is_symlink() and Path(os.path.realpath(link)).is_relative_to(os.path.realpath(root))
        assert (link / "SKILL.md").read_text() == "---\nname: shared\n---\nshared body\n"
    for gone in (skills / "out", skills / "history", home / ".conf" / "doc"):
        assert not os.path.lexists(root / gone.relative_to(home))
    assert {link.path for link in built.relinked} == {".conf/skills/shared", ".conf/skills/relative"}
    assert {link.path for link in built.unlinked} == {".conf/skills/out", ".conf/skills/history", ".conf/doc"}
    for directory, dirs, files in os.walk(root):
        for name in dirs + files:
            path = os.path.join(directory, name)
            assert os.path.realpath(path).startswith(os.path.realpath(root) + os.sep), path
    assert (skills / "out").is_symlink() and outside.read_text() == "outside"  # the home's own links untouched


def test_off_apfs_it_copies_instead(tmp_path: Path, agentlint_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = fake_home(tmp_path)
    os.symlink(home / ".conf" / "market" / "skills" / "shared", home / ".conf" / "skills" / "shared")
    monkeypatch.setattr(variant, "_clonefile", None)
    built = build_home(home, PLAN, agentlint_home / "v" / "home")
    assert built.cloned is False
    assert (built.root / ".conf" / "skills" / "mine" / "SKILL.md").is_file()
    assert (built.root / ".conf" / "skills" / "shared").is_symlink()
    assert not (built.root / ".conf" / "projects").exists()


def test_a_filesystem_that_cannot_clone_falls_back(tmp_path: Path, agentlint_home: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes
    import errno

    home = fake_home(tmp_path)

    def unsupported(src: bytes, dst: bytes, flags: int) -> int:
        ctypes.set_errno(errno.ENOTSUP)
        return -1

    monkeypatch.setattr(variant, "_clonefile", unsupported)
    built = build_home(home, PLAN, agentlint_home / "v" / "home")
    assert built.cloned is False and (built.root / ".conf.json").is_file()


@pytest.mark.skipif(variant._clonefile is None, reason="clonefile(2) is macOS-only")
def test_on_apfs_it_clones(tmp_path: Path, agentlint_home: Path) -> None:
    built = build_home(fake_home(tmp_path), PLAN, agentlint_home / "v" / "home")
    assert built.cloned is True


def test_it_builds_only_under_agentlint_home_and_only_fresh(tmp_path: Path, agentlint_home: Path) -> None:
    home = fake_home(tmp_path)
    with pytest.raises(UnsafePath):
        build_home(home, PLAN, tmp_path / "somewhere-else")
    with pytest.raises(UnsafePath):
        build_home(home, PLAN, agentlint_home)  # the home itself, not a place under it
    with pytest.raises(UnsafePath):
        build_home(home, PLAN, agentlint_home / ".." / "escape")
    (agentlint_home / "taken").mkdir()
    with pytest.raises(VariantError, match="fresh"):
        build_home(home, PLAN, agentlint_home / "taken")
    assert not (tmp_path / "somewhere-else").exists()


def test_it_refuses_to_copy_a_home_into_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = fake_home(tmp_path)
    monkeypatch.setenv("AGENTLINT_HOME", str(home / ".conf" / "agentlint"))
    with pytest.raises(UnsafePath, match="copy itself"):
        build_home(home, PLAN, home / ".conf" / "agentlint" / "v")


def test_a_write_outside_the_variant_is_refused(tmp_path: Path, agentlint_home: Path) -> None:
    root = agentlint_home / "v"
    root.mkdir()
    variant.guard(root / "a" / "b", root)
    with pytest.raises(UnsafePath):
        variant.guard(agentlint_home / "other", root)
    os.symlink(tmp_path, root / "jump")
    with pytest.raises(UnsafePath):
        variant.guard(root / "jump" / "file", root)  # through a link out
    variant.guard(root / "jump", root)  # the link itself is inside: removing it is no write to its target


def test_a_copy_plan_says_what_it_copies() -> None:
    assert PLAN.copies(PurePath(".conf/settings.json"))
    assert PLAN.copies(PurePath(".conf.json"))
    assert not PLAN.copies(PurePath(".conf/projects/-p/memory/MEMORY.md"))
    assert not PLAN.copies(PurePath(".conf/history.jsonl"))
    assert not PLAN.copies(PurePath("Documents/x"))
    assert not PLAN.copies(PurePath(".conf/../Documents"))
    with pytest.raises(ValueError):
        CopyEntry("a/b")
    with pytest.raises(ValueError):
        CopyPlan(())


def test_a_copy_that_fails_halfway_leaves_nothing_behind(tmp_path: Path, agentlint_home: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    home = fake_home(tmp_path)

    def broken(src: Path, dst: Path, root: Path) -> bool:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(variant, "_copy", broken)
    with pytest.raises(OSError):
        build_home(home, PLAN, agentlint_home / "v" / "home")
    assert not (agentlint_home / "v" / "home").exists()


def test_a_relative_link_through_a_linked_directory_is_judged_where_it_really_leads(
        tmp_path: Path, agentlint_home: Path) -> None:
    home = fake_home(tmp_path)
    write(home / ".conf" / "CLAUDE.md", "the user's own")
    write(tmp_path / "outside" / "CLAUDE.md", "someone else's")
    (tmp_path / "outside" / "a").mkdir()
    os.symlink(tmp_path / "outside" / "a", home / ".conf" / "dl")
    os.symlink("dl/../CLAUDE.md", home / ".conf" / "rl")  # in the home: outside/CLAUDE.md, not .conf/CLAUDE.md
    assert (home / ".conf" / "rl").read_text() == "someone else's"
    built = build_home(home, PLAN, agentlint_home / "v")
    assert not os.path.lexists(built.root / ".conf" / "rl")
    assert {link.path for link in built.unlinked} == {".conf/dl", ".conf/rl"}
    assert ".conf/rl" not in {link.path for link in built.relinked}


def test_an_entry_that_is_itself_a_link_is_copied_as_what_it_leads_to(tmp_path: Path, agentlint_home: Path) -> None:
    home = fake_home(tmp_path)
    kept = write(tmp_path / "dotfiles" / "conf.json", '{"k": true}')
    (home / ".conf.json").unlink()
    os.symlink(kept, home / ".conf.json")  # a dotfile manager's link
    built = build_home(home, PLAN, agentlint_home / "v")
    copy = built.root / ".conf.json"
    assert not copy.is_symlink() and copy.read_text() == '{"k": true}'
    os.unlink(home / ".conf.json")
    os.symlink(tmp_path / "nowhere.json", home / ".conf.json")
    assert build_home(home, PLAN, agentlint_home / "w").missing == (".conf.json",)


def test_a_copied_read_only_directory_is_deleted_with_the_home(tmp_path: Path, agentlint_home: Path) -> None:
    home = fake_home(tmp_path)
    write(home / ".conf" / "cache-ro" / "sub" / "f.txt", "x")
    os.chmod(home / ".conf" / "cache-ro" / "sub", 0o555)
    os.chmod(home / ".conf" / "cache-ro", 0o555)
    try:
        built = build_home(home, PLAN, agentlint_home / "v")
        assert stat.S_IMODE(os.stat(built.root / ".conf" / "cache-ro" / "sub").st_mode) == 0o555  # modes kept
        remove_home(built.root)
        assert not os.path.lexists(built.root)
        assert stat.S_IMODE(os.stat(home / ".conf" / "cache-ro" / "sub").st_mode) == 0o555  # the source untouched
    finally:
        os.chmod(home / ".conf" / "cache-ro", 0o755)
        os.chmod(home / ".conf" / "cache-ro" / "sub", 0o755)


def test_a_cleanup_that_fails_too_is_noted_on_the_error_that_is_raised(tmp_path: Path, agentlint_home: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    home = fake_home(tmp_path)

    def broken(src: Path, dst: Path, root: Path) -> bool:
        raise OSError(28, "No space left on device")

    def stuck(root: Path) -> None:
        raise PermissionError(13, "Permission denied", str(root))

    monkeypatch.setattr(variant, "_copy", broken)
    monkeypatch.setattr(variant, "remove_home", stuck)
    with pytest.raises(OSError, match="No space left") as raised:
        build_home(home, PLAN, agentlint_home / "v")
    assert any("could not be deleted" in note for note in raised.value.__notes__)


def test_a_read_only_directory_holding_a_link_out_is_built_and_keeps_its_mode(tmp_path: Path,
                                                                              agentlint_home: Path) -> None:
    home = fake_home(tmp_path)
    outside = write(tmp_path / "elsewhere" / "hosts", "outside")
    write(home / ".conf" / "ro" / "settings.json", "{}")
    os.symlink(outside, home / ".conf" / "ro" / "l")
    os.symlink(home / ".conf" / "settings.json", home / ".conf" / "ro" / "in")
    os.chmod(home / ".conf" / "ro", 0o555)
    try:
        built = build_home(home, PLAN, agentlint_home / "v")
        ro = built.root / ".conf" / "ro"
        assert not os.path.lexists(ro / "l") and ".conf/ro/l" in {link.path for link in built.unlinked}
        assert os.readlink(ro / "in") == str(built.root / ".conf" / "settings.json")
        assert stat.S_IMODE(os.stat(ro).st_mode) == 0o555  # the copy stays faithful to the source's mode
        assert os.path.islink(home / ".conf" / "ro" / "l")  # the source untouched
        remove_home(built.root)
        assert not os.path.lexists(built.root)
    finally:
        os.chmod(home / ".conf" / "ro", 0o755)


def test_a_read_only_entry_with_children_left_out_is_built(tmp_path: Path, agentlint_home: Path) -> None:
    home = fake_home(tmp_path)
    os.chmod(home / ".conf", 0o555)
    try:
        built = build_home(home, PLAN, agentlint_home / "v")
        assert (built.root / ".conf" / "settings.json").is_file() and not (built.root / ".conf" / "projects").exists()
        assert stat.S_IMODE(os.stat(built.root / ".conf").st_mode) == 0o555
        remove_home(built.root)
        assert not os.path.lexists(built.root)
    finally:
        os.chmod(home / ".conf", 0o755)


def test_a_directory_that_cant_be_listed_fails_the_build_rather_than_hide_a_link(tmp_path: Path,
                                                                                 agentlint_home: Path) -> None:
    home = fake_home(tmp_path)
    write(home / ".conf" / "locked" / "x.md", "x")
    os.symlink(tmp_path, home / ".conf" / "locked" / "out")
    os.chmod(home / ".conf" / "locked", 0o311)  # search but no listing: a known name inside can still be opened
    try:
        with pytest.raises(OSError):
            build_home(home, PLAN, agentlint_home / "v")
        assert not os.path.lexists(agentlint_home / "v")
    finally:
        os.chmod(home / ".conf" / "locked", 0o755)


needs_flags = pytest.mark.skipif(not hasattr(os, "lchflags"), reason="this system has no file flags")


@needs_flags
def test_a_locked_file_or_directory_is_copied_unlocked_and_the_home_deletes(tmp_path: Path,
                                                                           agentlint_home: Path) -> None:
    home = fake_home(tmp_path)
    locked_file = home / ".conf" / "skills" / "mine" / "SKILL.md"
    locked_dir = home / ".conf" / "market"
    os.chflags(locked_file, stat.UF_IMMUTABLE)  # what Finder's "Locked" sets
    os.chflags(locked_dir, stat.UF_APPEND)
    try:
        built = build_home(home, PLAN, agentlint_home / "v")
        for copied in (built.root / ".conf" / "skills" / "mine" / "SKILL.md", built.root / ".conf" / "market"):
            assert not os.lstat(copied).st_flags & (stat.UF_IMMUTABLE | stat.UF_APPEND)
        assert os.lstat(locked_file).st_flags & stat.UF_IMMUTABLE  # the source keeps its locks
        assert os.lstat(locked_dir).st_flags & stat.UF_APPEND
        remove_home(built.root)
        assert not os.path.lexists(built.root)
    finally:
        os.chflags(locked_file, 0)
        os.chflags(locked_dir, 0)


@needs_flags
def test_a_file_locked_in_the_variant_after_building_doesnt_stop_its_deletion(tmp_path: Path,
                                                                             agentlint_home: Path) -> None:
    built = build_home(fake_home(tmp_path), PLAN, agentlint_home / "v")
    os.chflags(built.root / ".conf" / "settings.json", stat.UF_IMMUTABLE)  # as a run might
    os.chflags(built.root / ".conf" / "skills", stat.UF_IMMUTABLE)
    remove_home(built.root)
    assert not os.path.lexists(built.root)


@needs_flags
def test_a_lock_only_the_superuser_can_clear_fails_the_build(tmp_path: Path, agentlint_home: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    home = fake_home(tmp_path)
    locked = home / ".conf" / "settings.json"
    os.chflags(locked, stat.UF_IMMUTABLE)
    try:
        with monkeypatch.context() as patched, pytest.raises(VariantError, match="only the superuser") as raised:
            patched.setattr(variant, "_SYSTEM_LOCKS", stat.UF_IMMUTABLE)  # stands in for schg, which needs root
            build_home(home, PLAN, agentlint_home / "v")
        assert any("could not be deleted" in note for note in raised.value.__notes__)  # nor can the copy be
    finally:
        os.chflags(locked, 0)
        remove_home(agentlint_home / "v")


def test_a_directory_that_cant_be_searched_fails_the_build_rather_than_keep_a_link_out(tmp_path: Path,
                                                                                      agentlint_home: Path) -> None:
    home = fake_home(tmp_path)
    secret = write(tmp_path / "secret", "outside")
    write(home / ".conf" / "odd" / "x.md", "x")
    os.symlink(secret, home / ".conf" / "odd" / "out")
    os.chmod(home / ".conf" / "odd", 0o644)  # listable, but no entry in it can be looked at
    try:
        with pytest.raises(VariantError, match=r"odd in the variant can't be searched"):
            build_home(home, PLAN, agentlint_home / "v")
        assert not os.path.lexists(agentlint_home / "v")
    finally:
        os.chmod(home / ".conf" / "odd", 0o755)
