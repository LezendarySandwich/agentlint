"""Skills, commands and agents defined on disk, named the way the harness names them.

Rules VERIFIED 2026-10-02 against the delivered skill listing of a clean 2.1.285 session (the
"listing oracle", see tests and docs/tech_impl/01-config-reader.md):

- Package skills: the immediate children of ``skills/`` that hold a ``SKILL.md``, plus every path
  the package manifest lists under ``skills`` — a listed path holding a ``SKILL.md`` is itself a
  skill (even the package root, listed as ``./``), otherwise its immediate children are searched.
  Nothing deeper loads: ``skills/group/x/SKILL.md`` and ``skills/x/sub/SKILL.md`` are ignored. Named
  ``<package>:<directory name>`` — the directory, not the frontmatter ``name`` — except a
  ``SKILL.md`` sitting directly in a ``skills/`` root, which takes its frontmatter name (or the
  package name).
- Package commands: ``commands/**/*.md`` plus manifest-listed paths. Named
  ``<package>:<path segments joined by ':'>``.
- Package workflows: ``workflows/*.workflow.js``, named ``<package>:<meta name>``.
- User and project skills/commands (``~/.claude``, ``<cwd>/.claude``): the same layout, bare names.
- ``disable-model-invocation: true`` keeps any of these out of the model's listing; a command also
  needs a description to be listed.
- Package agents: the paths the manifest lists under ``agents`` *replace* the default ``agents/``
  directory (unlike skills, which the list supplements). Named ``<package>:<sub-directories>:<name>``
  with the frontmatter ``name`` (or the file stem). User and project agents use bare names.
  Verified against the delivered agent listing the same way.
- The harness also lists built-in skills that exist only in its binary; disk cannot show those.

Frontmatter is read strictly first; when strict YAML fails (the harness is more lenient), simple
top-level ``key: value`` lines are read instead.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agentlint.adapters.claude_code.discovery import Discovery, Package, Paths
from agentlint.adapters.claude_code.points import Kind, Scope
from agentlint.config import frontmatter
from agentlint.config.regions import FormatError

_KEY_LINE = re.compile(r"^([A-Za-z][\w-]*):[ \t]*(.*)$")
_WORKFLOW_NAME = re.compile(r"""\bname\s*:\s*['"]([^'"]+)['"]""")



@dataclass(frozen=True)
class DiskEntity:
    kind: Kind  # SKILL, COMMAND, WORKFLOW or AGENT
    name: str  # as the harness names it
    path: Path  # the defining file
    text: str
    meta: dict[str, Any] = field(default_factory=dict)
    package: Package | None = None
    origin: Scope = Scope.PACKAGE  # PACKAGE, USER or PROJECT
    lenient: bool = False  # frontmatter needed the lenient reader

    @property
    def description(self) -> str | None:
        value = self.meta.get("description")
        return value if isinstance(value, str) and value.strip() else None

    @property
    def model_invocable(self) -> bool:
        if self.meta.get("disable-model-invocation") is True:
            return False
        return self.description is not None if self.kind is Kind.COMMAND else True


def read_frontmatter(text: str) -> tuple[dict[str, Any], bool]:
    """Parsed frontmatter, and whether the lenient fallback was needed."""
    try:
        return frontmatter.split(text).data, False
    except FormatError:
        pass
    lines = text.splitlines()
    data: dict[str, Any] = {}
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            match = _KEY_LINE.match(line)
            if match:
                value: Any = match.group(2).strip()
                data[match.group(1)] = {"true": True, "false": False}.get(value, value)
    return data, True


def _entity(kind: Kind, name: str, path: Path, package: Package | None, origin: Scope) -> DiskEntity | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")  # a stray byte mustn't make it "missing"
    except OSError:
        return None
    meta, lenient = read_frontmatter(text)
    return DiskEntity(kind, name, path, text, meta, package, origin, lenient)


def _listed(root: Path, entries: object) -> list[Path]:
    return [(root / entry).resolve() for entry in entries if isinstance(entry, str)] if isinstance(entries, list) else []


def _skill_dirs(root: Path, manifest_entries: object) -> list[Path]:
    found: dict[Path, None] = {}
    default = root / "skills"
    listed = _listed(root, manifest_entries)
    for start in [default, *listed]:
        if start in listed and (start / "SKILL.md").is_file():
            found[start] = None
        elif start.is_dir():
            for child in sorted(start.iterdir()):
                if child.is_dir() and (child / "SKILL.md").is_file():
                    found[child] = None
    return list(found)


def _skill_name(directory: Path, package: Package | None, meta: dict[str, Any]) -> str:
    if directory.name == "skills":
        declared = meta.get("name")
        return declared if isinstance(declared, str) and declared else (package.name if package else directory.name)
    return directory.name


def _command_files(root: Path, manifest_entries: object) -> list[tuple[Path, Path]]:
    """(file, the directory its name is relative to)."""
    found: dict[Path, Path] = {}
    default = root / "commands"
    if default.is_dir():
        for file in sorted(default.rglob("*.md")):
            found[file] = default
    for listed in _listed(root, manifest_entries):
        if listed.is_file() and listed.suffix == ".md":
            found.setdefault(listed, listed.parent)
        elif listed.is_dir():
            for file in sorted(listed.rglob("*.md")):
                found.setdefault(file, listed)
    return list(found.items())


def _command_name(prefix: str, file: Path, base: Path) -> str:
    return prefix + ":".join(file.relative_to(base).with_suffix("").parts)


def _from_root(root: Path, package: Package | None, origin: Scope, prefix: str) -> Iterable[DiskEntity]:
    manifest = package.manifest if package else {}
    for directory in _skill_dirs(root, manifest.get("skills")):
        entity = _entity(Kind.SKILL, "", directory / "SKILL.md", package, origin)
        if entity:
            yield replace(entity, name=prefix + _skill_name(directory, package, entity.meta))
    for file, base in _command_files(root, manifest.get("commands")):
        entity = _entity(Kind.COMMAND, _command_name(prefix, file, base), file, package, origin)
        if entity:
            yield entity
    workflows = root / "workflows"
    for file in sorted(workflows.glob("*.workflow.js")) if workflows.is_dir() else []:
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = _WORKFLOW_NAME.search(text[:4000])
        name = match.group(1) if match else file.name.removesuffix(".workflow.js")
        yield DiskEntity(Kind.WORKFLOW, prefix + name, file, text, {}, package, origin)


def _agent_files(root: Path, manifest_entries: object) -> list[tuple[Path, Path]]:
    listed = _listed(root, manifest_entries)
    starts = listed if listed else [root / "agents"]
    found: dict[Path, Path] = {}
    for start in starts:
        if start.is_file() and start.suffix == ".md":
            found.setdefault(start, start.parent)
        elif start.is_dir():
            for file in sorted(start.rglob("*.md")):
                found.setdefault(file, start)
    return list(found.items())


def _agents(root: Path, package: Package | None, origin: Scope, prefix: str) -> Iterable[DiskEntity]:
    manifest = package.manifest if package else {}
    for file, base in _agent_files(root, manifest.get("agents")):
        entity = _entity(Kind.AGENT, "", file, package, origin)
        if entity:
            declared = entity.meta.get("name")
            name = declared if isinstance(declared, str) and declared else file.stem
            yield replace(entity, name=prefix + ":".join([*file.relative_to(base).parent.parts, name]))


def disk_entities(discovery: Discovery, paths: Paths, cwd: Path) -> list[DiskEntity]:
    found: list[DiskEntity] = []
    for package in discovery.packages:
        found.extend(_from_root(package.root, package, Scope.PACKAGE, f"{package.name}:"))
        found.extend(_agents(package.root, package, Scope.PACKAGE, f"{package.name}:"))
    roots = [(paths.config, Scope.USER)]
    if (cwd / ".claude").resolve() != paths.config.resolve():
        roots.append((cwd / ".claude", Scope.PROJECT))
    for root, origin in roots:
        found.extend(_from_root(root, None, origin, ""))
        found.extend(_agents(root, None, origin, ""))
    return found
