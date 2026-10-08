"""What we know about a source's text — exactly one of four things.

    FileText      read from disk: deterministic, addressable, excisable
    ObservedText  not on disk; the text recent sessions show the harness delivered
    SizeOnly      the size is known but the text is not
    NotObserved   declared on disk, but absent from every session examined

Each state is its own class so nothing downstream has to interpret sentinels: there is no empty
string meaning "unknown" and no zero meaning "unmeasured".

Addressing and excision are dispatched by format. There is deliberately no generic "delete a
character span" operation: for three of the five formats that produces a corrupt artifact
(docs/tech_impl/01-config-reader.md "How excision works, per format").
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from agentlint.config import frontmatter, markdown, plain, structured
from agentlint.config.formats import ContentFormat
from agentlint.config.locators import Body, KeyPath, LineRange, Locator, MarkdownSection, Whole
from agentlint.config.regions import Granularity, LocatorNotFound, Region, split_lines
from agentlint.config.sessions import SessionHeader
from agentlint.config.tokens import TokenCount, TokenMethod, estimated


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()  # JSON can carry lone surrogates


def _check_text(owner: str, text: str, digest: str, tokens: TokenCount) -> None:
    if tokens.method is not TokenMethod.ESTIMATED:
        raise ValueError(f"{owner}.tokens are counted from its text, so must be ESTIMATED")
    if digest != content_hash(text):
        raise ValueError(f"{owner}.content_hash does not match its text")


class _Addressable:
    """Region addressing shared by the content states that carry text."""

    format: ContentFormat
    text: str

    def regions(self, granularity: Granularity = Granularity.COARSE) -> list[Region]:
        """Non-overlapping regions, one per rule, at the requested granularity."""
        match self.format:
            case ContentFormat.MARKDOWN:
                return markdown.regions(self.text, granularity)
            case ContentFormat.PLAIN:
                return plain.regions(self.text, granularity)
            case ContentFormat.JSON:
                return structured.json_regions(self.text, granularity)
            case ContentFormat.YAML:
                return structured.yaml_regions(self.text, granularity)
            case ContentFormat.FRONTMATTER_DOC:
                return frontmatter.regions(self.text, granularity)
            case ContentFormat.CODE:
                return [Region(Whole(), self.text, 1, max(1, len(split_lines(self.text))))] if self.text.strip() else []
        raise AssertionError(self.format)

    def region(self, locator: Locator) -> Region:
        """Resolve any supported locator to its text and line span."""
        if isinstance(locator, Whole):
            return Region(locator, self.text, 1, max(1, len(split_lines(self.text))))
        match self.format, locator:
            case ContentFormat.MARKDOWN, (MarkdownSection() | LineRange()):
                return markdown.region(self.text, locator)
            case ContentFormat.PLAIN, LineRange():
                return plain.region(self.text, locator)
            case ContentFormat.JSON, KeyPath():
                return structured.json_region(self.text, locator)
            case ContentFormat.YAML, KeyPath():
                return structured.yaml_region(self.text, locator)
            case ContentFormat.FRONTMATTER_DOC, (KeyPath() | Body() | MarkdownSection() | LineRange()):
                return frontmatter.region(self.text, locator)
        raise LocatorNotFound(f"{type(locator).__name__} does not address {self.format.value} content")


@dataclass(frozen=True)
class FileText(_Addressable):
    """Text read from a file on disk."""

    format: ContentFormat
    text: str
    content_hash: str
    tokens: TokenCount  # always ESTIMATED
    required_keys: tuple[KeyPath, ...] = ()  # keys an excision must never remove

    def __post_init__(self) -> None:
        _check_text("FileText", self.text, self.content_hash, self.tokens)

    def excise(self, locator: Locator) -> str:
        """The text with the addressed region removed. Validated before it is returned."""
        match self.format, locator:
            case (ContentFormat.MARKDOWN | ContentFormat.PLAIN | ContentFormat.CODE), Whole():
                new_text = ""
            case ContentFormat.MARKDOWN, (MarkdownSection() | LineRange()):
                new_text = markdown.excise(self.text, locator)
            case ContentFormat.PLAIN, LineRange():
                new_text = plain.excise(self.text, locator)
            case ContentFormat.JSON, KeyPath():
                new_text = structured.json_excise(self.text, locator, self.required_keys)
            case ContentFormat.YAML, KeyPath():
                new_text = structured.yaml_excise(self.text, locator, self.required_keys)
            case ContentFormat.FRONTMATTER_DOC, (KeyPath() | Body() | MarkdownSection() | LineRange() | Whole()):
                new_text = frontmatter.excise(self.text, locator, self.required_keys)
            case _:
                raise LocatorNotFound(f"{type(locator).__name__} cannot excise from {self.format.value} content")
        self.validate(new_text)
        return new_text

    def validate(self, new_text: str) -> None:
        """Raise ExcisionInvalid if ``new_text`` is not a valid replacement for this text."""
        match self.format:
            case ContentFormat.MARKDOWN | ContentFormat.PLAIN | ContentFormat.CODE:
                return  # these tolerate any deletion (code is only ever removed whole)
            case ContentFormat.JSON:
                structured.json_validate(self.text, new_text, self.required_keys)
            case ContentFormat.YAML:
                structured.yaml_validate(self.text, new_text, self.required_keys)
            case ContentFormat.FRONTMATTER_DOC:
                frontmatter.validate(self.text, new_text, self.required_keys)


@dataclass(frozen=True)
class ObservedText(_Addressable):
    """Text that is not on disk, as recent sessions show the harness delivered it.

    It has no ``excise()``: you cannot edit what a command prints. Removing it is an adapter-level
    operation (disable the command), described by the source's ablation policy.
    """

    format: ContentFormat
    text: str  # the most recent delivery
    content_hash: str
    tokens: TokenCount  # ESTIMATED from that text
    seen_in: tuple[SessionHeader, ...]  # sessions it appeared in, most recent first
    sessions_examined: int  # how many sessions could have shown it: the denominator

    def __post_init__(self) -> None:
        _check_text("ObservedText", self.text, self.content_hash, self.tokens)
        if not self.seen_in:
            raise ValueError("ObservedText.seen_in must name at least one session; otherwise use NotObserved")
        if len(self.seen_in) > self.sessions_examined:
            raise ValueError("ObservedText.seen_in cannot exceed sessions_examined")
        starts = [ref.started_at for ref in self.seen_in]
        if starts != sorted(starts, reverse=True):
            raise ValueError("ObservedText.seen_in must be ordered most recent first")


@dataclass(frozen=True)
class SizeOnly:
    """The size is known but the text is not."""

    tokens: TokenCount  # OBSERVED or DERIVED, never ESTIMATED
    reason: str  # why the text is unavailable

    def __post_init__(self) -> None:
        if self.tokens.method is TokenMethod.ESTIMATED:
            raise ValueError("SizeOnly has no text to estimate from; its tokens must be OBSERVED or DERIVED")
        if not self.reason.strip():
            raise ValueError("SizeOnly.reason must say why the text is unavailable")


@dataclass(frozen=True)
class NotObserved:
    """Declared on disk, but absent from every session examined, so neither text nor size is known."""

    sessions_examined: int
    reason: str

    def __post_init__(self) -> None:
        if self.sessions_examined < 0:
            raise ValueError("NotObserved.sessions_examined must be non-negative")
        if not self.reason.strip():
            raise ValueError("NotObserved.reason must not be empty")


Content = FileText | ObservedText | SizeOnly | NotObserved


def file_text(format: ContentFormat, text: str, *, required_keys: tuple[KeyPath, ...] = ()) -> FileText:
    return FileText(format, text, content_hash(text), estimated(text), required_keys)


def observed_text(
    format: ContentFormat, text: str, seen_in: tuple[SessionHeader, ...], sessions_examined: int
) -> ObservedText:
    return ObservedText(format, text, content_hash(text), estimated(text), seen_in, sessions_examined)


def tokens_of(content: Content) -> TokenCount | None:
    """The content's size, or None when it is not known."""
    return None if isinstance(content, NotObserved) else content.tokens
