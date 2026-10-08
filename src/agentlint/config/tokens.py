"""Token counts, and how each one was obtained.

A count always says how it was obtained, because "12,810 tokens" means something different when it
was read from the harness's own usage numbers than when it was estimated from text.

The harness tokenizer is not available offline, so text is estimated: split into tokenizer-like
pieces (letter runs, digits, punctuation, whitespace runs, CJK characters), then scaled.
Characters-per-token would not do: it swings ~25% between JSON-heavy and code-heavy text on the
reference machine, where pieces hold within 2%.

``TOKENS_PER_PIECE`` is VERIFIED 2026-10-02 against two independent sets of exact usage numbers
from local sessions — see docs/tech_impl/01-config-reader.md "Token counting" and
scripts/calibrate_tokens.py to re-measure.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum

TOKENS_PER_PIECE = 1.22
LONG_WORD = 7  # letters per extra piece: long words split into several tokens
NOISE = 0.03  # estimates are good to a few percent: a remainder below this share of a total is noise, not text unseen

_PIECE = re.compile(
    r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]"  # kana, CJK ideographs, hangul: about one token each
    r"|[^\W\d_]+"  # a run of letters
    r"|\d"  # each digit
    r"|\s+"  # a whitespace run
    r"|[^\w\s]|_"  # a punctuation character
)


class TokenMethod(Enum):
    ESTIMATED = "estimated"  # counted from text with the approximate tokenizer
    OBSERVED = "observed"  # the harness's own usage numbers, from a session
    DERIVED = "derived"  # an observed total minus everything we could attribute


@dataclass(frozen=True)
class TokenCount:
    value: int
    method: TokenMethod

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 0:
            raise ValueError(f"a token count must be a non-negative int, got {self.value!r}")
        if not isinstance(self.method, TokenMethod):
            raise TypeError(f"TokenCount.method must be a TokenMethod, got {self.method!r}")


def count_pieces(text: str) -> int:
    pieces = 0
    for match in _PIECE.finditer(text):
        piece = match.group(0)
        if piece.isspace():
            pieces += len(piece) > 1  # a single space merges into the following word
        elif piece[0].isalpha() and len(piece) > 1:
            pieces += 1 + (len(piece) - 1) // LONG_WORD
        else:
            pieces += 1
    return pieces


REMEMBERED_FROM = 2_000  # characters: a text this long is counted once per process, by its digest, and remembered
REMEMBERED = 8_192  # how many such counts are kept, the oldest let go first: digests only, never the texts


_remembered: dict[bytes, int] = {}


def estimate_tokens(text: str) -> int:
    """A text's estimated tokens. A long one is remembered by its digest: the same tool definitions, listings and
    hook output come back byte for byte in every subagent of a workflow and after every compaction, and counting is
    the cost (the transcript reader, docs/tech_impl/11-ui.md)."""
    if len(text) < REMEMBERED_FROM:
        return round(count_pieces(text) * TOKENS_PER_PIECE)
    key = hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=16).digest()
    counted = _remembered.get(key)
    if counted is None:
        counted = round(count_pieces(text) * TOKENS_PER_PIECE)
        if len(_remembered) >= REMEMBERED:
            try:
                _remembered.pop(next(iter(_remembered)), None)
            except (RuntimeError, StopIteration):  # another request's thread let one go first
                pass
        _remembered[key] = counted
    return counted


def estimated(text: str) -> TokenCount:
    return TokenCount(estimate_tokens(text), TokenMethod.ESTIMATED)
