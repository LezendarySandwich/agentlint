"""Markdown addressing: headings, sections, excision.

A section runs from a heading to the next heading of the same or higher level. Lines inside
fenced code blocks are never headings, and a leading YAML frontmatter block is skipped. Only
ATX headings (``# Title``) are recognised; an unrecognised heading makes rules coarser, never
wrong. ``blank_code`` gives checks the prose of a text without its code, at the same offsets.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from agentlint.config.locators import LineRange, MarkdownSection, Whole
from agentlint.config.regions import Granularity, LocatorNotFound, Region, split_lines

_FENCE = re.compile(r"^([ \t]*)(`{3,}|~{3,})(.*)$")  # any indent: fences inside list items sit 4+ spaces in
_CODE_SPAN = re.compile(r"`[^`\n]*`")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_CLOSING_HASHES = re.compile(r"(?:^|[ \t]+)#+$")
_DEEPEST = 6


@dataclass(frozen=True)
class Heading:
    index: int  # 0-based line index
    level: int
    text: str
    path: tuple[str, ...]
    occurrence: int


def frontmatter_end(lines: list[str]) -> int:
    """Index of the first line after a leading ``---`` frontmatter block, or 0 if there is none."""
    if not lines or lines[0].rstrip("\r\n") != "---":
        return 0
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            return index + 1
    return 0


def _clean(text: str) -> str:
    text = _CLOSING_HASHES.sub("", text.strip())
    return " ".join(text.split())


def _indent(whitespace: str) -> int:
    return len(whitespace.expandtabs(4))


def fenced_lines(lines: list[str], start: int = 0) -> list[bool]:
    """For each line, whether it belongs to a fenced code block (the fences included).

    A fence opens at any indent (one inside a list item sits 4 or more spaces in) and closes only
    with a fence of the same character, at least as long, no deeper than 3 spaces past where it
    opened — so an indented fence shown inside an outer code block doesn't close the outer one."""
    inside = [False] * len(lines)
    fence: tuple[str, int, int] | None = None  # character, length, indent
    for index in range(start, len(lines)):
        match = _FENCE.match(lines[index].rstrip("\r\n"))
        if fence is not None:
            inside[index] = True
            if (
                match
                and match.group(2)[0] == fence[0]
                and len(match.group(2)) >= fence[1]
                and _indent(match.group(1)) <= fence[2] + 3
                and not match.group(3).strip()
            ):
                fence = None
        elif match:
            marker, info = match.group(2), match.group(3)
            if not (marker[0] == "`" and "`" in info):  # backtick info strings can't hold backticks
                fence = (marker[0], len(marker), _indent(match.group(1)))
                inside[index] = True
    return inside


def blank_code(text: str, *, spans: bool = True) -> str:
    """The text with fenced code blocks — and code spans, unless ``spans`` is false — replaced by
    spaces. Offsets and line numbers are unchanged, so a match in the result points into the text."""
    lines = text.split("\n")
    code = fenced_lines(lines)
    result = "\n".join(" " * len(line) if inside else line for line, inside in zip(lines, code))
    return _CODE_SPAN.sub(lambda m: " " * len(m.group(0)), result) if spans else result


def parse_headings(lines: list[str]) -> list[Heading]:
    headings: list[Heading] = []
    stack: list[tuple[int, str]] = []
    seen: Counter[tuple[str, ...]] = Counter()
    start = frontmatter_end(lines)
    code = fenced_lines(lines, start)
    for index in range(start, len(lines)):
        if code[index]:
            continue
        raw = lines[index].rstrip("\r\n")
        heading = _ATX.match(raw)
        if not heading:
            continue
        level = len(heading.group(1))
        text = _clean(heading.group(2) or "")
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
        path = tuple(part for _, part in stack)
        headings.append(Heading(index, level, text, path, seen[path]))
        seen[path] += 1
    return headings


def _section_end(headings: list[Heading], position: int, limit: int, n_lines: int) -> int:
    for later in headings[position + 1 :]:
        if later.level <= limit:
            return later.index
    return n_lines


def resolve(text: str, locator: MarkdownSection) -> tuple[int, int]:
    """0-based half-open line span ``[start, end)`` addressed by ``locator``."""
    lines = split_lines(text)
    headings = parse_headings(lines)
    if locator.heading_path == ():
        limit = _DEEPEST if locator.until_level is None else locator.until_level
        ends = [h.index for h in headings if h.level <= limit]
        return 0, ends[0] if ends else len(lines)
    for position, heading in enumerate(headings):
        if heading.path == locator.heading_path and heading.occurrence == locator.occurrence:
            limit = max(heading.level, locator.until_level or heading.level)
            return heading.index, _section_end(headings, position, limit, len(lines))
    raise LocatorNotFound(f"no section {locator.heading_path!r} (occurrence {locator.occurrence})")


def _boundary_level(headings: list[Heading], granularity: Granularity) -> int:
    """Level at which a document is cut into rules.

    Coarse: the shallowest level with at least two headings, so a single title heading doesn't
    swallow the whole file into one rule. Fine: every heading starts a rule.
    """
    if granularity is Granularity.FINE:
        return _DEEPEST
    counts = Counter(h.level for h in headings)
    repeated = [level for level in sorted(counts) if counts[level] >= 2]
    return repeated[0] if repeated else max(counts)


def regions(text: str, granularity: Granularity = Granularity.COARSE) -> list[Region]:
    """Partition the document into non-overlapping regions, one per rule."""
    lines = split_lines(text)
    if not text.strip():
        return []
    headings = parse_headings(lines)
    if not headings:
        return [Region(Whole(), text, 1, len(lines))]
    level = _boundary_level(headings, granularity)
    found: list[Region] = []
    boundaries = [(position, h) for position, h in enumerate(headings) if h.level <= level]
    first = boundaries[0][1].index
    if any(line.strip() for line in lines[:first]):
        until = None if headings[0].index == first else level
        found.append(Region(MarkdownSection((), 0, until), "".join(lines[:first]), 1, first))
    for position, heading in boundaries:
        end = _section_end(headings, position, level, len(lines))
        until = None if heading.level == level else level
        locator = MarkdownSection(heading.path, heading.occurrence, until)
        found.append(Region(locator, "".join(lines[heading.index : end]), heading.index + 1, end))
    return found


def region(text: str, locator: MarkdownSection | LineRange) -> Region:
    lines = split_lines(text)
    if isinstance(locator, LineRange):
        start, end = _line_span(lines, locator)
    else:
        start, end = resolve(text, locator)
    return Region(locator, "".join(lines[start:end]), start + 1, end)


def _line_span(lines: list[str], locator: LineRange) -> tuple[int, int]:
    if not 1 <= locator.start <= locator.end <= len(lines):
        raise LocatorNotFound(f"{locator} is outside a {len(lines)}-line text")
    return locator.start - 1, locator.end


def excise(text: str, locator: MarkdownSection | LineRange) -> str:
    """Delete the addressed lines. Markdown tolerates missing sections, so this is always valid."""
    lines = split_lines(text)
    if isinstance(locator, LineRange):
        start, end = _line_span(lines, locator)
    else:
        start, end = resolve(text, locator)
    return "".join(lines[:start] + lines[end:])
