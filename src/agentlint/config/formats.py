"""Content formats. Addressing and excision are dispatched on these (see content.py)."""

from __future__ import annotations

from enum import Enum


class ContentFormat(Enum):
    MARKDOWN = "markdown"
    JSON = "json"
    YAML = "yaml"
    FRONTMATTER_DOC = "frontmatter_doc"  # YAML frontmatter + markdown body
    PLAIN = "plain"
    CODE = "code"  # program text, not instructions: addressed whole, never divided into rules
