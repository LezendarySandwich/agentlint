from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agentlint import cli
from agentlint.config.assemble import assemble
from agentlint.config.model import Declared
from agentlint.config.serialize import SNAPSHOT_FORMAT_VERSION, snapshot_data
from agentlint.config.taxonomy import Authority, InjectionPoint, Load, Mechanism
from agentlint.report.show import label, render, render_rules

from ..fakes import NoTranscripts, Readers
from ..config.helpers import HARNESS, PROJECT, PROMPT, SETTINGS, delivery, file_source, observable_source, ref, session


def declared() -> Declared:
    sources = (
        file_source("notes", "## Style\nBe brief.\n## Tests\nRun them.\n"),
        file_source("settings", '{"a": 1}', load=Load.NOT_IN_CONTEXT, point=SETTINGS),
        file_source("big-entity", "x " * 5000, load=Load.ON_INVOKE, authority=Authority.PACKAGE),
        observable_source("start-output"),
        observable_source("prompt", point=PROMPT, authority=Authority.MANAGED),
        observable_source("silent"),
    )
    return Declared(HARNESS, PROJECT, "2.0", "model-x", sources, ())


def snapshot(total: int | None):
    s = session(ref(1), delivery("start-output", "hello from a hook"), delivery("prompt", "You are an agent."),
                delivery(None, "an untraced reminder", label='hook output "an untraced reminder" — not traced'),
                total=total)
    return assemble(declared(), [s])


def test_render_reconciled_total_and_ranking() -> None:
    snap = snapshot(total=10_000)
    text = render(snap)
    assert "ALWAYS-ON  10,000 tokens" in text and "not attributed" in text
    assert "an untraced reminder" in text
    assert "1 more are configured to load every session" in text  # "silent" never delivered
    assert "ON INVOKE    1 definitions" in text and "CONFIG" in text
    always = render(snap, always_only=True)
    assert "ON INVOKE" not in always


def test_render_states_noise_and_overcount_honestly() -> None:
    snap = snapshot(total=None)
    assert "no observed total to check it against" in render(snap)
    over = snapshot(total=1)
    assert "exceeds the observed total" in render(over) and "more than estimation noise" in render(over)


def test_the_always_on_session_date_is_local(local_zone) -> None:
    """The observed session's date is the day the UI shows it on: local time, as every other time shown (round 12)."""
    local_zone("Pacific/Kiritimati")  # UTC+14: the session began 10:00Z on 1 Oct, midnight on 2 Oct here
    for total in (10_000, 1):  # reconciled, and overcounted
        assert "(observed in session s1, 2026-10-02)" in render(snapshot(total=total))


def test_render_rules() -> None:
    text = render_rules(snapshot(total=None), "notes")
    assert '§ "Style"' in text and '§ "Tests"' in text and "1-2" in text
    assert "no rules" in render_rules(snapshot(total=None), "silent")


def test_serialized_snapshot_is_tagged_and_versioned() -> None:
    data = snapshot_data(snapshot(total=10_000))
    assert data["schema_version"] == SNAPSHOT_FORMAT_VERSION
    assert data["always_on"]["type"] == "reconciled"
    states = {s["id"]: s["content"]["type"] for s in data["sources"]}
    assert states == {"notes": "file_text", "settings": "file_text", "big-entity": "file_text",
                      "start-output": "observed_text", "prompt": "observed_text", "silent": "not_observed"}
    json.dumps(data)  # fully JSON-serialisable


class FakeAdapter(Readers, NoTranscripts):
    name = HARNESS

    def read_declared(self, cwd: Path) -> Declared:
        return declared()

    def read_sessions(self, cwd: Path, limit: int):
        return [session(ref(1), delivery("start-output", "hi"), total=5_000)]



def test_cli_show(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    assert cli.main(["show", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    assert "ALWAYS-ON  5,000 tokens" in capsys.readouterr().out
    assert cli.main(["show", "--json", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["harness"] == HARNESS
    assert cli.main(["show", "--rules", "missing", "--cwd", str(PROJECT)]) == cli.EXIT_TOOL_ERROR


def test_a_listing_split_per_package_names_each_part() -> None:
    listing = InjectionPoint(HARNESS, "listing", "skill listing", Mechanism.HARNESS_GENERATED)
    whole = observable_source("listing", point=listing)
    part = replace(whole, id="listing:pkg",
                   provenance=replace(whole.provenance, authority=Authority.PACKAGE, package="pkg@market"))
    assert label(whole, {}) == "skill listing"
    assert label(part, {}) == "skill listing · pkg@market"  # each slice says whose it is


def test_a_location_is_shown_as_its_sources_file_or_label() -> None:
    """A finding points into a source by id; the terminal shows that source's file (and the place inside it, written
    after it), or for text no file holds its label — never a label pretending to be a path."""
    from agentlint.report.finding import Location
    from agentlint.report.terminal import where

    home = Path.home()
    notes = replace(file_source("notes", "# Notes\n"), provenance=replace(file_source("notes", "x").provenance,
                                                                         file=home / "notes.md"))
    sources = (notes, observable_source("hook"), observable_source("prompt", point=PROMPT))
    snap = assemble(Declared(HARNESS, PROJECT, "2.0", "m", sources, ()), [])
    assert where(snap, Location("notes", 3)) == "~/notes.md"
    assert where(snap, Location("hook")) == "/home/u/settings.json#/hooks/hook"
    assert where(snap, Location("prompt")) == PROMPT.label
    assert where(snap, Location("gone")) == "gone"  # not in this reading: its id, rather than nothing
