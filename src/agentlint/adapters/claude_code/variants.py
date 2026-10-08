"""What a Claude Code run gets copied: its home, the project's configuration beside it, and how a package's hook is
cut from its copies (docs/tech_impl/05-runner.md "Variants").

A run's HOME is a copy of ``~/.claude`` and ``~/.claude.json`` without what is history rather than configuration:
the transcripts (``projects/``), typed history, file-history snapshots, pastes, session state, shell snapshots,
backups and debug logs. VERIFIED 2026-10-04 by listing this machine's ``~/.claude`` (read-only): those are the
entries that aren't configuration.

Beside it (D1, user 2026-10-04), a run in an empty directory gets the project's configuration copied in — never its
code: ``AGENTS.md``, ``CLAUDE.md``, ``CLAUDE.local.md`` and ``.mcp.json`` at its top, and from ``.claude/`` the settings,
instructions, rules, skills, agents, commands, workflows, hook scripts and output styles — and the project's memory folder
(``~/.claude/projects/<slug of the project>/memory``) copied into the variant home under the run directory's slug,
where the harness looks for that directory's memory. A hook in the project's settings, or a server in its
``.mcp.json``, that runs a file of the project outside what is placed (its code) is reported by ``unplaced`` for the
baseline check, never passed over.

A package's hook (D3) is removed by editing the copied package: E2 (2026-10-04) removed brain's SessionStart hook from a
run by deleting it from both copies of its ``plugin.json`` — the marketplace checkout agentlint reads and the plugin
cache ``installed_plugins.json`` names — with no path rewritten. **The marketplace checkout is the copy that loads**:
E2b (VERIFIED 2026-10-04) cut the hook from the variant's cache copy alone and it still fired, from the variant's
marketplace checkout alone and it stopped — though ``known_marketplaces.json`` names the real home's checkout by
absolute path. So the checkout under the variant home is cut first and must hold the entry; every cache copy that
holds it is cut too (harmless, and robust to a harness version that reads the cache), and one that doesn't is left.
The checkout is found in the variant by the rule agentlint's reader of a copied home finds it (``discovery.in_copy``),
applied to the same input — the marketplace location ``known_marketplaces.json`` records, never the reading's resolved
file: a location under the real home's ``.claude/`` is the same path under the variant's, so the cut edits exactly
the file that verification then reads, and that the harness loads. A location that rule doesn't map (outside
``.claude/``, even one that links into it, or relative) is read where it is written, in the real home, so a variant
has no copy of it to cut: such a hook is refused (TargetNotSupported), never cut from a copy nothing reads. So is a
hook of a mapped marketplace whose package's own manifests name the way to it by absolute path — the package's
``source`` in ``marketplace.json``, or the ``hooks`` item of ``plugin.json`` naming its file: joined to the variant's
copy, an absolute path is the path as written, so the variant's reader reads that file in the real home too.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any

from agentlint.adapters.claude_code.discovery import Paths, clean, marketplaces, under_config
from agentlint.adapters.claude_code.hooks import _from_mapping, _read
from agentlint.adapters.claude_code.instructions import project_slug
from agentlint.adapters.claude_code.points import Point
from agentlint.config.locators import KeyPath
from agentlint.config.model import Source
from agentlint.config.taxonomy import Authority
from agentlint.runner.variant import CopyEntry, CopyPlan, EntryCut, Placement, TargetNotSupported

HISTORY = frozenset({"projects", "history.jsonl", "file-history", "paste-cache", "sessions", "session-env",
                     "shell-snapshots", "backups", "debug"})
COPY_PLAN = CopyPlan((CopyEntry(".claude", HISTORY), CopyEntry(".claude.json")))

# The project's configuration (D1): what the reader and the harness read from a project directory, never its code.
PROJECT_TOP = CopyPlan(tuple(CopyEntry(name) for name in ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", ".mcp.json")))
# ``hooks`` and ``output-styles`` are configuration too: the scripts the project's own settings run as hooks
# ("$CLAUDE_PROJECT_DIR"/.claude/hooks/...), and the styles they name.
PROJECT_DOT_CLAUDE = CopyPlan(tuple(CopyEntry(name) for name in (
    "settings.json", "settings.local.json", "CLAUDE.md", "AGENTS.md", "rules", "skills", "agents", "commands",
    "workflows", "hooks", "output-styles")))
PROJECT_SETTINGS = ("settings.json", "settings.local.json")
_PROJECT_DIR = ("$CLAUDE_PROJECT_DIR/", "${CLAUDE_PROJECT_DIR}/")
MEMORY = CopyPlan((CopyEntry("memory"),))

_WHOLE_FILE = {Point.AGENT_DEFINITION.value, Point.COMMAND.value, Point.WORKFLOW.value}
_HOOKS = {Point.SESSION_START_HOOK.value, Point.USER_PROMPT_HOOK.value, Point.TOOL_HOOK.value, Point.OTHER_HOOK.value}
MANIFEST = Path(".claude-plugin") / "plugin.json"


def removal(source: Source) -> tuple[Path, ...]:
    """What to delete for ``source`` to be gone: a skill is its directory (the harness finds a skill by a directory
    holding SKILL.md), an agent, command or workflow its file. Nothing for anything else."""
    file = source.provenance.file
    if file is None or source.provenance.anchor is not None:
        return ()
    if source.provenance.point == Point.SKILL.value:
        return (file.parent,) if file.name == "SKILL.md" else ()
    return (file,) if source.provenance.point in _WHOLE_FILE else ()


def memory_dir(home: Path, cwd: Path) -> Path:
    """Where the harness keeps a working directory's memory under ``home``."""
    return home / ".claude" / "projects" / project_slug(cwd)


def placements(real_home: Path, project: Path, home: Path, workdir: Path) -> tuple[Placement, ...]:
    """The project's configuration copied into ``workdir``, and its memory into ``home`` under ``workdir``'s slug."""
    found = [Placement(project, PROJECT_TOP, workdir)]
    if (project / ".claude").is_dir():
        found.append(Placement(project / ".claude", PROJECT_DOT_CLAUDE, workdir / ".claude"))
    if (memory_dir(real_home, project) / "memory").is_dir():
        found.append(Placement(memory_dir(real_home, project), MEMORY, memory_dir(home, workdir)))
    return tuple(found)


_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})


def _shell_words(command: str) -> list[str]:
    """A command's words as a shell splits them: quotes removed, and shell punctuation (``;``, ``|``, ``>``, ``(``,
    ``&&``) words of its own, so ``python3 guard.py; exit 0`` holds ``guard.py``, not ``guard.py;``."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:  # unbalanced quotes: the words as written
        return command.split()


def _with_inner(words: list[str], depth: int = 0) -> list[str]:
    """``words`` and, for each string a shell runs with ``-c`` (``bash -c 'python3 guard.py'``, ``sh -lc ...``), the
    words of that string too, as far down as they nest."""
    found = list(words)
    for i, word in enumerate(words):
        if os.path.basename(word) not in _SHELLS or depth > 4:
            continue
        for j in range(i + 1, len(words)):
            flag = words[j]
            if not flag.startswith("-") or flag.startswith("--"):
                break
            if "c" in flag[1:] and j + 1 < len(words):
                found += _with_inner(_shell_words(words[j + 1]), depth + 1)
                break
    return found


def _named_files(command: str, project: Path) -> list[str]:
    """The project-relative paths a hook command names (``_named_in`` on its shell words, those of a ``-c`` string
    included)."""
    return _named_in(_with_inner(_shell_words(command)), project)


def _named_in(words: list[str], project: Path) -> list[str]:
    """The project-relative paths among ``words``: after ``$CLAUDE_PROJECT_DIR``, under the project's own path, or
    relative — with a '/' (``tools/server.py``) or bare (``server.py``, ``index.js``) — since a hook, or a project MCP
    server, runs in the project's directory. Never a flag, a variable, a home path or one leading out. Whether a word
    is a file of the project is the caller's test: a bare word that names none (``npx``, ``uvx``, a package or tool
    name) is no path at all."""
    found = []
    for word in words:
        rel = None
        for prefix in _PROJECT_DIR + (f"{project}/",):
            if word.startswith(prefix):
                rel = word[len(prefix):]
        if rel is None and word and not word.startswith(("/", "-", "$", "~")) and "=" not in word:
            rel = word
        if rel:
            rel = os.path.normpath(rel)
            if rel != "." and not rel.startswith(".."):
                found.append(rel)
    return found


def unplaced(project: Path, workdir: Path) -> tuple[str, ...]:
    """Each file of the project that the placed configuration runs and the run's directory doesn't hold (D1 places the
    configuration, never the code): a hook in the placed project settings, or a server in the placed ``.mcp.json`` (or
    an ``mcpServers`` of the placed project settings) whose command or arguments name it. In a run that hook or server
    would fail to find it instead of acting as it does for real, so the baseline check reports it. Every word of a hook's
    command and of a server's command and arguments is resolved from the project's directory, a bare filename
    (``python3 server.py``, ``node index.js``, ``uv run server.py``) included. A hook's command is split as a shell
    splits it, punctuation apart (``python3 guard.py; exit 0``, ``(python3 guard.py)``, ``guard.py>/dev/null``), and a
    string run by ``sh``/``bash``/``zsh -c``, in a hook or a server's arguments, is split and resolved too (``bash -c
    'python3 guard.py'``, ``{"command": "sh", "args": ["-c", "python3 server.py"]}``). A word that names no existing
    file of the project (``npx some-package``, ``uvx tool``, a flag) is not reported. A server the placed project
    settings list in ``disabledMcpjsonServers`` runs in neither, and is not reported."""
    said = []
    disabled: set[str] = set()
    servers: list[tuple[str, str, Any]] = []  # (where it is declared, its name, its declaration)
    for name in PROJECT_SETTINGS:
        file = workdir / ".claude" / name
        if not file.is_file():
            continue
        settings = _read(file)
        for entry in _from_mapping(settings.get("hooks"), name, file, "/hooks", Authority.PROJECT, None):
            for rel in _named_files(entry.command, project):
                if (project / rel).is_file() and not os.path.lexists(workdir / rel):
                    said.append(f"the {entry.event} hook in the project's .claude/{name} runs {rel}, a file of the "
                                "project a run's directory doesn't get (only its configuration is copied, D1)")
        listed = settings.get("disabledMcpjsonServers")
        if isinstance(listed, list):
            disabled.update(n for n in listed if isinstance(n, str))
        declared = settings.get("mcpServers")
        if isinstance(declared, dict):
            servers.extend((f".claude/{name}", server, spec) for server, spec in declared.items())
    mcp = workdir / ".mcp.json"
    if mcp.is_file():
        declared = _read(mcp).get("mcpServers")
        if isinstance(declared, dict):
            servers.extend((".mcp.json", server, spec) for server, spec in declared.items()
                           if server not in disabled)
    for where, server, spec in servers:
        if not isinstance(spec, dict):
            continue
        command, args = spec.get("command"), spec.get("args")
        words = ([command] if isinstance(command, str) else []) + (
            [a for a in args if isinstance(a, str)] if isinstance(args, list) else [])
        for rel in _named_in(_with_inner(words), project):
            if (project / rel).is_file() and not os.path.lexists(workdir / rel):
                said.append(f"the MCP server {server} in the project's {where} runs {rel}, a file of the project a "
                            "run's directory doesn't get (only its configuration is copied, D1)")
    return tuple(dict.fromkeys(said))


def _package_root(file: Path) -> Path | None:
    for directory in file.parents:
        if (directory / MANIFEST).is_file():
            return directory
    return None


def _install_paths(real_home: Path, package: str) -> list[Path]:
    """Where ``installed_plugins.json`` says a package is installed: the plugin cache's copy."""
    try:
        data = json.loads((real_home / ".claude" / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    plugins = data.get("plugins") if isinstance(data, dict) else None
    entries = plugins.get(package) if isinstance(plugins, dict) else None
    return [Path(e["installPath"]) for e in entries or [] if isinstance(e, dict) and isinstance(e.get("installPath"), str)]


def _as_copied(real_home: Path, file: Path) -> Path:
    """A cache copy's ``file`` spelled as the path under ``real_home/.claude`` that a variant home copies it to
    (``under_config``): ``installed_plugins.json`` may record it resolved (``/private/tmp/...``, or through a
    ``.claude`` that is a link), and copied relative to the home as written it could land elsewhere or nowhere. A file
    under no ``.claude/`` of the home is returned as it is, and the runner refuses to cut what its copy doesn't hold."""
    relative = under_config(real_home, file)
    return real_home / ".claude" / relative if relative is not None else file


def _checkout_in_copy(real_home: Path, package: str, file: Path, where: str) -> Path:
    """The variant's copy of ``file``, a package's file the real reading read from its marketplace checkout, spelled
    as the path under ``real_home`` that a variant copies to it — the file the variant's reader reads. That reader
    maps the location ``known_marketplaces.json`` records for the package's marketplace (``discovery.in_copy``): one
    under the real home's ``.claude/`` (``under_config``) is read at the same path under the variant's, and any other
    is read where it is written, in the real home. So the cut is decided on that recorded location, never on ``file``,
    which the reader resolved: a location outside ``.claude/`` that links into it, or a relative one, resolves into
    ``.claude/`` though a variant reads it in the real home. Such a hook raises TargetNotSupported: no copy of it can
    be cut. For a mapped location both readers resolve the package's directory (``resolve_packages``), so the
    variant's file is ``file`` spelled under the variant's ``.claude/`` (``_as_copied``) — through a link within
    ``.claude/`` too, which the copy keeps pointing within it, and spelled without the link, as the cut must be.
    Also refused: a file outside its checkout's resolved directory, which the mapping doesn't speak for, and one the
    package's manifests reach by an absolute path (``_named_relatively``), which a variant reads in the real home."""
    market = package.partition("@")[2]
    written = marketplaces(Paths(real_home), []).get(market, (None, None))[0]
    if written is None:
        raise TargetNotSupported(f"{where} comes from the package {package}, whose marketplace {market} "
                                 "known_marketplaces.json doesn't record: a variant cuts a package's hook only from "
                                 "a recorded marketplace's checkout")
    if under_config(real_home, written) is None:
        raise TargetNotSupported(f"{where} comes from the package {package}, whose marketplace {market} "
                                 f"known_marketplaces.json records at {written}: not under the home's .claude/, so a "
                                 "variant reads it there, in the real home, and has no copy of it to cut")
    if not file.is_relative_to(os.path.realpath(written)):
        raise TargetNotSupported(f"{where} comes from the package {package}, read from {file}, outside its "
                                 f"marketplace's checkout ({written}): which copy a variant reads isn't known")
    _named_relatively(package, written, file, where)
    return _as_copied(real_home, file)


def _named_relatively(package: str, written: Path, file: Path, where: str) -> None:
    """Raise TargetNotSupported unless the variant's reader reaches ``file`` through the mapped checkout: the package's
    manifests name it relative to that checkout. The reader finds the package's directory as the marketplace's
    directory joined with its ``source`` in ``marketplace.json``, and a hooks file as that directory joined with an
    item of ``hooks`` in ``plugin.json`` (``discovery.resolve_packages``, ``hooks._package_entries``). Joined to the
    variant's copy, an absolute ``source`` or ``hooks`` item is the path as written: the real home's file, which a
    variant reads in the real home however the checkout is mapped, so it has no copy of it to cut."""
    name, _, market = package.partition("@")
    listed = _read(written / ".claude-plugin" / "marketplace.json").get("plugins")
    entries = [p for p in listed if isinstance(p, dict) and p.get("name") == name] if isinstance(listed, list) else []
    source = entries[0].get("source") if entries else None
    if not isinstance(source, str):
        raise TargetNotSupported(f"{where} comes from the package {package}, which the {market} marketplace's manifest "
                                 "no longer lists: which copy a variant reads isn't known")
    if Path(source).is_absolute():
        raise TargetNotSupported(f"{where} comes from the package {package}, whose directory the {market} "
                                 f"marketplace's manifest names by absolute path ({source}): a variant reads it there, "
                                 "in the real home, and has no copy of it to cut")
    root = (written / source).resolve()
    declared = _read(root / MANIFEST).get("hooks")
    items = [declared] if isinstance(declared, str) else declared if isinstance(declared, list) else []
    for item in items:
        if isinstance(item, str) and Path(item).is_absolute() and (root / item).resolve() == file:
            raise TargetNotSupported(f"{where} comes from the package {package}, whose plugin.json names that hooks "
                                     f"file by absolute path ({item}): a variant reads it there, in the real home, "
                                     "and has no copy of it to cut")


def _key(pointer: str, wrapped: bool) -> KeyPath:
    parts: list[str | int] = [int(p) if p.isdigit() else p for p in pointer.strip("/").split("/")]
    return KeyPath(tuple(parts if wrapped else parts[1:]))


def entry_cut(real_home: Path, source: Source) -> EntryCut | None:
    """For a package's hook: every copy of the file declaring it — first the one read, the package's marketplace
    checkout, which is the copy the harness loads under a substituted HOME (E2b), spelled as the variant's reader
    finds it (``_checkout_in_copy``); then the plugin cache's — and how to find the very entry that was read in one:
    same event, matcher, command and place, by its declared hash. Raises TargetNotSupported for a hook a
    variant's reader reads in the real home: its marketplace isn't mapped, or its package's manifests name the way to
    it by absolute path."""
    provenance = source.provenance
    if (provenance.package is None or provenance.point not in _HOOKS or provenance.file is None
            or not provenance.anchor or not provenance.anchor.startswith("/hooks/")):
        return None
    file = Path(provenance.file)
    root = _package_root(file)
    if root is None:
        return None
    relative = file.relative_to(root)
    files = [_checkout_in_copy(real_home, provenance.package, file, f"the hook {provenance.anchor} of {file}")]
    for install in _install_paths(real_home, provenance.package):
        copy = _as_copied(real_home, install / relative)
        if copy not in files and copy.is_file():
            files.append(copy)
    package, pointer, expected = provenance.package, provenance.anchor, source.declared_hash

    def locate(data: Any) -> KeyPath | None:
        data = clean(data)
        if not isinstance(data, dict):
            return None
        wrapped = relative == MANIFEST or isinstance(data.get("hooks"), dict)
        mapping = data.get("hooks") if wrapped else data
        for entry in _from_mapping(mapping, package, file, "/hooks", Authority.PACKAGE, None):
            if entry.pointer == pointer and entry.declared_hash() == expected:
                return _key(pointer, wrapped)
        return None

    return EntryCut(tuple(files), locate)
