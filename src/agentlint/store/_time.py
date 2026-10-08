"""Every time the store keeps as TEXT, in one form: ISO 8601 in UTC, to the microsecond.

One form, so comparing two stored times as TEXT — what retention and the usage window do in SQL — orders them as
times. A naive datetime has no zone to convert from; guessing local time would shift it silently, so it is refused.

The one time not kept as TEXT is ``archive_files.mtime_epoch_s``: a transcript's modification time, kept as the Unix
seconds its file's stat gives and compared with the next stat for equality, which a microsecond ISO round trip would
break. Retention compares it with ``datetime.timestamp()``, never with this module's text.
"""

from __future__ import annotations

from datetime import UTC, datetime


def encode(at: datetime) -> str:
    """``2026-10-03T09:00:00.000000+00:00``. Raises ValueError on a naive datetime."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError(f"the store keeps only timezone-aware times, got naive {at.isoformat()}")
    return at.astimezone(UTC).isoformat(timespec="microseconds")


def decode(text: str) -> datetime:
    """A stored time, timezone-aware. Also reads the whole-second form written before 2026-10-03."""
    at = datetime.fromisoformat(text)
    if at.tzinfo is None:
        raise ValueError(f"a stored time has no zone: {text!r}")
    return at
