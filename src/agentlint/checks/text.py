"""Which sources are prose an author wrote — the text checks read for references and emphasis."""

from __future__ import annotations

from collections.abc import Iterator

from agentlint.config.content import FileText, ObservedText
from agentlint.config.formats import ContentFormat
from agentlint.config.model import ConfigSnapshot, Source
from agentlint.config.taxonomy import Mechanism

PROSE_FORMATS = frozenset({ContentFormat.MARKDOWN, ContentFormat.PLAIN, ContentFormat.FRONTMATTER_DOC})


def prose_sources(snapshot: ConfigSnapshot) -> Iterator[Source]:
    """Sources whose text we have and which a person wrote: not settings, code or harness-made text."""
    for source in snapshot.sources:
        if (isinstance(source.content, (FileText, ObservedText)) and source.content.format in PROSE_FORMATS
                and source.provenance.point.mechanism not in (Mechanism.HARNESS_GENERATED, Mechanism.HARNESS_BUILTIN)):
            yield source


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1
