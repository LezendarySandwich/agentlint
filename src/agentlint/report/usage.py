"""Terminal rendering of usage: what your turns used, and what they never touch (docs/tech_impl/08-recorder.md)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from agentlint.config.usage import UsageIndex, UsageSummary


def render(summary: UsageSummary, index: UsageIndex, *, days: int, unused: bool, package_of: Mapping[str, str]) -> str:
    since = f" · history kept since {index.history_start.astimezone():%Y-%m-%d}" if index.history_start else ""
    lines = [f"agentlint usage — last {days} days · {index.turns_observed:,} of your turns{since}"]
    if index.is_empty():
        lines.append("No turns in this window yet. Usage is read from session transcripts each time agentlint runs.")
        return "\n".join(lines) + "\n"
    total = len(summary.used) + len(summary.unused)
    start = index.window_start
    if index.history_start and (start is None or index.history_start > start):
        start = index.history_start  # nothing older was kept: say nothing about before it
    since = f"since {start.astimezone():%Y-%m-%d}" if start else "in the window"
    lines += ["", f"USED  {len(summary.used)} of {total} skills and agents"]
    width = max((len(f"{e.kind.label} {e.name}") for e, _ in summary.used), default=0)
    for entity, usage in summary.used:
        name = f"{entity.kind.label} {entity.name}"
        lines.append(f"  {usage.invocations:>5}  {name:<{width}}  last used {usage.last_seen.astimezone():%Y-%m-%d}")
    if unused:
        groups: dict[str, list[str]] = defaultdict(list)
        for entity in summary.unused:
            groups[package_of.get(entity.id, "your own and built-in")].append(entity.name)
        lines += ["", f"NEVER USED {since}  {len(summary.unused)}"]
        for package, names in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
            shown = ", ".join(names[:8]) + (f", … {len(names) - 8} more" if len(names) > 8 else "")
            lines.append(f"  {len(names):>5}  {package}: {shown}")
    else:
        lines += ["", f"{len(summary.unused)} skills and agents were never used {since} (--unused lists them)."]
    if summary.unmatched:
        named = ", ".join(f"{ref.split('/', 1)[1]} ×{n}" for ref, n in sorted(summary.unmatched.items(), key=lambda i: -i[1]))
        lines.append(f"Also used, but not in this configuration (built-in commands, removed packages): {named}")
    return "\n".join(lines) + "\n"
