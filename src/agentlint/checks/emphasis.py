"""emphasis-density: sources that shout far more than the rest. Suggestive, never certain.

A proxy for alert fatigue, and a sign that an author was fighting behaviour they couldn't test.
Markers are data — ``agentlint/data/emphasis.toml``, extended by ``~/.agentlint/emphasis.toml``.
Each match scores ``weight × scale(run length)``; a source's loudness is its points per 100 words
of prose, code excluded. Reported against the user's other sources, never absolutely: each source
in the top decile gets a finding, saying when it is in context.
"""

from __future__ import annotations

import math
import re
import statistics
import tomllib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from agentlint.checks import Check, CheckError, NoSession, NotEvaluated
from agentlint.checks.text import prose_sources
from agentlint.config.markdown import blank_code, frontmatter_end
from agentlint.config.model import ConfigSnapshot, Source
from agentlint.config.taxonomy import Load
from agentlint.report.finding import Confidence, Figure, Finding, FindingKind, Location, Severity
from agentlint.report.show import label

USER_MARKERS: Path | None = None  # None: emphasis.toml in agentlint's home
MIN_WORDS = 50  # below this a single marker swings the score; such sources are not scored
MIN_POPULATION = 20  # fewer sources than this and "louder than most" means nothing
TOP_SHARE = 0.10  # the top decile
SHOWN_DRIVERS = 6

_WHEN = {
    Load.ALWAYS: "and in context every session",
    Load.PER_MESSAGE: "and in context with every message",
    Load.ON_INVOKE: "in context when invoked",
    Load.CONDITIONAL: "in context when its condition holds",
}

SCALES = {
    "flat": lambda n: 1.0,
    "linear": lambda n: float(n),
    "superlinear": lambda n: 2.0 * n - 1,  # 1, 3, 5, 7: escalation penalised, bounded
}
UNITS = ("chars", "words")
_FIELDS = {"id", "pattern", "weight", "scale", "unit", "enabled"}
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")


@dataclass(frozen=True)
class Marker:
    id: str
    pattern: re.Pattern[str]
    weight: float
    scale: str = "flat"
    unit: str = "chars"

    def points(self, match: str) -> float:
        run = len(match.split()) if self.unit == "words" else len(match)
        return self.weight * SCALES[self.scale](run)


@dataclass(frozen=True)
class Driver:
    """One distinct piece of emphasis in a source — ``MUST``, ``"!!!"`` — and what it contributed."""

    text: str
    marker: str
    count: int
    points: float

    def __str__(self) -> str:
        shown = self.text if re.fullmatch(r"[A-Za-z']+", self.text) else f'"{self.text}"'
        return f"{shown} ×{self.count}"


@dataclass(frozen=True)
class Loudness:
    source: Source
    words: int
    points: float
    drivers: tuple[Driver, ...]  # most points first

    @property
    def per_100_words(self) -> float:
        return self.points * 100 / self.words


def _read(text: str, where: str) -> list[dict]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise CheckError(f"{where}: {error}") from error
    unknown = sorted(set(data) - {"marker"})
    if unknown:
        raise CheckError(f"{where}: unknown table or key {', '.join(unknown)}; markers go in [[marker]] tables")
    markers = data.get("marker", [])
    if not isinstance(markers, list) or not all(isinstance(m, dict) for m in markers):
        raise CheckError(f"{where}: markers must be [[marker]] tables")
    for marker in markers:
        if not isinstance(marker.get("id"), str) or not marker["id"]:
            raise CheckError(f"{where}: every [[marker]] needs an id")
    return markers


def _marker(fields: dict, where: str) -> Marker:
    name = fields["id"]
    unknown = set(fields) - _FIELDS
    if unknown:
        raise CheckError(f"{where}: marker {name!r} has unknown field(s): {', '.join(sorted(unknown))}")
    if not isinstance(fields.get("pattern"), str):
        raise CheckError(f"{where}: marker {name!r} needs a pattern")
    try:
        pattern = re.compile(fields["pattern"])
    except re.error as error:
        raise CheckError(f"{where}: marker {name!r} has a bad pattern: {error}") from error
    if pattern.search("") is not None:
        raise CheckError(f"{where}: marker {name!r} matches empty text, so it would match everywhere")
    weight = fields.get("weight", 1.0)
    if isinstance(weight, bool) or not isinstance(weight, int | float) or weight < 0:
        raise CheckError(f"{where}: marker {name!r} needs a weight of zero or more")
    scale, unit = fields.get("scale", "flat"), fields.get("unit", "chars")
    if scale not in SCALES:
        raise CheckError(f"{where}: marker {name!r} has scale {scale!r}; use one of {', '.join(SCALES)}")
    if unit not in UNITS:
        raise CheckError(f"{where}: marker {name!r} has unit {unit!r}; use one of {', '.join(UNITS)}")
    if not isinstance(fields.get("enabled", True), bool):
        raise CheckError(f"{where}: marker {name!r} has enabled = {fields['enabled']!r}; use true or false")
    return Marker(name, pattern, float(weight), scale, unit)


def load_markers(user_file: Path | None) -> list[Marker]:
    """The shipped markers with the user's file merged over them, field by field, by id."""
    shipped = resources.files("agentlint").joinpath("data", "emphasis.toml").read_text("utf-8")
    merged = {fields["id"]: dict(fields) for fields in _read(shipped, "shipped emphasis.toml")}
    touched: set[str] = set()
    if user_file is not None and user_file.is_file():
        try:
            overrides = _read(user_file.read_text("utf-8"), str(user_file))
        except UnicodeDecodeError as error:
            raise CheckError(f"{user_file}: not UTF-8 text ({error.reason} at byte {error.start})") from error
        except OSError as error:
            raise CheckError(f"{user_file}: can't read it ({error})") from error
        for override in overrides:
            merged.setdefault(override["id"], {}).update(override)
            touched.add(override["id"])
    for name, fields in merged.items():
        if name in touched and "pattern" not in fields:
            raise CheckError(f"{user_file}: marker {name!r} isn't one of the shipped markers; "
                             "a new marker needs a pattern")
    checked = [(_marker(fields, str(user_file) if name in touched else "shipped emphasis.toml"), fields)
               for name, fields in merged.items()]  # every marker is checked, switched off or not
    return [marker for marker, fields in checked if fields.get("enabled", True)]


def _prose_of(source: Source) -> str:
    """What the source delivers as prose: its body (frontmatter is listing metadata), code removed."""
    lines = source.content.text.splitlines(keepends=True)
    return blank_code("".join(lines[frontmatter_end(lines):]))


def loudness(source: Source, markers: Sequence[Marker]) -> Loudness | None:
    """How loud a source is, or None if it is too short to say.

    Where markers overlap, the text scores once: the highest-scoring match wins, and a run counted
    in words (capitals) is found in the text as written, then counts only the words no other marker
    scored — so "PKG ACL MUST" is one MUST, not a three-word shout as well, and "API!!! KEY TOKEN"
    is no run at all."""
    text = _prose_of(source)
    words = len(_WORD.findall(text))
    if words < MIN_WORDS:
        return None
    candidates = [(match.start(), match.end(), marker.points(match.group(0)), marker.id, match.group(0))
                  for marker in markers if marker.unit != "words"
                  for match in marker.pattern.finditer(text) if match.group(0)]
    chosen: list[tuple[int, int, float, str, str]] = []
    for candidate in sorted(candidates, key=lambda c: (-c[2], c[0])):
        if all(candidate[1] <= kept[0] or candidate[0] >= kept[1] for kept in chosen):
            chosen.append(candidate)
    runs = []
    for marker in markers:
        if marker.unit != "words":
            continue
        for match in marker.pattern.finditer(text):  # runs as written: a run never spans other punctuation
            span = list(match.group(0))
            for start, end, *_ in chosen:  # words another marker scored don't count toward the run
                low, high = max(start, match.start()), min(end, match.end())
                if low < high:
                    span[low - match.start():high - match.start()] = " " * (high - low)
            for part in marker.pattern.finditer("".join(span)):
                if part.group(0):
                    runs.append((match.start() + part.start(), match.start() + part.end(),
                                 marker.points(part.group(0)), marker.id, part.group(0)))
    chosen += runs
    found: dict[tuple[str, str], list[float]] = defaultdict(list)
    for _, _, points, marker_id, matched in chosen:
        found[(" ".join(matched.split()), marker_id)].append(points)
    drivers = sorted((Driver(text_, marker, len(points), sum(points)) for (text_, marker), points in found.items()),
                     key=lambda d: (-d.points, d.text))
    return Loudness(source, words, sum(d.points for d in drivers), tuple(drivers))


def measure(snapshot: ConfigSnapshot, markers: Sequence[Marker]) -> list[Loudness]:
    """Loudness of every prose source that reaches the model and is long enough to score."""
    measured = (loudness(s, markers) for s in prose_sources(snapshot) if s.activation.load is not Load.NOT_IN_CONTEXT)
    return [m for m in measured if m is not None]


def _ordinal(rank: int) -> str:
    return "" if rank == 1 else f"{rank}{ {2: 'nd', 3: 'rd'}.get(rank, 'th')} "


def _share_below(m: Loudness, rates: Sequence[float]) -> float:
    return sum(1 for rate in rates if rate < m.per_100_words) / len(rates)


def _drivers(measured: Loudness) -> str:
    shown = ", ".join(str(d) for d in measured.drivers[:SHOWN_DRIVERS])
    rest = len(measured.drivers) - SHOWN_DRIVERS
    return shown + (f", and {rest} more" if rest > 0 else "")


def findings(snapshot: ConfigSnapshot, markers: Sequence[Marker]) -> list[Finding]:
    if not snapshot.sessions:  # hook output is part of the population; ranking without it shifts every rank
        raise NoSession("no recent sessions, so hook output can't be compared with the rest")
    measured = measure(snapshot, markers)
    if len(measured) < MIN_POPULATION:
        raise NotEvaluated(f"only {len(measured)} sources are long enough to score; comparing needs {MIN_POPULATION}")
    rates = sorted(m.per_100_words for m in measured)
    median = statistics.median(rates)
    cutoff = rates[len(rates) - max(1, math.ceil(len(rates) * TOP_SHARE))]  # the quietest of the top decile
    loud = sorted((m for m in measured if m.points > 0 and m.per_100_words >= cutoff and m.per_100_words > median),
                  key=lambda m: -m.per_100_words)  # louder than the typical source, whatever ties at the cutoff

    def standing(m: Loudness) -> str:
        rank = 1 + sum(1 for rate in rates if rate > m.per_100_words)
        if rank <= 10:
            return f"the {_ordinal(rank)}loudest of your {len(rates)} sources"
        return f"louder than {int(_share_below(m, rates) * 100)}% of your {len(rates)} sources"

    results = []
    for m in loud:
        source = m.source
        results.append(CHECK.finding(
            kind=FindingKind.EMPHASIS, severity=Severity.INFO,
            confidence=Confidence.SUGGESTIVE,
            title=f"{label(source, snapshot.entities_by_source)} is {standing(m)}, {_WHEN.get(source.activation.load, 'in context')}",
            detail=(f"{m.per_100_words:.1f} emphasis points per 100 words; your median source scores {round(median, 1):g}.\n"
                    f"Driven by: {_drivers(m)}"),
            discriminator=f"source:{source.id}",
            sources=(source.id,),
            locations=(Location(source.id),),
            figures=(Figure("per 100 words", round(m.per_100_words, 1), "points", "point"),
                     Figure("median source", round(median, 2), "points", "point"),
                     Figure("louder than", int(_share_below(m, rates) * 100), "percent", "percent")),
            suggested_action="Emphasis stops working when everything is emphasised: keep it for the few rules "
                             "that really are exceptional.",
        ))
    return results


def run(snapshot: ConfigSnapshot) -> list[Finding]:
    from agentlint.store import home

    return findings(snapshot, load_markers(USER_MARKERS or home() / "emphasis.toml"))


CHECK = Check("emphasis-density", "sources that shout far more than the rest (suggestive)",
              lambda reading: run(reading.snapshot), needs_change=True)  # ranked
