"""The docs are the spec: a table in them renders whole, or a row silently becomes a stray line of pipes."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DELIMITER = re.compile(r"^\|(\s*:?-+:?\s*\|)+\s*$")


def stray_rows(text: str) -> list[int]:
    """The line numbers of table rows that start no table and continue none — outside fenced code."""
    stray, fenced, previous = [], False, ""
    lines = text.splitlines()
    for number, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("|") and not previous.startswith("|"):
            following = lines[number] if number < len(lines) else ""
            if not DELIMITER.match(following.strip()):
                stray.append(number)
        previous = "" if fenced else line
    return stray


def test_every_table_row_belongs_to_a_table() -> None:
    files = [ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").rglob("*.md"))]
    found = {str(f.relative_to(ROOT)): rows for f in files if (rows := stray_rows(f.read_text()))}
    assert found == {}


def test_a_row_after_a_paragraph_is_stray() -> None:
    table = "| a | b |\n|---|---|\n| 1 | 2 |\n"
    assert stray_rows(table) == []
    assert stray_rows(table + "\nA paragraph.\n| 3 | 4 |\n") == [6]
