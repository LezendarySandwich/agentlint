"""trigger-overlap: entities whose descriptions are so alike that choosing between them is a coin flip.

Lexical, not semantic (decided 2026-10-02): TF-IDF over each entity's short name and description,
cosine similarity between every two entities chosen from the same list (namespace). Deterministic,
no model, no cache. The threshold comes from hand-labelled pairs: related descriptions that share
wording (up to 0.69 on the reference machine) were all judged distinguishable, so only near-copies
are reported — the same description installed under two names.

- One entity per name: a skill and a command sharing a name are one choice for the agent; the one
  it was shown stands for both.
- Pairs sharing a short name are duplicate-name's findings, not these.
- Pairs inside one package are usually a deliberate family, so they are skipped by default.
- The pair are the finding's ``members`` (name, kind, source, where it sits, what the agent saw, the description),
  which the renderers lay out; ``detail`` holds the shared wording and what the exposure means.
- Exposure comes from ``Visibility``: **active** when the agent was shown both descriptions,
  **latent** when it wasn't — the overlap is there, waiting for both to be described — and
  **unknown** when no recent session shows what it saw.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from agentlint.checks import Check, member
from agentlint.config.entity import Entity, Visibility
from agentlint.config.model import ConfigSnapshot
from agentlint.report.finding import Confidence, Figure, Finding, FindingKind, Location, Severity

THRESHOLD = 0.8  # above every pair labelled distinguishable (16 of 16, 0.27–0.69; 2026-10-02)
SHOWN_TERMS = 6
_STOPWORDS = frozenset(
    "a an the and or of to in for on with by is are be this that it its as at from use used using when you your "
    "user asks ask wants want any all can will should into via".split()
)
_TERM = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Overlap:
    similarity: float
    first: Entity
    second: Entity
    shared: tuple[str, ...]  # the terms contributing most to the similarity

    @property
    def names(self) -> tuple[str, str]:
        return self.first.name, self.second.name


def _terms(entity: Entity) -> list[str]:
    text = f"{entity.short_name.replace('-', ' ').replace('_', ' ')} {entity.description}".lower()
    return [term for term in _TERM.findall(text) if len(term) > 1 and term not in _STOPWORDS]


def _package(snapshot: ConfigSnapshot, entity: Entity) -> str | None:
    try:
        return snapshot.source(entity.provided_by).provenance.package
    except KeyError:
        return None


def choices(snapshot: ConfigSnapshot) -> list[Entity]:
    """One described entity per (namespace, name): the one the agent was shown, if any."""
    best: dict[tuple[str, str], Entity] = {}
    for entity in snapshot.entities:
        if not (entity.description or "").strip():
            continue
        key = (entity.kind.namespace, entity.name)
        held = best.get(key)
        if held is None or Visibility.rank_seen(snapshot.seen_as(entity.id)) < Visibility.rank_seen(snapshot.seen_as(held.id)):
            best[key] = entity
    return sorted(best.values(), key=lambda e: (e.kind.namespace, e.name))


def _vectors(entities: Sequence[Entity]) -> list[dict[str, float]]:
    """Sublinear TF × smoothed IDF over all the descriptions, each vector of unit length."""
    counts = [Counter(_terms(entity)) for entity in entities]
    documents = Counter(term for count in counts for term in count)
    total = len(counts)
    vectors = []
    for count in counts:
        weights = {term: (1 + math.log(n)) * (math.log((total + 1) / (documents[term] + 1)) + 1)
                   for term, n in count.items()}
        norm = math.sqrt(sum(w * w for w in weights.values())) or 1.0
        vectors.append({term: w / norm for term, w in weights.items()})
    return vectors


def overlaps(snapshot: ConfigSnapshot, *, floor: float = THRESHOLD, same_package: bool = False) -> list[Overlap]:
    """Every pair of choices at or above ``floor``, most similar first."""
    entities = choices(snapshot)
    vectors = _vectors(entities)
    packages = [_package(snapshot, entity) for entity in entities]
    found = []
    for i, first in enumerate(entities):
        for j in range(i + 1, len(entities)):
            second = entities[j]
            if first.kind.namespace != second.kind.namespace or first.short_name == second.short_name:
                continue
            if not same_package and packages[i] is not None and packages[i] == packages[j]:
                continue
            small, large = sorted((vectors[i], vectors[j]), key=len)
            products = {term: weight * large[term] for term, weight in small.items() if term in large}
            similarity = sum(products.values())
            if similarity >= floor:
                shared = tuple(term for term, _ in sorted(products.items(), key=lambda kv: (-kv[1], kv[0]))[:SHOWN_TERMS])
                found.append(Overlap(similarity, first, second, shared))
    return sorted(found, key=lambda o: (-o.similarity, o.names))


def run(snapshot: ConfigSnapshot) -> list[Finding]:
    findings = []
    for overlap in overlaps(snapshot):
        first, second = overlap.first, overlap.second
        seen = [snapshot.seen_as(e.id) for e in (first, second)]
        exposure = ("active" if all(v is Visibility.DESCRIBED for v in seen) else
                    "unknown" if any(v is None for v in seen) else "latent")
        meaning = {
            "active": "The agent was shown both descriptions and has nothing in them to choose between by.",
            "latent": "Latent: the agent wasn't shown both descriptions in the latest session; if it is, it has "
                      "nothing in them to choose between by.",
            "unknown": "No recent session shows what the agent saw of them; if it sees both descriptions, it has "
                       "nothing in them to choose between by.",
        }[exposure]
        locations = []
        for e in (first, second):
            try:
                locations.append(Location(snapshot.source(e.provided_by).id))
            except KeyError:
                pass
        findings.append(CHECK.finding(
            kind=FindingKind.COLLISION,
            severity=Severity.INFO if exposure == "latent" else Severity.WARN,
            confidence=Confidence.SUGGESTIVE,
            title=f"{first.name} and {second.name} have nearly identical descriptions ({overlap.similarity:.2f})"
                  + {"active": "", "latent": " (latent)", "unknown": " (exposure unknown)"}[exposure],
            detail=f"Shared wording: {', '.join(overlap.shared)}\n{meaning}",
            discriminator=f"{first.kind.namespace}:{first.name}|{second.name}",
            sources=tuple(dict.fromkeys((first.provided_by, second.provided_by))),
            locations=tuple(locations),
            figures=(Figure("similarity", round(overlap.similarity, 2), "cosine", "cosine"),),
            suggested_action="If they are copies, remove the one you don't use; if not, rewrite one description "
                             "to say when to use it rather than the other.",
            members=(member(snapshot, first), member(snapshot, second)),
        ))
    return findings


CHECK = Check("trigger-overlap", "entities whose descriptions are nearly identical (suggestive)",
              lambda reading: run(reading.snapshot),
              needs_change=True)  # similarity weighs terms by how common they are across all descriptions
