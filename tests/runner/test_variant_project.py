"""A run's copies beyond its home (D1): the project's configuration in its empty directory, the project's memory under
the run directory's slug; project targets cut there; a package's hook cut from every copy of its package (D3); and the
baseline check (D4), which must reproduce the real configuration or say exactly how it doesn't."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentlint.adapters.claude_code.instructions import project_slug
from agentlint.rank.plan import targets
from agentlint.runner.variant import SourceChanged, TargetNotSupported, build_variant
from agentlint.runner.verify import AblationFailed, BaselineMismatch, check_baseline, read_real, read_variant, verify
from tests.runner.fake_claude import fake_machine, reading, target, write

HOOKED = {"name": "pk", "hooks": {"SessionStart": [{"hooks": [
    {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/start.sh"},
    {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/other.sh"}]}]}}


def run_dirs(root: Path) -> tuple[Path, Path]:
    return root / "home", root / "work"


def with_project_config(project: Path) -> None:
    write(project / ".claude" / "settings.json", json.dumps({"permissions": {"allow": ["Bash(make)"]}}))
    write(project / ".claude" / "skills" / "deploy" / "SKILL.md", "---\nname: deploy\ndescription: ships\n---\nShip.\n")
    write(project / ".claude" / "scheduled_tasks.lock", "{}")  # not configuration
    write(project / ".mcp.json", json.dumps({"mcpServers": {}}))
    write(project / "src" / "main.py", "print('code')\n")  # never copied


def with_hooked_package(adapter) -> tuple[Path, Path]:
    """The fake package gets a SessionStart hook in its manifest, and a plugin-cache copy installed_plugins.json names."""
    config = adapter.home_dir() / ".claude"
    checkout = config / "plugins" / "marketplaces" / "mk" / "pk" / ".claude-plugin" / "plugin.json"
    cache = config / "plugins" / "cache" / "mk" / "pk" / "1.0"
    write(checkout, json.dumps(HOOKED, indent=2))
    write(cache / ".claude-plugin" / "plugin.json", json.dumps(HOOKED, indent=2))
    write(config / "plugins" / "installed_plugins.json",
          json.dumps({"version": 2, "plugins": {"pk@mk": [{"scope": "user", "installPath": str(cache)}]}}))
    return checkout, cache / ".claude-plugin" / "plugin.json"


def test_a_run_gets_the_projects_configuration_and_memory_never_its_code(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    with_project_config(project)
    home, work = run_dirs(agentlint_home / "r")
    variant = build_variant(adapter, home, None, project=project, workdir=work)
    assert (work / "AGENTS.md").read_text() == (project / "AGENTS.md").read_text()
    assert (work / ".claude" / "skills" / "deploy" / "SKILL.md").is_file() and (work / ".mcp.json").is_file()
    assert not (work / "src").exists() and not (work / ".claude" / "scheduled_tasks.lock").exists()
    memory = home / ".claude" / "projects" / project_slug(work) / "memory" / "MEMORY.md"
    assert memory.read_text() == "# Index\n- a memory\n"
    assert not (home / ".claude" / "projects" / project_slug(project)).exists()  # only under the run's own slug
    assert variant.workdir == work and len(variant.path_pairs()) == 3


def test_the_baseline_reproduces_the_real_configuration(tmp_path: Path, agentlint_home: Path) -> None:
    """A package is read from the variant's own marketplace checkout, as the harness loads it (E2b), never from the
    real home: the baseline reproduces everything and lists no escapes."""
    adapter, project = fake_machine(tmp_path, package=True)
    with_project_config(project)
    home, work = run_dirs(agentlint_home / "r")
    variant = build_variant(adapter, home, None, project=project, workdir=work)
    checked = check_baseline(read_real(adapter, project),
                             read_variant(adapter, home, work, project, variant.path_pairs()))
    assert checked.sources > 10
    assert checked.escapes == ()
    package = [s for s in read_variant(adapter, home, work, project, variant.path_pairs()).snapshot.sources
               if s.provenance.package == "pk@mk"]
    assert package and all(s.provenance.file.is_relative_to(home.resolve()) for s in package
                           if s.provenance.file is not None)


def with_project_hook(project: Path, command: str) -> None:
    write(project / ".claude" / "settings.json", json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": command}]}]}}))


def test_the_projects_hook_scripts_and_output_styles_are_placed_and_the_baseline_passes(
        tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    with_project_hook(project, '"$CLAUDE_PROJECT_DIR"/.claude/hooks/guard.sh')
    write(project / ".claude" / "hooks" / "guard.sh", "#!/bin/sh\nexit 2\n")
    write(project / ".claude" / "output-styles" / "terse.md", "---\nname: terse\n---\nBe brief.\n")
    home, work = run_dirs(agentlint_home / "r")
    variant = build_variant(adapter, home, None, project=project, workdir=work)
    assert (work / ".claude" / "hooks" / "guard.sh").read_text() == "#!/bin/sh\nexit 2\n"
    assert (work / ".claude" / "output-styles" / "terse.md").is_file()
    assert adapter.unplaced(project, work) == ()
    check_baseline(read_real(adapter, project), read_variant(adapter, home, work, project, variant.path_pairs()),
                   adapter.unplaced(project, work))


@pytest.mark.parametrize("command", ['"$CLAUDE_PROJECT_DIR"/scripts/guard.py', "python3 scripts/guard.py --strict",
                                     "${CLAUDE_PROJECT_DIR}/scripts/guard.py", "{project}/scripts/guard.py"])
def test_a_project_hook_running_the_projects_code_is_a_mismatch_not_passed(tmp_path: Path, agentlint_home: Path,
                                                                          command: str) -> None:
    adapter, project = fake_machine(tmp_path)
    with_project_hook(project, command.replace("{project}", str(project)))
    write(project / "scripts" / "guard.py", "raise SystemExit(2)\n")  # code: never placed
    home, work = run_dirs(agentlint_home / "r")
    variant = build_variant(adapter, home, None, project=project, workdir=work)
    unplaced = adapter.unplaced(project, work)
    assert len(unplaced) == 1 and "scripts/guard.py" in unplaced[0] and "PreToolUse" in unplaced[0]
    with pytest.raises(BaselineMismatch, match="not placed: the PreToolUse hook"):
        check_baseline(read_real(adapter, project),
                       read_variant(adapter, home, work, project, variant.path_pairs()), unplaced)


@pytest.mark.parametrize("server", [{"command": "python3", "args": ["tools/server.py"]},
                                    {"command": "tools/server.py"},
                                    {"command": "node", "args": ["--inspect", "{project}/tools/server.py"]},
                                    {"type": "stdio", "command": "sh", "args": ["${CLAUDE_PROJECT_DIR}/tools/server.py"]}])
def test_a_project_mcp_server_running_the_projects_code_is_a_mismatch_not_passed(tmp_path: Path, agentlint_home: Path,
                                                                                server: dict) -> None:
    """The project's .mcp.json is placed but the code its server runs isn't: in every run that server fails to start
    while the real configuration has its tools."""
    adapter, project = fake_machine(tmp_path)
    spec = json.loads(json.dumps(server).replace("{project}", str(project)))
    write(project / ".mcp.json", json.dumps({"mcpServers": {"guard": spec}}))
    write(project / "tools" / "server.py", "print('serving')\n")  # code: never placed
    home, work = run_dirs(agentlint_home / "r")
    variant = build_variant(adapter, home, None, project=project, workdir=work)
    assert (work / ".mcp.json").is_file() and not (work / "tools").exists()
    unplaced = adapter.unplaced(project, work)
    assert len(unplaced) == 1 and "MCP server guard" in unplaced[0] and "tools/server.py" in unplaced[0]
    with pytest.raises(BaselineMismatch, match="not placed: the MCP server guard in the project's .mcp.json"):
        check_baseline(read_real(adapter, project),
                       read_variant(adapter, home, work, project, variant.path_pairs()), unplaced)


def test_a_project_settings_mcp_server_is_read_too_and_a_disabled_mcp_json_server_is_not_reported(
        tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    write(project / ".mcp.json", json.dumps({"mcpServers": {
        "off": {"command": "python3", "args": ["tools/off.py"]},
        "remote": {"type": "http", "url": "https://example.invalid/mcp"},
        "elsewhere": {"command": "python3", "args": ["/usr/lib/server.py", "../outside/x.py", "tools/missing.py"]}}}))
    write(project / ".claude" / "settings.local.json", json.dumps({
        "disabledMcpjsonServers": ["off"],
        "mcpServers": {"local": {"command": "bash", "args": ["tools/local.sh"]}}}))
    write(project / "tools" / "off.py", "\n")
    write(project / "tools" / "local.sh", "\n")
    home, work = run_dirs(agentlint_home / "r")
    build_variant(adapter, home, None, project=project, workdir=work)
    unplaced = adapter.unplaced(project, work)
    assert len(unplaced) == 1 and "MCP server local in the project's .claude/settings.local.json runs tools/local.sh" \
        in unplaced[0]


@pytest.mark.parametrize(("server", "file"), [
    ({"command": "python3", "args": ["server.py"]}, "server.py"),
    ({"command": "node", "args": ["index.js"]}, "index.js"),
    ({"command": "uv", "args": ["run", "server.py"]}, "server.py"),
    ({"command": "./serve.sh"}, "serve.sh"),
])
def test_a_project_mcp_server_running_a_bare_file_in_the_project_root_is_a_mismatch(
        tmp_path: Path, agentlint_home: Path, server: dict, file: str) -> None:
    """A bare filename resolves from the project's directory, where the server starts; the run's directory holds the
    configuration only, so there the server would fail to start in every run."""
    adapter, project = fake_machine(tmp_path)
    write(project / ".mcp.json", json.dumps({"mcpServers": {"local": server}}))
    write(project / file, "print('serving')\n")
    home, work = run_dirs(agentlint_home / "r")
    variant = build_variant(adapter, home, None, project=project, workdir=work)
    unplaced = adapter.unplaced(project, work)
    assert len(unplaced) == 1 and f"MCP server local in the project's .mcp.json runs {file}" in unplaced[0]
    with pytest.raises(BaselineMismatch, match="not placed: the MCP server local"):
        check_baseline(read_real(adapter, project),
                       read_variant(adapter, home, work, project, variant.path_pairs()), unplaced)


@pytest.mark.parametrize("command", ["python3 guard.py", "bash guard.sh --strict", "node guard.js"])
def test_a_project_hook_running_a_bare_file_in_the_project_root_is_a_mismatch(tmp_path: Path, agentlint_home: Path,
                                                                             command: str) -> None:
    adapter, project = fake_machine(tmp_path)
    with_project_hook(project, command)
    file = command.split()[1]
    write(project / file, "\n")
    home, work = run_dirs(agentlint_home / "r")
    build_variant(adapter, home, None, project=project, workdir=work)
    unplaced = adapter.unplaced(project, work)
    assert len(unplaced) == 1 and f"PreToolUse hook in the project's .claude/settings.json runs {file}" in unplaced[0]


@pytest.mark.parametrize("command", ["python3 guard.py; exit 0", "python3 guard.py|tee /tmp/x",
                                     "python3 guard.py>/dev/null", "(python3 guard.py)", "bash -c 'python3 guard.py'",
                                     "python3 guard.py 2>/dev/null || true", "/bin/sh -ec 'cd . && python3 guard.py'",
                                     "zsh -c \"bash -c 'python3 guard.py'\""])
def test_a_hook_naming_a_bare_file_beside_shell_punctuation_or_inside_a_dash_c_string_is_a_mismatch(
        tmp_path: Path, agentlint_home: Path, command: str) -> None:
    """The command is split as a shell splits it, and a string a shell runs with -c is split again: guard.py is found
    whatever punctuation sits beside it."""
    adapter, project = fake_machine(tmp_path)
    with_project_hook(project, command)
    write(project / "guard.py", "\n")
    home, work = run_dirs(agentlint_home / "r")
    build_variant(adapter, home, None, project=project, workdir=work)
    unplaced = adapter.unplaced(project, work)
    assert len(unplaced) == 1 and "PreToolUse hook in the project's .claude/settings.json runs guard.py" in unplaced[0]


@pytest.mark.parametrize("server", [{"command": "sh", "args": ["-c", "python3 server.py"]},
                                    {"command": "/bin/bash", "args": ["-lc", "exec python3 server.py >&2"]},
                                    {"command": "zsh", "args": ["-c", "(node server.py)"]}])
def test_a_server_running_a_bare_file_inside_a_dash_c_string_is_a_mismatch(tmp_path: Path, agentlint_home: Path,
                                                                           server: dict) -> None:
    adapter, project = fake_machine(tmp_path)
    write(project / ".mcp.json", json.dumps({"mcpServers": {"local": server}}))
    write(project / "server.py", "\n")
    home, work = run_dirs(agentlint_home / "r")
    build_variant(adapter, home, None, project=project, workdir=work)
    unplaced = adapter.unplaced(project, work)
    assert len(unplaced) == 1 and "MCP server local in the project's .mcp.json runs server.py" in unplaced[0]


def test_a_dash_c_string_naming_no_file_of_the_project_is_not_reported(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    write(project / ".mcp.json", json.dumps({"mcpServers": {
        "pkg": {"command": "sh", "args": ["-c", "npx -y @scope/server; exit 0"]}}}))
    with_project_hook(project, "bash -c 'uvx ruff check . | tee /tmp/out' || true")
    home, work = run_dirs(agentlint_home / "r")
    build_variant(adapter, home, None, project=project, workdir=work)
    assert adapter.unplaced(project, work) == ()


def test_words_that_name_no_file_of_the_project_are_not_reported(tmp_path: Path, agentlint_home: Path) -> None:
    """Package runners, tool names, flags, interpreters and placed configuration: none is a file of the project's code
    the run's directory lacks."""
    adapter, project = fake_machine(tmp_path)
    write(project / ".mcp.json", json.dumps({"mcpServers": {
        "pkg": {"command": "npx", "args": ["-y", "@scope/server-files", "--port=3000", "."]},
        "tool": {"command": "uvx", "args": ["mcp-server-git", "--repository", ".."]},
        "env": {"command": "python3", "args": ["-m", "tools.server", "$HOME/x.py", "~/y.py"]},
        "placed": {"command": "cat", "args": ["AGENTS.md", ".mcp.json"]}}}))
    with_project_hook(project, "npx prettier --write && echo done")
    write(project / "tools" / "server.py", "\n")  # named only as a module: not seen
    home, work = run_dirs(agentlint_home / "r")
    build_variant(adapter, home, None, project=project, workdir=work)
    assert adapter.unplaced(project, work) == ()


def test_a_hook_naming_no_file_of_the_project_is_not_reported(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    with_project_hook(project, 'echo "a/b" && /usr/bin/true ../outside/x.sh $HOME/x.sh scripts/missing.sh')
    home, work = run_dirs(agentlint_home / "r")
    build_variant(adapter, home, None, project=project, workdir=work)
    assert adapter.unplaced(project, work) == ()


def test_a_baseline_without_the_project_copied_lists_what_it_is_missing(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    home, work = run_dirs(agentlint_home / "r")
    build_variant(adapter, home, None)
    work.mkdir()
    with pytest.raises(BaselineMismatch) as refused:
        check_baseline(read_real(adapter, project), read_variant(adapter, home, work, project))
    said = "\n".join(refused.value.differences)
    assert "missing from the baseline" in said and "AGENTS.md" in said and "MEMORY.md" in said
    assert len(refused.value.differences) == 2


def test_the_real_users_file_read_above_the_run_directory_is_a_mismatch_not_hidden(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """agentlint's home inside the real home: the run directory's ancestors include the real home, whose
    .claude/CLAUDE.md the harness then reads as a project file on top of the copy's own."""
    adapter, project = fake_machine(tmp_path)
    store = adapter.home_dir() / ".agentlint"
    monkeypatch.setenv("AGENTLINT_HOME", str(store))
    home, work = run_dirs(store / "runs" / "r")
    variant = build_variant(adapter, home, None, project=project, workdir=work)
    with pytest.raises(BaselineMismatch) as refused:
        check_baseline(read_real(adapter, project), read_variant(adapter, home, work, project, variant.path_pairs()))
    (said,) = refused.value.differences
    assert said.startswith("read 2 times in the baseline, 1 in your configuration: ")
    assert str(adapter.home_dir() / ".claude" / "CLAUDE.md") in said
    assert said.endswith("read from the real home, not the copy")


def test_a_project_section_and_a_project_skill_are_cut_in_the_run_directory_only(tmp_path: Path,
                                                                                agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    with_project_config(project)
    snapshot = reading(adapter, project)
    section = target(snapshot, "/project/AGENTS.md", "section:/Project")
    home, work = run_dirs(agentlint_home / "a")
    variant = build_variant(adapter, home, section, project=project, workdir=work)
    assert (work / "AGENTS.md").read_text() == "" and variant.cut.rewritten == ("AGENTS.md",)
    assert (project / "AGENTS.md").read_text() == "# Project\nProject rules.\n"
    skill = target(snapshot, "skill:project:deploy")
    home, work = run_dirs(agentlint_home / "b")
    variant = build_variant(adapter, home, skill, project=project, workdir=work)
    assert not (work / ".claude" / "skills" / "deploy").exists() and variant.cut.removed == ("skills/deploy",)
    assert (project / ".claude" / "skills" / "deploy" / "SKILL.md").is_file()


def test_the_projects_memory_index_is_cut_in_its_copy_and_verified_gone(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    snapshot = reading(adapter, project)
    t = target(snapshot, "/memory/MEMORY.md", "section:/Index")
    readings = []
    for name, removed in (("base", None), ("without", t)):
        home, work = run_dirs(agentlint_home / name)
        variant = build_variant(adapter, home, removed, project=project, workdir=work)
        readings.append(read_variant(adapter, home, work, project, variant.path_pairs()))
    assert verify(t, *readings).target_id == t.id
    real = adapter.home_dir() / ".claude" / "projects" / project_slug(project) / "memory" / "MEMORY.md"
    assert real.read_text() == "# Index\n- a memory\n"


def test_a_packages_hook_is_cut_from_the_checkout_that_loads_and_the_cache_copy(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path, package=True)
    checkout, cache = with_hooked_package(adapter)
    hook = next(t for t in targets(reading(adapter, project))
                if t.source.provenance.package == "pk@mk" and t.source.provenance.anchor == "/hooks/SessionStart/0/hooks/0")
    home, work = run_dirs(agentlint_home / "r")
    variant = build_variant(adapter, home, hook, project=project, workdir=work)
    real = adapter.home_dir()
    for original in (checkout, cache):
        copy = json.loads((home / original.relative_to(real)).read_text())
        assert copy["hooks"]["SessionStart"][0]["hooks"] == [HOOKED["hooks"]["SessionStart"][0]["hooks"][1]]
        assert json.loads(original.read_text()) == HOOKED  # the real package untouched
    assert len(variant.cut.rewritten) == 2


def hook_target(adapter, project):
    return next(t for t in targets(reading(adapter, project))
                if t.source.provenance.package == "pk@mk" and t.source.provenance.anchor == "/hooks/SessionStart/0/hooks/0")


def test_a_cache_copy_without_the_hook_is_left_and_the_marketplace_checkout_that_loads_is_cut(
        tmp_path: Path, agentlint_home: Path) -> None:
    """E2b: under a substituted HOME the harness loads the marketplace checkout, so a cache copy that is another
    version is no reason to refuse; the checkout is cut, the cache copy left as it is."""
    adapter, project = fake_machine(tmp_path, package=True)
    checkout, cache = with_hooked_package(adapter)
    hook = hook_target(adapter, project)
    other = json.loads(json.dumps(HOOKED))
    other["hooks"]["SessionStart"][0]["hooks"][0]["command"] = "${CLAUDE_PLUGIN_ROOT}/newer.sh"
    write(cache, json.dumps(other))
    home, work = run_dirs(agentlint_home / "r")
    variant = build_variant(adapter, home, hook, project=project, workdir=work)
    real = adapter.home_dir()
    assert variant.cut.rewritten == (str(checkout.relative_to(real)),)
    loaded = json.loads((home / checkout.relative_to(real)).read_text())
    assert loaded["hooks"]["SessionStart"][0]["hooks"] == [HOOKED["hooks"]["SessionStart"][0]["hooks"][1]]
    assert json.loads((home / cache.relative_to(real)).read_text()) == other


def test_a_marketplace_checkout_changed_since_it_was_read_is_refused(tmp_path: Path, agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path, package=True)
    checkout, _ = with_hooked_package(adapter)
    hook = hook_target(adapter, project)
    other = json.loads(json.dumps(HOOKED))
    other["hooks"]["SessionStart"][0]["hooks"][0]["command"] = "${CLAUDE_PLUGIN_ROOT}/newer.sh"
    write(checkout, json.dumps(other))
    home, work = run_dirs(agentlint_home / "r")
    with pytest.raises(SourceChanged):
        build_variant(adapter, home, hook, project=project, workdir=work)
    assert not home.exists() and not work.exists()


def _readings(adapter, project, agentlint_home: Path, hook) -> list:
    readings = []
    for name, removed in (("base", None), ("without", hook)):
        home, work = run_dirs(agentlint_home / name)
        variant = build_variant(adapter, home, removed, project=project, workdir=work)
        readings.append(read_variant(adapter, home, work, project, variant.path_pairs()))
    return readings


def test_a_package_hook_cut_from_the_variants_marketplace_checkout_passes_verification(tmp_path: Path,
                                                                                     agentlint_home: Path) -> None:
    """The cut edits the variant's marketplace checkout — the copy the harness loads (E2b) and the one the variant's
    reader reads — so verification finds the hook gone, though the next hook has moved into its place."""
    adapter, project = fake_machine(tmp_path, package=True)
    with_hooked_package(adapter)
    hook = hook_target(adapter, project)
    baseline, without = _readings(adapter, project, agentlint_home, hook)
    assert without.escapes == ()
    verified = verify(hook, baseline, without)
    assert verified.target_id == hook.id and verified.changed == ()
    moved = [s for s in without.snapshot.sources
             if s.provenance.package == "pk@mk" and s.provenance.anchor == "/hooks/SessionStart/0/hooks/0"]
    assert len(moved) == 1 and moved[0].declared_hash != hook.source.declared_hash  # other.sh, now at index 0


def test_a_package_hook_cut_only_from_the_cache_copy_fails_verification_saying_where_it_is_read(
        tmp_path: Path, agentlint_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cut that reaches only the plugin cache's copy leaves the hook in the variant's marketplace checkout, which the
    harness loads (E2b): verification reads it there and says so."""
    adapter, project = fake_machine(tmp_path, package=True)
    with_hooked_package(adapter)
    hook = hook_target(adapter, project)
    real_cut = adapter.entry_cut

    def cache_only(source):
        found = real_cut(source)
        return type(found)(found.files[1:], found.locate) if found is not None else None

    monkeypatch.setattr(adapter, "entry_cut", cache_only)
    baseline, without = _readings(adapter, project, agentlint_home, hook)
    checkout = agentlint_home / "without" / "home" / ".claude" / "plugins" / "marketplaces" / "mk" / "pk"
    with pytest.raises(AblationFailed, match="it is still read, from .*/without/home/.claude/plugins/marketplaces/mk/"
                                             "pk/.claude-plugin/plugin.json$") as raised:
        verify(hook, baseline, without)
    assert "real home" not in str(raised.value)
    assert json.loads((checkout / ".claude-plugin" / "plugin.json").read_text()) == HOOKED


def test_a_package_hook_is_cut_where_the_reader_reads_it_when_the_homes_claude_is_a_link(
        tmp_path: Path, agentlint_home: Path) -> None:
    """With ``~/.claude`` a link to elsewhere, the real reading names the package's files by their resolved path,
    outside the home; the cut maps them into the variant by the reader's own rule (in_copy), so it edits the checkout
    the variant's reader reads instead of refusing. Both readings key a source by its resolved path, so the baseline
    reproduces the real configuration and verify finds the hook gone (it once reported "the baseline variant doesn't
    read it either": the real reading keyed the hook by the resolved path, the variant's by the linked one)."""
    adapter, project = fake_machine(tmp_path, package=True)
    with_hooked_package(adapter)
    config = adapter.home_dir() / ".claude"
    elsewhere = tmp_path / "dotfiles" / "claude"
    elsewhere.parent.mkdir()
    config.rename(elsewhere)
    config.symlink_to(elsewhere)
    hook = hook_target(adapter, project)
    assert hook.source.provenance.file.is_relative_to(elsewhere.resolve())
    home, work = run_dirs(agentlint_home / "r")
    variant = build_variant(adapter, home, hook, project=project, workdir=work)
    assert variant.cut.rewritten[0] == ".claude/plugins/marketplaces/mk/pk/.claude-plugin/plugin.json"
    read = read_variant(adapter, home, work, project, variant.path_pairs())
    hooks = [s for s in read.snapshot.sources if s.provenance.package == "pk@mk" and s.provenance.anchor]
    assert [s.provenance.file for s in hooks] == [home.resolve() / variant.cut.rewritten[0]]
    assert hooks[0].declared_hash != hook.source.declared_hash  # other.sh: the one cut is gone from what is read
    baseline, without = _readings(adapter, project, agentlint_home, hook)
    checked = check_baseline(read_real(adapter, project), baseline)
    assert checked.escapes == () and without.escapes == ()
    assert verify(hook, baseline, without).target_id == hook.id


@pytest.mark.parametrize("layout", ["acme-market", "linked-within", "resolved-spelling"])
def test_a_package_hook_is_cut_where_the_reader_reads_it_in_every_layout_the_reader_maps(
        tmp_path: Path, agentlint_home: Path, layout: str) -> None:
    """A marketplace recorded under ``.claude/`` but not in ``plugins/marketplaces`` (``acme-market``), one whose
    recorded directory is a link to another directory within ``.claude/`` (read, as the reader resolves a package's
    directory, at the link's target in the copy, so cut there and not through the link), and one recorded by the other
    spelling of ``.claude/`` (``/private/var`` for ``/var``, or the reverse): the variant's reader reads each from the
    variant's ``.claude/``; the cut edits the very file read, so verify passes and nothing is read from the real
    home."""
    adapter, project = fake_machine(tmp_path, package=True)
    with_hooked_package(adapter)
    config = adapter.home_dir() / ".claude"
    recorded = config / "plugins" / "marketplaces" / "mk"
    if layout != "resolved-spelling":
        moved = config / "acme-market"
        recorded.rename(moved)
        if layout == "linked-within":
            recorded.symlink_to(moved)
        else:
            recorded = moved
    else:
        resolved = Path(os.path.realpath(config))
        recorded = (resolved if resolved != config else Path(str(config).replace("/private/var/", "/var/", 1))
                    ) / "plugins" / "marketplaces" / "mk"
        assert recorded != config / "plugins" / "marketplaces" / "mk"  # a different spelling of the same place
    write(config / "plugins" / "known_marketplaces.json", json.dumps({"mk": {"installLocation": str(recorded)}}))
    hook = hook_target(adapter, project)
    home, work = run_dirs(agentlint_home / "r")
    variant = build_variant(adapter, home, hook, project=project, workdir=work)
    checkout = ".claude/plugins/marketplaces/mk" if layout == "resolved-spelling" else ".claude/acme-market"
    assert variant.cut.rewritten[0] == f"{checkout}/pk/.claude-plugin/plugin.json"  # never through the link
    read = read_variant(adapter, home, work, project, variant.path_pairs())
    hooks = [s for s in read.snapshot.sources if s.provenance.package == "pk@mk" and s.provenance.anchor]
    assert [s.provenance.file for s in hooks] == [home.resolve() / variant.cut.rewritten[0]]  # the file cut is read
    baseline, without = _readings(adapter, project, agentlint_home, hook)
    assert without.escapes == ()
    assert verify(hook, baseline, without).target_id == hook.id


def _unmapped_location(adapter, location: str) -> None:
    write(adapter.home_dir() / ".claude" / "plugins" / "known_marketplaces.json",
          json.dumps({"mk": {"installLocation": location}}))


def test_a_package_hook_whose_marketplace_is_recorded_outside_claude_through_a_link_into_it_is_refused(
        tmp_path: Path, agentlint_home: Path) -> None:
    """``installLocation`` outside ``.claude/`` that links into it: the reader resolves the package's files into
    ``.claude/``, but a variant's reader maps only the location as recorded, which is under no ``.claude/``, so it
    reads the hook from the real home. A cut derived from the resolved file would edit a copy nothing reads (and
    verify would then fail); it is refused before anything is copied, and the real package is untouched."""
    adapter, project = fake_machine(tmp_path, package=True)
    checkout, _ = with_hooked_package(adapter)
    link = adapter.home_dir() / "mk-link"
    link.symlink_to(adapter.home_dir() / ".claude" / "plugins" / "marketplaces" / "mk")
    _unmapped_location(adapter, str(link))
    hook = hook_target(adapter, project)
    assert hook.source.provenance.file.resolve() == checkout.resolve()  # the reading resolved it into .claude/
    home, work = run_dirs(agentlint_home / "without")
    with pytest.raises(TargetNotSupported, match=f"records at {link}: not under the home's .claude/"):
        build_variant(adapter, home, hook, project=project, workdir=work)
    assert not home.exists() and not work.exists()
    assert json.loads(checkout.read_text()) == HOOKED


def test_a_package_hook_whose_marketplace_is_recorded_as_a_relative_path_is_refused(
        tmp_path: Path, agentlint_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A relative ``installLocation`` is read where it is written (relative to the directory agentlint runs in, here
    the real home's ``.claude/``), by a variant's reader too: it is relative to no home, so a variant has no copy of
    it to cut, and the hook is refused rather than cut from the variant's ``.claude/``."""
    adapter, project = fake_machine(tmp_path, package=True)
    checkout, _ = with_hooked_package(adapter)
    _unmapped_location(adapter, "plugins/marketplaces/mk")
    monkeypatch.chdir(adapter.home_dir() / ".claude")
    hook = hook_target(adapter, project)
    assert hook.source.provenance.file.resolve() == checkout.resolve()
    home, work = run_dirs(agentlint_home / "without")
    with pytest.raises(TargetNotSupported, match="records at plugins/marketplaces/mk: not under the home's .claude/"):
        build_variant(adapter, home, hook, project=project, workdir=work)
    assert not home.exists() and not work.exists()


def test_a_package_hook_whose_directory_the_marketplace_names_by_absolute_path_is_refused(
        tmp_path: Path, agentlint_home: Path) -> None:
    """``source`` in ``marketplace.json`` an absolute path into the real home's checkout: the marketplace is mapped,
    but the variant's reader joins that absolute path to its copy's directory and gets the path as written, so it reads
    the package in the real home. It is refused before anything is copied, never cut from a copy nothing reads."""
    adapter, project = fake_machine(tmp_path, package=True)
    checkout, _ = with_hooked_package(adapter)
    market = adapter.home_dir() / ".claude" / "plugins" / "marketplaces" / "mk"
    write(market / ".claude-plugin" / "marketplace.json",
          json.dumps({"plugins": [{"name": "pk", "source": str(market / "pk")}]}))
    hook = hook_target(adapter, project)
    assert hook.source.provenance.file.resolve() == checkout.resolve()
    home, work = run_dirs(agentlint_home / "without")
    with pytest.raises(TargetNotSupported, match="names by absolute path"):
        build_variant(adapter, home, hook, project=project, workdir=work)
    assert not home.exists() and not work.exists()
    assert json.loads(checkout.read_text()) == HOOKED


def test_a_package_hook_from_a_hooks_file_its_manifest_names_by_absolute_path_is_refused(
        tmp_path: Path, agentlint_home: Path) -> None:
    """``hooks`` in ``plugin.json`` an absolute path to a file inside the real home's checkout: the variant's reader
    reads that file where it is written, in the real home, so the hook is refused before anything is copied; a hook
    from a hooks file named relatively is still cut."""
    adapter, project = fake_machine(tmp_path, package=True)
    checkout, _ = with_hooked_package(adapter)
    custom = checkout.parent.parent / "custom" / "h.json"
    write(custom, json.dumps({"hooks": HOOKED["hooks"]}))
    write(checkout, json.dumps({"name": "pk", "hooks": str(custom)}))
    hook = hook_target(adapter, project)
    assert hook.source.provenance.file == custom.resolve()
    home, work = run_dirs(agentlint_home / "without")
    with pytest.raises(TargetNotSupported, match="names that hooks file by absolute path"):
        build_variant(adapter, home, hook, project=project, workdir=work)
    assert not home.exists() and not work.exists()
    write(checkout, json.dumps({"name": "pk", "hooks": "./custom/h.json"}))
    hook = hook_target(adapter, project)
    variant = build_variant(adapter, home, hook, project=project, workdir=work)
    assert variant.cut.rewritten[0] == ".claude/plugins/marketplaces/mk/pk/custom/h.json"


def test_a_user_hook_and_a_project_target_without_a_project_are_still_refused(tmp_path: Path,
                                                                             agentlint_home: Path) -> None:
    adapter, project = fake_machine(tmp_path)
    snapshot = reading(adapter, project)
    hook = next(t for t in targets(snapshot) if t.source.provenance.point.name == "session_start_hook")
    with pytest.raises(TargetNotSupported, match="only for a package's hook"):
        build_variant(adapter, agentlint_home / "h", hook, project=project, workdir=agentlint_home / "w")
    with pytest.raises(TargetNotSupported, match="no project is copied"):
        build_variant(adapter, agentlint_home / "h", target(snapshot, "/project/AGENTS.md", "section:/Project"))
    assert not (agentlint_home / "h").exists() and not (agentlint_home / "w").exists()
