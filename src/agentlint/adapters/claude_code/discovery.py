"""Where Claude Code's configuration lives on disk, and which packages are enabled.

Read-only and deterministic. Never invokes the harness: on the reference machine the launcher
rewrites a preset settings file on every invocation, even ``--help`` (Verified Facts).

Settings layers, in the order the harness applies ``enabledPlugins`` (each later layer can enable
with ``true`` or disable with ``false``):

    add-dir    <dir>/.claude/settings.json and settings.local.json, per additional directory
               (a launcher that wraps the harness can pass its preset this way)
    managed    organization-managed settings
    user       ~/.claude/settings.json
    project    <cwd>/.claude/settings.json
    local      <cwd>/.claude/settings.local.json

Packages resolve through marketplaces, not ``installed_plugins.json``: the package root is the
marketplace directory joined with the package's ``source`` in that marketplace's manifest. A copied home (a variant's)
reads a marketplace that ``known_marketplaces.json`` records under the home it was copied from at the same place under
its own ``.claude/``, as the harness does (Verified Facts, E2b), and never from the home it was copied from.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

from agentlint.config.taxonomy import Authority
from agentlint.runner.variant import VariantError

MANAGED_SETTINGS = Path("/Library/Application Support/ClaudeCode/managed-settings.json")
INSTALL_DIR = Path("/usr/local/bin/claude_code")  # where a packaged build of the harness installs it
PRESETS = "*/preset"  # under ~/.claude: a launcher wrapping the harness keeps its preset at <launcher>/preset


@dataclass(frozen=True)
class Paths:
    home: Path
    managed_settings: Path = MANAGED_SETTINGS
    install_dir: Path = INSTALL_DIR
    # the home this one is a copy of, for a variant's; None for a home read as itself. The copied
    # known_marketplaces.json still records marketplace locations under that home's .claude/ by absolute path
    copied_from: Path | None = None

    @property
    def config(self) -> Path:
        return self.home / ".claude"

    @property
    def projects(self) -> Path:
        return self.config / "projects"

    @property
    def preset(self) -> Path:
        """The launcher's preset directory: the first ``~/.claude/<launcher>/preset`` there is (one holding a settings
        file first), else a place none is (``~/.claude/launcher/preset``)."""
        found = sorted((p for p in self.config.glob(PRESETS) if p.is_dir()),
                       key=lambda p: (not (p / ".claude" / "settings.json").is_file(), str(p)))
        return found[0] if found else self.config / "launcher" / "preset"

    @classmethod
    def default(cls) -> Paths:
        return cls(Path(os.environ.get("HOME", str(Path.home()))))


@dataclass(frozen=True)
class SettingsLayer:
    name: str  # "add-dir" | "managed" | "user" | "project" | "local"
    path: Path
    authority: Authority
    text: str
    data: dict[str, Any]


@dataclass(frozen=True)
class Package:
    id: str  # "<name>@<marketplace>"
    name: str
    marketplace: str
    root: Path
    manifest: dict[str, Any]
    enabled_by: str  # the settings layer that enabled it


@dataclass(frozen=True)
class Unresolved:
    id: str
    enabled_by: str
    reason: str


@dataclass(frozen=True)
class Discovery:
    layers: tuple[SettingsLayer, ...]
    packages: tuple[Package, ...]
    unresolved: tuple[Unresolved, ...]
    add_dirs: tuple[Path, ...] = field(default=())


def clean(value: Any) -> Any:
    """JSON with every lone surrogate — what a "\\ud800" escape decodes to — replaced by U+FFFD. No filesystem can
    name a path holding one, and every path operation raises on it; a replaced character just names nothing."""
    if isinstance(value, str):
        try:
            value.encode("utf-8")
            return value
        except UnicodeEncodeError:
            return "".join("\ufffd" if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in value)
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, dict):
        return {clean(key): clean(item) for key, item in value.items()}
    return value


def _read_json(path: Path) -> tuple[str, dict[str, Any]] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")  # a stray byte mustn't drop the whole file
    except (OSError, ValueError):  # missing, unreadable, or a path no filesystem can name (a lone surrogate, a NUL)
        return None
    try:
        data = clean(json.loads(text))
    except ValueError:
        data = {}
    return text, data if isinstance(data, dict) else {}


def expand(paths: Paths, written: str) -> Path:
    """A path as settings write it, ``~`` meaning the home being read — a variant's, for a variant — never the HOME
    this process happens to run under (``os.path.expanduser``), which would read the real home from a variant."""
    if written == "~" or written.startswith("~/"):
        return paths.home / written[2:]
    return Path(written)


def _layer(name: str, path: Path, authority: Authority) -> SettingsLayer | None:
    read = _read_json(path)
    return SettingsLayer(name, path, authority, *read) if read else None


def add_dirs(paths: Paths, base_layers: list[SettingsLayer]) -> list[Path]:
    """Additional directories: settings' permissions.additionalDirectories, plus the launcher preset."""
    found: list[Path] = []
    for layer in base_layers:
        permissions = layer.data.get("permissions")
        extra = permissions.get("additionalDirectories") if isinstance(permissions, dict) else None
        for entry in extra if isinstance(extra, list) else []:
            if isinstance(entry, str):
                found.append(expand(paths, entry))
    if (paths.preset / ".claude" / "settings.json").is_file():
        found.append(paths.preset)
    unique: list[Path] = []
    for directory in found:
        if directory not in unique:
            unique.append(directory)
    return unique


def settings_layers(paths: Paths, cwd: Path) -> tuple[list[SettingsLayer], list[Path]]:
    managed = _layer("managed", paths.managed_settings, Authority.MANAGED)
    user = _layer("user", paths.config / "settings.json", Authority.USER)
    project = _layer("project", cwd / ".claude" / "settings.json", Authority.PROJECT)
    local = _layer("local", cwd / ".claude" / "settings.local.json", Authority.PROJECT)
    base = [layer for layer in (managed, user, project, local) if layer]
    directories = add_dirs(paths, base)
    extra: list[SettingsLayer] = []
    for directory in directories:
        for filename in ("settings.json", "settings.local.json"):
            layer = _layer("add-dir", directory / ".claude" / filename, Authority.USER)
            if layer:
                extra.append(layer)
    ordered = extra + [layer for layer in (managed, user, project, local) if layer]
    return ordered, directories


def enabled_packages(layers: list[SettingsLayer]) -> dict[str, str]:
    """Package id -> the layer that last enabled it. ``false`` in a later layer disables."""
    enabled: dict[str, str] = {}
    for layer in layers:
        values = layer.data.get("enabledPlugins")
        if not isinstance(values, dict):
            continue
        for package_id, on in values.items():
            if on is True:
                enabled[package_id] = layer.name
            elif on is False:
                enabled.pop(package_id, None)
    return enabled


class MarketplaceNotCopied(VariantError):
    """A copied home has no copy of a marketplace that an enabled package resolves through. Its packages are not read
    from the home it was copied from instead: the harness reads the copy's (Verified Facts, E2b), and what it does
    when there is none is not known."""


def under_config(home: Path, written: Path) -> PurePath | None:
    """Where ``written`` is under ``home``'s ``.claude/``, relative to it; None when it is anywhere else, or relative.

    Either side may be spelled through a symlink (``/tmp`` and ``/private/tmp``, or a ``.claude`` that is a link): the
    path is matched as written against ``.claude/`` as written and resolved, and failing that, its shallowest ancestor
    that resolves to the same place as ``.claude/`` stands for it. Only that ancestor is resolved, never the rest of
    the path, so a link inside a marketplace doesn't change which relative path it is."""
    origin = home / ".claude"
    real = Path(os.path.realpath(origin))
    for base in dict.fromkeys((origin, real)):  # as written, and resolved
        if written.is_relative_to(base):
            return PurePath(written.relative_to(base))
    if not written.is_absolute():  # a relative location is relative to no home; it is read where it is written
        return None
    for ancestor in reversed((written, *written.parents)):  # shallowest first
        if Path(os.path.realpath(ancestor)) == real:
            return PurePath(written.relative_to(ancestor))
    return None


def in_copy(paths: Paths, written: Path) -> Path | None:
    """Where a marketplace location ``known_marketplaces.json`` records under the ``.claude/`` of the home this one
    was copied from is in this home: the same path under this home's ``.claude/`` (``under_config``), as the harness
    reads it under a substituted HOME (Verified Facts, E2b). None for a location anywhere else, and always for a home
    read as itself (no ``copied_from``): such a location is read where it is written. A variant's cut of a package's
    hook maps the same recorded location by the same rule (``variants._checkout_in_copy``), so for every location
    mapped here it edits the very copy read here, and it refuses a hook whose location isn't mapped — or whose
    package's manifests name its directory (``source``) or hooks file by absolute path, which no mapping changes: a
    copied home reads that path where it is written."""
    if paths.copied_from is None or written.is_relative_to(paths.config):
        return None
    relative = under_config(paths.copied_from, written)
    return paths.config / relative if relative is not None else None


def marketplaces(paths: Paths, layers: list[SettingsLayer]) -> dict[str, tuple[Path, Path | None]]:
    """Marketplace name -> (its directory in this home, the location recorded in the home this one was copied from
    when that was mapped into this one, else None)."""
    found: dict[str, tuple[Path, Path | None]] = {}
    known = _read_json(paths.config / "plugins" / "known_marketplaces.json")
    for name, entry in (known[1] if known else {}).items():
        if isinstance(entry, dict) and isinstance(entry.get("installLocation"), str):
            written = Path(entry["installLocation"])
            mapped = in_copy(paths, written)
            found[name] = (mapped, written) if mapped is not None else (written, None)
    for layer in layers:
        extra = layer.data.get("extraKnownMarketplaces")
        for name, entry in (extra if isinstance(extra, dict) else {}).items():
            source = entry.get("source") if isinstance(entry, dict) else None
            if isinstance(source, dict) and source.get("source") == "directory" and isinstance(source.get("path"), str):
                found.setdefault(name, (expand(paths, source["path"]), None))
    return found


def resolve_packages(paths: Paths, layers: list[SettingsLayer]) -> tuple[list[Package], list[Unresolved]]:
    places = marketplaces(paths, layers)
    manifests: dict[str, dict[str, Any]] = {}
    packages: list[Package] = []
    unresolved: list[Unresolved] = []
    for package_id, layer_name in sorted(enabled_packages(layers).items()):
        name, _, market = package_id.partition("@")
        if market not in places:
            unresolved.append(Unresolved(package_id, layer_name, f"unknown marketplace {market!r}"))
            continue
        place, written = places[market]
        if written is not None and not place.is_dir():
            raise MarketplaceNotCopied(
                f"{package_id} can't be read as the harness would read it: this home ({paths.home}) has no {place}, "
                f"its copy of the {market} marketplace that known_marketplaces.json records at {written} in the home "
                f"it was copied from ({paths.copied_from}). It is not read from there instead: the harness reads the "
                "copy's, and what it does when there is none is not known")
        if market not in manifests:
            read = _read_json(place / ".claude-plugin" / "marketplace.json")
            manifests[market] = read[1] if read else {}
        entries = [p for p in manifests[market].get("plugins", []) if isinstance(p, dict) and p.get("name") == name]
        source = entries[0].get("source") if entries else None
        if not isinstance(source, str):
            unresolved.append(Unresolved(package_id, layer_name, f"not listed in the {market} marketplace manifest"))
            continue
        root = (place / source).resolve()
        if not root.is_dir():
            unresolved.append(Unresolved(package_id, layer_name, f"package directory does not exist: {root}"))
            continue
        manifest = _read_json(root / ".claude-plugin" / "plugin.json")
        packages.append(Package(package_id, name, market, root, manifest[1] if manifest else {}, layer_name))
    return packages, unresolved


def harness_version(paths: Paths) -> str | None:
    """The installed harness version, read from the install's package.json — never by running it."""
    read = _read_json(paths.install_dir / "package.json")
    dependencies = read[1].get("dependencies") if read else None
    version = dependencies.get("@anthropic-ai/claude-code") if isinstance(dependencies, dict) else None
    return version if isinstance(version, str) else None


def configured_model(layers: list[SettingsLayer]) -> str | None:
    """The ``model`` setting, from the last layer that sets one."""
    model = None
    for layer in layers:
        if isinstance(layer.data.get("model"), str):
            model = layer.data["model"]
    return model


def discover(paths: Paths, cwd: Path) -> Discovery:
    layers, directories = settings_layers(paths, cwd)
    packages, unresolved = resolve_packages(paths, layers)
    return Discovery(tuple(layers), tuple(packages), tuple(unresolved), tuple(directories))
