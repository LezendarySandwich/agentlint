"""Plain-text addressing: blank-line-separated paragraphs."""

from __future__ import annotations

from agentlint.config.locators import LineRange
from agentlint.config.regions import Granularity, LocatorNotFound, Region, split_lines


def regions(text: str, granularity: Granularity = Granularity.COARSE) -> list[Region]:
    lines = split_lines(text)
    found: list[Region] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        end = index + 1
        if granularity is Granularity.COARSE:
            while end < len(lines) and lines[end].strip():
                end += 1
        found.append(Region(LineRange(index + 1, end), "".join(lines[index:end]), index + 1, end))
        index = end
    return found


def _span(lines: list[str], locator: LineRange) -> tuple[int, int]:
    if not 1 <= locator.start <= locator.end <= len(lines):
        raise LocatorNotFound(f"{locator} is outside a {len(lines)}-line text")
    return locator.start - 1, locator.end


def region(text: str, locator: LineRange) -> Region:
    lines = split_lines(text)
    start, end = _span(lines, locator)
    return Region(locator, "".join(lines[start:end]), start + 1, end)


def excise(text: str, locator: LineRange) -> str:
    """Delete the lines, plus one following blank line so paragraphs don't merge or double-space."""
    lines = split_lines(text)
    start, end = _span(lines, locator)
    if end < len(lines) and not lines[end].strip() and (start == 0 or not lines[start - 1].strip()):
        end += 1
    return "".join(lines[:start] + lines[end:])
