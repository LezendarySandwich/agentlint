"""Generated situations in the database (docs/tech_impl/04-probe-generator.md, "What a probe is").

A ``probes`` row is one situation the generator wrote for a target, with what matters in it, the model that answered,
the prompt's version, when, and where review leaves it: written kept, with no review time until a person keeps, drops
or edits it (decided 2026-10-05). The target's text hash is the only cache key (D15): a
target's situations stay valid until its text changes, whatever the model or prompt, and two targets with the same
text share them. The model and prompt version are recorded so a different rule stays possible later.

``replace`` writes a target's new situations in one transaction, deleting the ones it had: a review never carries
across a regeneration, even for a situation written again word for word.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from agentlint.store import ReviewStatus, StoreError, transaction
from agentlint.store._time import decode, encode

_COLUMNS = ("probe_id, target_id, target_hash, situation, matters, model, prompt_version, created, review, "
            "edited_text, reviewed")


class NoSuchProbe(StoreError):
    """No probe has this id: never written, regenerated away, or forgotten."""


@dataclass(frozen=True)
class Probe:
    probe_id: str
    target_id: str
    target_hash: str
    situation: str  # verbatim, as the generator wrote it
    matters: str  # a Matters value (probes/matters.py); the store keeps it as text
    model: str | None
    prompt_version: int
    created: datetime
    review: ReviewStatus = ReviewStatus.ACCEPTED  # written kept
    edited_text: str | None = None
    reviewed: datetime | None = None  # when a person last reviewed it; None: kept as written, nobody reviewed it

    def __post_init__(self) -> None:
        if (self.review is ReviewStatus.EDITED) != (self.edited_text is not None):
            raise ValueError("a probe has an edited text exactly when its review is 'edited'")

    @property
    def text(self) -> str:
        """What a run would type: the reviewer's text when they edited it, the generator's otherwise."""
        return self.edited_text if self.edited_text is not None else self.situation


def _probe(row: sqlite3.Row) -> Probe:
    return Probe(row["probe_id"], row["target_id"], row["target_hash"], row["situation"], row["matters"], row["model"],
                 row["prompt_version"], decode(row["created"]), ReviewStatus(row["review"]), row["edited_text"],
                 decode(row["reviewed"]) if row["reviewed"] else None)


def for_hash(db: sqlite3.Connection, target_hash: str) -> list[Probe]:
    """A target text's situations, in the order they were written."""
    return [_probe(row) for row in db.execute(
        f"SELECT {_COLUMNS} FROM probes WHERE target_hash = ? ORDER BY created, rowid", (target_hash,))]


def load(db: sqlite3.Connection, probe_id: str) -> Probe | None:
    row = db.execute(f"SELECT {_COLUMNS} FROM probes WHERE probe_id = ?", (probe_id,)).fetchone()
    return _probe(row) if row else None


def replace(db: sqlite3.Connection, target_hash: str, probes: Iterable[Probe]) -> None:
    """The target text's situations become ``probes``, all of one target hash, in one transaction."""
    probes = list(probes)
    if any(p.target_hash != target_hash for p in probes):
        raise ValueError("every probe replaced for a target hash must have that hash")
    with transaction(db):
        db.execute("DELETE FROM probes WHERE target_hash = ?", (target_hash,))
        db.executemany(f"INSERT INTO probes ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            (p.probe_id, p.target_id, p.target_hash, p.situation, p.matters, p.model, p.prompt_version,
             encode(p.created), p.review.value, p.edited_text, encode(p.reviewed) if p.reviewed else None)
            for p in probes])


def set_review(db: sqlite3.Connection, probe_id: str, review: ReviewStatus, now: datetime,
               edited_text: str | None = None) -> Probe:
    """Record what review made of a probe, and when: an edited text goes with 'edited' and nothing else. A keep is
    recorded too, so a situation you kept has a review time and one kept as written has none. NoSuchProbe when there is
    no such probe."""
    if (review is ReviewStatus.EDITED) != (edited_text is not None):
        raise ValueError("an edited text goes with review 'edited', and only with it")
    with transaction(db):
        changed = db.execute("UPDATE probes SET review = ?, edited_text = ?, reviewed = ? WHERE probe_id = ?",
                             (review.value, edited_text, encode(now), probe_id)).rowcount
    if changed != 1:
        raise NoSuchProbe(f"no probe {probe_id}: it was regenerated or forgotten")
    probe = load(db, probe_id)
    assert probe is not None
    return probe


def delete_all(db: sqlite3.Connection) -> int:
    """``forget``: every generated situation. How many went."""
    with transaction(db):
        return db.execute("DELETE FROM probes").rowcount
