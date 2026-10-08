"""How a path is shown to the user: one rule for every renderer."""

from __future__ import annotations

import re
from pathlib import Path

from agentlint.config.model import Provenance

# What may stand before a path that starts at your home directory, and what may follow its last part: the start or a
# space, quote, bracket or separator before; a ``/``, the end, or a character no file name in it would hold after.
_BEFORE = r"(?<![^\s\"'`(\[<=,])"
_AFTER = r"(?=/|$|[\s\"'`)\]>,;:]|\.(?:\s|$))"


def display(text: str) -> str:
    """``text`` with your home directory written ``~`` where a path starts with it: the directory itself or a path
    under it, never a match inside another path (``/Volumes/bak/Users/sam``) or a longer name (``/Users/samuel``).
    Left as it is when home is ``/``, where every path would match — the rule ``home`` in ``ui/src/format.ts`` keeps."""
    home = str(Path.home()).rstrip("/")
    if not home:
        return text
    return re.sub(_BEFORE + re.escape(home) + _AFTER, "~", text, flags=re.MULTILINE)


def place(provenance: Provenance) -> str | None:
    """Where a source is, for a person: its file as ``display`` writes it, then the adapter's anchor inside it after a
    ``#`` — written to be read, never split back apart. None when no file holds it: its point's label says what it is."""
    if provenance.file is None:
        return None
    return display(str(provenance.file)) + (f"#{provenance.anchor}" if provenance.anchor else "")
