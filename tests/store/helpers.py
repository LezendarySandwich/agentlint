"""Builders for recording findings: a configuration reading with given declared hashes, and check results."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from agentlint.checks import CheckResults
from agentlint.config.assemble import assemble
from agentlint.config.model import ConfigSnapshot, Declared
from agentlint.report.finding import Finding
from agentlint.store.findings import Lifecycle, record

from ..config.helpers import HARNESS, file_source, observable_source


def rows(db, query: str, args: tuple = ()) -> list[tuple]:
    """A query's rows as plain tuples, to compare with literals (the store reads its own rows by column name)."""
    return [tuple(row) for row in db.execute(query, args)]


def configuration(declared: Mapping[str, str], *, project: str = "/p", unobserved: Collection[str] = ()) -> ConfigSnapshot:
    """A reading of ``project`` whose sources are declared as these texts (source id -> text: the same text, the
    same declared hash); those in ``unobserved`` are sources only a session could show, and none was read."""
    sources = tuple(observable_source(source_id, declared=text) if source_id in unobserved else file_source(source_id, text)
                    for source_id, text in declared.items())
    return assemble(Declared(HARNESS, Path(project), "2.0", "m", sources, ()), [])


def ran(findings: Iterable[Finding], *, evaluated: Collection[str] = ("demo-check",),
        judged: Mapping[str, Callable[[str], bool]] | None = None, needs_change: Collection[str] = (),
        not_by_text: Collection[str] = ()) -> CheckResults:
    return CheckResults(list(findings), tuple(evaluated), {}, dict(judged or {}), frozenset(needs_change),
                        frozenset(not_by_text))


def resting_on(finding: Finding, sources: Iterable[str]) -> Finding:
    """The same finding (its id ignores its sources), resting on these."""
    return replace(finding, sources=tuple(sources))


def check_at(db, at: datetime, findings: Iterable[Finding], declared: Mapping[str, str], *, project: str = "/p",
             write: bool = True, unobserved: Collection[str] = (), **judging) -> Lifecycle:
    """Record (or, without ``write``, only read) a check of ``project`` that found ``findings`` in a configuration
    declaring ``declared``."""
    return record(db, project=project, results=ran(findings, **judging),
                  snapshot=configuration(declared, project=project, unobserved=unobserved), at=at, write=write,
                  note=None if write else "not recorded")
