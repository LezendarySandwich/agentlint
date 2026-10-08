"""Variant verification: read each variant back and prove the target is gone before any run is spent on it
(docs/tech_impl/05-runner.md "Verify the variant before running against it").

A variant that silently failed to remove its target reads like the baseline, every run against it matches, and the
tool would report "no effect" — a confident lie. So the check raises, never warns. Source ids embed absolute paths
(an instruction file's id is its path), and a variant's files are at other paths, so ids can't be compared as they
are: "the target's id is absent from the variant" would always hold. Every source is compared by what it stands for
instead — its injection point, its file with the variant home mapped back to the home it copies and the run's
directory to the project, then resolved (so a linked directory of the home and its target are one place), and its
anchor — with its declared hash, and a rule by that plus its locator and text. An
anchor that is a position in a list (a hook's) is taken by the next entry when the target's is cut, so a whole entry
is the one at its place with its own declared hash.

Reading a variant goes through the adapter's reader of that home (``VariantHomes.at_home``): it never reads the real
home, except where the copied configuration names a path there that the harness would read there too (one the
harness maps into the substituted home, such as a package's marketplace checkout, is read in the variant). The
sources read from the real home are reported as ``escapes``; the other files opened on the way (a package's or
marketplace's manifest) are not listed.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from agentlint.config.content import FileText, ObservedText
from agentlint.config.locators import locator_key
from agentlint.config.model import ConfigSnapshot, Rule, Source
from agentlint.config.taxonomy import Load
from agentlint.harness import VariantHomes, read_config
from agentlint.rank.plan import AblationTarget
from agentlint.runner.variant import VariantError


class AblationFailed(VariantError):
    """The variant still has its target, or reads exactly like the baseline: runs against it would measure nothing."""

    def __init__(self, target_id: str, reason: str) -> None:
        super().__init__(f"the variant for {target_id} can't be used: {reason}")
        self.target_id = target_id
        self.reason = reason


def _forms(path: Path) -> list[Path]:
    absolute = Path(os.path.abspath(path))
    real = Path(os.path.realpath(path))
    return [absolute] if real == absolute else [absolute, real]


@dataclass(frozen=True)
class PathMap:
    """Paths as a reading has them, mapped to what they stand for — (variant home, real home), (workdir, project) —
    each written one way (its resolved form, every link on the way followed in the real home), so a path through /tmp
    and one through /private/tmp are one path, and so are a file read through a linked directory of the home and the
    same file read at its resolved place (a reader that resolves a package's directory names it there, while a
    variant's copy of that directory is no link)."""

    pairs: tuple[tuple[Path, Path], ...] = ()

    def apply(self, path: Path) -> Path:
        candidates = sorted(((form, Path(os.path.realpath(to))) for start, to in self.pairs for form in _forms(start)),
                            key=lambda pair: len(pair[0].parts), reverse=True)  # the deepest first: work inside home
        for start, to in candidates:
            if path.is_relative_to(start):
                return Path(os.path.realpath(to / path.relative_to(start)))
        return Path(os.path.realpath(path))


Key = tuple[str, str, str]  # (injection point, file as it stands for — or the source id when no file holds it, anchor)


def source_key(source: Source, paths: PathMap = PathMap()) -> Key:
    provenance = source.provenance
    where = str(paths.apply(provenance.file)) if provenance.file is not None else source.id
    return (f"{provenance.point.harness}:{provenance.point.name}", where, provenance.anchor or "")


@dataclass(frozen=True)
class VariantReading:
    """A variant's configuration as the harness would see it, and how its paths map back."""

    snapshot: ConfigSnapshot
    paths: PathMap
    # the files of sources read from the real home, outside the variant (named by the copied configuration); not
    # every file opened there — the manifests that lead to a package's sources are read but not listed
    escapes: tuple[Path, ...]

    def sources(self) -> dict[Key, tuple[Source, ...]]:
        found: dict[Key, list[Source]] = {}
        for source in self.snapshot.sources:
            found.setdefault(source_key(source, self.paths), []).append(source)
        return {key: tuple(sources) for key, sources in found.items()}

    def declared(self) -> Counter[tuple[Key, str]]:
        """What is configured, location-free: compare two of these to say whether two readings differ."""
        return Counter((source_key(s, self.paths), s.declared_hash) for s in self.snapshot.sources)


def _under(path: Path, root: Path) -> bool:
    return any(form.is_relative_to(base) for form in _forms(path) for base in _forms(root))


def read_variant(adapter: VariantHomes, home: Path, workdir: Path, project: Path,
                 copies: tuple[tuple[Path, Path], ...] = ()) -> VariantReading:
    """The configuration a run in ``workdir`` with ``home`` as HOME is given, read from disk through the adapter's
    reader of that home: no sessions (a variant has none), no agent run, nothing written. ``copies`` are the further
    (copy, original) pairs the variant placed (``Variant.path_pairs``: the project's memory under the home, say), so
    their paths map back too; the deepest pair wins."""
    snapshot = read_config(adapter.at_home(home), workdir, sessions=0).snapshot
    real = adapter.home_dir()
    escapes = sorted({s.provenance.file for s in snapshot.sources if s.provenance.file is not None
                      and _under(s.provenance.file, real)
                      and not _under(s.provenance.file, home) and not _under(s.provenance.file, workdir)})
    paths = PathMap(((home, real), (workdir, project), *copies, (real, real), (project, project)))  # the real's too
    return VariantReading(snapshot, paths, tuple(escapes))


def read_real(adapter: VariantHomes, project: Path) -> VariantReading:
    """The real configuration in ``project``, read the way a variant is (the adapter's reader of its own home, no
    sessions) and keyed the same way, so the two compare."""
    snapshot = read_config(adapter.at_home(adapter.home_dir()), project, sessions=0).snapshot
    real = adapter.home_dir()
    return VariantReading(snapshot, PathMap(((real, real), (project, project))), ())


def _in_context(source: Source) -> bool:
    return source.activation.load is not Load.NOT_IN_CONTEXT and isinstance(source.content, (FileText, ObservedText))


def _holds(sources: tuple[Source, ...], rule: Rule) -> Source | None:
    key = locator_key(rule.locator)
    for source in sources:
        if any(locator_key(r.locator) == key and r.content_hash == rule.content_hash for r in source.rules()):
            return source
    return None


def _delivers(reading: VariantReading, rule: Rule) -> Source | None:
    """An in-context source of the reading one of whose regions is the rule's text, wherever it is read from."""
    for source in reading.snapshot.sources:
        if _in_context(source) and rule.text in source.content.text:
            if any(r.content_hash == rule.content_hash for r in source.rules()):
                return source
    return None


@dataclass(frozen=True)
class Verified:
    target_id: str
    changed: tuple[str, ...]  # what else reads differently from the baseline, beyond the target's own file


def _itself(sources: tuple[Source, ...], target: Source) -> tuple[Source, ...]:
    """Those of ``sources`` (all at the target's place) that are the target itself. A whole file is whatever is read
    at its place. An entry inside a file has a place that is its position there (``/hooks/SessionStart/0/hooks/0``):
    cutting it moves the next entry into that position, so it is the one there with its declared hash."""
    if target.provenance.anchor is None:
        return sources
    return tuple(s for s in sources if s.declared_hash == target.declared_hash)


def _where(source: Source) -> str:
    return str(source.provenance.file or source.id)


def _escaped(source: Source, reading: VariantReading) -> str:
    """A note when ``source`` was read from the real home rather than the variant's copy: a file the copied
    configuration names outside what a variant copies, read where it is (05-runner.md "Escapes"). A package the
    adapter's reader of a copied home maps into the variant is not among them: it reads the variant's own copy, the
    one the harness loads (Verified Facts, E2b) and the one a cut edits. A file it reads in the real home — a package
    it doesn't map, or a path the package's own manifests name absolutely — is, and the adapter refuses a cut of a hook
    read there before the variant is built."""
    if source.provenance.file is not None and source.provenance.file in reading.escapes:
        return " — read from the real home, not the variant's copy"
    return ""


def verify(target: AblationTarget, baseline: VariantReading, without: VariantReading) -> Verified:
    """Raise AblationFailed unless the baseline variant has ``target`` (read from the real configuration), the
    variant without it doesn't — not at its place, and for a region of text not anywhere in context either — and
    the two read differently. Returns what else changed, which removing the target took with it."""
    key = source_key(target.source, baseline.paths)  # the target is read from the real configuration
    in_baseline, in_without = baseline.sources().get(key, ()), without.sources().get(key, ())
    fail = lambda reason: AblationFailed(target.id, reason)  # noqa: E731
    if target.whole:
        if not _itself(in_baseline, target.source):
            raise fail("the baseline variant doesn't read it either, so removing it can change nothing")
        if still_read := _itself(in_without, target.source):
            raise fail(f"it is still read, from {_where(still_read[0])}{_escaped(still_read[0], without)}")
    else:
        (rule,) = target.rules
        if _holds(in_baseline, rule) is None:
            raise fail("the baseline variant doesn't have its text either, so removing it can change nothing")
        if (still := _holds(in_without, rule)) is not None:
            raise fail(f"its text is still in {_where(still)}{_escaped(still, without)}")
        if _in_context(target.source) and (elsewhere := _delivers(without, rule)) is not None:
            raise fail(f"its text is still read, from {_where(elsewhere)}{_escaped(elsewhere, without)}")
        if Counter(s.declared_hash for s in in_baseline) == Counter(s.declared_hash for s in in_without):
            raise fail("its file reads the same as in the baseline")
    before, after = baseline.declared(), without.declared()
    if before == after:
        raise fail("the variant reads exactly like the baseline")
    changed = {k for k, _ in (before - after) + (after - before) if k[1] != key[1]}
    return Verified(target.id, tuple(sorted(f"{where}#{anchor}" if anchor else where for _, where, anchor in changed)))


class BaselineMismatch(VariantError):
    """The baseline variant doesn't reproduce the real configuration (D4): every verdict from it would describe a
    configuration you don't have. Lists each difference."""

    def __init__(self, differences: tuple[str, ...]) -> None:
        n = len(differences)
        super().__init__(f"the baseline variant doesn't reproduce your configuration: {n} "
                         f"{'difference' if n == 1 else 'differences'}\n  " + "\n  ".join(differences))
        self.differences = differences


@dataclass(frozen=True)
class BaselineChecked:
    """The baseline reproduces the real configuration. ``escapes`` are the files of the sources both read from the
    real home rather than the copy — the same files, so not a difference, but read live: listed, never hidden."""

    sources: int
    escapes: tuple[Path, ...]


def check_baseline(real: VariantReading, baseline: VariantReading, unplaced: tuple[str, ...] = ()) -> BaselineChecked:
    """Raise BaselineMismatch unless the baseline variant reads exactly what the real configuration declares — the
    same sources, at the same places once paths are mapped, with the same declared content (D4) — and nothing the
    placed configuration uses was left behind (``unplaced``, the adapter's sentences: VariantHomes.unplaced)."""
    before, after = real.declared(), baseline.declared()
    if before == after and not unplaced:
        return BaselineChecked(sum(before.values()), baseline.escapes)
    escaped = {str(baseline.paths.apply(file)) for file in baseline.escapes}
    by_key: dict[Key, tuple[Counter[str], Counter[str]]] = {}
    for (key, digest), n in before.items():
        by_key.setdefault(key, (Counter(), Counter()))[0][digest] += n
    for (key, digest), n in after.items():
        by_key.setdefault(key, (Counter(), Counter()))[1][digest] += n
    said = []
    for key, (mine, theirs) in sorted(by_key.items()):
        if mine == theirs:
            continue
        point, where, anchor = key
        place = f"{where}#{anchor}" if anchor else where
        note = " — read from the real home, not the copy" if where in escaped else ""
        if not theirs:
            said.append(f"missing from the baseline: {place} ({point})")
        elif not mine:
            said.append(f"only in the baseline: {place} ({point}){note}")
        elif set(mine) == set(theirs):
            said.append(f"read {sum(theirs.values())} times in the baseline, {sum(mine.values())} in your "
                        f"configuration: {place} ({point}){note}")
        else:
            said.append(f"reads differently in the baseline: {place} ({point}){note}")
    said.extend(f"not placed: {sentence}" for sentence in unplaced)
    raise BaselineMismatch(tuple(said))
