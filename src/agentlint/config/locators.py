"""Format-appropriate addresses inside a source (docs/tech_impl/01-config-reader.md "Locator").

Deliberately not character spans: a heading path survives edits elsewhere in a file and a span
does not, and rule verdicts are cached across runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class Whole:
    """The entire source, atomic."""


@dataclass(frozen=True)
class Body:
    """The body of a frontmatter document: everything after the closing ``---``."""


@dataclass(frozen=True)
class MarkdownSection:
    """A heading and the lines under it.

    The section runs from the heading to the next heading whose level is at most
    ``max(own level, until_level)``. With ``until_level=None`` that is the documented default,
    "heading to next heading of same or higher level". A larger ``until_level`` stops the section
    earlier, at nested headings, which is how fine-grained and mixed-level sections are addressed.

    ``heading_path == ()`` addresses the preamble: the lines before the first heading whose level
    is at most ``until_level`` (any heading, when ``until_level`` is None).

    ``occurrence`` disambiguates repeated heading paths; 0 is the first.
    """

    heading_path: tuple[str, ...]
    occurrence: int = 0
    until_level: int | None = None

    def __post_init__(self) -> None:
        if self.occurrence < 0:
            raise ValueError("MarkdownSection.occurrence counts from 0, never below")
        if self.until_level is not None and not 1 <= self.until_level <= 6:
            raise ValueError("MarkdownSection.until_level is a heading level, 1 to 6")


@dataclass(frozen=True)
class LineRange:
    start: int  # 1-indexed, inclusive
    end: int  # 1-indexed, inclusive

    def __post_init__(self) -> None:
        if not 1 <= self.start <= self.end:
            raise ValueError(f"a LineRange runs forward from line 1: got {self.start}-{self.end}")


@dataclass(frozen=True)
class KeyPath:
    path: tuple[str | int, ...]


Locator = Whole | Body | MarkdownSection | LineRange | KeyPath


def _segment(value: str | int) -> str:
    return quote(str(value), safe=" ")


def locator_key(locator: Locator) -> str:
    """A stable, unambiguous string for a locator, used inside rule ids.

    Path segments are percent-encoded, so no heading or key text can collide with the
    separators.
    """
    match locator:
        case Whole():
            return "whole"
        case Body():
            return "body"
        case MarkdownSection(heading_path=path, occurrence=occurrence, until_level=until):
            key = "section:" + "".join("/" + _segment(part) for part in path)
            if occurrence:
                key += f"?n={occurrence}"
            if until is not None:
                key += ("&" if occurrence else "?") + f"until={until}"
            return key
        case LineRange(start=start, end=end):
            return f"lines:{start}-{end}"
        case KeyPath(path=path):
            return "key:" + "".join("/" + _segment(part) for part in path)
    raise TypeError(f"not a locator: {locator!r}")


def describe(locator: Locator) -> str:
    """Human-readable form for terminal and UI output."""
    match locator:
        case Whole():
            return "whole"
        case Body():
            return "body"
        case MarkdownSection(heading_path=()):
            return "preamble"
        case MarkdownSection(heading_path=path, occurrence=occurrence):
            label = "§ " + " › ".join(f'"{part}"' for part in path)
            return label + (f" (#{occurrence + 1})" if occurrence else "")
        case LineRange(start=start, end=end):
            return f"lines {start}-{end}" if start != end else f"line {start}"
        case KeyPath(path=path):
            return ".".join(f"[{part}]" if isinstance(part, int) else str(part) for part in path)
    raise TypeError(f"not a locator: {locator!r}")
