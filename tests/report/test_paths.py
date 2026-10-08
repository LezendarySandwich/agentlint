from __future__ import annotations

from pathlib import Path

import pytest

from agentlint.report.paths import display


@pytest.mark.parametrize(("text", "shown"), [
    ("/Users/sam", "~"),
    ("/Users/sam/x/CLAUDE.md", "~/x/CLAUDE.md"),
    ("/Users/samuel/x", "/Users/samuel/x"),  # a longer name is someone else's directory
    ("/Volumes/bak/Users/sam/x", "/Volumes/bak/Users/sam/x"),  # the home path inside another path is not the home
    ("/private/Users/sam/proj", "/private/Users/sam/proj"),
    ("/Users/sam.bak/x", "/Users/sam.bak/x"),
    ("  at /Users/sam/skills/a/SKILL.md:94", "  at ~/skills/a/SKILL.md:94"),  # a path inside a line of text
    ("from (/Users/sam/x) and '/Users/sam/y'", "from (~/x) and '~/y'"),
    ("one\n/Users/sam/x", "one\n~/x"),
])
def test_home_is_written_tilde_only_where_a_path_starts_with_it(monkeypatch: pytest.MonkeyPatch, text: str,
                                                                  shown: str) -> None:
    """`~` stands for your home directory and nothing else: the rule the UI's `home` keeps (round 12)."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/Users/sam")))
    assert display(text) == shown


def test_a_root_home_leaves_every_path_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/")))
    assert display("/Users/sam/x") == "/Users/sam/x"
