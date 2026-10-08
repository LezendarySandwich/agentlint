"""Frontmatter documents: a ``---``-delimited YAML block followed by a markdown body.

Rules are frontmatter keys (``KeyPath``) and the body (``Body``). Removing the ``description``
key leaves the entity installed but strips its trigger text, which answers whether selection
follows the description or the body (docs/tech_impl/01-config-reader.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentlint.config import markdown, structured
from agentlint.config.locators import Body, KeyPath, LineRange, MarkdownSection, Whole
from agentlint.config.regions import (
    ExcisionInvalid,
    FormatError,
    Granularity,
    LocatorNotFound,
    Region,
    split_lines,
)


@dataclass(frozen=True)
class Parts:
    head: str  # opening delimiter, frontmatter, closing delimiter — empty if no frontmatter
    frontmatter: str | None  # the YAML text between the delimiters
    body: str
    body_first_line: int  # 0-based index of the first body line
    data: dict[str, Any] = field(default_factory=dict)


def split(text: str) -> Parts:
    lines = split_lines(text)
    if lines and lines[0].rstrip("\r\n") == "---":
        for index in range(1, len(lines)):
            if lines[index].rstrip("\r\n") == "---":
                frontmatter = "".join(lines[1:index])
                data = structured.yaml_load(frontmatter)
                if data is None:
                    data = {}
                if not isinstance(data, dict):
                    raise FormatError("frontmatter is not a mapping")
                return Parts("".join(lines[: index + 1]), frontmatter, "".join(lines[index + 1 :]), index + 1, data)
    return Parts("", None, text, 0, {})


def regions(text: str, granularity: Granularity = Granularity.COARSE) -> list[Region]:
    parts = split(text)
    found: list[Region] = []
    if parts.frontmatter is not None:
        found.extend(
            structured.yaml_regions(parts.frontmatter, Granularity.COARSE, line_offset=1)
        )
    if not parts.body.strip():
        return found
    if granularity is Granularity.COARSE:
        found.append(Region(Body(), parts.body, parts.body_first_line + 1, len(split_lines(text))))
        return found
    for region in markdown.regions(text, Granularity.FINE):
        if isinstance(region.locator, MarkdownSection) and region.locator.heading_path == ():
            # The markdown preamble would include the frontmatter; address only the body part.
            start, end = parts.body_first_line + 1, region.end_line
            body_part = "".join(split_lines(text)[start - 1 : end])
            if body_part.strip():
                found.append(Region(LineRange(start, end), body_part, start, end))
        elif isinstance(region.locator, Whole):
            found.append(Region(Body(), parts.body, parts.body_first_line + 1, region.end_line))
        else:
            found.append(region)
    return found


def region(text: str, locator: KeyPath | Body | MarkdownSection | LineRange) -> Region:
    parts = split(text)
    if isinstance(locator, KeyPath):
        if parts.frontmatter is None:
            raise LocatorNotFound(f"no frontmatter, so no key path {locator.path!r}")
        return structured.yaml_region(parts.frontmatter, locator, line_offset=1)
    if isinstance(locator, Body):
        return Region(locator, parts.body, parts.body_first_line + 1, len(split_lines(text)))
    return markdown.region(text, locator)


def excise(text: str, locator: KeyPath | Body | MarkdownSection | LineRange | Whole, required: tuple[KeyPath, ...] = ()) -> str:
    parts = split(text)
    if isinstance(locator, KeyPath):
        if parts.frontmatter is None:
            raise LocatorNotFound(f"no frontmatter, so no key path {locator.path!r}")
        lines = split_lines(text)
        new_frontmatter = structured.yaml_excise(parts.frontmatter, locator)
        new_text = lines[0] + new_frontmatter + lines[parts.body_first_line - 1] + parts.body
    elif isinstance(locator, Body):
        new_text = parts.head
    elif isinstance(locator, Whole):
        new_text = ""
    else:
        new_text = markdown.excise(text, locator)
    validate(text, new_text, required)
    return new_text


def validate(original_text: str, new_text: str, required: tuple[KeyPath, ...] = ()) -> None:
    """The new document's frontmatter must parse, and required keys must survive."""
    try:
        after = split(new_text)
    except FormatError as error:
        raise ExcisionInvalid(f"frontmatter no longer parses: {error}") from None
    structured.check_survivors(split(original_text).data, after.data, required)
