"""How core asks a model to write situations, without knowing which harness or model answers.

A generator writes inputs, never verdicts (AGENTS.md non-negotiable 2). Core hands it a prompt, the JSON schema
the reply must fit and the model to ask for, and gets back the parsed reply, the model that actually answered and
the raw text. How the call is made and which model is the default belong to an adapter (04, decision D14 open).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class GenerationFailed(Exception):
    """No reply was obtained: the call failed, timed out or answered with something that isn't JSON. A generator
    raises this rather than returning an empty reply, so a failed call never reads as 'no situation fits'."""


@dataclass(frozen=True)
class Reply:
    data: object  # the reply parsed as JSON, unchecked: validate.situations decides what of it counts
    model_used: str | None  # the model that answered, as the call reports it; None when it doesn't say
    raw: str  # the reply as received, kept for the record

    def __post_init__(self) -> None:
        if self.model_used is not None and not self.model_used.strip():
            raise ValueError("Reply.model_used must be None or a name, not blank")


class Generator(Protocol):
    def generate(self, prompt: str, schema: Mapping[str, Any], model: str) -> Reply:
        """One call, one structured reply. Raises GenerationFailed when there is no reply to give."""
        ...
