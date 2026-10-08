"""The JSON form and the way back: ``from_data`` undoes ``snapshot_data`` exactly (config/serialize.py)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from agentlint.config.locators import Body, KeyPath, LineRange, MarkdownSection, Whole
from agentlint.config.model import NotReconciled, Overcounted, Rule
from agentlint.config.serialize import (SNAPSHOT_FORMAT_VERSION, FormatError, as_model, from_data, snapshot_data,
                                        texts_out, to_data)
from agentlint.config.tokens import TokenCount, TokenMethod

from ..contract_data import every_shape
from .helpers import ref


def _outcomes():
    snapshot = every_shape()
    attributed, attribution = snapshot.always_on.attributed, snapshot.always_on.attribution
    return [snapshot,
            replace(snapshot, always_on=Overcounted(TokenCount(attributed - 1, TokenMethod.OBSERVED), attribution, ref(2))),
            replace(snapshot, always_on=NotReconciled(attribution, "no session from this project reported a total"))]


@pytest.mark.parametrize("snapshot", _outcomes(), ids=["reconciled", "overcounted", "not_reconciled"])
def test_a_snapshot_reads_back_as_the_same_model(snapshot) -> None:
    data = json.loads(json.dumps(snapshot_data(snapshot)))  # through JSON text, as stored and served
    back = from_data(data)
    assert back == snapshot
    assert to_data(back) == to_data(snapshot)  # every field, those equality ignores too


def test_texts_moved_out_come_back_in_by_content_hash() -> None:
    snapshot = every_shape()
    texts: dict[str, str] = {}
    data = texts_out(snapshot_data(snapshot), texts)
    rules = snapshot.source("rules").content
    assert texts[rules.content_hash] == rules.text and "Run the tests" not in json.dumps(data)
    assert from_data(data, text_of=texts.get) == snapshot
    with pytest.raises(FormatError, match="isn't available"):
        from_data(data, text_of=lambda ref: None)


def test_every_locator_and_a_finding_read_back() -> None:
    for locator in (Whole(), Body(), MarkdownSection(("Rules", "Style"), 1, 3), LineRange(2, 4), KeyPath(("hooks", 0))):
        rule = Rule("rules", locator, "text", 1, 1)
        assert as_model(Rule, json.loads(json.dumps(to_data(rule)))) == rule
    from ..contract_data import _findings

    for finding in _findings(every_shape()):
        assert as_model(type(finding), json.loads(json.dumps(to_data(finding)))) == finding


def test_what_does_not_read_is_a_format_error_not_a_crash() -> None:
    data = snapshot_data(every_shape())
    with pytest.raises(FormatError, match=f"format 0; this agentlint reads {SNAPSHOT_FORMAT_VERSION}"):
        from_data({**data, "schema_version": 0})
    with pytest.raises(FormatError, match="no 'fingerprint'"):
        from_data({k: v for k, v in data.items() if k != "fingerprint"})
    broken = json.loads(json.dumps(data))
    broken["sources"][0]["content"]["content_hash"] = "sha256:0"  # the model's own invariant still holds on reading
    with pytest.raises(FormatError, match="content_hash does not match"):
        from_data(broken)
    broken = json.loads(json.dumps(data))
    broken["always_on"]["type"] = "file_text"  # a tag that isn't a member of this union
    with pytest.raises(FormatError):
        from_data(broken)
    unknown = json.loads(json.dumps(data)) | {"added_later": 1}
    assert from_data(unknown) == every_shape()  # a key this agentlint doesn't know is left alone


def test_a_conversations_entries_carry_a_type_tag_and_read_back() -> None:
    """Each kind holds only its own fields, and a cut count travels with its text (Clipped)."""
    from datetime import UTC, datetime

    from agentlint.config.conversation import (
        Clipped, Conversation, Injected, Note, NoteKind, Prompt, Reply, Thinking, ToolCall)

    at = datetime(2026, 10, 4, 9, tzinfo=UTC)
    info = "Fix the flaky test"
    items = (Prompt(at, "hi"), Reply(None, "ok"), Thinking(at, "hmm"),
             ToolCall(at, "Bash", "ls", "{}", Clipped("out", 7), True, ("tools/Bash",), "agent:a1"),
             Injected(at, "hook output · Stop", Clipped("x")), Note(at, NoteKind.HOOK_ERROR, Clipped("Stop failed")))
    data = to_data(Conversation(info, items))
    assert [item["type"] for item in data["items"]] == ["prompt", "reply", "thinking", "tool_call", "injected", "note"]
    assert data["items"][3]["result"] == {"text": "out", "omitted": 7} and data["items"][5]["kind"] == "hook_error"
    assert as_model(Conversation, data) == Conversation(info, items)
    with pytest.raises(ValueError):
        Clipped("x", -1)


def test_a_session_header_says_none_for_what_the_transcript_doesnt_say() -> None:
    """One header for a reading's sessions, the session list and usage turns: unknowns are None, never '' or a word
    (a snapshot stored before 2026-10-04 wrote an unknown project as '', read back as None)."""
    from datetime import UTC, datetime
    from pathlib import Path

    from agentlint.config.sessions import SessionHeader

    at = datetime(2026, 10, 4, 9, tzinfo=UTC)
    header = SessionHeader("s1", None, None, None, at, interactive=False)
    assert to_data(header) == {"session_id": "s1", "project": None, "harness_version": None, "model": None,
                               "started_at": at.isoformat(), "interactive": False}
    assert as_model(SessionHeader, to_data(header)) == header
    assert as_model(SessionHeader, to_data(header) | {"project": ""}).project is None
    assert as_model(SessionHeader, to_data(header) | {"project": "/w"}).project == Path("/w")
    for empty in ({"harness_version": ""}, {"model": ""}):
        with pytest.raises(FormatError):
            as_model(SessionHeader, to_data(header) | empty)
    with pytest.raises(ValueError):
        SessionHeader("s1", None, None, None, datetime(2026, 10, 4, 9))  # naive


def test_the_config_reader_doc_names_the_schema_version_show_json_writes() -> None:
    """01-config-reader.md's CLI block says which schema_version `show --json` writes; it is this one."""
    import re
    from pathlib import Path

    doc = (Path(__file__).parents[2] / "docs" / "tech_impl" / "01-config-reader.md").read_text(encoding="utf-8")
    line = next(line for line in doc.splitlines() if line.startswith("agentlint show --json"))
    assert re.findall(r"schema_version (\d+)", line) == [str(SNAPSHOT_FORMAT_VERSION)]
