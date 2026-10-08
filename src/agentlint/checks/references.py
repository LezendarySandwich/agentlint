"""broken-reference, core part: links that provably point at nothing. Zero false positives is the bar.

A relative markdown link to a file inside the referencing file's own directory that doesn't exist.
Links escaping the directory (``../``), absolute paths, URLs and placeholders (``[title](url)``) are
out of scope, and so is anything inside code. Categories that produced any false positive on real
data were cut, not tuned (docs/tech_impl/02-static-checks.md). References to entities by name
depend on how a harness names and invokes them, so adapters contribute those under the same id.
"""

from __future__ import annotations

from agentlint.checks import Check
from agentlint.checks.files import own_file, relative_links
from agentlint.checks.text import line_of, prose_sources
from agentlint.config.content import FileText
from agentlint.config.model import ConfigSnapshot
from agentlint.config.reading import FileFacts
from agentlint.report.finding import Confidence, Finding, FindingKind, Location, Severity


def broken_links(snapshot: ConfigSnapshot, files: FileFacts) -> list[Finding]:
    """Whether a link's target exists was settled when the configuration was read (``checks/files.py``)."""
    findings = []
    for source in prose_sources(snapshot):
        if not isinstance(source.content, FileText):
            continue
        text = source.content.text
        missing: dict[str, int] = {}
        for target, path, offset in relative_links(text):
            if (own_file(source), path) in files.missing_links:
                missing.setdefault(target, line_of(text, offset))
        for target, line in missing.items():
            findings.append(CHECK.finding(
                kind=FindingKind.BROKEN, severity=Severity.ERROR,
                confidence=Confidence.CERTAIN,
                title=f"Link to {target} points at a file that doesn't exist",
                detail=f"{source.provenance.file} links to {target}, which is not in its directory.",
                discriminator=f"link:{source.id}:{target}", sources=(source.id,),
                locations=(Location(source.id, line),),
                suggested_action="Add the file, fix the link, or remove it.",
            ))
    return findings


CHECK = Check("broken-reference", "references that provably point at nothing",
              lambda reading: broken_links(reading.snapshot, reading.files))
