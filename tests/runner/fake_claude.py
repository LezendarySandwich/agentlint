"""A fake Claude Code home for variant tests: user instructions, settings, skills (one linked the way this machine
links them) and an agent; optionally a package whose marketplace path points into the home, as installed ones do."""

from __future__ import annotations

import json
from pathlib import Path

from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
from agentlint.adapters.claude_code.discovery import Paths
from agentlint.adapters.claude_code.instructions import project_slug
from agentlint.config.model import ConfigSnapshot
from agentlint.harness import read_config
from agentlint.rank.plan import AblationTarget, targets

USER_MD = "# Memory\nRemember things.\n\n# Testing\nRun the tests first.\n"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def fake_machine(tmp: Path, *, package: bool = False) -> tuple[ClaudeCodeAdapter, Path]:
    """(an adapter reading the fake real home, the project directory)."""
    home, project, install = tmp / "real-home", tmp / "project", tmp / "install"
    config = home / ".claude"
    write(install / "package.json", json.dumps({"dependencies": {"@anthropic-ai/claude-code": "9.9.9"}}))
    write(tmp / "managed.json", json.dumps({"claudeMd": "Org policy text."}))
    settings = {"model": "model-x", "permissions": {"allow": ["Bash(ls)"]},
                "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}
    if package:
        market = config / "plugins" / "marketplaces" / "mk"
        write(config / "plugins" / "known_marketplaces.json", json.dumps({"mk": {"installLocation": str(market)}}))
        write(market / ".claude-plugin" / "marketplace.json", json.dumps({"plugins": [{"name": "pk", "source": "./pk"}]}))
        write(market / "pk" / ".claude-plugin" / "plugin.json", "{}")
        write(market / "pk" / "skills" / "helper" / "SKILL.md", "---\nname: helper\ndescription: helps\n---\nbody\n")
        settings["enabledPlugins"] = {"pk@mk": True}
    write(config / "settings.json", json.dumps(settings, indent=2))
    write(config / "CLAUDE.md", USER_MD)
    write(config / "skills" / "mine" / "SKILL.md", "---\nname: mine\ndescription: my skill\n---\nmine body\n")
    write(config / "shared" / "linked" / "SKILL.md", "---\nname: linked\ndescription: linked skill\n---\nlinked body\n")
    (config / "skills" / "linked").symlink_to(config / "shared" / "linked")  # absolute, as this machine's are
    write(config / "agents" / "reviewer.md", "---\nname: reviewer\ndescription: reviews\n---\nReview carefully.\n")
    write(config / "history.jsonl", "typed\n")
    write(config / "projects" / project_slug(project) / "memory" / "MEMORY.md", "# Index\n- a memory\n")
    write(home / ".claude.json", "{}")
    write(project / "AGENTS.md", "# Project\nProject rules.\n")
    return ClaudeCodeAdapter(Paths(home, tmp / "managed.json", install)), project


def reading(adapter: ClaudeCodeAdapter, cwd: Path) -> ConfigSnapshot:
    return read_config(adapter, cwd, sessions=0).snapshot


def target(snapshot: ConfigSnapshot, source_end: str, key: str | None = None) -> AblationTarget:
    """The target whose source id ends with ``source_end``: its rule at locator ``key``, or the whole source."""
    found = [t for t in targets(snapshot) if t.source.id.endswith(source_end)
             and (key is None or t.id.endswith("#" + key))]
    assert len(found) == 1, [t.id for t in targets(snapshot) if t.source.id.endswith(source_end)]
    return found[0]
