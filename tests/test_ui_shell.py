"""The shell agentlint ships was built from the UI sources in this repository (docs/tech_impl/11-ui.md).

Mirrors sourceHash() in ui/vite.config.ts: if this fails, ui/ changed without `npm run build`.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "ui"
SHELL = REPO / "src" / "agentlint" / "ui" / "shell.html"
ROOT_FILES = ("index.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts")


def source_hash() -> str:
    found = [*ROOT_FILES, *(p.relative_to(UI).as_posix() for p in (UI / "src").rglob("*") if p.is_file())]
    paths = sorted(p for p in found
                   if not re.search(r"\.test\.tsx?$", p) and not any(part.startswith(".") for part in p.split("/")))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.encode("utf-8") + b"\0")
        digest.update((UI / path).read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def test_the_shipped_shell_was_built_from_these_sources() -> None:
    built = re.search(r'<meta name="agentlint-ui-source" content="([^"]+)"', SHELL.read_text(encoding="utf-8"))
    assert built, "the shell carries no source hash: rebuild it with `npm run build` in ui/"
    assert built.group(1) == source_hash(), "ui/ changed since the shell was built: run `npm run build` in ui/"


def test_the_shell_carries_no_report_and_fetches_nothing_from_elsewhere() -> None:
    """It holds no report, and no slot for one: the offline HTML report was dropped 2026-10-04, so the page always asks
    `agentlint ui` for its report. Everything it needs is inlined."""
    from agentlint.ui.shell import shell

    html = shell()
    assert "agentlint-data" not in html and "agentlint-texts" not in html
    assert not re.search(r'(src|href)="(https?:)?//', html)
