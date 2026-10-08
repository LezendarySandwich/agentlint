"""The report document the UI reads (report/document.py), as `agentlint ui` builds it (cli._report)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentlint import cli
from agentlint.config.model import Declared
from agentlint.settings import Options, Settings

from ..checks.test_findings_and_cli import PROJECT, FakeAdapter, declared
from ..config.helpers import file_source


def test_the_report_carries_every_text_it_refers_to_and_says_what_each_part_is() -> None:
    data, texts = cli._report(FakeAdapter(), PROJECT, Options(Settings()))
    assert data["schema_version"] == 1 and data["snapshot"]["cwd"] == str(PROJECT)
    refs = [s["content"]["text_ref"] for s in data["snapshot"]["sources"] if "text_ref" in s["content"]]
    assert refs and all(ref in texts for ref in refs)  # every text it refers to is served beside it
    assert data["labels"] and data["check"]["findings"]
    assert data["harness_label"] == "Test harness"  # the adapter's label
    assert data["package_labels"] == {}  # no source here shipped in a package
    assert data["check"]["summaries"]["token-budget"] == "what is spending the always-on context"  # groups say what they are


class OddAdapter(FakeAdapter):
    """A configuration holding a lone surrogate — what a JSON escape like "\\ud800" in a settings file becomes."""

    def read_declared(self, cwd: Path) -> Declared:
        base = declared()
        odd = file_source("odd", "Match Bash\ud800 first.\n")
        return Declared(base.harness, base.cwd, base.harness_version, base.model, (*base.sources, odd), ())


def test_a_lone_surrogate_in_the_configuration_never_crashes_a_command(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_adapter", OddAdapter)
    assert cli.main(["check", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    assert cli.main(["check", "--json", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    assert cli.main(["show", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    assert cli.main(["show", "--json", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    data, texts = cli._report(OddAdapter(), PROJECT, Options(Settings()))  # what the UI is served
    assert "Match Bash\ud800 first.\n" in texts.values()
    assert json.dumps(texts).encode("ascii")  # as the server answers it: ASCII JSON, any text survives


def test_a_session_view_takes_the_first_turn_rule_and_the_observed_total_from_one_place() -> None:
    from agentlint.config.assemble import assemble
    from agentlint.config.model import Overcounted
    from agentlint.config.taxonomy import Load
    from agentlint.report.document import context_data, loads_data

    from ..config.helpers import delivery, ref, session
    from ..config.test_assemble import declared as assemble_declared

    gated = delivery(None, "printed before a Bash call", label="gated", load=Load.CONDITIONAL)
    start = delivery(None, "printed at session start", label="start")
    snap = assemble(assemble_declared(), [session(ref(1), gated, start, total=1)])
    assert isinstance(snap.always_on, Overcounted)  # we attributed more than the session observed
    context = context_data(snap, ref(1))
    assert context["observed_total"] == 1  # an overcounted session still observed its total
    by_id = {u.id: u for u in snap.untraced}
    assert [by_id[i].label for i in context["untraced"]] == ["start"]  # conditional text isn't first-turn, and by id
    assert context_data(snap, ref(2))["observed_total"] is None  # another session observed nothing here
    assert loads_data() == {load.value: {"in_first_turn": load.in_first_turn} for load in Load}
    assert {k for k, v in loads_data().items() if v["in_first_turn"]} == {"always", "per_message"}
