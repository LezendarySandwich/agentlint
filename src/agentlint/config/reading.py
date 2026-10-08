"""One reading of the configuration: the snapshot, and everything the checks judge it by, from the same read.

A check is a pure function of the ``Reading`` it is given (docs/tech_impl/02-static-checks.md): what it needs
from the filesystem or from transcripts was taken when the configuration was read, never looked up while it
runs, and nothing is left behind for the next reading — so a second reading sees the disk as it is then.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agentlint.config.model import ConfigSnapshot
from agentlint.directives.pair import Occurrence


@dataclass(frozen=True)
class FileFacts:
    """What the filesystem said, when the configuration was read, about the paths the core checks ask after."""

    missing_links: frozenset[tuple[Path, str]] = frozenset()  # (file, link path): a relative link in a file
    # that resolved, inside that file's own directory, to nothing
    symlinks: dict[Path, tuple[str, str]] = field(default_factory=dict)  # file -> (the first symlink among the
    # file and its two parent directories, where it resolves)


@dataclass(frozen=True)
class TestedSituation:
    """One situation of a target's latest decided test, as its expectation's strength said (``analysis/expectation.py``)."""

    situation: str  # its id
    text: str
    expected: str  # the thing it expects, as the generator named it: "skill:tdd"
    verdict: str  # works, partial, adds nothing, backfires, not followed
    with_met: int
    with_runs: int
    without_met: int | None  # None when never run without (not followed)
    without_runs: int | None
    strength: float | None
    instead: tuple[tuple[str, int], ...] = ()  # not followed: what the runs with it did, by term, most runs first
    consistent: bool = True  # its runs with the target used the same tools, skills and agents (D10)


@dataclass(frozen=True)
class TestedTarget:
    """A target's latest decided test, taken from the store when the configuration was read: only for a target whose
    text is unchanged since (its situations were written for the text it has now)."""

    target: str  # the target's id
    job_id: str
    tested: datetime  # when the test was made
    verdict: str  # its weakest situation's, or works
    strength: float | None  # mean, a situation not followed counting 0
    situations: tuple[TestedSituation, ...]


@dataclass(frozen=True)
class TestEvidence:
    """What tests said of this configuration's targets, from the same read; a target never tested, or whose text has
    changed since, is absent."""

    targets: tuple[TestedTarget, ...] = ()


@dataclass(frozen=True)
class DirectiveEvidence:
    """The directives stored for the snapshot's sections, from the same read (``directives/extract.py``): where each
    was found, and how many sections were read of all that can be."""

    occurrences: tuple[Occurrence, ...] = ()
    read: int = 0
    sections: int = 0


@dataclass(frozen=True)
class Reading:
    snapshot: ConfigSnapshot
    files: FileFacts = FileFacts()
    evidence: object = None  # what the adapter's own checks need beyond the snapshot; core never looks inside
    forgotten_before: datetime | None = None  # sessions were read, none from before this: when you last forgot
    tests: TestEvidence | None = None  # the targets' latest tests, from the store; None when none were read
    directives: DirectiveEvidence | None = None  # the sections' stored directives; None when the store wasn't asked
