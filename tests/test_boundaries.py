"""Core stays harness-agnostic (AGENTS.md non-negotiable #6, docs/tech_impl/09-adapters.md).

Two structural rules instead of a word ban:
- nothing outside adapters/ imports an adapter; cli.py, the composition root, is the one exception
- core never names a harness, and neither does the UI's code (ui/src, tests aside)

Harness-specific names live in each adapter's own enums of InjectionPoint / EntityKind values.
Shared concepts (hook, plugin, skill, MCP) are fine anywhere.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "agentlint"
UI_SOURCES = Path(__file__).resolve().parents[1] / "ui" / "src"  # minified into shell.html, comments stripped: read here
COMPOSITION_ROOT = {"cli.py"}
# "cursor" is deliberately absent: it is an ordinary word (database cursors, CSS) and would misfire.
# A name in lower, Capitalised or UPPER form, not inside a longer word: identifiers such as
# CLAUDE_PLUGIN_ROOT, ClaudeCodeAdapter or CODEX_HOME count; "claudette" does not.
_NAMES = ("claude", "codex", "copilot", "gemini")
HARNESS_NAMES = re.compile(
    r"(?<![A-Za-z])(?:" + "|".join(f"{n}|{n.capitalize()}|{n.upper()}" for n in _NAMES) + r")(?![a-z])")
SCANNED = {".py", ".html", ".js", ".css", ".toml"}


def core_files(suffixes: set[str]) -> Iterator[Path]:
    for path in sorted(PACKAGE.rglob("*")):
        relative = path.relative_to(PACKAGE)
        if (
            path.is_file()
            and path.suffix in suffixes
            and relative.parts[0] != "adapters"
            and str(relative) not in COMPOSITION_ROOT
        ):
            yield path


def imported_modules(path: Path, package_root: Path = PACKAGE) -> list[str]:
    """Absolute names of every module a file imports, resolving relative imports."""
    package = [package_root.name, *path.relative_to(package_root).parent.parts]
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            module = ".".join([*base, *([node.module] if node.module else [])])
            found.append(module)
            found.extend(f"{module}.{alias.name}" for alias in node.names)
    return found


def _is_adapter(module: str) -> bool:
    return module == "agentlint.adapters" or module.startswith("agentlint.adapters.")


def test_core_never_imports_an_adapter() -> None:
    files = list(core_files({".py"}))
    assert files, f"no core files under {PACKAGE}; the check would pass vacuously"
    offenders = [
        f"{path.relative_to(PACKAGE)} imports {module}"
        for path in files
        for module in imported_modules(path)
        if _is_adapter(module)
    ]
    assert not offenders, "core imports an adapter:\n" + "\n".join(offenders)


def test_core_never_names_a_harness() -> None:
    hits = [
        f"{path.relative_to(PACKAGE)}:{number}: {match.group(0)!r}"
        for path in core_files(SCANNED)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if (match := HARNESS_NAMES.search(line))
    ]
    assert not hits, "core names a harness:\n" + "\n".join(hits)


def ui_files() -> Iterator[Path]:
    """The UI's own code, tests aside: labels come from the data (/api/scope, the report), never from here."""
    for path in sorted(UI_SOURCES.rglob("*")):
        if path.is_file() and path.suffix in {".ts", ".tsx"} and ".test." not in path.name:
            yield path


def test_the_ui_never_names_a_harness() -> None:
    files = list(ui_files())
    assert files, f"no UI sources under {UI_SOURCES}; the check would pass vacuously"
    hits = [
        f"{path.relative_to(UI_SOURCES)}:{number}: {match.group(0)!r}"
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if (match := HARNESS_NAMES.search(line))
    ]
    assert not hits, "the UI names a harness:\n" + "\n".join(hits)


def test_checks_catch_planted_violations(tmp_path: Path) -> None:
    root = tmp_path / "agentlint"
    (root / "checks").mkdir(parents=True)
    absolute = root / "checks" / "a.py"
    absolute.write_text("from agentlint.adapters.harness_x import reader\nimport agentlint.adapters\n")
    relative = root / "checks" / "b.py"
    relative.write_text("from ..adapters import harness_x\n")
    assert [m for m in imported_modules(absolute, root) if _is_adapter(m)]
    assert "agentlint.adapters" in imported_modules(relative, root)
    assert HARNESS_NAMES.search("reads the Claude transcript")
    for identifier in ("CLAUDE_PLUGIN_ROOT", "ClaudeCodeAdapter", "CODEX_HOME", "claude_code", "copilot-cli"):
        assert HARNESS_NAMES.search(identifier), identifier
    assert not HARNESS_NAMES.search("a claudette, a cursor, a hook, a plugin, a skill, MCP")
