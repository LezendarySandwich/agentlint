"""Instruction files the harness delivers at session start.

Discovery follows the 2.1.285 loader. VERIFIED 2026-10-02 against the ``instructions`` attachment
of every local session for the cases that exist on this machine — managed ``claudeMd``, a project
``CLAUDE.md``, a project ``AGENTS.md`` fallback, and the auto-memory index. The rest is from the
harness code and not run-verified here (no such files exist locally): ancestor walking,
``.claude/CLAUDE.md``, ``CLAUDE.local.md``, ``rules/`` directories, and the managed directory.

- Managed: ``claudeMd`` in managed settings; ``CLAUDE.md`` and ``.claude/rules/**`` in the managed
  directory.
- User: ``~/.claude/CLAUDE.md`` and ``~/.claude/rules/**``.
- Project: for each directory from cwd up to (not including) ``/``: ``CLAUDE.md``,
  ``.claude/CLAUDE.md``, ``.claude/rules/**`` (Project) and ``CLAUDE.local.md`` (Local).
- ``AGENTS.md`` (or ``.claude/AGENTS.md``) at cwd, only when cwd has no ``CLAUDE.md`` of its own —
  the default mode of a built-in mod.
- Auto memory: ``~/.claude/projects/<cwd slug>/memory/MEMORY.md``, fixed at session start.

Not yet handled: ``@path`` imports (none exist locally to verify against).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agentlint.adapters.claude_code.discovery import Paths, SettingsLayer
from agentlint.adapters.claude_code.points import Point
from agentlint.config.taxonomy import Authority, InjectionPoint

MAX_BYTES = 4 * 1024 * 1024  # larger files are skipped by the harness
EMBEDDED_KEY = "claudeMd"  # the managed settings key whose value is instruction text


@dataclass(frozen=True)
class Instructions:
    path: Path  # the file, or the managed settings file for embedded text
    display_path: str  # the path as users see it, ``#claudeMd`` after the settings file for embedded text; unique per
    # source, and part of its id
    text: str
    point: InjectionPoint
    authority: Authority
    harness_label: str  # "Managed" | "User" | "Project" | "Local" | "AutoMem"
    embedded: bool = False  # text inside a settings file rather than a file of its own

    @property
    def anchor(self) -> str | None:
        """Where in ``path`` the text is: the settings key for embedded text, else None (the file is the text)."""
        return EMBEDDED_KEY if self.embedded else None


def project_slug(cwd: Path) -> str:
    """Directory name the harness uses under ~/.claude/projects for a working directory."""
    return re.sub(r"[^A-Za-z0-9-]", "-", str(cwd))


def _read(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        text = path.read_text(encoding="utf-8", errors="replace")  # a stray byte mustn't hide the file
    except OSError:
        return None
    return text if text.strip() else None


def _file(path: Path, point: Point, authority: Authority, label: str) -> Instructions | None:
    text = _read(path)
    return Instructions(path, str(path), text, point.value, authority, label) if text else None


def _rules(directory: Path, authority: Authority, label: str) -> list[Instructions]:
    rules = directory / "rules"
    found = []
    for path in sorted(rules.rglob("*.md")) if rules.is_dir() else []:
        entry = _file(path, Point.CLAUDE_MD, authority, label)
        if entry:
            found.append(entry)
    return found


def _ancestors(cwd: Path) -> list[Path]:
    chain = [cwd, *cwd.parents]
    return [directory for directory in chain if directory != Path(directory.anchor)]


def instruction_files(paths: Paths, cwd: Path, managed: SettingsLayer | None) -> list[Instructions]:
    found: list[Instructions] = []
    embedded = managed.data.get(EMBEDDED_KEY) if managed else None
    if managed and isinstance(embedded, str) and embedded.strip():
        found.append(Instructions(managed.path, f"{managed.path}#{EMBEDDED_KEY}", embedded,
                                  Point.MANAGED_INSTRUCTIONS.value, Authority.MANAGED, "Managed", embedded=True))
    managed_dir = paths.managed_settings.parent
    for entry in [_file(managed_dir / "CLAUDE.md", Point.CLAUDE_MD, Authority.MANAGED, "Managed"),
                  *_rules(managed_dir / ".claude", Authority.MANAGED, "Managed")]:
        if entry:
            found.append(entry)
    for entry in [_file(paths.config / "CLAUDE.md", Point.CLAUDE_MD, Authority.USER, "User"),
                  *_rules(paths.config, Authority.USER, "User")]:
        if entry:
            found.append(entry)
    for directory in _ancestors(cwd):
        candidates = [
            _file(directory / "CLAUDE.md", Point.CLAUDE_MD, Authority.PROJECT, "Project"),
            _file(directory / ".claude" / "CLAUDE.md", Point.CLAUDE_MD, Authority.PROJECT, "Project"),
            *_rules(directory / ".claude", Authority.PROJECT, "Project"),
            _file(directory / "CLAUDE.local.md", Point.CLAUDE_MD, Authority.PROJECT, "Local"),
        ]
        found.extend(entry for entry in candidates if entry and entry.path.parent != paths.config)
    if not any((cwd / name).is_file() for name in ("CLAUDE.md", ".claude/CLAUDE.md")):
        for name in ("AGENTS.md", ".claude/AGENTS.md"):
            entry = _file(cwd / name, Point.AGENTS_MD, Authority.PROJECT, "Project")
            if entry:
                found.append(entry)
                break
    memory = _file(paths.projects / project_slug(cwd) / "memory" / "MEMORY.md", Point.MEMORY_INDEX,
                   Authority.USER, "AutoMem")
    if memory:
        found.append(memory)
    unique: dict[str, Instructions] = {}
    for entry in found:
        unique.setdefault(entry.display_path, entry)
    return list(unique.values())
