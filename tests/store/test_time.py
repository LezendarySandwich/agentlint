"""Times the store keeps as TEXT: one form, so SQL can compare them (store/_time.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from agentlint.store import connect
from agentlint.store._time import decode, encode
from agentlint.store.suppressions import suppress


def test_every_time_is_utc_to_the_microsecond_and_reads_back() -> None:
    paris = timezone(timedelta(hours=2))
    at = datetime(2026, 10, 3, 11, 0, tzinfo=paris)
    assert encode(at) == "2026-10-03T09:00:00.000000+00:00"
    assert decode(encode(at)) == at
    assert decode("2026-10-03T09:00:00+00:00") == at  # the whole-second form written before


def test_a_naive_time_is_refused_not_taken_as_local() -> None:
    with pytest.raises(ValueError, match="naive"):
        encode(datetime(2026, 10, 3, 9, 0))
    with pytest.raises(ValueError, match="no zone"):
        decode("2026-10-03T09:00:00")
    with pytest.raises(ValueError, match="naive"):  # a store entry point too, before anything is written
        suppress(connect(), "abcd1111", "reason", datetime(2026, 10, 3, 9, 0))


def test_stored_times_order_as_text_the_way_they_order_as_times() -> None:
    base = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
    times = [base + timedelta(microseconds=n) for n in (0, 1, 999_999, 1_000_000)] + [base - timedelta(days=400)]
    assert sorted(times, key=encode) == sorted(times)
    assert "2026-10-03T09:00:00+00:00" < encode(base + timedelta(microseconds=1))  # the old form sorts with it
