from __future__ import annotations

import pytest

from agentlint.config import markdown
from agentlint.config.locators import LineRange, MarkdownSection, Whole
from agentlint.config.regions import Granularity, LocatorNotFound, split_lines

DOC = """\
# Project
Intro line.

## Memory
Remember things.
### Routing
Route them.

## Environment
Env stuff.
"""


def paths(text: str) -> list[tuple[str, ...]]:
    return [h.path for h in markdown.parse_headings(split_lines(text))]


def test_heading_paths_follow_nesting() -> None:
    assert paths(DOC) == [
        ("Project",),
        ("Project", "Memory"),
        ("Project", "Memory", "Routing"),
        ("Project", "Environment"),
    ]


@pytest.mark.parametrize(
    "line, expected",
    [
        ("# Title", ["Title"]),
        ("### Deep ###", ["Deep"]),
        ("   ## Indented three", ["Indented three"]),
        ("#   Spaced    out  ", ["Spaced out"]),
        ("#hashtag", []),
        ("    # four-space indent is code", []),
        ("\\# escaped", []),
        ("####### seven", []),
    ],
)
def test_atx_heading_recognition(line: str, expected: list[str]) -> None:
    assert [h.text for h in markdown.parse_headings(split_lines(line + "\n"))] == expected


def test_headings_inside_fences_are_ignored() -> None:
    text = "# Real\n```bash\n# not a heading\n```\n~~~~\n# also not\n~~~\n# still inside\n~~~~\n## After\n"
    assert paths(text) == [("Real",), ("Real", "After")]


def test_backtick_info_string_with_backtick_is_not_a_fence() -> None:
    text = "``` a`b\n# Heading\n"
    assert paths(text) == [("Heading",)]


def test_leading_frontmatter_is_skipped() -> None:
    text = "---\nname: x\n# comment in yaml\n---\n# Body Title\n"
    assert paths(text) == [("Body Title",)]


def test_duplicate_heading_paths_get_occurrences() -> None:
    text = "## Notes\na\n## Notes\nb\n"
    headings = markdown.parse_headings(split_lines(text))
    assert [(h.path, h.occurrence) for h in headings] == [(("Notes",), 0), (("Notes",), 1)]
    second = markdown.region(text, MarkdownSection(("Notes",), occurrence=1))
    assert second.text == "## Notes\nb\n"


def test_coarse_regions_cut_at_shallowest_repeated_level() -> None:
    regions = markdown.regions(DOC)
    assert [r.locator for r in regions] == [
        MarkdownSection(("Project",), 0, 2),  # the title's own text, up to the first h2
        MarkdownSection(("Project", "Memory")),
        MarkdownSection(("Project", "Environment")),
    ]
    assert regions[1].text == "## Memory\nRemember things.\n### Routing\nRoute them.\n\n"
    assert (regions[1].start_line, regions[1].end_line) == (4, 8)
    assert "".join(r.text for r in regions) == DOC


def test_fine_regions_cut_at_every_heading() -> None:
    regions = markdown.regions(DOC, Granularity.FINE)
    # until_level is pinned (not inferred from neighbours) so locators stay stable across edits.
    assert [r.locator for r in regions] == [
        MarkdownSection(("Project",), 0, 6),
        MarkdownSection(("Project", "Memory"), 0, 6),
        MarkdownSection(("Project", "Memory", "Routing"), 0, 6),
        MarkdownSection(("Project", "Environment"), 0, 6),
    ]
    assert "".join(r.text for r in regions) == DOC


def test_preamble_before_first_heading() -> None:
    text = "Some intro.\n\n## A\na\n## B\nb\n"
    regions = markdown.regions(text)
    assert regions[0].locator == MarkdownSection(())
    assert regions[0].text == "Some intro.\n\n"
    assert markdown.excise(text, MarkdownSection(())) == "## A\na\n## B\nb\n"


def test_blank_preamble_is_not_a_region() -> None:
    assert markdown.regions("\n\n## A\na\n## B\nb\n")[0].locator == MarkdownSection(("A",))


def test_document_without_headings_is_one_whole_region() -> None:
    assert [r.locator for r in markdown.regions("just text\nmore\n")] == [Whole()]
    assert markdown.regions("") == []
    assert markdown.regions("\n  \n") == []


def test_every_region_resolves_back_to_the_same_text() -> None:
    for granularity in Granularity:
        for region in markdown.regions(DOC, granularity):
            if isinstance(region.locator, MarkdownSection):
                assert markdown.region(DOC, region.locator) == region


def test_excise_section_removes_it_and_its_subsections_only() -> None:
    result = markdown.excise(DOC, MarkdownSection(("Project", "Memory")))
    assert result == "# Project\nIntro line.\n\n## Environment\nEnv stuff.\n"


def test_excise_nested_section_with_default_semantics() -> None:
    result = markdown.excise(DOC, MarkdownSection(("Project", "Memory", "Routing")))
    assert "Route them." not in result
    assert "Remember things." in result and "## Environment" in result


def test_excise_line_range() -> None:
    assert markdown.excise("a\nb\nc\n", LineRange(2, 2)) == "a\nc\n"


def test_missing_locators_raise() -> None:
    with pytest.raises(LocatorNotFound):
        markdown.resolve(DOC, MarkdownSection(("Nope",)))
    with pytest.raises(LocatorNotFound):
        markdown.resolve(DOC, MarkdownSection(("Project", "Memory"), occurrence=1))
    with pytest.raises(LocatorNotFound):
        markdown.excise(DOC, LineRange(5, 99))


def test_crlf_line_endings_are_preserved() -> None:
    text = "## A\r\na\r\n## B\r\nb\r\n"
    regions = markdown.regions(text)
    assert [r.text for r in regions] == ["## A\r\na\r\n", "## B\r\nb\r\n"]
    assert markdown.excise(text, MarkdownSection(("A",))) == "## B\r\nb\r\n"


def test_single_title_with_one_subsection() -> None:
    text = "# Title\nintro\n## Only\nstuff\n"
    regions = markdown.regions(text)
    assert [r.locator for r in regions] == [MarkdownSection(("Title",), 0, 2), MarkdownSection(("Title", "Only"))]
    assert "".join(r.text for r in regions) == text
