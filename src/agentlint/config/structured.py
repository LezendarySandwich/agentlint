"""JSON and YAML addressing by key path.

Excision deletes a key and re-checks the result semantically: the new document must parse, and
must equal the original with exactly that key removed. Deleting a character range from JSON
would yield an invalid file and you would measure a crash, not a rule's absence.
"""

from __future__ import annotations

import copy
import json
import re
from bisect import bisect_right
from json.decoder import scanstring
from typing import Any

import yaml

from agentlint.config.locators import KeyPath
from agentlint.config.regions import (
    ExcisionInvalid,
    FormatError,
    Granularity,
    LocatorNotFound,
    Region,
    split_lines,
)

Path = tuple[str | int, ...]

_WS = re.compile(r"[ \t\n\r]*")
_SCALAR = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null")
_INDENT = re.compile(r"^([ \t]+)\S", re.MULTILINE)


# --- paths -------------------------------------------------------------------------------------


def get_path(data: Any, path: Path) -> Any:
    node = data
    for part in path:
        try:
            node = node[part]
        except (KeyError, IndexError, TypeError):
            raise LocatorNotFound(f"no key path {path!r}") from None
    return node


def delete_path(data: Any, path: Path) -> Any:
    """A deep copy of ``data`` with the node at ``path`` removed."""
    if not path:
        raise LocatorNotFound("cannot delete the document root by key path")
    result = copy.deepcopy(data)
    parent = get_path(result, path[:-1])
    try:
        del parent[path[-1]]
    except (KeyError, IndexError, TypeError):
        raise LocatorNotFound(f"no key path {path!r}") from None
    return result


def has_path(data: Any, path: Path) -> bool:
    try:
        get_path(data, path)
    except LocatorNotFound:
        return False
    return True


def check_survivors(before: Any, after: Any, required: tuple[KeyPath, ...]) -> None:
    """Required keys must survive excision: present before means present after.

    A key that was never there is not this excision's problem. Real entity files omit keys the
    harness treats as required and fall back to a default (e.g. a name taken from the directory).
    """
    lost = [key.path for key in required if has_path(before, key.path) and not has_path(after, key.path)]
    if lost:
        raise ExcisionInvalid(f"required keys removed by excision: {lost}")


def _child_paths(data: Any, granularity: Granularity) -> list[Path]:
    """Coarse: top-level keys. Fine: second-level keys, plus top-level leaves."""
    if not isinstance(data, (dict, list)):
        return []
    top = list(data.keys()) if isinstance(data, dict) else list(range(len(data)))
    if granularity is Granularity.COARSE:
        return [(key,) for key in top]
    paths: list[Path] = []
    for key in top:
        child = data[key]
        if isinstance(child, dict) and child:
            paths.extend((key, sub) for sub in child)
        elif isinstance(child, list) and child:
            paths.extend((key, index) for index in range(len(child)))
        else:
            paths.append((key,))
    return paths


# --- JSON --------------------------------------------------------------------------------------


def json_load(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise FormatError(f"invalid JSON: {error}") from None


def _json_spans(text: str) -> dict[Path, tuple[int, int]]:
    """Character span of every key (opening quote to end of value) and every array element.

    Assumes the text is valid JSON; call json_load first. Duplicate keys keep the last
    occurrence, matching json.loads.
    """
    spans: dict[Path, tuple[int, int]] = {}

    def skip(index: int) -> int:
        return _WS.match(text, index).end()  # type: ignore[union-attr]

    def value(index: int, path: Path) -> int:
        index = skip(index)
        char = text[index]
        if char == "{":
            index = skip(index + 1)
            if text[index] == "}":
                return index + 1
            while True:
                index = skip(index)
                key_start = index
                key, index = scanstring(text, index + 1)
                index = skip(index) + 1  # past ':'
                index = value(index, path + (key,))
                spans[path + (key,)] = (key_start, index)
                index = skip(index)
                if text[index] == ",":
                    index += 1
                    continue
                return index + 1  # past '}'
        if char == "[":
            index = skip(index + 1)
            if text[index] == "]":
                return index + 1
            position = 0
            while True:
                index = skip(index)
                start = index
                index = value(index, path + (position,))
                spans[path + (position,)] = (start, index)
                position += 1
                index = skip(index)
                if text[index] == ",":
                    index += 1
                    continue
                return index + 1  # past ']'
        if char == '"':
            return scanstring(text, index + 1)[1]
        match = _SCALAR.match(text, index)
        if not match:
            raise FormatError(f"unexpected JSON at offset {index}")
        return match.end()

    value(0, ())
    return spans


def _line_starts(text: str) -> list[int]:
    return [0] + [index + 1 for index, char in enumerate(text) if char == "\n"]


def _json_region(text: str, path: Path, spans: dict[Path, tuple[int, int]], starts: list[int]) -> Region:
    if path not in spans:
        raise LocatorNotFound(f"no key path {path!r}")
    start, end = spans[path]
    return Region(KeyPath(path), text[start:end], bisect_right(starts, start), bisect_right(starts, end - 1))


def json_regions(text: str, granularity: Granularity = Granularity.COARSE) -> list[Region]:
    data = json_load(text)
    spans, starts = _json_spans(text), _line_starts(text)
    return [_json_region(text, path, spans, starts) for path in _child_paths(data, granularity)]


def json_region(text: str, locator: KeyPath) -> Region:
    json_load(text)
    return _json_region(text, locator.path, _json_spans(text), _line_starts(text))


def _json_indent(text: str) -> int | str:
    match = _INDENT.search(text)
    if not match:
        return 2
    indent = match.group(1)
    return "\t" if "\t" in indent else len(indent)


def json_excise(text: str, locator: KeyPath, required: tuple[KeyPath, ...] = ()) -> str:
    original = json_load(text)
    expected = delete_path(original, locator.path)
    new_text = json.dumps(expected, indent=_json_indent(text), ensure_ascii=False)
    if text.endswith("\n"):
        new_text += "\n"
    if json_load(new_text) != expected:
        raise ExcisionInvalid(f"re-serialised JSON does not round-trip after removing {locator.path!r}")
    check_survivors(original, expected, required)
    return new_text


def json_validate(original_text: str, new_text: str, required: tuple[KeyPath, ...] = ()) -> None:
    try:
        data = json_load(new_text)
    except FormatError as error:
        raise ExcisionInvalid(str(error)) from None
    check_survivors(json_load(original_text), data, required)


# --- YAML --------------------------------------------------------------------------------------


def yaml_load(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise FormatError(f"invalid YAML: {error}") from None


def yaml_spans(text: str) -> dict[Path, tuple[int, int]]:
    """0-based half-open line span ``[start, end)`` of every key and sequence item.

    A key's span runs to the line where its next sibling starts, so comments and blank lines
    between keys belong to the key above them. Flow-style collections give empty spans.
    """
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as error:
        raise FormatError(f"invalid YAML: {error}") from None
    constructor = yaml.SafeLoader("")
    spans: dict[Path, tuple[int, int]] = {}

    def walk(node: yaml.Node, path: Path, end: int) -> None:
        if isinstance(node, yaml.MappingNode):
            entries = node.value
            for position, (key_node, value_node) in enumerate(entries):
                key = constructor.construct_object(key_node, deep=True)
                start = key_node.start_mark.line
                stop = entries[position + 1][0].start_mark.line if position + 1 < len(entries) else end
                spans[path + (key,)] = (start, stop)
                walk(value_node, path + (key,), stop)
        elif isinstance(node, yaml.SequenceNode):
            items = node.value
            for position, item in enumerate(items):
                start = item.start_mark.line
                stop = items[position + 1].start_mark.line if position + 1 < len(items) else end
                spans[path + (position,)] = (start, stop)
                walk(item, path + (position,), stop)

    if root is not None:
        walk(root, (), len(split_lines(text)))
    return spans


def yaml_regions(text: str, granularity: Granularity = Granularity.COARSE, line_offset: int = 0) -> list[Region]:
    data = yaml_load(text)
    spans, lines = yaml_spans(text), split_lines(text)
    found = []
    for path in _child_paths(data, granularity):
        start, end = spans.get(path, (0, 0))
        excerpt = "".join(lines[start:end]) if end > start else yaml.safe_dump({path[-1]: get_path(data, path)})
        found.append(Region(KeyPath(path), excerpt, start + 1 + line_offset, max(start + 1, end) + line_offset))
    return found


def yaml_region(text: str, locator: KeyPath, line_offset: int = 0) -> Region:
    data = yaml_load(text)
    value = get_path(data, locator.path)
    start, end = yaml_spans(text).get(locator.path, (0, 0))
    lines = split_lines(text)
    excerpt = "".join(lines[start:end]) if end > start else yaml.safe_dump({locator.path[-1]: value})
    return Region(locator, excerpt, start + 1 + line_offset, max(start + 1, end) + line_offset)


def yaml_excise(text: str, locator: KeyPath, required: tuple[KeyPath, ...] = ()) -> str:
    """Remove a key, preferring a minimal textual edit; fall back to re-serialising.

    The textual edit keeps every other byte identical, which matters because a re-dump can
    change how a different YAML parser reads untouched values (``yes`` vs ``true``). Either
    way the result must equal the original with exactly that key removed.
    """
    original = yaml_load(text)
    expected = delete_path(original, locator.path)
    check_survivors(original, expected, required)
    start, end = yaml_spans(text).get(locator.path, (0, 0))
    if end > start:
        lines = split_lines(text)
        candidate = "".join(lines[:start] + lines[end:])
        try:
            loaded = yaml_load(candidate)
            if loaded == expected or (loaded is None and expected == {}):  # emptied mapping
                return candidate
        except FormatError:
            pass
    candidate = yaml.safe_dump(expected, sort_keys=False, allow_unicode=True, default_flow_style=False)
    if yaml_load(candidate) != expected:
        raise ExcisionInvalid(f"could not remove {locator.path!r} without changing other values")
    return candidate


def yaml_validate(original_text: str, new_text: str, required: tuple[KeyPath, ...] = ()) -> None:
    try:
        data = yaml_load(new_text)
    except FormatError as error:
        raise ExcisionInvalid(str(error)) from None
    check_survivors(yaml_load(original_text), data, required)
