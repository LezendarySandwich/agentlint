from __future__ import annotations

import pytest

from agentlint.config import frontmatter, plain
from agentlint.config.content import FileText, content_hash, file_text
from agentlint.config.formats import ContentFormat
from agentlint.config.locators import (
    Body,
    KeyPath,
    LineRange,
    MarkdownSection,
    Whole,
    describe,
    locator_key,
)
from agentlint.config.regions import ExcisionInvalid, FormatError, Granularity, LocatorNotFound

ENTITY = """\
---
name: sql-query
description: >-
  Run a SQL query against a Hive table
  and inspect results.
allowed-tools: Bash
---
# SQL

Intro.

## Steps
Do it.
"""

NAME = (KeyPath(("name",)),)


def test_split_frontmatter() -> None:
    parts = frontmatter.split(ENTITY)
    assert parts.data["name"] == "sql-query"
    assert parts.data["description"] == "Run a SQL query against a Hive table and inspect results."
    assert parts.body.startswith("# SQL")
    assert parts.body_first_line == 7


def test_no_frontmatter_means_all_body() -> None:
    parts = frontmatter.split("# Just a body\n")
    assert parts.frontmatter is None and parts.body == "# Just a body\n" and parts.data == {}


def test_unterminated_frontmatter_is_body() -> None:
    assert frontmatter.split("---\nname: x\n").frontmatter is None


def test_coarse_regions_are_keys_then_body() -> None:
    regions = frontmatter.regions(ENTITY)
    assert [r.locator for r in regions] == [
        KeyPath(("name",)),
        KeyPath(("description",)),
        KeyPath(("allowed-tools",)),
        Body(),
    ]
    assert (regions[1].start_line, regions[1].end_line) == (3, 5)
    assert regions[3].start_line == 8


def test_fine_regions_split_the_body() -> None:
    locators = [r.locator for r in frontmatter.regions(ENTITY, Granularity.FINE)]
    assert locators[:3] == [KeyPath(("name",)), KeyPath(("description",)), KeyPath(("allowed-tools",))]
    assert MarkdownSection(("SQL", "Steps")) in locators or MarkdownSection(("SQL", "Steps"), 0, 6) in locators


def test_excise_description_leaves_everything_else_byte_identical() -> None:
    result = frontmatter.excise(ENTITY, KeyPath(("description",)), NAME)
    assert result == ENTITY.replace(
        "description: >-\n  Run a SQL query against a Hive table\n  and inspect results.\n", ""
    )


def test_excise_required_key_is_refused() -> None:
    with pytest.raises(ExcisionInvalid):
        frontmatter.excise(ENTITY, KeyPath(("name",)), NAME)


def test_excise_body_keeps_frontmatter() -> None:
    result = frontmatter.excise(ENTITY, Body(), NAME)
    assert result == ENTITY.split("# SQL")[0]
    assert frontmatter.split(result).data["name"] == "sql-query"


def test_excise_whole_with_required_keys_is_refused() -> None:
    with pytest.raises(ExcisionInvalid):
        frontmatter.excise(ENTITY, Whole(), NAME)


def test_required_key_that_was_never_present_does_not_block_excision() -> None:
    # Found on real data: 66 entity files omit `name` and the harness takes it from the directory.
    nameless = "---\ndescription: does things\n---\nbody\n"
    result = frontmatter.excise(nameless, KeyPath(("description",)), NAME)
    assert result == "---\n---\nbody\n"
    assert frontmatter.excise(nameless, Body(), NAME) == "---\ndescription: does things\n---\n"


def test_invalid_frontmatter_yaml_is_a_format_error() -> None:
    with pytest.raises(FormatError):
        frontmatter.regions("---\ndescription: Use this: when x\n  bad: [\n---\nbody\n")


def test_plain_paragraphs_and_excision() -> None:
    text = "first para\nstill first\n\nsecond\n\nthird\n"
    regions = plain.regions(text)
    assert [r.locator for r in regions] == [LineRange(1, 2), LineRange(4, 4), LineRange(6, 6)]
    assert plain.excise(text, LineRange(4, 4)) == "first para\nstill first\n\nthird\n"
    assert [r.locator for r in plain.regions("a\nb\n", Granularity.FINE)] == [LineRange(1, 1), LineRange(2, 2)]


def test_file_text_hashes_and_counts() -> None:
    content = file_text(ContentFormat.MARKDOWN, "# Hi\n")
    assert content.content_hash == content_hash("# Hi\n")
    assert content.content_hash.startswith("sha256:")
    assert content.tokens.value > 0


def test_content_dispatch_and_validation() -> None:
    entity = file_text(ContentFormat.FRONTMATTER_DOC, ENTITY, required_keys=NAME)
    assert [r.locator for r in entity.regions()][-1] == Body()
    assert "description" not in frontmatter.split(entity.excise(KeyPath(("description",)))).data
    with pytest.raises(ExcisionInvalid):
        entity.excise(KeyPath(("name",)))
    with pytest.raises(LocatorNotFound):
        file_text(ContentFormat.JSON, "{}").excise(MarkdownSection(("A",)))
    md = file_text(ContentFormat.MARKDOWN, "## A\na\n## B\nb\n")
    assert md.excise(Whole()) == ""
    assert md.region(MarkdownSection(("B",))).text == "## B\nb\n"
    settings = file_text(ContentFormat.JSON, '{"a": 1}', required_keys=(KeyPath(("a",)),))
    with pytest.raises(ExcisionInvalid):
        settings.excise(KeyPath(("a",)))
    with pytest.raises(ExcisionInvalid):
        settings.validate("{not json")


def test_locator_keys_are_unambiguous() -> None:
    locators = [
        Whole(),
        Body(),
        MarkdownSection(()),
        MarkdownSection(("A", "B")),
        MarkdownSection(("A/B",)),
        MarkdownSection(("Why?",)),
        MarkdownSection(("Why",), occurrence=1),
        MarkdownSection(("Why",), until_level=3),
        MarkdownSection(("Why",), occurrence=1, until_level=3),
        LineRange(1, 2),
        KeyPath(("hooks", "UserPromptSubmit", 0)),
        KeyPath(("hooks/UserPromptSubmit", 0)),
    ]
    keys = [locator_key(loc) for loc in locators]
    assert len(set(keys)) == len(keys), keys
    assert locator_key(MarkdownSection(("Memory", "Routing"))) == "section:/Memory/Routing"
    assert locator_key(KeyPath(("hooks", "UserPromptSubmit", 0))) == "key:/hooks/UserPromptSubmit/0"


def test_describe_is_readable() -> None:
    assert describe(MarkdownSection(("Memory",))) == '§ "Memory"'
    assert describe(MarkdownSection(())) == "preamble"
    assert describe(KeyPath(("hooks", "UserPromptSubmit", 0))) == "hooks.UserPromptSubmit.[0]"
    assert describe(LineRange(3, 3)) == "line 3"


def test_file_text_is_frozen() -> None:
    content = file_text(ContentFormat.PLAIN, "x")
    with pytest.raises(AttributeError):
        content.text = "y"  # type: ignore[misc]
    assert isinstance(content, FileText)


def test_code_is_addressed_whole_and_never_divided() -> None:
    code = file_text(ContentFormat.CODE, "export const meta = { name: 'x' }\n\nfunction run() {}\n")
    assert [r.locator for r in code.regions()] == [Whole()] and [r.locator for r in code.regions(Granularity.FINE)] == [Whole()]
    assert code.excise(Whole()) == ""
    with pytest.raises(LocatorNotFound):
        code.excise(LineRange(1, 1))
