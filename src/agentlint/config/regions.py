"""Shared types for the per-format addressing code."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agentlint.config.locators import Locator


class Granularity(Enum):
    """How finely a source is divided into rules.

    Coarse is the default because excising a sentence from a paragraph measures incoherence
    rather than the rule's absence (docs/tech_impl/01-config-reader.md).
    """

    COARSE = "coarse"
    FINE = "fine"


@dataclass(frozen=True)
class Region:
    """An addressable region of a source's text."""

    locator: Locator
    text: str
    start_line: int  # 1-indexed, inclusive
    end_line: int  # 1-indexed, inclusive


class FormatError(ValueError):
    """The text does not parse in its declared format."""


class LocatorNotFound(LookupError):
    """The locator does not address anything in this text."""


class ExcisionInvalid(ValueError):
    """Excision produced an artifact that fails validation; it must not be used in a variant."""


def split_lines(text: str) -> list[str]:
    """Lines with their endings kept, so joining them reproduces the text exactly."""
    return text.splitlines(keepends=True)
