"""The filesystem facts core checks need, taken when the configuration is read (``read_config``).

broken-reference asks whether a relative link in an instruction file points at a file in that file's
directory; duplicate-name asks whether a copy is a symlink. Both are answered here, at read time, so the
checks themselves never touch the disk. A path the reading did not look at is never reported.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote

from agentlint.checks.text import prose_sources
from agentlint.config.content import FileText
from agentlint.config.markdown import blank_code
from agentlint.config.model import ConfigSnapshot, Source
from agentlint.config.reading import FileFacts

_LINK = re.compile(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def relative_links(text: str) -> Iterator[tuple[str, str, int]]:
    """(target as written, the path it names, offset) for each markdown link outside code that could be a
    relative path. URLs, anchors, absolute paths and placeholders (``[title](url)``) are not."""
    for match in _LINK.finditer(blank_code(text)):
        target = match.group(1)
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith(("#", "/", "~")):
            continue
        path = unquote(target.split("#")[0].split("?")[0])
        if not path or any(ch in path for ch in "{}$<>*") or ("/" not in path and "." not in path):
            continue  # a placeholder such as [title](url), not a path
        yield target, path, match.start()


def _missing(home: Path, path: str) -> bool:
    try:
        resolved = (home / path).resolve()
        if not resolved.is_relative_to(home.resolve()):
            return False  # escapes the file's own directory: out of scope
        return not resolved.exists()
    except (OSError, ValueError):
        return False  # not a path the filesystem can even look up (a NUL byte, say): no claim either way


def _symlink(path: Path) -> tuple[str, str] | None:
    try:
        if not path.exists():
            return None  # gone since it was read: nothing to say about links
        for candidate in (path, *path.parents[:2]):
            if candidate.is_symlink():
                return str(candidate), str(candidate.resolve())
    except (OSError, ValueError):
        return None
    return None


def own_file(source: Source) -> Path | None:
    """The file whose text this source is — not a file it is one part of (an anchor inside a settings file)."""
    provenance = source.provenance
    return provenance.file if provenance.file is not None and provenance.anchor is None else None


def file_facts(snapshot: ConfigSnapshot) -> FileFacts:
    missing: set[tuple[Path, str]] = set()
    for source in prose_sources(snapshot):
        file = own_file(source)
        try:
            if file is None or not isinstance(source.content, FileText) or not file.is_file():
                continue
        except (OSError, ValueError):
            continue
        missing |= {(file, path) for _, path, _ in relative_links(source.content.text) if _missing(file.parent, path)}
    symlinks = {}
    files = {source.id: own_file(source) for source in snapshot.sources}
    for file in dict.fromkeys(files[e.provided_by] for e in snapshot.entities if files.get(e.provided_by)):
        if (link := _symlink(file)) is not None:
            symlinks[file] = link
    return FileFacts(frozenset(missing), symlinks)
