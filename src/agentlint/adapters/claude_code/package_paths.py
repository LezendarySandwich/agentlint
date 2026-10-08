"""``${CLAUDE_PLUGIN_ROOT}`` paths that hooks and package text run, and which of them exist.

The paths are found the same way twice: when the configuration is read, to ask the filesystem which are
missing (``missing_paths``), and by the broken-reference check, to place what it reports. The check itself
never touches the disk: a path the reading did not ask about is never reported.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from pathlib import Path

_PLUGIN_PATH = re.compile(r"\$\{?CLAUDE_PLUGIN_ROOT\}?/([^\s'\"`)\]]+)")
_EXECUTED_PATH = re.compile(r"\b(?:python3?|fbpython|bash|sh|zsh|node|bun|ruby|perl|source)\s+[\"']?"
                            r"\$\{?CLAUDE_PLUGIN_ROOT\}?/([^\s'\"`)]+)")


def _usable(rel: str) -> bool:
    return bool(rel) and not any(ch in rel for ch in "*:{}$")


def hook_paths(command: str) -> Iterator[str]:
    """Every package path a hook command names: the harness runs the command, so each one counts."""
    for match in _PLUGIN_PATH.finditer(command):
        if _usable(rel := match.group(1).rstrip(".,;")):
            yield rel


def executed_paths(text: str) -> Iterator[tuple[str, int]]:
    """(path, offset) for each package path package text hands to an interpreter (``python3 ${CLAUDE_PLUGIN_ROOT}/x.py``)."""
    for match in _EXECUTED_PATH.finditer(text):
        if _usable(rel := match.group(1).rstrip(".,;")):
            yield rel, match.start()


def missing_paths(roots: dict[str, Path], hooks: Iterable[tuple[str, str]],
                  texts: Iterable[tuple[str, str]]) -> frozenset[tuple[str, str]]:
    """(package id, path) for each path named in a hook command or a package's text that its root lacks.

    ``hooks`` and ``texts`` are (package id, command or text) pairs."""
    named = {(package, rel) for package, command in hooks for rel in hook_paths(command)}
    named |= {(package, rel) for package, text in texts for rel, _ in executed_paths(text)}
    missing = set()
    for package, rel in named:
        if package not in roots:
            continue
        try:
            if not (roots[package] / rel).exists():
                missing.add((package, rel))
        except (OSError, ValueError):
            continue  # no filesystem can look it up: no claim either way
    return frozenset(missing)
