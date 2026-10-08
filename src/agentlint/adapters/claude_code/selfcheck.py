"""Reader self-check: does disk enumeration reproduce what a real session delivered?

The oracle is the most recent clean session (no abandoned first submission) on the installed
harness version. A name on only one side is our bug until it is explained; explanations are
mechanical — the harness lists its built-ins, files have creation times — never guesses.

Run: ``uv run python scripts/selfcheck.py``. Re-run after every harness update.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter, scan as discover_scan
from agentlint.adapters.claude_code.discovery import harness_version
from agentlint.adapters.claude_code.points import Kind
from agentlint.adapters.claude_code.transcripts import FirstTurn, main_transcripts, parse_session

IN_BUNDLE = "defined in the harness bundle"
NOWHERE = "no definition anywhere on disk (compressed in the bundle or synced from the account; not determinable)"
NEWER = "installed after the oracle session started"
BUNDLE = Path("native/claude")  # relative to the install directory


@dataclass(frozen=True)
class Oracle:
    name: str  # "skills and commands" | "agents" | "instruction files"
    session_id: str
    delivered: int
    matched: int
    on_disk_only: dict[str, str] = field(default_factory=dict)  # name -> explanation or ""
    delivered_only: dict[str, str] = field(default_factory=dict)

    @property
    def unexplained(self) -> list[str]:
        return sorted(name for side in (self.on_disk_only, self.delivered_only)
                      for name, why in side.items() if not why)


def _oracle_session(adapter: ClaudeCodeAdapter, version: str | None) -> FirstTurn | None:
    for path in main_transcripts(adapter.paths.projects, 100):
        raw = parse_session(path)
        if raw.skill_names and not raw.interrupted_start and (version is None or raw.header.harness_version == version):
            return raw
    return None


def _in_bundle(install_dir: Path, names: set[str]) -> set[str]:
    """Names the harness bundle defines as literal entries (``name:"x"``)."""
    bundle = install_dir / BUNDLE
    if not names or not bundle.is_file():
        return set()
    import mmap

    with bundle.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        return {name for name in names if data.find(f'name:"{name}"'.encode()) >= 0}


def _defined_on_disk(adapter: ClaudeCodeAdapter, scan, cwd: Path, name: str) -> bool:
    """Whether a file that could define this name exists — searched the way the harness names things:
    a bare name can only come from user or project directories, ``pkg:x`` only from that package."""
    package, _, leaf = name.rpartition(":")
    if package:
        roots = [p.root for p in scan.discovery.packages if p.name == package.split(":")[0]]
    else:
        bases = [adapter.paths.config, cwd / ".claude"]
        roots = [base / sub for base in bases for sub in ("skills", "commands", "agents")]
    return any(any(root.rglob(leaf)) or any(root.rglob(f"{leaf}.md")) for root in roots if root.is_dir())


def _explain_missing(adapter: ClaudeCodeAdapter, scan, cwd: Path, names: set[str], structured: set[str]) -> dict[str, str]:
    bundled = _in_bundle(adapter.paths.install_dir, names - structured)
    explained = {}
    for name in names:
        if name in structured or name in bundled:
            explained[name] = IN_BUNDLE
        elif not _defined_on_disk(adapter, scan, cwd, name):
            explained[name] = NOWHERE
        else:
            explained[name] = ""  # a file exists that we did not read: a reader bug
    return explained


def _created(path: Path) -> datetime:
    stat = path.stat()
    return datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_mtime)).astimezone()


def self_check(adapter: ClaudeCodeAdapter | None = None) -> list[Oracle]:
    adapter = adapter or ClaudeCodeAdapter()
    raw = _oracle_session(adapter, harness_version(adapter.paths))
    if raw is None or not raw.dated:
        return []
    cwd = raw.header.project or Path(".")
    scan = discover_scan(adapter.paths, cwd)
    started = raw.header.started_at

    def compare(name: str, predicted: dict[str, Path], delivered: set[str], structured: set[str]) -> Oracle:
        on_disk = {n: (NEWER if _created(path) > started else "") for n, path in predicted.items() if n not in delivered}
        missing = {n for n in delivered if n not in predicted}
        delivered_only = _explain_missing(adapter, scan, cwd, missing, structured) if missing else {}
        return Oracle(name, raw.header.session_id, len(delivered), len(delivered & set(predicted)), on_disk, delivered_only)

    listed = {e.name: e.path for e in scan.entities if e.kind is not Kind.AGENT and e.model_invocable}
    agents = {e.name: e.path for e in scan.entities if e.kind is Kind.AGENT}
    results = [
        compare("skills and commands", listed, set(raw.skill_names), set()),
        compare("agents", agents, set(raw.agent_types), set(raw.agent_built_ins)),
    ]
    files = {("<managed-settings>" if e.embedded else str(e.path)): e.path for e in scan.instructions}
    delivered_files = {f.path for f in raw.instruction_files}
    memory_after = {k for k, p in files.items() if k.endswith("MEMORY.md") and _created(p) > started}
    instruction = compare("instruction files", {k: p for k, p in files.items() if k not in memory_after},
                          delivered_files, set())
    return [*results, instruction]
