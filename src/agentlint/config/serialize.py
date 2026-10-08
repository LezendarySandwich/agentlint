"""The JSON form of a ConfigSnapshot — the one data contract the CLI, report and UI share — and the way back.

``to_data`` writes every dataclass field under its own name (a field the class computes itself, such as a rule's
``id``, is written too, and computed again on reading), and beside them any values a class names in ``DERIVED``
(the always-on outcome's ``attributed``, say) so a JSON reader never re-derives them; reading back ignores those. Members of a union (content states, locators, the
always-on outcome — and a conversation's entries) carry a ``"type"`` tag so a reader never has to guess which shape it holds. ``from_data`` reads
it back into the model, driven by the same field types, so every reader works on model objects — with the model's
own rules — rather than on dictionaries. ``texts_out`` moves each text out to a ``text_ref`` (its content hash), as
the store and the UI report keep them; ``from_data`` takes them back in with ``text_of``.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from agentlint.config.content import FileText, NotObserved, ObservedText, SizeOnly
from agentlint.config.conversation import Briefing, Injected, Note, Prompt, Reply, Thinking, ToolCall
from agentlint.config.locators import Body, KeyPath, LineRange, MarkdownSection, Whole
from agentlint.config.model import ConfigSnapshot, NotReconciled, Overcounted, Reconciled

SNAPSHOT_FORMAT_VERSION = 3  # the snapshot JSON's "schema_version"; bump when a field changes meaning or goes

_TAGGED = {
    FileText: "file_text",
    ObservedText: "observed_text",
    SizeOnly: "size_only",
    NotObserved: "not_observed",
    Whole: "whole",
    Body: "body",
    MarkdownSection: "markdown_section",
    LineRange: "line_range",
    KeyPath: "key_path",
    Reconciled: "reconciled",
    Overcounted: "overcounted",
    NotReconciled: "not_reconciled",
}
_TAGGED_LIVE = {  # a conversation's entries: served live by `agentlint ui`, never in a snapshot or a report
    Prompt: "prompt",
    Reply: "reply",
    Thinking: "thinking",
    ToolCall: "tool_call",
    Injected: "injected",
    Note: "note",
    Briefing: "briefing",
}


def to_data(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        data = {field.name: to_data(getattr(value, field.name)) for field in dataclasses.fields(value)}
        data |= {name: to_data(getattr(value, name)) for name in getattr(value, "DERIVED", ())}
        tag = _TAGGED.get(type(value)) or _TAGGED_LIVE.get(type(value))
        return {"type": tag, **data} if tag else data
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [to_data(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_data(item) for key, item in value.items()}
    return value


def snapshot_data(snapshot: ConfigSnapshot) -> dict[str, Any]:
    return {"schema_version": SNAPSHOT_FORMAT_VERSION, **to_data(snapshot)}


_BY_TAG = {tag: cls for cls, tag in (_TAGGED | _TAGGED_LIVE).items()}
TEXT_TAGS = frozenset(tag for cls, tag in _TAGGED.items() if "text" in {f.name for f in dataclasses.fields(cls)})


def texts_out(node: Any, texts: dict[str, str]) -> Any:
    """The data with each text replaced by a ``text_ref`` — its content hash — the texts collected by ref."""
    if isinstance(node, dict):
        if node.get("type") in TEXT_TAGS and isinstance(node.get("text"), str):
            texts[node["content_hash"]] = node["text"]
            node = {k: v for k, v in node.items() if k != "text"} | {"text_ref": node["content_hash"]}
        return {k: texts_out(v, texts) for k, v in node.items()}
    if isinstance(node, list):
        return [texts_out(item, texts) for item in node]
    return node


class FormatError(ValueError):
    """Data that isn't the JSON form of what was asked for: another format version, or a field that won't read."""


T = TypeVar("T")


def from_data(data: dict[str, Any], *, text_of: Callable[[str], str | None] | None = None) -> ConfigSnapshot:
    """A snapshot from ``snapshot_data`` output, its invariants checked again on construction. A text moved out to a
    ``text_ref`` is fetched with ``text_of``. Raises FormatError for another format version or data that won't read."""
    if data.get("schema_version") != SNAPSHOT_FORMAT_VERSION:
        raise FormatError(f"snapshot format {data.get('schema_version')}; this agentlint reads {SNAPSHOT_FORMAT_VERSION}")
    return as_model(ConfigSnapshot, data, text_of=text_of)


def as_model(cls: type[T], data: Any, *, text_of: Callable[[str], str | None] | None = None) -> T:
    """``to_data`` undone for any model dataclass: each field read as its declared type. A field the data lacks takes
    its default; a key the class doesn't have is ignored."""
    try:
        return _decode(cls, data, text_of)
    except FormatError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise FormatError(f"not a {cls.__name__}: {type(error).__name__}: {error}") from error


_HINTS: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    if cls not in _HINTS:
        _HINTS[cls] = typing.get_type_hints(cls)
    return _HINTS[cls]


def _decode(tp: Any, value: Any, text_of: Callable[[str], str | None] | None) -> Any:
    origin = typing.get_origin(tp)
    if origin in (types.UnionType, typing.Union):
        members = typing.get_args(tp)
        if value is None and type(None) in members:
            return None
        if isinstance(value, dict) and "type" in value:
            cls = _BY_TAG[value["type"]]
            if cls not in members:
                raise TypeError(f"{value['type']!r} is not one of {tp}")
            return _decode(cls, value, text_of)
        untagged = [member for member in members if dataclasses.is_dataclass(member) and member not in _BY_TAG.values()]
        if isinstance(value, dict) and len(untagged) == 1:  # e.g. ``TokenCount | None``: one shape to be
            return _decode(untagged[0], value, text_of)
        for member in members:
            if member in (str, int, float, bool) and type(value) is member:
                return value
        if float in members and type(value) is int:
            return value
        optional = [member for member in members if member is not type(None)] if type(None) in members else []
        if isinstance(value, str) and optional == [Path]:  # an empty path is none: how snapshots stored before
            return Path(value) if value else None  # 2026-10-04 wrote a session's unknown project
        if isinstance(value, str) and optional == [datetime]:
            return datetime.fromisoformat(value)
        enums = [member for member in members if isinstance(member, type) and issubclass(member, Enum)]
        if len(enums) == 1 and isinstance(value, str):  # e.g. ``Visibility | None``: one enum to be
            return enums[0](value)
        raise TypeError(f"{value!r} is not a {tp}")
    if origin is tuple:
        args = typing.get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], item, text_of) for item in value)
        if len(args) != len(value):
            raise TypeError(f"{value!r} is not a {tp}")
        return tuple(_decode(arg, item, text_of) for arg, item in zip(args, value, strict=True))
    if dataclasses.is_dataclass(tp):
        if not isinstance(value, dict):
            raise TypeError(f"{tp.__name__} is written as an object, got {value!r}")
        if "text" not in value and isinstance(value.get("text_ref"), str) and "text" in _hints(tp):
            if text_of is None or (text := text_of(value["text_ref"])) is None:
                raise FormatError(f"the text {value['text_ref']} isn't available")
            value = value | {"text": text}
        hints, kwargs = _hints(tp), {}
        for field in dataclasses.fields(tp):
            if not field.init:
                continue  # computed from the other fields on construction, as ``DERIVED`` values are
            if field.name in value:
                kwargs[field.name] = _decode(hints[field.name], value[field.name], text_of)
            elif field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:
                raise FormatError(f"{tp.__name__} has no {field.name!r}")
        return tp(**kwargs)
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(value)
    if tp is Path:
        return Path(value)
    if tp is datetime:
        return datetime.fromisoformat(value)
    if tp in (str, int, bool) and type(value) is not tp:
        raise TypeError(f"{value!r} is not a {tp.__name__}")
    if tp is float and type(value) not in (int, float):
        raise TypeError(f"{value!r} is not a float")
    return value
