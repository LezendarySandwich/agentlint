from __future__ import annotations

from pathlib import Path

import pytest

from agentlint import cli
from agentlint.checks import CheckError, NotEvaluated, emphasis
from agentlint.checks.emphasis import MIN_WORDS, findings, load_markers, loudness
from agentlint.config.taxonomy import Load

from ..config.helpers import file_source
from .test_core_checks import build

FILLER = " ".join(["plain words that say nothing loud"] * 20)  # 120 words


def shipped(marker_id: str):
    return next(m for m in load_markers(None) if m.id == marker_id)


def points(marker_id: str, text: str) -> float:
    marker = shipped(marker_id)
    return sum(marker.points(m.group(0)) for m in marker.pattern.finditer(text))


def test_run_length_scaling_matches_the_spec_table() -> None:
    assert points("exclamation", "fix this!") == 1.0
    assert points("exclamation", "fix this!!") == 3.0
    assert points("exclamation", "fix this!!!") == 5.0
    assert points("exclamation", "fix a! fix b! fix c!") == 3.0  # three ordinary emphases, not one escalation
    assert points("exclamation", "<!-- note --> a != b ![img](x.png)") == 0.0
    assert points("caps-run", "DO NOT SKIP THIS step") == 4.0  # counted in words
    assert points("caps-run", "use GROUP BY and the REST API") == 0.0  # acronyms and SQL are not shouting


def test_user_markers_merge_over_the_defaults_by_id(tmp_path: Path) -> None:
    user = tmp_path / "emphasis.toml"
    user.write_text('[[marker]]\nid = "must"\nweight = 5.0\n\n[[marker]]\nid = "never"\nenabled = false\n\n'
                    '[[marker]]\nid = "please"\npattern = \'\\bPLEASE\\b\'\n')
    merged = {m.id: m for m in load_markers(user)}
    assert merged["must"].weight == 5.0 and merged["must"].pattern.pattern == r"\bMUST\b"  # only the weight changed
    assert "never" not in merged and merged["please"].weight == 1.0
    assert list(merged)[-1] == "please"  # additions come after the defaults


@pytest.mark.parametrize("body, complaint", [
    ('id = "x"\npattern = "("', "bad pattern"),
    ('id = "x"\npattern = "a*"', "matches empty text"),
    ('id = "x"\npattern = "a"\nscale = "huge"', "scale"),
    ('id = "x"\npattern = "a"\ncolour = "red"', "unknown field"),
    ('pattern = "a"', "needs an id"),
    ('id = "brand-new"', "needs a pattern"),
])
def test_bad_user_markers_fail_loudly(tmp_path: Path, body: str, complaint: str) -> None:
    user = tmp_path / "emphasis.toml"
    user.write_text(f"[[marker]]\n{body}\n")
    with pytest.raises(CheckError, match=complaint) as raised:
        load_markers(user)
    assert str(user) in str(raised.value) or "needs an id" in complaint


def test_code_is_excluded_and_short_sources_are_not_scored() -> None:
    markers = load_markers(None)
    loud = loudness(file_source("a", f"{FILLER}\nYou MUST do it.\n"), markers)
    quiet = loudness(file_source("b", f"{FILLER}\n```\nMUST NEVER\n```\nand `MUST` in a span\n"), markers)
    assert loud.points == 2.0 and [str(d) for d in loud.drivers] == ["MUST ×1"]
    assert quiet.points == 0.0
    assert loudness(file_source("c", "MUST " * (MIN_WORDS - 1)), markers) is None


def test_every_loud_source_gets_its_own_finding_saying_when_it_is_in_context() -> None:
    markers = load_markers(None)
    quiet = [file_source(f"q{i}", FILLER, load=Load.ON_INVOKE) for i in range(25)]
    always = file_source("hook", f"{FILLER}\n<EXTREMELY_IMPORTANT> YOU MUST USE IT. No exceptions!")
    skill = file_source("skill", f"{FILLER}\nNEVER do this. DO NOT do that.", load=Load.ON_INVOKE)
    result = findings(build([*quiet, skill, always], []), markers)
    assert [f.discriminator for f in result] == ["source:hook", "source:skill"]  # loudest first
    named, invoked = result
    assert "the loudest of your 27 sources, and in context every session" in named.title
    assert "the 2nd loudest of your 27 sources, in context when invoked" in invoked.title
    assert "your median source scores 0." in named.detail and "×" not in named.detail.split("\n")[0]
    assert {f.confidence.value for f in result} == {"suggestive"} and {f.severity.value for f in result} == {"info"}
    with pytest.raises(NotEvaluated, match="comparing needs 20"):  # too few sources to compare against
        findings(build(quiet[:5] + [always], []), markers)


def test_cli_reports_a_broken_marker_file_as_a_tool_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    from .test_findings_and_cli import PROJECT, FakeAdapter

    user = tmp_path / "emphasis.toml"
    user.write_text("[[marker]]\nid = 'x'\npattern = '('\n")
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    monkeypatch.setattr(emphasis, "USER_MARKERS", user)
    assert cli.main(["check", "--cwd", str(PROJECT), "--only", "emphasis-density"]) == cli.EXIT_TOOL_ERROR
    assert "bad pattern" in capsys.readouterr().err


def test_frontmatter_is_not_what_an_invocation_delivers() -> None:
    from agentlint.config.formats import ContentFormat

    doc = f"---\ndescription: DO NOT use for X. DO NOT use for Y.\n---\n{FILLER}\n"
    measured = loudness(file_source("skill", doc, fmt=ContentFormat.FRONTMATTER_DOC, load=Load.ON_INVOKE),
                        load_markers(None))
    assert measured.points == 0.0  # the description's DO NOTs belong to the listing, not the invoked body


@pytest.mark.parametrize("shout, points, drivers", [
    ("The PKG ACL MUST have grants.", 2.0, ["MUST ×1"]),  # two acronyms plus MUST are not a three-word shout
    ("YOU MUST USE IT.", 5.0, ["MUST ×1", '"YOU USE IT" ×1']),  # MUST once, the shout around it once
    ("<EXTREMELY-IMPORTANT> read this", 4.0, ['"<EXTREMELY-IMPORTANT>" ×1']),  # not the tag and IMPORTANT again
    ("The API!!! KEY TOKEN is set.", 5.0, ['"!!!" ×1']),  # blanking the shout doesn't make a run of capitals
])
def test_overlapping_markers_score_the_text_once(shout: str, points: float, drivers: list[str]) -> None:
    measured = loudness(file_source("s", f"{FILLER}\n{shout}\n"), load_markers(None))
    assert measured.points == points and sorted(str(d) for d in measured.drivers) == sorted(drivers)


def test_the_top_decile_is_the_top_decile() -> None:
    sources = [file_source(f"s{i}", f"{FILLER}\n" + "You MUST. " * i, load=Load.ON_INVOKE) for i in range(1, 21)]
    result = findings(build(sources, []), load_markers(None))
    assert [f.discriminator for f in result] == ["source:s20", "source:s19"]  # 2 of 20, not 1


@pytest.mark.parametrize("body, complaint", [
    ('[[markers]]\nid = "new"\npattern = "a"', "unknown table"),
    ('[[marker]]\nid = "must"\nenabled = "no"', "use true or false"),
    ('[[marker]]\nid = "nosuch"\nenabled = false', "isn't one of the shipped markers"),
])
def test_user_files_that_would_be_silently_ignored_fail_instead(tmp_path: Path, body: str, complaint: str) -> None:
    user = tmp_path / "emphasis.toml"
    user.write_text(body + "\n")
    with pytest.raises(CheckError, match=complaint):
        load_markers(user)


def test_a_population_of_equals_has_no_loud_source() -> None:
    sources = [file_source(f"s{i}", f"{FILLER}\nYou MUST.", load=Load.ON_INVOKE) for i in range(20)]
    assert findings(build(sources, []), load_markers(None)) == []  # nobody is louder than the rest


def test_a_user_marker_file_that_is_not_utf8_says_so(tmp_path: Path) -> None:
    user = tmp_path / "emphasis.toml"
    user.write_bytes(b"# caf\xe9\n[[marker]]\nid = \"must\"\nweight = 2\n")
    with pytest.raises(CheckError, match="not UTF-8"):
        load_markers(user)


def test_a_user_file_that_cannot_be_read_is_a_check_error(tmp_path: Path) -> None:
    user = tmp_path / "emphasis.toml"
    user.write_text('[[marker]]\nid = "must"\nweight = 2\n')
    user.chmod(0)
    try:
        with pytest.raises(CheckError, match="can't read it"):
            load_markers(user)
    finally:
        user.chmod(0o600)
