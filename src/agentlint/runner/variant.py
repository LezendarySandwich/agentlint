"""Variant homes: a throwaway copy of the harness's home, under agentlint's own, that a run gets as its HOME
(docs/tech_impl/05-runner.md "Variants").

What is copied is the adapter's to say (``CopyPlan``): the configuration, never the transcripts, history or caches
beside it. Each entry is copied with one clonefile(2) call where the filesystem has it (APFS: copy-on-write, a
directory of thousands of files in a fraction of a second) and by an ordinary copy where it doesn't. Every path
written — the home's directories, the copies, the links re-pointed, the files a cut rewrites or removes — is checked
first to lie under agentlint's home and under the variant's own root: a variant can never write into the home it
copies. A symbolic link is never followed out of the copy: one that leads to something the copy holds is re-pointed
at the copy's own, and one that leads anywhere else is removed and reported. The copy keeps the source's modes but
not its owner lock flags (Finder's "Locked"), which would stop a cut or a deletion.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import json
import os
import shutil
import stat
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

from agentlint import store
from agentlint.config.content import FileText, content_hash, file_text
from agentlint.config.locators import KeyPath
from agentlint.config.model import Ablation, Source
from agentlint.config.formats import ContentFormat
from agentlint.config.taxonomy import Authority
from agentlint.rank.plan import AblationTarget

if TYPE_CHECKING:
    from agentlint.harness import VariantHomes


class VariantError(Exception):
    """A variant home could not be built or cut as asked. Nothing ran against it."""


class UnsafePath(VariantError):
    """A write would land outside agentlint's home or outside the variant's own root."""


@dataclass(frozen=True)
class CopyEntry:
    """One entry of the home to copy: a file, or a directory less the children named in ``skip``."""

    name: str  # a child of the home
    skip: frozenset[str] = frozenset()  # children of a directory entry left out: history, transcripts, caches

    def __post_init__(self) -> None:
        for part in (self.name, *self.skip):
            if not part or part in (".", "..") or "/" in part or "\0" in part:
                raise ValueError(f"a copy plan names single path components, got {part!r}")


@dataclass(frozen=True)
class CopyPlan:
    """What of a home a variant copies. Declared by the adapter, because which files are configuration is the
    harness's to know; core only follows it."""

    entries: tuple[CopyEntry, ...]

    def __post_init__(self) -> None:
        names = [entry.name for entry in self.entries]
        if not names or len(set(names)) != len(names):
            raise ValueError("a copy plan names at least one entry, each once")

    def copies(self, relative: PurePath) -> bool:
        """Whether a path, relative to the home, is inside what this plan copies."""
        parts = relative.parts
        if not parts or relative.is_absolute() or ".." in parts:
            return False
        entry = next((e for e in self.entries if e.name == parts[0]), None)
        return entry is not None and not (len(parts) > 1 and parts[1] in entry.skip)

    def skips(self, relative: PurePath) -> bool:
        """Whether a path, relative to the home, is under a child an entry leaves out (history, transcripts)."""
        parts = relative.parts
        if len(parts) < 2 or relative.is_absolute() or ".." in parts:
            return False
        return any(e.name == parts[0] and parts[1] in e.skip for e in self.entries)


@dataclass(frozen=True)
class Link:
    path: str  # where the link is, relative to the variant home
    target: str  # what it pointed at, as written


@dataclass(frozen=True)
class BuiltHome:
    """A variant home as built: nothing removed yet."""

    root: Path  # the variant home: a run's HOME
    source: Path  # the home it copies
    plan: CopyPlan
    copied: tuple[str, ...]  # entries copied
    missing: tuple[str, ...]  # entries the plan names that the home doesn't have
    cloned: bool  # every copy was a clonefile(2); False when any fell back to an ordinary copy
    relinked: tuple[Link, ...]  # links re-pointed at the copy's own target
    unlinked: tuple[Link, ...]  # links that led out of the copy, removed
    seconds: float = field(compare=False)


def _real(path: Path | str) -> Path:
    return Path(os.path.realpath(path))


def _inside(path: Path, root: Path) -> bool:
    """Whether ``path`` lies strictly under ``root``, its parent's links resolved and the path itself not: a link is
    judged by where it is, so removing one is never a write to its target."""
    if path.name in ("", ".", ".."):
        return False
    return (_real(path.parent) / path.name).is_relative_to(_real(root)) and _real(path.parent) / path.name != _real(root)


def guard(path: Path, root: Path) -> Path:
    """``path``, checked to lie under ``root`` and under agentlint's home; raises UnsafePath otherwise. Every write
    a variant makes passes through here first."""
    if not (_inside(path, root) or _real(path.parent) / path.name == _real(root)):
        raise UnsafePath(f"refusing to write {path}: it is outside the variant at {root}")
    if not _inside(path, store.home()):
        raise UnsafePath(f"refusing to write {path}: it is outside agentlint's home {store.home()}")
    return path


@contextmanager
def _opened(directory: Path, root: Path) -> Iterator[None]:
    """``directory`` of a variant home with owner write and search for as long as entries are added to or removed
    from it, its own mode put back after. A copy keeps the source's modes, so a read-only directory of the home is a
    read-only directory of the copy; the copy stays faithful to it."""
    real = guard(_real(directory), root)
    mode = stat.S_IMODE(os.lstat(real).st_mode)
    if mode & (stat.S_IWUSR | stat.S_IXUSR) == stat.S_IWUSR | stat.S_IXUSR:
        yield
        return
    os.chmod(real, mode | stat.S_IWUSR | stat.S_IXUSR)
    try:
        yield
    finally:
        if os.path.isdir(real):
            os.chmod(real, mode)


# --- copying ----------------------------------------------------------------------------------------------------

_CLONE_NOFOLLOW = 0x0001  # a link is cloned as a link, never followed
_CLONE_NOOWNERCOPY = 0x0002  # the copy belongs to whoever makes it
_UNSUPPORTED = {errno.ENOTSUP, errno.EXDEV, errno.ENOSYS, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}


def _load_clonefile() -> Callable[[bytes, bytes, int], int] | None:
    if sys.platform != "darwin":
        return None
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.dylib", use_errno=True)
        function = libc.clonefile
    except (OSError, AttributeError):
        return None
    function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
    function.restype = ctypes.c_int
    return function


_clonefile = _load_clonefile()


def clone(src: Path, dst: Path) -> bool:
    """Copy ``src`` (a file, a link or a whole directory) to ``dst`` with one clonefile(2) call. False when this
    system or filesystem can't clone, so the caller copies instead; any other failure raises."""
    if _clonefile is None:
        return False
    if _clonefile(os.fsencode(src), os.fsencode(dst), _CLONE_NOFOLLOW | _CLONE_NOOWNERCOPY) == 0:
        return True
    code = ctypes.get_errno()
    if code in _UNSUPPORTED:
        return False
    raise OSError(code, os.strerror(code), str(src))


def _ordinary_copy(src: Path, dst: Path) -> None:
    """The fallback off APFS: links copied as links, sockets and pipes left out."""
    mode = os.lstat(src).st_mode
    if stat.S_ISLNK(mode):
        os.symlink(os.readlink(src), dst)
    elif stat.S_ISDIR(mode):
        def specials(directory: str, names: list[str]) -> set[str]:
            kept = set()
            for name in names:
                kind = os.lstat(os.path.join(directory, name)).st_mode
                if not (stat.S_ISREG(kind) or stat.S_ISDIR(kind) or stat.S_ISLNK(kind)):
                    kept.add(name)
            return kept

        shutil.copytree(src, dst, symlinks=True, ignore=specials)
    elif stat.S_ISREG(mode):
        shutil.copy2(src, dst, follow_symlinks=False)


def _copy(src: Path, dst: Path, root: Path) -> bool:
    guard(dst, root)
    if clone(src, dst):
        return True
    _ordinary_copy(src, dst)
    return False


# --- links ------------------------------------------------------------------------------------------------------

def _in_copy(target: str, source: Path, plan: CopyPlan) -> PurePath | None:
    """Where a link's target is in the copy, relative to the home, when the plan copies it; else None. The home is
    tried as written and resolved, and each directory entry as resolved too, so a home or entry that is itself a
    link still counts as the home."""
    bases: list[tuple[Path, PurePath]] = [(source, PurePath()), (_real(source), PurePath())]
    for entry in plan.entries:
        bases.append((_real(source / entry.name), PurePath(entry.name)))
    for base, prefix in bases:
        if Path(target).is_relative_to(base):
            relative = prefix / Path(target).relative_to(base)
            if plan.copies(relative):
                return relative
    return None


# Finder's "Locked" (uchg) and append-only (uappnd): flags the owner may clear, which a copy carries over and which
# stop a cut replacing a file or a deletion removing it
_USER_LOCKS = stat.UF_IMMUTABLE | stat.UF_APPEND
# flags only the superuser may clear: a home holding one can't be copied into something agentlint can change or delete
_SYSTEM_LOCKS = stat.SF_IMMUTABLE | stat.SF_APPEND | getattr(stat, "SF_NOUNLINK", 0)


def _unlock(path: Path, info: os.stat_result, root: Path) -> None:
    """Clear the owner's lock flags on ``path`` of the variant ``root`` (not following a link); a system lock raises
    VariantError. Systems without file flags have nothing to clear."""
    flags = getattr(info, "st_flags", 0)
    if flags & _SYSTEM_LOCKS:
        raise VariantError(f"{path} carries a system immutable or append-only flag, which only the superuser can "
                           "clear: a variant home holding it could be neither cut nor deleted")
    if flags & _USER_LOCKS:
        os.lchflags(guard(path, root), flags & ~_USER_LOCKS)


def _settle(root: Path) -> list[str]:
    """Every symbolic link in the copy ``root``, every lock flag the copy carried over cleared on the way. Refuses
    (raises) rather than skip a directory whose entries can't be examined: one that can't be listed, or one that
    can't be searched, where a link can be named but not looked at."""
    links: list[str] = []
    def unreadable(error: OSError) -> None:  # a directory that can't be listed may hold a link out: refuse, never skip
        raise error

    for directory, dirs, files in os.walk(root, onerror=unreadable):  # never descends a linked directory
        if not os.lstat(directory).st_mode & stat.S_IXUSR:
            raise VariantError(f"{directory} in the variant can't be searched (no owner execute in its mode), so the "
                               "links in it can't be checked to lead inside the copy")
        for name in dirs + files:
            path = os.path.join(directory, name)
            info = os.lstat(path)
            _unlock(Path(path), info, root)
            if stat.S_ISLNK(info.st_mode):
                links.append(path)
    return links


def _contain_links(root: Path, source: Path, plan: CopyPlan, links: list[str]) -> tuple[list[Link], list[Link]]:
    relinked: list[Link] = []
    unlinked: list[Link] = []
    for path in links:
        written = os.readlink(path)
        relative = os.path.relpath(path, root)
        # where the link leads in the home itself, every link on the way followed: "dl/../x" through a linked dl is
        # wherever dl leads, then up — collapsing ".." on the string would land on a different file in the copy
        target = os.path.realpath(os.path.join(os.path.dirname(os.path.join(source, relative)), written))
        inside = _in_copy(target, source, plan)
        with _opened(Path(path).parent, root):
            os.unlink(guard(Path(path), root))
            if inside is not None:
                os.symlink(root / inside, path)
        (unlinked if inside is None else relinked).append(Link(relative, written))
    for path in links:  # whatever a chain of links reaches, it reaches inside the copy
        if os.path.islink(path) and not _real(path).is_relative_to(_real(root)):
            relative = os.path.relpath(path, root)
            written = next(link.target for link in relinked if link.path == relative)
            with _opened(Path(path).parent, root):
                os.unlink(guard(Path(path), root))
            relinked = [link for link in relinked if link.path != relative]
            unlinked.append(Link(relative, written))
    return relinked, unlinked


# --- building ---------------------------------------------------------------------------------------------------

def _check_places(source: Path, dest: Path, plan: CopyPlan) -> None:
    home = _real(store.home())
    if not _inside(dest, home):
        raise UnsafePath(f"a variant home is built under agentlint's home {home}, not at {dest}")
    if os.path.lexists(dest):
        raise VariantError(f"{dest} already exists: a variant home is built fresh, never reused")
    for entry in plan.entries:
        copied = _real(source / entry.name)
        if _real(dest.parent).is_relative_to(copied) or home.is_relative_to(copied):
            raise UnsafePath(f"agentlint's home {home} is inside {copied}, which a variant copies: it would copy itself")
    if _real(source).is_relative_to(_real(dest.parent) / dest.name):
        raise UnsafePath(f"the home {source} is inside the variant {dest}")


def build_home(source: Path, plan: CopyPlan, dest: Path) -> BuiltHome:
    """A fresh variant home at ``dest`` (under agentlint's home, not yet existing, made mode 0700) holding what
    ``plan`` copies of ``source``. Only reads ``source``."""
    started = time.monotonic()
    source, dest = Path(os.path.abspath(source)), Path(os.path.abspath(dest))
    _check_places(source, dest, plan)
    store.private_dirs(guard(dest, dest), exist_ok=False)  # it and each directory made above it 0700, whatever the umask
    try:
        return _fill(source, plan, dest, started)
    except BaseException as error:
        _cleanup(dest, error)  # half a home is no home
        raise


def _fill(source: Path, plan: CopyPlan, dest: Path, started: float) -> BuiltHome:
    copied: list[str] = []
    missing: list[str] = []
    cloned = True
    for entry in plan.entries:
        src = source / entry.name
        if not os.path.exists(src):  # missing, or a link that leads nowhere
            missing.append(entry.name)
            continue
        if os.path.islink(src):  # an entry kept elsewhere (a dotfile manager's link): the copy holds what it leads to,
            src = _real(src)     # never a link to itself
        if entry.skip and os.path.isdir(src):  # each child its own clone, the skipped ones left behind
            store.private_dirs(guard(dest / entry.name, dest), exist_ok=False)
            for name in sorted(os.listdir(src)):
                if name not in entry.skip:
                    cloned &= _copy(src / name, dest / entry.name / name, dest)
            shutil.copystat(src, dest / entry.name, follow_symlinks=True)  # last: the source's mode may be read-only
        else:
            cloned &= _copy(src, dest / entry.name, dest)
        copied.append(entry.name)
    relinked, unlinked = _contain_links(dest, source, plan, _settle(dest))
    return BuiltHome(dest, source, plan, tuple(copied), tuple(missing), cloned, tuple(relinked), tuple(unlinked),
                     time.monotonic() - started)


def _make_writable(root: Path, tree: Path | None = None) -> None:
    """Give the owner rwx on every directory of ``tree`` (a variant home ``root`` by default) and clear the owner's
    lock flags on every entry, links left alone: a copy keeps the source's modes, an entry of a read-only directory
    can't be deleted, and a run may have locked a file since the home was built."""
    tree = root if tree is None else tree

    def open_up(path: Path, directory: bool) -> None:
        info = os.lstat(path)
        _unlock(path, info, root)
        if directory and not stat.S_ISLNK(info.st_mode):
            os.chmod(guard(path, root), stat.S_IMODE(info.st_mode) | stat.S_IRWXU)

    open_up(tree, True)
    for directory, dirs, files in os.walk(tree):  # top-down: each directory is opened before it is listed
        for name in dirs:
            open_up(Path(directory) / name, True)
        for name in files:
            open_up(Path(directory) / name, False)


def remove_home(root: Path) -> None:
    """Delete a variant home, read-only directories copied from the source included: links are removed, never
    followed."""
    if not os.path.lexists(root):
        return
    _delete_tree(root, root)


def _delete_tree(tree: Path, root: Path) -> None:
    """Delete the directory ``tree`` of the variant home ``root``; when a read-only directory copied from the source
    or a locked entry stops it, give the owner write and clear lock flags throughout and delete again."""
    try:
        shutil.rmtree(guard(tree, root))
    except PermissionError:
        _make_writable(root, tree)
        shutil.rmtree(tree)


def _cleanup(root: Path, error: BaseException) -> None:
    """Delete a half-built home after ``error``; should that fail too, say so on ``error``, which is still raised."""
    try:
        remove_home(root)
    except Exception as failed:  # noqa: BLE001 - the original error is the one to raise
        error.add_note(f"the half-built variant home {root} could not be deleted either: {failed!r}")


# --- what else a run gets: the project's configuration ------------------------------------------------------

@dataclass(frozen=True)
class Placement:
    """One more copy a run gets beside its home: what of the directory ``source`` (``plan``) goes to ``dest``. The
    adapter says which (``VariantHomes.placements``): for a run in an empty directory, the project's configuration
    copied into it and the project's memory into the variant home under that directory's name (D1)."""

    source: Path  # a directory on this machine
    plan: CopyPlan
    dest: Path  # absolute: inside the variant home, or the run's working directory


@dataclass(frozen=True)
class EntryCut:
    """How to remove one entry of a JSON file the harness may read from more than one copy (a package's hook, D3):
    every copy, as paths under the home a variant copies — first the copy the harness loads (the source's own file,
    spelled as the adapter's reader of a copied home finds its copy, which may differ from the reading's resolved
    path), then the others, cut too so that a harness version reading one of them loses the entry as well — and how to find
    the entry in one copy's parsed JSON: its key path, or None when the entry there is not the one that was read."""

    files: tuple[Path, ...]
    locate: Callable[[object], KeyPath | None]


# --- cutting a target out ---------------------------------------------------------------------------------------

class TargetNotSupported(VariantError):
    """No variant can be built for this target yet: what it is, and what it waits on."""


class SourceChanged(VariantError):
    """The file in the copy is gone or is not the text the target was read from: the configuration changed between
    reading it and copying it. (A file a link leads to outside the copy is refused before copying, by ``removable``,
    as TargetNotSupported.)"""


@dataclass(frozen=True)
class Cut:
    """What a variant took out of its run's copies for one target."""

    target_id: str
    rewritten: tuple[str, ...] = ()  # files rewritten with the target's region or entry excised, relative to the copy
    removed: tuple[str, ...] = ()  # files or directories deleted, relative to the copy they were in


def _place(source: Source) -> str:
    file = source.provenance.file
    return f"{file}#{source.provenance.anchor}" if file and source.provenance.anchor else str(file or source.id)


@dataclass(frozen=True)
class Located:
    """A file of the configuration and where a run's copy of it is: ``copy.dest / relative``."""

    copy: Placement
    relative: PurePath

    @property
    def path(self) -> Path:
        return self.copy.dest / self.relative


def _locate(file: Path, layout: tuple[Placement, ...], place: str) -> Located:
    """Where ``file`` is copied for a run laid out as ``layout`` (the home first, then the adapter's placements); the
    deepest placement whose source holds it wins, so a project's memory beats the home that skips it. Raises
    TargetNotSupported when no copy holds it."""
    file = Path(os.path.abspath(file))
    skipped = False
    for copy in sorted(layout, key=lambda c: len(Path(os.path.abspath(c.source)).parts), reverse=True):
        for base in (Path(os.path.abspath(copy.source)), _real(copy.source)):
            if file.is_relative_to(base):
                relative = PurePath(file.relative_to(base))
                if copy.plan.copies(relative):
                    return Located(copy, relative)
                skipped |= copy.plan.skips(relative)
    if skipped:
        raise TargetNotSupported(f"{place} is kept beside history a run doesn't get a copy of (a project's "
                                 "transcripts, or another project's memory): only what the run copies can be removed")
    raise TargetNotSupported(f"{place} is outside what this variant copies (the home {layout[0].source}"
                             + (", the project and its memory" if len(layout) > 1 else "; no project is copied") + ")")


def _check_source(source: Source, layout: tuple[Placement, ...],
                  entry_cut: Callable[[Source], EntryCut | None]) -> Located:
    provenance = source.provenance
    place = _place(source)
    if source.ablation.mechanism is Ablation.NONE:
        raise TargetNotSupported(f"{place} can't be removed: {source.ablation.blocked_reason}")
    entry = entry_cut(source)
    if provenance.package is not None and entry is None:
        raise TargetNotSupported(f"{place} comes from the package {provenance.package}: a variant removes a package's "
                                 "hook by editing its copies (D3); removing a package's skill, agent or other piece "
                                 "isn't built")
    if provenance.file is None:
        raise TargetNotSupported(f"{place} is in no file a variant could change")
    if provenance.authority not in (Authority.USER, Authority.PROJECT, Authority.PACKAGE):
        raise TargetNotSupported(f"{place} is {provenance.authority.value}-level: a variant removes only user-level, "
                                 "project-level and package sources")
    # an entry cut's first file is the source's own, spelled as the adapter's reader of a copied home finds its copy
    return _locate(entry.files[0] if entry is not None and entry.files else provenance.file, layout, place)


def _read_copy(path: Path, source: Source, *, rewrite: bool) -> str:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise SourceChanged(f"{path} is not in the variant: {error.strerror}") from error
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:  # the reader decoded it leniently; writing that back would change other bytes
        if rewrite:
            raise TargetNotSupported(f"{_place(source)} is not valid UTF-8: rewriting it would change more than the "
                                     "target") from error
        text = data.decode("utf-8", errors="replace")
    if content_hash(text) != source.declared_hash:
        raise SourceChanged(f"{_place(source)} changed since it was read: its copy is not the text the target is in")
    return text


def _write(path: Path, text: str, root: Path) -> None:
    """Replace ``path`` in the variant with a new file holding ``text``: a link there is replaced, never written
    through, and a read-only copy keeps its mode without having to be writable."""
    guard(path, root)
    if _real(path.parent) != _real(root) / path.parent.relative_to(root):
        raise UnsafePath(f"{path} is reached through a link: rewriting it would change what the link leads to")
    mode = None if path.is_symlink() else stat.S_IMODE(os.lstat(path).st_mode)
    fresh = guard(path.with_name(f".{path.name}.agentlint-cut"), root)
    with _opened(path.parent, root):
        handle = os.open(fresh, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(text)
            if mode is not None:
                os.chmod(fresh, mode)
            os.replace(fresh, path)  # a rename: replaces a link itself, never what it leads to
        except BaseException:
            fresh.unlink(missing_ok=True)
            raise


def _remove(path: Path, root: Path) -> None:
    guard(path, root)
    with _opened(path.parent, root):
        if path.is_symlink() or not path.is_dir():
            path.unlink()
        else:
            _delete_tree(path, root)


def _linked_out(located: Located, place: str) -> None:
    """Raise TargetNotSupported when ``located`` (a path its copy's plan copies) really leads out of that copy through
    a link on the way: the copy would hold no such file, since links out of it are removed. Only resolves links;
    reads no file."""
    source, plan, relative = Path(os.path.abspath(located.copy.source)), located.copy.plan, located.relative
    if _in_copy(os.path.realpath(source / relative), source, plan) is not None:
        return
    for end in range(1, len(relative.parts) + 1):
        step = source.joinpath(*relative.parts[:end])
        if os.path.islink(step) and _in_copy(os.path.realpath(step), source, plan) is None:
            raise TargetNotSupported(f"{place}: {step} is a link to {os.readlink(step)}, outside the copy; a variant "
                                     "home doesn't follow links out of it")
    raise TargetNotSupported(f"{place}: {source / relative} leads to {os.path.realpath(source / relative)}, outside the "
                             "copy; a variant home doesn't follow links out of it")


@dataclass(frozen=True)
class _Edits:
    file: Located  # the target's own file
    remove: tuple[Located, ...] = ()  # FILE_REMOVAL: what to delete
    entries: tuple[Located, ...] = ()  # an entry cut: every copy, the one the harness loads first
    entry: EntryCut | None = None


def _edits(target: AblationTarget, layout: tuple[Placement, ...], removal: Callable[[Source], tuple[Path, ...]],
           entry_cut: Callable[[Source], EntryCut | None]) -> _Edits:
    source = target.source
    place = _place(source)
    located = _check_source(source, layout, entry_cut)
    _linked_out(located, place)
    mechanism = source.ablation.mechanism
    if mechanism is Ablation.SPAN_EXCISION:
        entry = entry_cut(source)
        if entry is not None:
            copies = []
            for file in entry.files:
                copy = _locate(file, layout, place)
                _linked_out(copy, place)
                copies.append(copy)
            return _Edits(located, entries=tuple(copies), entry=entry)
        if target.whole or not isinstance(source.content, FileText) or source.provenance.anchor is not None:
            raise TargetNotSupported(f"{place} is removed by cutting its entry out of the file that configures it, "
                                     "which a variant does only for a package's hook so far")
        return _Edits(located)
    if mechanism is Ablation.FILE_REMOVAL:
        paths = []
        for path in removal(source):
            copy = _locate(path, layout, place)
            _linked_out(copy, place)  # a link itself is removable; a link that leads out of the copy isn't in it
            paths.append(copy)
        if not paths:
            raise TargetNotSupported(f"the adapter can't say which files make up {place}")
        return _Edits(located, remove=tuple(paths))
    raise TargetNotSupported(f"{place} is removed by {mechanism.value}, which a variant home doesn't do yet")


def removable(target: AblationTarget, home: Path, plan: CopyPlan, removal: Callable[[Source], tuple[Path, ...]],
              placements: tuple[Placement, ...] = (),
              entry_cut: Callable[[Source], EntryCut | None] = lambda source: None) -> None:
    """Raise TargetNotSupported, saying what it waits on, unless a variant of ``home`` (and the ``placements`` beside
    it) can remove ``target``. Reads no file, so every refusal comes before anything is copied."""
    _edits(target, (Placement(home, plan, home), *placements), removal, entry_cut)


def removal_refused(adapter: VariantHomes, target: AblationTarget, project: Path | None = None) -> str | None:
    """Why no variant can remove ``target`` yet, in the refusal's own words, or None when one can: what a test's plan
    asks before it plans a with/without comparison (D9), so a target a variant can't remove is tested for consistency
    only, and says why. Reads no file; the paths it places the copies at are never made."""
    unmade = Path(store.home()) / "runs.noindex" / "unmade"
    placements = adapter.placements(project, unmade / "home", unmade / "work") if project is not None else ()
    try:
        removable(target, adapter.home_dir(), adapter.copy_plan(), adapter.removal, placements, adapter.entry_cut)
    except VariantError as refused:
        return str(refused)
    return None


def _cut_entry(copy: Located, entry: EntryCut, source: Source, root: Path, *, loaded: bool) -> bool:
    """Excise the entry from one copy. The copy the harness loads must hold it (SourceChanged otherwise); another copy
    that doesn't, or can't be read, is left as it is — it isn't what loads — and False is returned."""
    path = copy.path
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        if not loaded:
            return False
        raise SourceChanged(f"{path} in the variant can't be read as JSON: {error}") from error
    key = entry.locate(data)
    if key is None:
        if not loaded:
            return False
        raise SourceChanged(f"{_place(source)}: its copy at {path}, the one the harness loads, holds no such entry — "
                            "it differs from what was read")
    _write(path, file_text(ContentFormat.JSON, text).excise(key), root)
    return True


def cut(built: BuiltHome, target: AblationTarget, removal: Callable[[Source], tuple[Path, ...]],
        placements: tuple[Placement, ...] = (),
        entry_cut: Callable[[Source], EntryCut | None] = lambda source: None) -> Cut:
    """Remove ``target`` from a freshly built run — its home ``built`` and the ``placements`` already copied beside it
    — by the method its source's ablation names: a region excised from the copy of its file (validated by the
    content's own ``excise``), the source's files deleted (``removal`` says which), or, for a package's hook, its
    entry excised from the copy of the file declaring it that the harness loads and from every other copy that holds
    it (``entry_cut``). Everything else raises TargetNotSupported
    with what it waits on. Only the run's copies are written."""
    source = target.source
    layout = (Placement(built.source, built.plan, built.root), *placements)
    edits = _edits(target, layout, removal, entry_cut)
    if edits.entry is not None:
        loaded, *others = edits.entries
        if not os.path.lexists(loaded.path):
            raise SourceChanged(f"{loaded.copy.source / loaded.relative} is not in the variant")
        rewritten = [loaded] if _cut_entry(loaded, edits.entry, source, loaded.copy.dest, loaded=True) else []
        rewritten += [copy for copy in others if os.path.lexists(copy.path)
                      and _cut_entry(copy, edits.entry, source, copy.copy.dest, loaded=False)]
        return Cut(target.id, rewritten=tuple(str(copy.relative) for copy in rewritten))
    if source.ablation.mechanism is Ablation.SPAN_EXCISION:
        (rule,) = target.rules
        _read_copy(edits.file.path, source, rewrite=True)
        _write(edits.file.path, source.content.excise(rule.locator), edits.file.copy.dest)
        return Cut(target.id, rewritten=(str(edits.file.relative),))
    if isinstance(source.content, FileText):
        _read_copy(edits.file.path, source, rewrite=False)
    for copy in edits.remove:
        if not os.path.lexists(copy.path):
            raise SourceChanged(f"{copy.copy.source / copy.relative} is not in the variant")
    for copy in edits.remove:
        _remove(copy.path, copy.copy.dest)
    return Cut(target.id, removed=tuple(str(copy.relative) for copy in edits.remove))


@dataclass(frozen=True)
class Variant:
    home: BuiltHome
    cut: Cut | None  # None for the baseline: nothing removed
    placed: tuple[BuiltHome, ...] = ()  # the copies beside the home: the project's configuration, its memory
    workdir: Path | None = None  # the run's working directory, when the variant was built for a project

    def path_pairs(self) -> tuple[tuple[Path, Path], ...]:
        """(copy, what it copies) for every copy beside the home, to map a reading's paths back to the machine's."""
        return tuple((placed.root, placed.source) for placed in self.placed)


def build_variant(adapter: VariantHomes, dest: Path, target: AblationTarget | None, *, project: Path | None = None,
                  workdir: Path | None = None) -> Variant:
    """A variant home at ``dest`` for ``target`` — the baseline when None — and, given a ``project``, the run's empty
    working directory ``workdir`` with the project's configuration copied in as the adapter places it (D1). A target
    no variant can remove yet is refused before anything is copied (``removable``)."""
    if (project is None) != (workdir is None):
        raise ValueError("a project and a working directory come together")
    placements = adapter.placements(project, dest, workdir) if project is not None and workdir is not None else ()
    if target is not None:
        removable(target, adapter.home_dir(), adapter.copy_plan(), adapter.removal, placements, adapter.entry_cut)
    built = build_home(adapter.home_dir(), adapter.copy_plan(), dest)
    placed: list[BuiltHome] = []
    try:
        if workdir is not None and not any(_real(p.dest) == _real(workdir) for p in placements):
            store.private_dirs(guard(workdir, workdir), exist_ok=False)
        for placement in placements:
            placed.append(build_home(placement.source, placement.plan, placement.dest))
        done = (cut(built, target, adapter.removal, placements, adapter.entry_cut) if target is not None else None)
        return Variant(built, done, tuple(placed), Path(workdir) if workdir is not None else None)
    except BaseException as error:
        _cleanup(built.root, error)  # half a variant is no variant
        if workdir is not None and os.path.lexists(workdir):
            _cleanup(Path(workdir), error)
        raise
