"""Configured hooks, and tracing the text they inject back to the entry that produced it.

A source is one configured hook entry — not one per event: on the reference machine 45
SessionStart entries come from 34 packages. Entries are declared in managed settings, in any
settings layer, inline in a package manifest's ``hooks``, in a file the manifest's ``hooks``
names, and in a package's ``hooks/hooks.json``. A ``{"modules": [...]}`` hooks file registers
handlers in code; its events and output are only knowable by observation.

Tracing a delivered text to its entry, in order — never guessing:

1. JSON ``additionalContext``: exact match against the stdout of each recorded execution, whose
   command then identifies the entry.
2. Plain stdout: the recorded (event, command) pair, when exactly one entry has it.
3. Text: line openings of the delivered text found in exactly one candidate's command, scripts,
   or — failing that — text files in its package (scripts often print a file or a heredoc).
   UserPromptSubmit records no execution, so this is its only route.

Commands are recorded unexpanded (``${CLAUDE_PLUGIN_ROOT}/hooks/x.sh``), so identical strings from
different packages stay ambiguous until step 3 separates them; anything still ambiguous is
reported untraced with its candidates.

Elimination is never evidence. Text with no recorded execution is attributed only on a text
match, even when a single entry is left: it may come from a code module, the harness itself, or a
package enabled without leaving a trace on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from agentlint.adapters.claude_code.discovery import Discovery, Package, SettingsLayer, clean
from agentlint.adapters.claude_code.points import Point
from agentlint.config.taxonomy import Authority, InjectionPoint, Load

if TYPE_CHECKING:  # the transcript reader reads this module's event table
    from agentlint.adapters.claude_code.transcripts import HookOutput, HookRun


class HookEvent(NamedTuple):
    load: Load  # when what its hooks print is in the agent's context
    point: Point  # how that text reaches the agent
    stdout_reaches_agent: bool  # plain stdout is added to context, not only shown in the transcript


# The one table of hook events: every rule about an event reads it, through the accessors below.
EVENTS: dict[str, HookEvent] = {
    "SessionStart": HookEvent(Load.ALWAYS, Point.SESSION_START_HOOK, True),
    "UserPromptSubmit": HookEvent(Load.PER_MESSAGE, Point.USER_PROMPT_HOOK, True),
    "UserPromptExpansion": HookEvent(Load.PER_MESSAGE, Point.USER_PROMPT_HOOK, True),
    "PreToolUse": HookEvent(Load.CONDITIONAL, Point.TOOL_HOOK, False),
    "PostToolUse": HookEvent(Load.CONDITIONAL, Point.TOOL_HOOK, False),
    "PostToolUseFailure": HookEvent(Load.CONDITIONAL, Point.TOOL_HOOK, False),
}
OTHER_EVENT = HookEvent(Load.NOT_IN_CONTEXT, Point.OTHER_HOOK, False)  # any other event: its output never reaches the agent
DELIVERING_STDOUT_EVENTS = frozenset(name for name, event in EVENTS.items() if event.stdout_reaches_agent)


def event_load(event: str) -> Load:
    return EVENTS.get(event, OTHER_EVENT).load


def event_point(event: str) -> InjectionPoint:
    return EVENTS.get(event, OTHER_EVENT).point.value

_PLUGIN_ROOT = re.compile(r"\$\{?CLAUDE_PLUGIN_ROOT\}?")
_PATHISH = re.compile(r"[^\s'\"]+")
SIGNATURE = 24  # characters from the start of a delivered line, matched literally
SHORT_SIGNATURE = 16  # fallback for lines whose tail is interpolated at runtime
MIN_LINE = 12  # shorter lines are too generic to identify anything
MAX_SIGNATURES = 8
_TEXT_SUFFIXES = {".md", ".sh", ".py", ".js", ".ts", ".mjs", ".json", ".txt", ".toml", ".yaml", ".yml"}
_SKIP_DIRS = {"node_modules", ".git", "__pycache__", "dist", "build", ".venv"}
_MAX_FILE = 256 * 1024


@dataclass(frozen=True)
class HookEntry:
    owner: str  # package id, or the settings layer name
    event: str
    matcher: str | None
    command: str  # as configured, unexpanded; empty for module hooks
    file: Path  # where the entry is declared
    pointer: str  # JSON pointer to the entry within that file
    authority: Authority
    package: Package | None = None
    module: bool = False  # handlers registered in code, events unknown

    @property
    def source_id(self) -> str:
        return f"claude-code:hook:{self.owner}:{self.file.name}#{self.pointer}"

    @property
    def load(self) -> Load:
        return Load.CONDITIONAL if self.module else event_load(self.event)

    @property
    def point(self) -> InjectionPoint:
        return Point.OTHER_HOOK.value if self.module else event_point(self.event)

    @property
    def condition(self) -> str | None:
        if self.module:
            return "handlers a code module registers at runtime"
        if self.load is Load.CONDITIONAL:
            return f"{self.event} on tools matching {self.matcher or 'any'}"
        return None

    def declared_hash(self) -> str:
        payload = [self.owner, self.event, self.matcher, self.command, self.pointer, self.module]
        return "sha256:" + hashlib.sha256(json.dumps(payload).encode("utf-8")).hexdigest()

    def script_paths(self) -> list[Path]:
        """Files the command runs that live inside its package, resolved from CLAUDE_PLUGIN_ROOT."""
        if not self.package or not self.command:
            return []
        expanded = _PLUGIN_ROOT.sub(str(self.package.root), self.command)
        return [Path(token) for token in _PATHISH.findall(expanded)
                if token.startswith(str(self.package.root)) and Path(token).is_file()]


def _entries(groups: object, event: str, owner: str, file: Path, base: str, authority: Authority,
             package: Package | None) -> Iterable[HookEntry]:
    for g, group in enumerate(groups if isinstance(groups, list) else []):
        if not isinstance(group, dict):
            continue
        matcher = group.get("matcher") if isinstance(group.get("matcher"), str) else None
        for h, hook in enumerate(group.get("hooks") or []):
            if isinstance(hook, dict) and hook.get("type", "command") == "command":
                yield HookEntry(owner, event, matcher or None, str(hook.get("command", "")), file,
                                f"{base}/{event}/{g}/hooks/{h}", authority, package)


def _from_mapping(hooks: object, owner: str, file: Path, base: str, authority: Authority,
                  package: Package | None) -> list[HookEntry]:
    if not isinstance(hooks, dict):
        return []
    if isinstance(hooks.get("modules"), list):
        return [HookEntry(owner, "*", None, "", file, f"{base}/modules", authority, package, module=True)]
    found: list[HookEntry] = []
    for event, groups in hooks.items():
        found.extend(_entries(groups, event, owner, file, base, authority, package))
    return found


def _read(path: Path) -> dict[str, Any]:
    try:
        data = clean(json.loads(path.read_text(encoding="utf-8", errors="replace")))  # a stray byte mustn't hide its hooks
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _package_entries(package: Package) -> list[HookEntry]:
    found: list[HookEntry] = []
    manifest_file = package.root / ".claude-plugin" / "plugin.json"
    declared = package.manifest.get("hooks")
    files: list[Path] = []
    if isinstance(declared, dict):
        found.extend(_from_mapping(declared, package.id, manifest_file, "/hooks", Authority.PACKAGE, package))
    for item in [declared] if isinstance(declared, str) else declared if isinstance(declared, list) else []:
        if isinstance(item, str):
            files.append((package.root / item).resolve())
    default = package.root / "hooks" / "hooks.json"
    if default.is_file() and default.resolve() not in files:
        files.append(default.resolve())
    for path in files:
        data = _read(path)
        mapping = data.get("hooks") if isinstance(data.get("hooks"), dict) else data
        found.extend(_from_mapping(mapping, package.id, path, "/hooks", Authority.PACKAGE, package))
    return found


def hook_entries(discovery: Discovery) -> list[HookEntry]:
    layers = list(discovery.layers)
    managed = next((layer for layer in layers if layer.name == "managed"), None)
    if managed and managed.data.get("disableAllHooks") is True:
        return []
    managed_only = bool(managed and managed.data.get("allowManagedHooksOnly") is True)
    found: list[HookEntry] = []
    for layer in layers:
        if managed_only and layer is not managed:
            continue
        found.extend(_from_mapping(layer.data.get("hooks"), layer.name, layer.path, "/hooks", layer.authority, None))
    if not managed_only:
        for package in discovery.packages:
            found.extend(_package_entries(package))
    return found


@dataclass(frozen=True)
class Traced:
    output: HookOutput
    entry: HookEntry | None  # None when it could not be traced
    candidates: tuple[HookEntry, ...]  # every entry it might be, when untraced
    command: str | None  # the command the transcript records for it: its own, or that of the run whose JSON carried it


def _additional_context(run: HookRun) -> str | None:
    try:
        data = json.loads(run.stdout)
    except ValueError:
        return None
    output = data.get("hookSpecificOutput") if isinstance(data, dict) else None
    context = output.get("additionalContext") if isinstance(output, dict) else None
    return context if isinstance(context, str) else None


def signatures(text: str, length: int = SIGNATURE) -> list[str]:
    """Literal openings of the delivered text's lines, long enough to identify their origin."""
    found: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= max(MIN_LINE, length) and line[:length] not in found:
            found.append(line[:length])
        if len(found) == MAX_SIGNATURES:
            break
    return found


class Haystacks:
    """Text to search for each entry, read lazily and cached."""

    def __init__(self) -> None:
        self._scripts: dict[str, str] = {}
        self._packages: dict[str, str] = {}

    def scripts(self, entry: HookEntry) -> str:
        if entry.source_id not in self._scripts:
            texts = [entry.command]
            for path in entry.script_paths():
                try:
                    texts.append(path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    pass
            self._scripts[entry.source_id] = "\n".join(texts)
        return self._scripts[entry.source_id]

    def package(self, entry: HookEntry) -> str:
        """All text files in the entry's package, read once per run."""
        if entry.package is None:
            return ""
        key = entry.package.id
        if key not in self._packages:
            texts = []
            for directory, subdirs, files in os.walk(entry.package.root):
                subdirs[:] = sorted(d for d in subdirs if d not in _SKIP_DIRS and not d.startswith("."))
                for name in sorted(files):
                    path = Path(directory, name)
                    try:
                        if path.suffix in _TEXT_SUFFIXES and path.stat().st_size <= _MAX_FILE:
                            texts.append(path.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        pass
            self._packages[key] = "\n".join(texts)
        return self._packages[key]


def _narrow_scripts(candidates: list[HookEntry], marks: list[str], haystacks: Haystacks) -> list[HookEntry]:
    return [entry for entry in candidates if any(mark in haystacks.scripts(entry) for mark in marks)]


def _narrow_packages(candidates: list[HookEntry], marks: list[str], haystacks: Haystacks) -> list[HookEntry]:
    return [entry for entry in candidates if any(mark in haystacks.package(entry) for mark in marks)]


def _matcher_allows(entry: HookEntry, target: str | None) -> bool:
    """Whether an entry's matcher accepts the run's match target ("startup", a tool name...)."""
    if target is None or entry.matcher in (None, "", "*"):
        return True
    try:
        return re.fullmatch(entry.matcher, target) is not None
    except re.error:
        return entry.matcher == target


def trace_outputs(
    outputs: Iterable[HookOutput],
    runs: Iterable[HookRun],
    entries: list[HookEntry],
    haystacks: Haystacks | None = None,
    memo: dict | None = None,
) -> list[Traced]:
    """Trace each output to its entry. Pass the same ``haystacks`` and ``memo`` across sessions."""
    by_event_command: dict[tuple[str, str], list[HookEntry]] = {}
    for entry in entries:
        by_event_command.setdefault((entry.event, entry.command), []).append(entry)
    command_for_context = {context: run for run in runs if (context := _additional_context(run))}
    haystacks = haystacks or Haystacks()
    memo = memo if memo is not None else {}
    traced: list[Traced] = []
    for output in outputs:
        command, hook_name = output.command, output.hook_name
        if command is None and output.text in command_for_context:
            run = command_for_context[output.text]
            command, hook_name = run.command, run.hook_name
        candidates = by_event_command.get((output.event, command), []) if command is not None else [
            entry for entry in entries if entry.event == output.event and not entry.module
        ]
        target = hook_name.partition(":")[2] or None
        key = (output.event, command, target, output.text)
        if key in memo:
            traced.append(Traced(output, *memo[key], command))
            continue
        candidates = [entry for entry in candidates if _matcher_allows(entry, target)]
        linked = command is not None  # a recorded execution names the command
        text_evidence = False
        for length in (SIGNATURE, SHORT_SIGNATURE):
            marks = signatures(output.text, length)
            if not candidates or not marks or (linked and len(candidates) == 1):
                break
            narrowed = _narrow_scripts(candidates, marks, haystacks) or _narrow_packages(candidates, marks, haystacks)
            if narrowed:
                candidates, text_evidence = narrowed, True
                break
        entry = candidates[0] if len(candidates) == 1 and (linked or text_evidence) else None
        memo[key] = (entry, tuple(candidates) if entry is None else ())
        traced.append(Traced(output, *memo[key], command))
    return traced
