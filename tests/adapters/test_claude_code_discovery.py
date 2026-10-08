"""Discovery and entity naming rules, each one verified against a real delivered listing.

The fixture is a synthetic home directory; the rules it pins came from the listing oracle
(docs/tech_impl/01-config-reader.md).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from agentlint.adapters.claude_code.discovery import MarketplaceNotCopied, Paths, discover, enabled_packages
from agentlint.adapters.claude_code.entities import disk_entities, read_frontmatter


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def doc(name: str | None = None, description: str | None = "does things", **extra: object) -> str:
    lines = ["---"]
    if name:
        lines.append(f"name: {name}")
    if description:
        lines.append(f"description: {description}")
    lines += [f"{k.replace('_', '-')}: {json.dumps(v)}" for k, v in extra.items()]
    return "\n".join(lines + ["---", "body"]) + "\n"


def build_home(tmp: Path) -> tuple[Paths, Path]:
    home = tmp / "home"
    market = home / ".claude" / "market"
    cwd = tmp / "project"
    write(home / ".claude" / "plugins" / "known_marketplaces.json",
          json.dumps({"mk": {"installLocation": str(market)}}))
    write(market / ".claude-plugin" / "marketplace.json", json.dumps({"plugins": [
        {"name": "pk", "source": "./plugins/pk"},
        {"name": "late", "source": "./plugins/late"},
        {"name": "ghost", "source": "./plugins/ghost"},
    ]}))
    pk = market / "plugins" / "pk"
    write(pk / ".claude-plugin" / "plugin.json", json.dumps({
        "name": "pk",
        "skills": ["./extra/", "./solo/"],
        "agents": ["./agents/listed.md"],
    }))
    write(pk / "skills" / "by-dir" / "SKILL.md", doc(name="frontmatter-name-ignored"))
    write(pk / "skills" / "hidden" / "SKILL.md", doc(disable_model_invocation=True))
    write(pk / "skills" / "group" / "nested" / "SKILL.md", doc())  # too deep: never loads
    write(pk / "skills" / "by-dir" / "sub" / "SKILL.md", doc())  # inside a skill: never loads
    write(pk / "extra" / "added" / "SKILL.md", doc())  # manifest supplements the default
    write(pk / "solo" / "SKILL.md", doc())  # a listed path that is itself a skill
    write(pk / "commands" / "go.md", doc())
    write(pk / "commands" / "sub" / "deep.md", doc())
    write(pk / "commands" / "manual.md", doc(disable_model_invocation=True))
    write(pk / "commands" / "helper.md", "no frontmatter at all\n")
    write(pk / "workflows" / "flow.workflow.js", "export const meta = { name: 'flowy', description: 'd' }\n")
    write(pk / "agents" / "listed.md", doc(name="named-agent"))
    write(pk / "agents" / "unlisted.md", doc(name="ignored"))  # the manifest list replaces the default
    late = market / "plugins" / "late"
    write(late / ".claude-plugin" / "plugin.json", json.dumps({"name": "late", "skills": ["./skills/"]}))
    write(late / "skills" / "SKILL.md", doc(name="late-root"))  # SKILL.md directly in a listed skills/
    write(late / "agents" / "team" / "lead.md", doc(name="lead"))
    write(home / ".claude" / "acme" / "preset" / ".claude" / "settings.json",
          json.dumps({"enabledPlugins": {"pk@mk": True, "late@mk": True, "ghost@mk": True}}))
    write(home / ".claude" / "settings.json", json.dumps({"enabledPlugins": {"nope@elsewhere": True}}))
    write(tmp / "managed.json", json.dumps({"enabledPlugins": {"late@mk": True}}))
    write(home / ".claude" / "skills" / "mine" / "SKILL.md", doc())
    write(home / ".claude" / "commands" / "quick.md", doc())
    write(cwd / ".claude" / "settings.local.json", json.dumps({"enabledPlugins": {"ghost@mk": False}}))
    write(cwd / ".claude" / "agents" / "local.md", doc(name="local-agent"))
    return Paths(home, tmp / "managed.json"), cwd


def test_layers_apply_in_harness_order(tmp_path: Path) -> None:
    paths, cwd = build_home(tmp_path)
    d = discover(paths, cwd)
    assert [layer.name for layer in d.layers] == ["add-dir", "managed", "user", "local"]
    assert d.add_dirs == (paths.preset,)
    enabled = enabled_packages(list(d.layers))
    assert enabled == {"pk@mk": "add-dir", "late@mk": "managed", "nope@elsewhere": "user"}  # ghost disabled by local
    assert [p.id for p in d.packages] == ["late@mk", "pk@mk"]
    assert [(u.id, u.reason) for u in d.unresolved] == [("nope@elsewhere", "unknown marketplace 'elsewhere'")]
    assert d.packages[1].root == (tmp_path / "home" / ".claude" / "market" / "plugins" / "pk").resolve()


def test_missing_package_directory_is_unresolved(tmp_path: Path) -> None:
    paths, cwd = build_home(tmp_path)
    write(cwd / ".claude" / "settings.local.json", "{}")  # stop disabling ghost
    reasons = {u.id: u.reason for u in discover(paths, cwd).unresolved}
    assert reasons["ghost@mk"].startswith("package directory does not exist")


def test_entity_names_and_listing_rules(tmp_path: Path) -> None:
    paths, cwd = build_home(tmp_path)
    entities = {(e.kind.value.name, e.name): e for e in disk_entities(discover(paths, cwd), paths, cwd)}
    listed = sorted(name for (kind, name), e in entities.items() if e.model_invocable and kind != "agent")
    assert listed == [
        "late:late-root",  # SKILL.md directly in a listed skills/ root takes its frontmatter name
        "mine",  # user skill: bare name
        "pk:added",  # manifest-listed directory supplements skills/
        "pk:by-dir",  # named by directory, not frontmatter
        "pk:flowy",  # workflow, by its meta name
        "pk:go",
        "pk:solo",  # listed path that is itself a skill
        "pk:sub:deep",  # nested command: path segments joined by ':'
        "quick",  # user command
    ]
    assert not entities[("skill", "pk:hidden")].model_invocable
    assert not entities[("command", "pk:manual")].model_invocable  # disable-model-invocation
    assert not entities[("command", "pk:helper")].model_invocable  # no description
    assert ("skill", "pk:nested") not in entities and ("skill", "pk:sub") not in entities
    agents = sorted(name for (kind, name) in entities if kind == "agent")
    assert agents == ["late:team:lead", "local-agent", "pk:named-agent"]  # unlisted.md ignored


def test_lenient_frontmatter_matches_the_harness() -> None:
    text = "---\nargument-hint: [path | N<number>] [--doc x]\ndescription: works anyway\ndisable-model-invocation: true\n---\nbody\n"
    meta, lenient = read_frontmatter(text)
    assert lenient and meta["description"] == "works anyway" and meta["disable-model-invocation"] is True


def test_a_tilde_in_settings_is_the_home_being_read_not_this_process_home(tmp_path: Path, monkeypatch) -> None:
    """Reading a variant home must never read the real one: ``~`` in its settings is the variant's home, as it is for
    a harness run with HOME set to it."""
    real, variant = tmp_path / "real", tmp_path / "variant"
    monkeypatch.setenv("HOME", str(real))
    write(variant / ".claude" / "settings.json", json.dumps({
        "permissions": {"additionalDirectories": ["~/extra", "/abs/dir"]},
        "extraKnownMarketplaces": {"mk": {"source": {"source": "directory", "path": "~/market"}}},
        "enabledPlugins": {"pk@mk": True}}))
    write(variant / "market" / ".claude-plugin" / "marketplace.json", json.dumps({"plugins": [{"name": "pk", "source": "./pk"}]}))
    write(variant / "market" / "pk" / ".claude-plugin" / "plugin.json", "{}")
    d = discover(Paths(variant, tmp_path / "none.json", tmp_path / "no-install"), tmp_path / "work")
    assert d.add_dirs == (variant / "extra", Path("/abs/dir"))
    assert [p.root for p in d.packages] == [(variant / "market" / "pk").resolve()]


def _copy(home: Path, to: Path) -> Path:
    shutil.copytree(home, to, symlinks=True)
    return to


def test_a_copied_home_reads_its_own_copy_of_a_marketplace_the_home_it_copies_records(tmp_path: Path) -> None:
    """known_marketplaces.json records the marketplace by absolute path in the home it was written in; a copy of that
    home (a variant) is read from its own copy of the marketplace, as the harness reads it under a substituted HOME
    (Verified Facts, E2b) — the home it was copied from is never read for it. Read as itself, a home is unchanged."""
    paths, cwd = build_home(tmp_path / "r")
    original = paths.home
    copy = _copy(original, tmp_path / "copy")
    as_itself = discover(paths, cwd)
    assert as_itself.packages and all(p.root.is_relative_to(original.resolve()) for p in as_itself.packages)
    alias = tmp_path / "alias"
    alias.symlink_to(original)  # the home it copies, named through a link: the recorded path is the resolved one
    for copied_from in (original, alias):
        read = discover(replace(paths, home=copy, copied_from=copied_from), cwd)
        assert [p.id for p in read.packages] == ["late@mk", "pk@mk"]
        assert all(p.root.is_relative_to((copy / ".claude" / "market").resolve()) for p in read.packages)
        assert [(u.id, u.reason) for u in read.unresolved] == [("nope@elsewhere", "unknown marketplace 'elsewhere'")]


def test_a_copied_home_maps_a_marketplace_recorded_through_a_link_to_the_home_it_copies(tmp_path: Path) -> None:
    """The other way round from the test above: the home it copies is given resolved and the location was recorded
    through a symlinked spelling of it (``/tmp`` against ``/private/tmp``). It is still the original's ``.claude/``,
    so the copy's checkout is read, never the original's."""
    paths, cwd = build_home(tmp_path / "r")
    original = paths.home.resolve()
    alias = tmp_path / "alias"
    alias.symlink_to(original)
    write(original / ".claude" / "plugins" / "known_marketplaces.json",
          json.dumps({"mk": {"installLocation": str(alias / ".claude" / "market")}}))
    copy = _copy(original, tmp_path / "copy")
    read = discover(replace(paths, home=copy, copied_from=original), cwd)
    assert [p.id for p in read.packages] == ["late@mk", "pk@mk"]
    assert all(p.root.is_relative_to((copy / ".claude" / "market").resolve()) for p in read.packages)
    shutil.rmtree(copy / ".claude" / "market")
    with pytest.raises(MarketplaceNotCopied):  # and with no copy, refused: the original's is not read through the link
        discover(replace(paths, home=copy, copied_from=original), cwd)


def test_a_copied_home_resolves_only_the_part_of_a_recorded_location_that_names_the_home_it_copies(
        tmp_path: Path) -> None:
    """A link inside ``.claude/`` (here the original's ``market`` pointing at ``real-market`` beside it) is not
    resolved: the copy reads the relative path the location records (``market``), as for a location recorded without
    any link."""
    paths, cwd = build_home(tmp_path / "r")
    original = paths.home.resolve()
    shutil.move(original / ".claude" / "market", original / ".claude" / "real-market")
    (original / ".claude" / "market").symlink_to("real-market")
    alias = tmp_path / "alias"
    alias.symlink_to(original)
    write(original / ".claude" / "plugins" / "known_marketplaces.json",
          json.dumps({"mk": {"installLocation": str(alias / ".claude" / "market")}}))
    copy = _copy(original, tmp_path / "copy")
    (copy / ".claude" / "market").unlink()
    shutil.move(copy / ".claude" / "real-market", copy / ".claude" / "market")  # the copy has market, no real-market
    read = discover(replace(paths, home=copy, copied_from=original), cwd)
    assert [p.root for p in read.packages] == [(copy / ".claude" / "market" / "plugins" / p).resolve()
                                               for p in ("late", "pk")]


def test_a_copied_home_reads_a_marketplace_outside_the_copied_claude_directory_where_it_is_recorded(
        tmp_path: Path) -> None:
    """Only a location under the copied home's ``.claude/`` is mapped: that is the case E2b verified. Anywhere else —
    a checkout under the home but beside ``.claude/`` — is read where it is recorded (a variant lists what it reads
    there as escapes)."""
    paths, cwd = build_home(tmp_path / "r")
    original = paths.home
    elsewhere = original / "dev" / "market"
    shutil.move(original / ".claude" / "market", elsewhere)
    write(original / ".claude" / "plugins" / "known_marketplaces.json",
          json.dumps({"mk": {"installLocation": str(elsewhere)}}))
    copy = _copy(original, tmp_path / "copy")
    read = discover(replace(paths, home=copy, copied_from=original), cwd)
    assert [p.root for p in read.packages] == [(elsewhere / "plugins" / p).resolve() for p in ("late", "pk")]


def test_a_copied_home_without_its_copy_of_a_marketplace_is_refused_not_read_from_the_home_it_copies(
        tmp_path: Path) -> None:
    paths, cwd = build_home(tmp_path / "r")
    copy = _copy(paths.home, tmp_path / "copy")
    shutil.rmtree(copy / ".claude" / "market")
    with pytest.raises(MarketplaceNotCopied) as refused:
        discover(replace(paths, home=copy, copied_from=paths.home), cwd)
    message = str(refused.value)
    assert str(copy / ".claude" / "market") in message and str(paths.home / ".claude" / "market") in message
    assert "not read from there" in message
    assert [p.id for p in discover(paths, cwd).packages] == ["late@mk", "pk@mk"]  # the original still reads
