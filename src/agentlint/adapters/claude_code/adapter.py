"""The Claude Code adapter: everything configured (disk) and what recent sessions delivered.

``read`` discovers what disk declares — fresh each time, nothing kept for the next read — and, when asked
for sessions, reads recent transcripts and traces what they delivered back to the declared sources; text it
cannot trace is passed on untraced, never attributed by guess. What this adapter's checks need beyond the
snapshot comes back from the same read as ``Evidence``.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from agentlint.adapters.claude_code import acting, terms
from agentlint.adapters.claude_code import conversation as conversations
from agentlint.adapters.claude_code import variants
from agentlint.adapters.claude_code.discovery import (
    Discovery,
    Package,
    Paths,
    configured_model,
    discover,
    harness_version,
)
from agentlint.adapters.claude_code.entities import DiskEntity, disk_entities
from agentlint.adapters.claude_code.hooks import Haystacks, HookEntry, event_load, event_point, hook_entries, trace_outputs
from agentlint.adapters.claude_code.instructions import Instructions, instruction_files, project_slug
from agentlint.adapters.claude_code.listings import listing_descriptions, split_listing, tool_package
from agentlint.adapters.claude_code.package_paths import missing_paths
from agentlint.adapters.claude_code.trace import read_trace
from agentlint.adapters.claude_code.points import HARNESS, Kind, Point, Scope, entity_id
from agentlint.adapters.claude_code.transcripts import (
    HookOutput,
    HookRecord,
    FirstTurn,
    hook_records,
    main_transcripts,
    parse_session,
    records,
    turns_from,
)
from agentlint.config.content import FileText, NotObserved, content_hash, file_text
from agentlint.config.conversation import BriefingPart, Conversation, SessionListing, Share, TranscriptFile
from agentlint.config.entity import Entity, Visibility
from agentlint.config.usage import Turn
from agentlint.config.formats import ContentFormat
from agentlint.config.locators import KeyPath
from agentlint.config.model import Ablation, AblationPolicy, Activation, Declared, Provenance, Source
from agentlint.config.reading import Reading
from agentlint.config.sessions import Delivery, SessionDeliveries
from agentlint.config.taxonomy import Authority, InjectionPoint, Load
from agentlint.config.tokens import TokenCount, TokenMethod, estimate_tokens, estimated
from agentlint.harness import AdapterRead
from agentlint.trace.model import Action, Trace
from agentlint.trace.terms import ActionTerms
from agentlint.runner.variant import CopyPlan

SYSTEM_PROMPT = "claude-code:builtin:system-prompt"
TOOLS = "claude-code:builtin:tools"
SKILL_LISTING = "claude-code:listing:skills"
AGENT_LISTING = "claude-code:listing:agents"
DEFERRED_TOOLS = "claude-code:listing:deferred-tools"
SESSION_CONTEXT = "claude-code:session-context"

_ONLY_A_SESSION = "only a session can show this text"
SCAN_SECONDS = 120.0  # how long the reader traces briefings against one discovery of a project's configuration
# before it starts another, in the background; the last one serves meanwhile
SCAN_KEPT_SECONDS = 1800.0  # and how long that last one may serve: older, a read waits for the fresh one
_BUILT_INS = (  # in no file: the harness holds or composes each, and its point's label says which it is
    (SYSTEM_PROMPT, Point.SYSTEM_PROMPT, Authority.HARNESS, "part of the harness"),
    (TOOLS, Point.TOOL_DEFINITIONS, Authority.HARNESS, "part of the harness"),
    (SKILL_LISTING, Point.SKILL_LISTING, Authority.RUNTIME,
     "composed by the harness from installed skills; remove those instead"),
    (AGENT_LISTING, Point.AGENT_LISTING, Authority.RUNTIME,
     "composed by the harness from installed agents; remove those instead"),
    (DEFERRED_TOOLS, Point.DEFERRED_TOOLS, Authority.RUNTIME,
     "composed by the harness from connected servers; disable those instead"),
    (SESSION_CONTEXT, Point.SESSION_CONTEXT, Authority.RUNTIME,
     "composed by the harness for each session"),
)
_ENTITY_POINTS = {
    Kind.SKILL: Point.SKILL,
    Kind.COMMAND: Point.COMMAND,
    Kind.WORKFLOW: Point.WORKFLOW,
    Kind.AGENT: Point.AGENT_DEFINITION,
}
_AUTHORITY = {Scope.PACKAGE: Authority.PACKAGE, Scope.USER: Authority.USER, Scope.PROJECT: Authority.PROJECT}


@dataclass(frozen=True)
class Scan:
    """What disk declares for one directory, discovered for one read."""

    discovery: Discovery
    entities: list[DiskEntity]
    instructions: list[Instructions]
    hooks: list[HookEntry]
    haystacks: Haystacks

    def skill_owners(self) -> dict[str, str | None]:
        return {e.name: e.package.id if e.package else None
                for e in self.entities if e.kind is not Kind.AGENT and e.model_invocable}

    def agent_owners(self) -> dict[str, str | None]:
        return {e.name: e.package.id if e.package else None for e in self.entities if e.kind is Kind.AGENT}

    def listing_parts(self) -> dict[str, set[str]]:
        """Base listing id -> the packages that get a share of it."""
        servers = {p.id for p in self.discovery.packages if p.manifest.get("mcpServers")}
        return {
            SKILL_LISTING: {owner for owner in self.skill_owners().values() if owner},
            AGENT_LISTING: {owner for owner in self.agent_owners().values() if owner},
            DEFERRED_TOOLS: servers,
        }


def scan(paths: Paths, cwd: Path) -> Scan:
    discovery = discover(paths, cwd)
    managed = next((layer for layer in discovery.layers if layer.name == "managed"), None)
    return Scan(discovery, disk_entities(discovery, paths, cwd), instruction_files(paths, cwd, managed),
                hook_entries(discovery), Haystacks())


@dataclass(frozen=True)
class Evidence:
    """What this adapter's checks judge a reading by beyond its snapshot, taken in the same read (``Reading.evidence``)."""

    hooks: tuple[HookEntry, ...] = ()  # every hook configured now
    packages: tuple[Package, ...] = ()  # every package installed and enabled
    sessions: tuple[FirstTurn, ...] = ()  # the sessions read, most recent first; none when no sessions were asked for
    hook_runs: tuple[HookRecord, ...] = ()  # every hook execution those sessions recorded
    missing_paths: frozenset[tuple[str, str]] = frozenset()  # (package id, path): named by a hook or package text,
    # absent from the package's root when read


def _package_texts(declared: Declared, sessions: tuple[SessionDeliveries, ...]) -> list[tuple[str, str]]:
    """(package id, text) for every text a package source may hold in the snapshot: its file, or what a session
    delivered for it."""
    package_of = {source.id: source.provenance.package for source in declared.sources if source.provenance.package}
    texts = [(package_of[s.id], s.content.text) for s in declared.sources
             if s.id in package_of and isinstance(s.content, FileText) and s.content.format is not ContentFormat.CODE]
    return texts + [(package_of[d.source_id], d.text) for session in sessions for d in session.deliveries
                    if d.source_id in package_of]


def _source(
    source_id: str,
    point: Point | InjectionPoint,
    file: Path | None,
    authority: Authority,
    activation: Activation,
    content,
    ablation: AblationPolicy,
    *,
    package: str | None = None,
    anchor: str | None = None,
    declared_hash: str | None = None,
) -> Source:
    injection = point.value if isinstance(point, Point) else point
    return Source(
        source_id,
        Provenance(injection, file, authority, package, anchor),
        activation,
        declared_hash or content.content_hash,
        content,
        ablation,
    )


def _parses_as_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def _untraced_label(output: HookOutput, candidates: tuple[HookEntry, ...]) -> str:
    opening = next((line.strip() for line in output.text.splitlines() if line.strip()), "")[:60]
    packages = sorted({entry.owner for entry in candidates})
    if len(packages) == 1 and candidates:
        origin = f"one of {len(candidates)} {output.event} hooks in {packages[0]}"
    elif candidates:
        origin = f"{len(candidates)} candidate {output.event} hooks"
    else:
        origin = f"no configured {output.event} hook matches"
    return f'{output.event} hook output "{opening}" — not traced ({origin})'


def _untraced_key(output: HookOutput, command: str | None) -> str:
    """What tells this untraced output from another at its point, the same from one reading to the next — unlike its
    label, which names its opening line and how many hooks it might be. ``command`` is the command the transcript
    records for it — its own for plain stdout; for JSON ``additionalContext``, that of the run whose stdout carried it
    — when no more than one hook the reading configures for the event has that command: then the output is known by
    that command, so its key holds while what it prints changes. Otherwise ``command`` is None — async responses and every
    UserPromptSubmit record none, and a command several configured hooks share (two packages'
    ``hooks/session-start.sh``) names none of them — and the output has nothing but its text to tell it apart, so its
    key is a digest of the text, whitespace aside, and holds while the text does. Whether a command is shared is read
    from the configuration, which is the same in every session of a reading, never from which hooks printed in one
    session: decided per session, a hook that printed alone took the command's key, so its id changed with whether the
    other printed, and two hooks that each printed alone in different sessions shared one key and one ``seen_in``. A
    command no configured hook has is known by itself: the configuration cannot say it is shared. Neither holds the
    hookName: past its event it names what fired
    the hook (for SessionStart, how the session began: startup, clear, resume, compact), which changes from one session
    to the next while the configured hook does not."""
    if command:
        return f"{output.event}\0command\0{command}"
    digest = hashlib.sha256(" ".join(output.text.split()).encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return f"{output.event}\0{output.channel}\0text\0{digest}"


def package_label(package_id: str) -> str:
    """A plugin's name: its id is "<name>@<marketplace>", split as discovery.resolve_packages splits it; an id
    without both parts is shown whole."""
    name, at, marketplace = package_id.partition("@")
    return name if at and name and marketplace else package_id


NESTED_ENV = "CLAUDECODE"  # set in every process a Claude Code session starts: a test can't run from there


class ClaudeCodeAdapter:
    name = HARNESS
    label = "Claude Code"

    def __init__(self, paths: Paths | None = None) -> None:
        self.paths = paths or Paths.default()
        self._listings: dict[Path, tuple[tuple[int, float], SessionListing | None]] = {}  # re-read only when a
        # transcript changes
        self._reader_scans: dict[Path, tuple[float, Scan | None]] = {}  # the configuration a briefing's parts are traced
        # to, per project (its real path), with when its discovery began: refreshed off the request path (``_discover``)
        # or left by a configuration reading of that project (``_keep``), which itself never looks here
        self._discovering: dict[Path, threading.Event] = {}  # the projects being discovered now, set when done
        self._scan_lock = threading.Lock()  # requests are served on threads

    def installed(self) -> bool:
        return self.paths.config.is_dir()  # ~/.claude: settings, transcripts, plugins all live under it

    def version(self) -> str | None:
        return harness_version(self.paths)

    # --- variant homes (v2) ---------------------------------------------------------------------

    def home_dir(self) -> Path:
        return self.paths.home

    def copy_plan(self) -> CopyPlan:
        return variants.COPY_PLAN

    def at_home(self, home: Path) -> ClaudeCodeAdapter:
        """A reader of the same machine with ``home`` as HOME: the managed settings and install directory are the
        machine's, everything under the home is the variant's — a marketplace the copied ``known_marketplaces.json``
        records under this adapter's home is read from the variant's copy of it, as the harness reads it."""
        return ClaudeCodeAdapter(replace(self.paths, home=home, copied_from=None if home == self.paths.home
                                         else self.paths.home))

    def removal(self, source: Source) -> tuple[Path, ...]:
        return variants.removal(source)

    def placements(self, project: Path, home: Path, workdir: Path) -> tuple:
        return variants.placements(self.paths.home, project, home, workdir)

    def entry_cut(self, source: Source):
        return variants.entry_cut(self.paths.home, source)

    def unplaced(self, project: Path, workdir: Path) -> tuple[str, ...]:
        return variants.unplaced(project, workdir)

    def acting_tools(self, reading: Reading) -> tuple[str, ...]:
        """The MCP tools the configuration is known to offer (what the reading's sessions' tool listings recorded, on
        any harness version) that act outside the machine or can't be told apart from one: removed from every test run
        (D6, ``acting.py``)."""
        return acting.acting_tools(_mcp_tool_names(reading))

    def tool_verdicts(self, reading: Reading) -> tuple[acting.ToolVerdict, ...]:
        """Every known MCP tool, removed or kept and why: for the onboarding screen and the Settings override."""
        return acting.verdicts(_mcp_tool_names(reading))

    def nested(self, environ: Mapping[str, str]) -> str | None:
        """Inside a Claude Code session ``CLAUDECODE`` is set (VERIFIED 2026-10-05: ``CLAUDECODE=1`` in a 2.1.285
        session's shell), and a ``claude`` started from there fails (``sandbox_apply: Operation not permitted``,
        VERIFIED, 05-runner.md "Cannot run from inside an agent session")."""
        if environ.get(NESTED_ENV):
            return ("agentlint is running inside a Claude Code session, which can't start the harness for a test; "
                    "start agentlint ui from a plain terminal")
        return None

    def telemetry(self) -> str:
        """D18's line. The launcher's session streaming and OpenTelemetry logging are named when its preset is on
        disk (``~/.claude/acme/preset``, which it rewrites on every launch); elsewhere Claude Code's own telemetry is
        named without them."""
        if self.paths.preset.is_dir():
            seen = ("Claude Code's own telemetry — the launcher's session streaming and its OpenTelemetry "
                    "logging — may see the situations")
        else:
            seen = "Claude Code's own telemetry may see the situations"
        return (f"A test run is a real Claude Code session, so {seen}, as it sees your sessions. agentlint itself "
                "sends nothing anywhere.")

    def package_label(self, package_id: str) -> str:
        return package_label(package_id)

    def read(self, cwd: Path, sessions: int, since: datetime | None = None) -> AdapterRead:
        """One read: disk discovered now, and the ``sessions`` most recent (this project's first) — of
        those that started at or after ``since``, when given (a start that can't be told is not after it)."""
        started = time.monotonic()
        found = scan(self.paths, cwd)
        self._keep(cwd, started, found)  # just discovered: the reader traces this project's briefings to it too
        declared = self._declared(found, cwd)
        paths: list[Path] = []
        raws: list[FirstTurn] = []
        for path in main_transcripts(self.paths.projects, sessions if since is None else None,
                                     prefer=project_slug(cwd), since=since) if sessions else []:
            if len(raws) == sessions:
                break
            raw = parse_session(path)
            if since is not None and (not raw.dated or raw.header.started_at < since):
                continue  # began before you forgot: nothing from it is read back in
            paths.append(path)
            raws.append(raw)
        memo: dict = {}
        delivered = tuple(self._session(raw, found, memo) for raw in raws)
        roots = {package.id: package.root for package in found.discovery.packages}
        evidence = Evidence(
            hooks=tuple(found.hooks),
            packages=found.discovery.packages,
            sessions=tuple(raws),
            hook_runs=tuple(record for path in paths for record in hook_records(path)),
            missing_paths=missing_paths(roots, [(e.package.id, e.command) for e in found.hooks if e.package],
                                        _package_texts(declared, delivered)),
        )
        return AdapterRead(declared, delivered, evidence)

    def checks(self) -> list:
        from agentlint.adapters.claude_code.checks import adapter_checks

        return adapter_checks()

    # --- disk -----------------------------------------------------------------------------------

    def _declared(self, scan: Scan, cwd: Path) -> Declared:
        sources: list[Source] = []
        entities: list[Entity] = []
        sources.extend(self._settings_sources(scan))
        sources.extend(self._instruction_sources(scan))
        entity_sources, entities = self._entity_sources(scan)
        sources.extend(entity_sources)
        sources.extend(self._hook_sources(scan))
        for source_id, point, authority, reason in _BUILT_INS:
            sources.append(_source(source_id, point, None, authority, Activation(Load.ALWAYS),
                                   NotObserved(0, _ONLY_A_SESSION), AblationPolicy.blocked(reason),
                                   declared_hash=content_hash(source_id)))
        points = {SKILL_LISTING: Point.SKILL_LISTING, AGENT_LISTING: Point.AGENT_LISTING, DEFERRED_TOOLS: Point.DEFERRED_TOOLS}
        for base, packages in scan.listing_parts().items():
            for package in sorted(packages):
                part_id = f"{base}:{package}"
                sources.append(_source(part_id, points[base], None, Authority.PACKAGE,
                                       Activation(Load.ALWAYS), NotObserved(0, _ONLY_A_SESSION),
                                       AblationPolicy.blocked("composed by the harness; disable the package instead"),
                                       package=package, declared_hash=content_hash(part_id)))
        return Declared(
            harness=HARNESS,
            cwd=cwd,
            harness_version=harness_version(self.paths),
            model=configured_model(list(scan.discovery.layers)),
            sources=tuple(sources),
            entities=tuple(entities),
        )

    def _settings_sources(self, scan: Scan) -> list[Source]:
        found = []
        for layer in scan.discovery.layers:
            fmt = ContentFormat.JSON if _parses_as_json(layer.text) else ContentFormat.PLAIN
            if layer.name == "managed":
                ablation = AblationPolicy.blocked("managed settings apply regardless of HOME")
            elif layer.path.is_relative_to(self.paths.preset):
                ablation = AblationPolicy.blocked("the launcher rewrites this preset on every launch")
            else:
                ablation = AblationPolicy(Ablation.SPAN_EXCISION)
            found.append(_source(f"claude-code:settings:{layer.name}:{layer.path}", Point.SETTINGS, layer.path,
                                 layer.authority, Activation(Load.NOT_IN_CONTEXT), file_text(fmt, layer.text), ablation))
        return found

    def _instruction_sources(self, scan: Scan) -> list[Source]:
        found = []
        for entry in scan.instructions:
            fmt = ContentFormat.PLAIN if entry.embedded else ContentFormat.MARKDOWN
            ablation = (AblationPolicy.blocked("managed instructions always apply")
                        if entry.authority is Authority.MANAGED else AblationPolicy(Ablation.SPAN_EXCISION))
            found.append(_source(f"claude-code:instructions:{entry.display_path}", entry.point, entry.path,
                                 entry.authority, Activation(Load.ALWAYS), file_text(fmt, entry.text), ablation,
                                 anchor=entry.anchor))
        return found

    def _entity_sources(self, scan: Scan) -> tuple[list[Source], list[Entity]]:
        sources: list[Source] = []
        entities: list[Entity] = []
        seen: set[str] = set()
        for disk in scan.entities:
            point = _ENTITY_POINTS[disk.kind]
            scope = "" if disk.origin is Scope.PACKAGE else f"{disk.origin.value}:"
            source_id = f"claude-code:{disk.kind.value.name}:{scope}{disk.name}"
            if source_id in seen:  # a same-named entity in another root: the harness shows one
                continue
            seen.add(source_id)
            if disk.kind is Kind.WORKFLOW:
                fmt = ContentFormat.CODE
            else:
                fmt = ContentFormat.MARKDOWN if disk.lenient else ContentFormat.FRONTMATTER_DOC
            required = (KeyPath(("name",)),) if fmt is ContentFormat.FRONTMATTER_DOC else ()
            authority = _AUTHORITY[disk.origin]
            if disk.kind is Kind.WORKFLOW:  # a script the harness runs; only its listing entry reaches the agent
                activation = Activation(Load.NOT_IN_CONTEXT)
            else:
                activation = Activation(Load.ON_INVOKE, f"when {disk.name} is invoked")
            sources.append(_source(source_id, point, disk.path, authority, activation,
                                   file_text(fmt, disk.text, required_keys=required),
                                   AblationPolicy(Ablation.FILE_REMOVAL),
                                   package=disk.package.id if disk.package else None))
            entities.append(Entity(entity_id(disk.kind, disk.origin, disk.name), disk.name, disk.kind.value, source_id,
                                   disk.description))
        return sources, entities

    def _hook_sources(self, scan: Scan) -> list[Source]:
        found = []
        for entry in scan.hooks:
            reason = ("this event's output never reaches the agent" if entry.load is Load.NOT_IN_CONTEXT
                      else "hook output is only knowable from a session")
            ablation = (AblationPolicy.blocked("managed hooks always apply")
                        if entry.owner == "managed" else AblationPolicy(Ablation.SPAN_EXCISION))
            found.append(_source(entry.source_id, entry.point, entry.file, entry.authority,
                                 Activation(entry.load, entry.condition), NotObserved(0, reason), ablation,
                                 package=entry.package.id if entry.package else None, anchor=entry.pointer or None,
                                 declared_hash=entry.declared_hash()))
        return found

    # --- sessions -------------------------------------------------------------------------------

    usage_reader_version = "claude-code/4"  # 4: a command older versions wrote only as a system line counts (2026-10-04)

    def transcripts(self) -> list[TranscriptFile]:
        """Each session's transcript, `<slug>/<session>.jsonl`, and each of its subagents',
        `<slug>/<session>/subagents/…/agent-*.jsonl` (workflow agents one level deeper). A workflow run's
        `journal.jsonl` beside its agents is a launch log, not a transcript: not listed."""
        root = self.paths.projects
        if not root.is_dir():
            return []
        found = []
        for path, subagent in [(p, False) for p in root.glob("*/*.jsonl")] + [(p, True) for p in root.glob("*/*/subagents/**/agent-*.jsonl")]:
            try:
                stat = path.stat()
            except OSError:
                continue  # gone since it was listed
            session = path.relative_to(root).parts[1] if subagent else path.stem
            found.append(TranscriptFile(str(path), stat.st_size, stat.st_mtime, session, subagent))
        return found

    def read_transcript(self, handle: str, offset: int = 0, length: int | None = None) -> bytes:
        with open(handle, "rb") as file:  # a handle is the transcript's path, as transcripts() listed it
            file.seek(offset)
            return file.read() if length is None else file.read(length)

    def turns_from(self, handle: str, data: bytes) -> list[Turn]:
        return turns_from(Path(handle).stem, records(data.splitlines()))

    def prepare(self, project: Path) -> None:
        self._discover(project, wait=False)

    def _scan_for(self, lines: list[dict]) -> Scan | None:
        """The configuration as discovered for the project a transcript ran in, to trace its briefings' parts to
        their sources as the Context view does; None when it names no project or can't be discovered."""
        cwd = next((record["cwd"] for record in lines if record.get("cwd")), None)
        return self._discover(Path(cwd), wait=True) if cwd else None

    def _discover(self, project: Path, *, wait: bool) -> Scan | None:
        """A project's configuration for the reader, kept so reading a transcript seldom waits for discovery: one
        under SCAN_SECONDS old serves as it is; an older one under SCAN_KEPT_SECONDS serves while a fresh one is
        discovered in the background; with none, a read (``wait``) waits for the discovery under way or runs it, and
        ``prepare`` starts one in the background. One discovery per project at a time. A configuration reading leaves
        its own here (``read``): the UI reads a project's report before any of its sessions, so an open seldom waits."""
        key, now = Path(os.path.realpath(project)), time.monotonic()
        with self._scan_lock:
            kept = self._reader_scans.get(key)
            if kept is not None and now - kept[0] <= SCAN_SECONDS:
                return kept[1]
            usable = kept is not None and now - kept[0] <= SCAN_KEPT_SECONDS
            running = self._discovering.get(key)
            starting = running is None
            if starting:
                running = self._discovering[key] = threading.Event()
        if starting and (usable or not wait):
            threading.Thread(target=self._discovery, args=(project, key, running), daemon=True,
                             name=f"agentlint-discovery {key}").start()
        elif starting:
            self._discovery(project, key, running)  # nothing kept to serve: this read discovers, others join it
        if usable:
            return kept[1]
        if not wait:
            return None
        running.wait()
        with self._scan_lock:
            kept = self._reader_scans.get(key)
        return kept[1] if kept else None

    def _discovery(self, project: Path, key: Path, done: threading.Event) -> None:
        started = time.monotonic()
        try:
            try:
                found = scan(self.paths, project)
            except (OSError, ValueError):
                found = None  # can't be discovered: briefings say only what the transcript does
            self._keep(project, started, found)
        finally:
            with self._scan_lock:
                self._discovering.pop(key, None)
            done.set()

    def _keep(self, project: Path, started: float, found: Scan | None) -> None:
        """Keep a project's configuration, discovered from ``started`` on, for the reader — whether the reader's own
        discovery or a configuration reading's (``read``), which the UI runs for the same project before you open any
        of its sessions — unless one discovered later is already kept. Stamped with when its discovery began, and with
        its haystacks too: the package texts a reading read to trace hook output are the ones a briefing's tracing needs."""
        key = Path(os.path.realpath(project))
        with self._scan_lock:
            now = time.monotonic()
            held = self._reader_scans.get(key)
            if held is None or held[0] <= started:
                self._reader_scans = {k: v for k, v in self._reader_scans.items()
                                      if now - v[0] <= SCAN_KEPT_SECONDS} | {key: (started, found)}

    def conversation_from(self, handle: str, data: bytes, *, parts: bool = True) -> Conversation:
        subagent = "subagents" in Path(handle).parts  # its lines are all sidechain: kept, not skipped
        lines = list(records(data.splitlines()))
        conversation = conversations.conversation_from(lines, subagent=subagent, parts=parts,
                                                       scan=self._scan_for(lines) if parts else None)
        title = conversations.subagent_title(Path(handle)) if subagent else None
        return replace(conversation, title=title) if title else conversation

    def subagent_id(self, handle: str) -> str:
        """A subagent's file name without its suffix: ``agent-<id>``."""
        return Path(handle).stem

    def is_subagent_ref(self, ref: str) -> bool:
        return conversations.is_subagent_ref(ref)

    def belongs(self, ref: str, handle: str, read: Callable[[], bytes]) -> bool:
        return conversations.belongs(ref, handle, read)

    def listing_from(self, handle: str, data: bytes) -> SessionListing | None:
        return conversations.listing_from(Path(handle).stem, list(records(data.splitlines())), len(data))

    def list_conversations(self) -> list[SessionListing]:
        found = []
        for path in self.paths.projects.glob("*/*.jsonl") if self.paths.projects.is_dir() else ():
            try:
                stat = path.stat()
                kept = self._listings.get(path)
                if kept is None or kept[0] != (stat.st_size, stat.st_mtime):
                    kept = ((stat.st_size, stat.st_mtime), conversations.listing(path))
                    self._listings[path] = kept
            except OSError:
                continue  # gone since it was listed
            if kept[1] is not None:  # nothing in it dated: no prompt, no reply, nothing to list
                found.append(kept[1])
        return sorted(found, key=lambda listing: listing.ended_at.timestamp(), reverse=True)

    def read_conversation(self, session_id: str) -> Conversation | None:
        path = conversations.find(self.paths.projects, session_id)
        if path is None:
            return None
        lines = list(records(path.read_bytes().splitlines()))
        return conversations.conversation_from(lines, scan=self._scan_for(lines))

    def trace(self, session_id: str) -> Trace:
        return read_trace(self.paths.home, session_id)

    def action_terms(self, action: Action) -> ActionTerms:
        return terms.action_terms(action)

    def _session(self, raw: FirstTurn, scan: Scan, memo: dict) -> SessionDeliveries:
        deliveries: list[Delivery] = []

        def add(point: Point | InjectionPoint, source_id: str | None, fmt: ContentFormat, text: str | None,
                load: Load, label: str, key: str = "") -> None:
            if text:
                injection = point.value if isinstance(point, Point) else point
                deliveries.append(Delivery(injection, source_id, fmt, text, load, label, key))

        add(Point.SYSTEM_PROMPT, SYSTEM_PROMPT, ContentFormat.PLAIN, raw.system_prompt, Load.ALWAYS, "system prompt")
        add(Point.TOOL_DEFINITIONS, TOOLS, ContentFormat.JSON, raw.tool_definitions, Load.ALWAYS, "tool definitions")
        for listing in _listings(raw, scan):  # the first record of each kind: a briefing joins on the later ones
            for owner, part in listing.split.items():
                add(listing.point, listing.source_of(owner), ContentFormat.PLAIN, part, Load.ALWAYS,
                    f"{listing.label} ({owner or 'unattributed'})")
        add(Point.SESSION_CONTEXT, SESSION_CONTEXT, ContentFormat.PLAIN, "\n".join(raw.session_context),
            Load.ALWAYS, "session context")
        for file in {f.path: f for f in raw.instruction_files}.values():
            point, source_id = _instruction_target(file, scan)
            add(point, source_id, ContentFormat.MARKDOWN, file.content, Load.ALWAYS, f"instructions: {file.path}")
        all_traced = trace_outputs(raw.hook_outputs, raw.hook_runs, scan.hooks, scan.haystacks, memo)
        configured = Counter((entry.event, entry.command) for entry in scan.hooks if entry.command)
        for traced in all_traced:
            output = traced.output
            if traced.entry:
                add(event_point(output.event), traced.entry.source_id, ContentFormat.PLAIN, output.text,
                    traced.entry.load, f"{output.event} hook ({traced.entry.owner})")
            else:
                add(event_point(output.event), None, ContentFormat.PLAIN, output.text, event_load(output.event),
                    _untraced_label(output, traced.candidates),
                    _untraced_key(output, traced.command if configured[output.event, traced.command] < 2 else None))
        return SessionDeliveries(raw.header, tuple(deliveries), self._first_turn_total(raw), self._observed_entities(raw, scan),
                       self._visibility(raw, scan))

    @staticmethod
    def _visibility(raw: FirstTurn, scan: Scan) -> tuple[tuple[str, Visibility], ...]:
        """What this session's listings showed of each entity. Silent when it had no skill listing.

        A listing shows names, not scopes: a name shown stands for every entity on disk of that kind and name."""
        if not raw.skill_listing:
            return ()
        kinds = {e.name: e.kind for e in scan.entities if e.kind is not Kind.AGENT}
        scopes: dict[tuple[Kind, str], list[Scope]] = {}
        for disk in scan.entities:
            scopes.setdefault((disk.kind, disk.name), []).append(disk.origin)

        def ids(kind: Kind, name: str) -> list[str]:
            return [entity_id(kind, scope, name) for scope in scopes.get((kind, name), [Scope.SESSION])]

        described = {line[2:].split(": ", 1)[0].strip() for line in raw.skill_listing.splitlines()
                     if line.startswith("- ") and ": " in line}
        seen: dict[str, Visibility] = {}
        for name in raw.skill_names:
            for shown in ids(kinds.get(name, Kind.SKILL), name):
                seen[shown] = Visibility.DESCRIBED if name in described else Visibility.NAME_ONLY
        for disk in scan.entities:
            if disk.kind is not Kind.AGENT:
                seen.setdefault(entity_id(disk.kind, disk.origin, disk.name), Visibility.NOT_SHOWN)
        for name in raw.agent_types:
            for shown in ids(Kind.AGENT, name):
                seen[shown] = Visibility.DESCRIBED  # the agent listing is delivered in full
        for name in raw.deferred_tool_names:
            seen[entity_id(_tool_kind(name, raw), Scope.SESSION, name)] = Visibility.NAME_ONLY  # schemas load only
            # through tool search
        for name in raw.tool_names:
            seen[entity_id(_tool_kind(name, raw), Scope.SESSION, name)] = Visibility.DESCRIBED
        return tuple(sorted(seen.items()))

    @staticmethod
    def _first_turn_total(raw: FirstTurn) -> TokenCount | None:
        """Tokens in context before the user's first words; unusable after an abandoned start."""
        if raw.interrupted_start or not raw.first_turn_input:
            return None
        user = estimate_tokens(raw.first_turn_user_text)
        total = raw.first_turn_input - user
        if total <= 0:
            return None
        return TokenCount(total, TokenMethod.DERIVED if user else TokenMethod.OBSERVED)

    @staticmethod
    def _observed_entities(raw: FirstTurn, scan: Scan) -> tuple[Entity, ...]:
        on_disk = {(disk.kind is Kind.AGENT, disk.name) for disk in scan.entities}
        found: dict[str, Entity] = {}

        def add(kind: Kind, name: str, provided_by: str, description: str | None = None) -> None:
            entity = Entity(entity_id(kind, Scope.SESSION, name), name, kind.value, provided_by, description,
                            _tool_aliases(name) if kind in (Kind.TOOL, Kind.MCP_TOOL) else ())
            found.setdefault(entity.id, entity)

        for name in raw.tool_names:
            add(_tool_kind(name, raw), name, TOOLS)
        for name in raw.deferred_tool_names:
            if name not in raw.tool_names:
                add(_tool_kind(name, raw), name, DEFERRED_TOOLS)
        skills = listing_descriptions(raw.skill_listing or "", raw.skill_names)
        agents = listing_descriptions(raw.agent_listing or "", raw.agent_types)
        for name in raw.skill_names:
            if (False, name) not in on_disk:
                add(Kind.SKILL, name, SKILL_LISTING, skills.get(name, ""))
        for name in raw.agent_types:
            if (True, name) not in on_disk:
                add(Kind.AGENT, name, AGENT_LISTING, agents.get(name, ""))
        return tuple(found.values())


def _mcp_tool_names(reading: Reading) -> list[str]:
    """The MCP tools a reading knows of: every ``mcp__…`` name the tool listing or definitions of any session it read
    named, whatever harness version that session ran. The snapshot's entities alone won't do: it takes a session's
    entities only on the installed version (always-on text depends on it), so right after a launcher update it would
    know no MCP tool and a new job would remove none (D6). A tool's name doesn't depend on the harness version."""
    names = [entity.name for entity in reading.snapshot.entities]
    if isinstance(reading.evidence, Evidence):
        for raw in reading.evidence.sessions:
            names.extend(raw.tool_names)
            names.extend(raw.deferred_tool_names)
    return [name for name in dict.fromkeys(names) if acting.own_name(name) is not None]


def _tool_aliases(name: str) -> tuple[str, ...]:
    """A server's tool goes by its own name too: ``mcp__<server>__search_files`` is search_files to a person, and a
    situation that says "use search_files" names it. An alias, not a separator: two servers' search_files are two
    tools the agent tells apart by their full names, not namesakes to report."""
    if not name.startswith("mcp__"):
        return ()
    server, _, tool = name[len("mcp__"):].partition("__")
    return (tool,) if server and tool.strip("_") else ()


def _tool_kind(name: str, raw: FirstTurn) -> Kind:
    """A tool the session defined in full is a tool; a deferred one a server exposes (``mcp__…``) an MCP tool."""
    return Kind.MCP_TOOL if name not in raw.tool_names and name.startswith("mcp__") else Kind.TOOL


@dataclass(frozen=True)
class _Listing:
    """One listing a session delivered, split by the package behind each entry (``split_listing``): its owners, and
    which of them are a source of their own (a package with a share of it); the rest is the listing's own part."""

    base: str  # the listing's own source id
    point: Point
    label: str
    text: str
    count: int  # the entries it names
    split: dict[str | None, str]  # owner (a package id, None for the listing's own part) -> its text
    packages: frozenset[str]  # the owners that are sources of their own

    def source_of(self, owner: str | None) -> str:
        return f"{self.base}:{owner}" if owner in self.packages else self.base


def _listings(raw: FirstTurn, scan: Scan | None) -> list[_Listing]:
    """The skill, agent and deferred-tool listings a session delivered, each split by owner as configured now; with no
    ``scan`` (nothing on disk to say whose an entry is) each is its own part whole."""
    parts = scan.listing_parts() if scan else {}
    packages = scan.discovery.packages if scan else ()
    tool_owners = {name: tool_package(name, packages) for name in raw.deferred_tool_names}
    found = []
    for base, point, text, names, owners, label in (
        (SKILL_LISTING, Point.SKILL_LISTING, raw.skill_listing, raw.skill_names,
         scan.skill_owners() if scan else {}, "skill listing"),
        (AGENT_LISTING, Point.AGENT_LISTING, raw.agent_listing, raw.agent_types,
         scan.agent_owners() if scan else {}, "agent listing"),
        (DEFERRED_TOOLS, Point.DEFERRED_TOOLS, raw.deferred_tools, raw.deferred_tool_names, tool_owners,
         "deferred tool listing"),
    ):
        found.append(_Listing(base, point, label, text or "", len(names), split_listing(text or "", names, owners),
                              frozenset(parts.get(base, ()))))
    return found


_INSTRUCTION_AUTHORITY = {"Managed": Authority.MANAGED, "User": Authority.USER, "Project": Authority.PROJECT,
                          "Local": Authority.PROJECT, "AutoMem": Authority.USER}  # the harness's file types (instructions.py)


def _instruction_target(file, scan: Scan | None) -> tuple[Point, str | None]:
    """An instruction file a session delivered: the point it reached the agent through, and its source's id — the
    managed policy's only when a scan found where it is configured."""
    if file.path == "<managed-settings>":
        managed = next((e.display_path for e in scan.instructions if e.embedded), None) if scan else None
        return Point.MANAGED_INSTRUCTIONS, f"claude-code:instructions:{managed}" if managed else None
    point = {"AutoMem": Point.MEMORY_INDEX}.get(file.kind, Point.AGENTS_MD if file.path.endswith("AGENTS.md")
                                                 else Point.CLAUDE_MD)
    return point, f"claude-code:instructions:{file.path}"


Keep = Callable[[str], tuple[str, TokenCount]]  # holds a text once in the conversation: its ref, its estimated tokens


def prompt_parts(system_prompt: str | None, tool_definitions: str | None, tool_count: int, keep: Keep) -> list[BriefingPart]:
    """The harness's own prompt and tool definitions, as a briefing shows them: the harness's, each its source."""
    parts = []
    for point, source_id, label, text, count in (
        (Point.SYSTEM_PROMPT, SYSTEM_PROMPT, "System prompt", system_prompt, None),
        (Point.TOOL_DEFINITIONS, TOOLS, "Tool definitions", tool_definitions, tool_count),
    ):
        if text:
            ref, tokens = keep(text)
            parts.append(BriefingPart(point.value, label, "", count, Authority.HARNESS, (), source_id, ref, tokens))
    return parts


def _with_later_listings(raw: FirstTurn) -> FirstTurn:
    """A turn's listings with what its later listing records added (``FirstTurn.later_listings``) joined on, as the
    model was shown them: each record's text after the first's, and the entries it names."""
    if not raw.later_listings:
        return raw
    fields = {"skill_listing": ("skill_listing", "skill_names"), "agent_listing_delta": ("agent_listing", "agent_types"),
              "deferred_tools_delta": ("deferred_tools", "deferred_tool_names")}
    changes: dict[str, object] = {}
    for later in raw.later_listings:
        text_field, names_field = fields[later.kind]
        text = changes.get(text_field, getattr(raw, text_field)) or ""
        names = changes.get(names_field, getattr(raw, names_field))
        changes[text_field] = f"{text}\n{later.text}" if text else later.text
        changes[names_field] = tuple(dict.fromkeys((*names, *later.names)))
    return replace(raw, **changes)


def _carried_skill_shares(carried: list, scan: Scan | None) -> tuple[Share, ...]:
    """Whose the skills a compaction carried over are, one share per owner as the skill listing is split
    (``_listings``): a package's share of its skills' text, and the listing's own part for the rest — each skill's
    own recorded content, estimated; with no ``scan``, none (the part is the listing's own, whole)."""
    if not scan:
        return ()
    owners = scan.skill_owners()
    packages = scan.listing_parts()[SKILL_LISTING]
    texts: dict[str | None, list[str]] = {}
    for c in carried:
        for index, name in enumerate(c.names):
            owner = owners.get(name)
            texts.setdefault(owner if owner in packages else None, []).append(
                c.pieces[index] if index < len(c.pieces) else "")
    return tuple(
        Share(package_label(owner) if owner else "the listing's own part",
              Authority.PACKAGE if owner else Authority.RUNTIME, estimated("\n\n".join(pieces)),
              f"{SKILL_LISTING}:{owner}" if owner else SKILL_LISTING)
        for owner, pieces in texts.items())


def briefing_parts(raw: FirstTurn, scan: Scan | None, memo: dict, keep: Keep) -> list[BriefingPart]:
    """What a turn's records show the harness gave the model, one part each, in the reader's order: its own prompt
    and tools, the instruction files and then the managed policy, the listings, the session's context, what a
    compaction re-attached, and SessionStart hook output. Each part's source and authority as the configuration
    reading traces it (``_session``) — with no ``scan``, only what the transcript itself says."""
    parts = prompt_parts(raw.system_prompt, raw.tool_definitions, len(raw.tool_names), keep)

    def part(point: Point | InjectionPoint, label: str, text: str, authority: Authority | None,
             source_id: str | None = None, detail: str = "", count: int | None = None,
             shares: tuple[Share, ...] = ()) -> None:
        if text.strip():
            ref, tokens = keep(text)
            parts.append(BriefingPart(point.value if isinstance(point, Point) else point, label, detail, count,
                                      authority, shares, source_id, ref, tokens))

    files = list({f.path: f for f in raw.instruction_files}.values())
    for file in sorted(files, key=lambda f: f.path == "<managed-settings>"):  # the managed policy after the files
        point, source_id = _instruction_target(file, scan)
        managed = file.path == "<managed-settings>"
        part(point, "Managed policy" if managed else Path(file.path).name, file.content,
             Authority.MANAGED if managed else _INSTRUCTION_AUTHORITY.get(file.kind), source_id,
             "" if managed else file.path)
    for listing in _listings(_with_later_listings(raw), scan):
        shares = tuple(
            Share(package_label(owner) if owner in listing.packages else "the listing's own part",
                  Authority.PACKAGE if owner in listing.packages else Authority.RUNTIME, estimated(text),
                  listing.source_of(owner))
            for owner, text in listing.split.items() if text.strip())
        one = {share.authority for share in shares}
        part(listing.point, listing.label.capitalize(), listing.text,
             next(iter(one)) if len(one) == 1 else None if shares else Authority.RUNTIME,
             listing.base, count=listing.count, shares=shares if len(one) > 1 else ())
    part(Point.SESSION_CONTEXT, "Environment · date · model · git status", "\n".join(raw.session_context),
         Authority.RUNTIME, SESSION_CONTEXT)
    files_carried = [c for c in raw.carried if c.kind == "file"]
    part(Point.CARRIED_FILES, "Files re-attached", "\n\n".join(c.text for c in files_carried), Authority.RUNTIME,
         detail=", ".join(name for c in files_carried for name in c.names), count=len(files_carried))
    skills = [c for c in raw.carried if c.kind == "skills"]
    names = [name for c in skills for name in c.names]
    shares = _carried_skill_shares(skills, scan)
    one = {share.authority for share in shares}
    part(Point.CARRIED_SKILLS, "Skills carried over", "\n\n".join(c.text for c in skills),
         next(iter(one)) if len(one) == 1 else None if shares else Authority.RUNTIME,
         shares[0].source_id if len(shares) == 1 and shares[0].authority is Authority.PACKAGE else None,
         ", ".join(names), len(names), shares if len(one) > 1 else ())
    starting = [output for output in raw.hook_outputs if output.event == "SessionStart"]
    for traced in trace_outputs(starting, raw.hook_runs, scan.hooks if scan else [], scan.haystacks if scan else None,
                                memo):
        entry = traced.entry
        part(event_point("SessionStart"), "SessionStart hook output", traced.output.rendered or traced.output.text,
             entry.authority if entry else None, entry.source_id if entry else None,
             (package_label(entry.owner) if entry.package else entry.owner) if entry else traced.output.hook_name)
    return parts
