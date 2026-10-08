from __future__ import annotations

import json

import pytest

from agentlint.config import structured
from agentlint.config.locators import KeyPath
from agentlint.config.regions import ExcisionInvalid, FormatError, Granularity, LocatorNotFound

SETTINGS = """\
{
  "model": "opus",
  "hooks": {
    "UserPromptSubmit": [
      {"matcher": "", "hooks": [{"type": "command", "command": "echo hi"}]}
    ]
  },
  "env": {"A": "1"}
}
"""


def test_json_top_level_regions_with_lines() -> None:
    regions = structured.json_regions(SETTINGS)
    assert [r.locator for r in regions] == [KeyPath(("model",)), KeyPath(("hooks",)), KeyPath(("env",))]
    assert regions[0].text == '"model": "opus"'
    assert (regions[0].start_line, regions[0].end_line) == (2, 2)
    assert (regions[1].start_line, regions[1].end_line) == (3, 7)
    assert (regions[2].start_line, regions[2].end_line) == (8, 8)


def test_json_fine_regions() -> None:
    regions = structured.json_regions(SETTINGS, Granularity.FINE)
    assert [r.locator.path for r in regions] == [("model",), ("hooks", "UserPromptSubmit"), ("env", "A")]


def test_json_region_for_nested_path() -> None:
    region = structured.json_region(SETTINGS, KeyPath(("hooks", "UserPromptSubmit", 0)))
    assert region.text.startswith('{"matcher"')
    assert region.start_line == region.end_line == 5


def test_json_excise_removes_exactly_one_key_and_keeps_style() -> None:
    result = structured.json_excise(SETTINGS, KeyPath(("hooks", "UserPromptSubmit", 0)))
    expected = json.loads(SETTINGS)
    expected["hooks"]["UserPromptSubmit"].pop(0)
    assert json.loads(result) == expected
    assert result.endswith("\n")
    assert '\n  "model"' in result  # two-space indent detected and kept


def test_json_excise_detects_four_space_indent() -> None:
    result = structured.json_excise('{\n    "a": 1,\n    "b": 2\n}', KeyPath(("a",)))
    assert result == '{\n    "b": 2\n}'


def test_json_escaped_and_unicode_keys() -> None:
    text = '{"a\\"b": 1, "ü": {"x": [1, 2]}}'
    assert [r.locator.path for r in structured.json_regions(text)] == [('a"b',), ("ü",)]
    assert json.loads(structured.json_excise(text, KeyPath(("ü", "x", 1)))) == {'a"b': 1, "ü": {"x": [1]}}


def test_json_missing_path_and_required_keys() -> None:
    with pytest.raises(LocatorNotFound):
        structured.json_excise(SETTINGS, KeyPath(("nope",)))
    with pytest.raises(ExcisionInvalid):
        structured.json_excise(SETTINGS, KeyPath(("model",)), required=(KeyPath(("model",)),))


def test_invalid_json_is_a_format_error() -> None:
    with pytest.raises(FormatError):
        structured.json_regions('{"a": }')


YAML_DOC = """\
name: thing
enabled: yes
description: >
  A folded description
  over two lines.
# comment about tags
tags:
  - a
  - b
"""


def test_yaml_regions_span_block_values() -> None:
    regions = structured.yaml_regions(YAML_DOC)
    spans = {r.locator.path: (r.start_line, r.end_line) for r in regions}
    assert spans == {("name",): (1, 1), ("enabled",): (2, 2), ("description",): (3, 6), ("tags",): (7, 9)}


def test_yaml_textual_excision_keeps_other_bytes_identical() -> None:
    result = structured.yaml_excise(YAML_DOC, KeyPath(("description",)))
    assert result == "name: thing\nenabled: yes\ntags:\n  - a\n  - b\n"
    assert "enabled: yes" in result  # a re-dump would have rewritten this as `true`


def test_yaml_sequence_item_excision() -> None:
    result = structured.yaml_excise(YAML_DOC, KeyPath(("tags", 0)))
    assert structured.yaml_load(result)["tags"] == ["b"]


def test_yaml_flow_style_falls_back_to_redump() -> None:
    result = structured.yaml_excise("a: {x: 1, y: 2}\n", KeyPath(("a", "x")))
    assert structured.yaml_load(result) == {"a": {"y": 2}}


def test_yaml_removing_only_child_keeps_empty_mapping() -> None:
    result = structured.yaml_excise("outer:\n  inner: 1\nother: 2\n", KeyPath(("outer", "inner")))
    assert structured.yaml_load(result) == {"outer": {}, "other": 2}


def test_invalid_yaml_is_a_format_error() -> None:
    with pytest.raises(FormatError):
        structured.yaml_regions("description: Use this: when x\n  bad: [\n")
