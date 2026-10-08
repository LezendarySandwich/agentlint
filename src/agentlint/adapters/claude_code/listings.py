"""Split a delivered listing into the text each package contributed.

Users disable packages, not listing lines, so the always-on cost of the skill, agent and
deferred-tool listings is attributed to the package behind each entry. The listing header,
the harness's built-ins and your own user/project entities stay unattributed (owner ``None``).

A line starts a new entry only when it names something the transcript recorded as delivered:
descriptions can span lines and can themselves contain ``- `` bullets.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from agentlint.adapters.claude_code.discovery import Package


def split_listing(text: str, delivered: Iterable[str], owner_of: Mapping[str, str | None]) -> dict[str | None, str]:
    names = set(delivered)
    parts: dict[str | None, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if line.startswith("- ") and line[2:].split(": ", 1)[0].strip() in names:
            current = owner_of.get(line[2:].split(": ", 1)[0].strip())
        elif stripped in names:  # deferred tools: one bare name per line
            parts.setdefault(owner_of.get(stripped), []).append(line)
            current = None
            continue
        elif stripped.startswith(("</", "<system-reminder")):
            current = None
        parts.setdefault(current, []).append(line)
    return {owner: "\n".join(lines) for owner, lines in parts.items()}


_TOOLS_SUFFIX = re.compile(r"\s*\(Tools: .*\)\s*$")


def listing_descriptions(text: str, delivered: Iterable[str]) -> dict[str, str]:
    """What a listing says about each delivered entry (``- name: description``), on one line.

    An entry ends at the next entry, a blank line or a closing tag, so prose after the list is never
    taken for a description; an agent entry's trailing ``(Tools: …)`` is not part of it.
    """
    names = set(delivered)
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("- ") and line[2:].split(": ", 1)[0].strip() in names:
            name, colon, rest = line[2:].partition(": ")
            current = name.strip() if colon else None  # a bare name has no description to continue
            if current:
                found[current] = [rest]
        elif not line.strip() or line.strip().startswith(("</", "<system-reminder")):
            current = None
        elif current:
            found[current].append(line)
    described = {name: _TOOLS_SUFFIX.sub("", " ".join(" ".join(parts).split())) for name, parts in found.items()}
    return {name: text for name, text in described.items() if text}


def tool_package(tool: str, packages: Iterable[Package]) -> str | None:
    """The package an MCP tool belongs to: ``mcp__plugin_<package>_<server>__<tool>``.

    Package names may contain underscores, so the longest matching package name wins.
    """
    if not tool.startswith("mcp__plugin_"):
        return None
    rest = tool[len("mcp__plugin_"):]
    matches = [p for p in packages if rest.startswith(p.name + "_")]
    return max(matches, key=lambda p: len(p.name)).id if matches else None
