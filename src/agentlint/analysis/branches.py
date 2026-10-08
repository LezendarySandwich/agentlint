"""The configured entities two branches invoked differently, as a finding's members (07, readiness D11).

By default that is the symmetric difference of what each branch invoked: the entities some run of one branch invoked
and no run of the other did (``symmetric_difference``). A stricter rule keeps only the entities every run of one
branch invoked (``every_run_of_one``). Which of the two a finding uses — whether an entity only some runs of a branch
used tells the branches apart — is D11's question, open, so the rule is a parameter. Entities are matched through
usage references (``namespace/name``), the one form a trace and usage share; the harness's own entities — its
built-in tools and agents — are not configured and never members. Whether a difference becomes a finding, and how it
is worded, is not decided here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from agentlint.checks import member
from agentlint.config.model import ConfigSnapshot
from agentlint.config.taxonomy import Authority
from agentlint.config.usage import reference
from agentlint.report.finding import Member
from agentlint.trace.model import Trace


Rule = Callable[[Sequence[Iterable[str]], Sequence[Iterable[str]]], tuple[frozenset[str], frozenset[str]]]


def _sets(a: Sequence[Iterable[str]], b: Sequence[Iterable[str]]) -> tuple[list[frozenset[str]], list[frozenset[str]]]:
    if not a or not b:
        raise ValueError("a branch has at least one run")
    return [frozenset(run) for run in a], [frozenset(run) for run in b]


def symmetric_difference(a: Sequence[Iterable[str]], b: Sequence[Iterable[str]]) -> tuple[frozenset[str], frozenset[str]]:
    """What each run of a branch invoked (usage references), for two branches -> the symmetric difference of what each
    branch invoked, split by side: the references some run of ``a`` invoked and no run of ``b`` did, and the other way
    round."""
    a_sets, b_sets = _sets(a, b)
    a_any, b_any = frozenset.union(*a_sets), frozenset.union(*b_sets)
    return a_any - b_any, b_any - a_any


def every_run_of_one(a: Sequence[Iterable[str]], b: Sequence[Iterable[str]]) -> tuple[frozenset[str], frozenset[str]]:
    """The stricter rule: the references every run of ``a`` invoked and no run of ``b`` did, and the other way round —
    a subset of ``symmetric_difference``."""
    a_sets, b_sets = _sets(a, b)
    a_all, b_all = frozenset.intersection(*a_sets), frozenset.intersection(*b_sets)
    a_any, b_any = frozenset.union(*a_sets), frozenset.union(*b_sets)
    return a_all - b_any, b_all - a_any


def _configured(snapshot: ConfigSnapshot, ref: str) -> list:
    found = []
    for entity in snapshot.entities:
        if reference(entity) != ref:
            continue
        try:
            authority = snapshot.source(entity.provided_by).provenance.authority
        except KeyError:
            continue
        if authority is not Authority.HARNESS:
            found.append(entity)
    return sorted(found, key=lambda entity: entity.id)


def branch_members(snapshot: ConfigSnapshot, a: Sequence[Trace], b: Sequence[Trace], *,
                   rule: Rule = symmetric_difference) -> tuple[Member, ...]:
    """The configured entities branch ``a``'s runs and branch ``b``'s invoked differently by ``rule``, as Members:
    ``a``'s first, then ``b``'s, each side by reference and then entity id. A reference several entities answer to
    (namesakes) gives each of them: the run named it, and which one the harness resolved is not in the trace."""
    only_a, only_b = rule([t.invoked() for t in a], [t.invoked() for t in b])
    return tuple(member(snapshot, entity)
                 for side in (only_a, only_b) for ref in sorted(side) for entity in _configured(snapshot, ref))
