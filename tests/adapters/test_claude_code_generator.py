"""The real generator's call, against a fake ``claude`` first on PATH that behaves as the real one was observed to."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentlint.adapters.claude_code.generator import MODEL, ClaudeCodeGenerator, command, model_used
from agentlint.adapters.claude_code.instructions import project_slug
from agentlint.probes.generate import Kind, generate, listed
from agentlint.probes.generator import GenerationFailed, Generator
from agentlint.probes.validate import schema
from agentlint.store import connect, home

from ..probes.test_generate import ONE, THREE, TWO, _reply, _targets
from ..probes.test_contamination import ENTITIES
from .fake_claude_cli import calls, environment

T0 = datetime(2026, 10, 4, 1, 0, tzinfo=UTC)


def test_the_call_is_the_one_e5_ran_in_an_empty_directory_under_agentlint_home(tmp_path: Path, monkeypatch) -> None:
    log = environment(monkeypatch, tmp_path, [{"structured_output": _reply(ONE)}])
    generator: Generator = ClaudeCodeGenerator()
    reply = generator.generate("Write situations.", schema(), MODEL)
    assert reply.data == _reply(ONE) and reply.model_used == "claude-haiku-4-5@20251001"
    assert json.loads(reply.raw)["structured_output"] == _reply(ONE)
    (call,) = calls(log)
    assert call["argv"] == ["-p", "Write situations.", "--bare", "--model", "haiku", "--tools", "",
                            "--no-session-persistence", "--output-format", "json", "--json-schema",
                            json.dumps(schema(), separators=(",", ":"))]
    assert Path(call["cwd"]).resolve() == (home() / "generate").resolve()
    assert list((home() / "generate").iterdir()) == []
    projects = tmp_path / "home" / ".claude" / "projects"
    (made,) = projects.iterdir()  # no transcript: the project folder with an empty memory/, as E5 saw
    assert made.name == project_slug(Path(call["cwd"])) and [p.name for p in made.iterdir()] == ["memory"]
    assert command("p", {}, "m")[:3] == ["claude", "-p", "p"]


def test_generating_for_a_target_through_the_real_call_keeps_its_situations(tmp_path: Path, monkeypatch) -> None:
    environment(monkeypatch, tmp_path, [{"structured_output": _reply(ONE, "Use search_files to grep a new repo for TODOs.",
                                                                     TWO)},
                                        {"structured_output": _reply(THREE, matters="order")}])
    db, target = connect(), _targets()["notes"]
    result = generate(db, target, ENTITIES, ClaudeCodeGenerator(), MODEL, now=T0)
    assert [p.situation for p in listed(db, target)] == [ONE, TWO, THREE]
    assert {p.model for p in result.probes} == {"claude-haiku-4-5@20251001"}
    assert [d.kind for d in result.diagnostics] == [Kind.CONTAMINATED.value]


@pytest.mark.parametrize("reply, complaint", [
    ({"error": "overloaded"}, r"failed \(error_during_execution\): 'overloaded'"),
    ({"stdout": "not json at all", "exit": 0}, "not a JSON result"),
    ({"stdout": "", "stderr": "auth failed", "exit": 2}, "exited 2: 'auth failed'"),
    ({"stdout": json.dumps({"is_error": False, "subtype": "success"}), "exit": 0}, "no structured output"),
    ({"stdout": json.dumps({"subtype": "success", "structured_output": {}}), "exit": 0}, "failed"),
])
def test_a_call_with_no_usable_reply_raises_rather_than_answering_empty(tmp_path: Path, monkeypatch, reply: dict,
                                                                         complaint: str) -> None:
    environment(monkeypatch, tmp_path, [reply])
    with pytest.raises(GenerationFailed, match=complaint):
        ClaudeCodeGenerator().generate("p", schema(), MODEL)


@pytest.mark.parametrize("reply, complaint", [
    ({"stdout_hex": "fffe", "exit": 0}, "not a JSON result"),
    ({"stdout_hex": "", "stderr_hex": "ff6e6f20617574680a", "exit": 2}, "exited 2: '\ufffdno auth'"),
])
def test_output_that_is_not_utf8_raises_generation_failed_not_a_decoding_error(tmp_path: Path, monkeypatch,
                                                                                reply: dict, complaint: str) -> None:
    environment(monkeypatch, tmp_path, [reply])
    with pytest.raises(GenerationFailed, match=complaint):
        ClaudeCodeGenerator().generate("p", schema(), MODEL)


def test_a_first_call_that_prints_bytes_not_utf8_is_a_diagnostic_and_the_top_up_still_runs(tmp_path: Path,
                                                                                            monkeypatch) -> None:
    log = environment(monkeypatch, tmp_path, [{"stdout_hex": "fffe", "exit": 0},
                                              {"structured_output": _reply(ONE, TWO)}])
    db, target = connect(), _targets()["notes"]
    result = generate(db, target, ENTITIES, ClaudeCodeGenerator(), MODEL, now=T0)
    assert len(calls(log)) == 2 and [p.situation for p in listed(db, target)] == [ONE, TWO]
    assert [d.kind for d in result.diagnostics] == [Kind.FAILED.value]


def test_a_launcher_line_before_the_result_is_skipped(tmp_path: Path, monkeypatch) -> None:
    result = {"is_error": False, "subtype": "success", "structured_output": _reply(ONE), "modelUsage": {"m-1": {}}}
    environment(monkeypatch, tmp_path, [{"stdout": "Claude Code, a company build\n" + json.dumps(result) + "\n"}])
    reply = ClaudeCodeGenerator().generate("p", schema(), MODEL)
    assert (reply.data, reply.model_used) == (_reply(ONE), "m-1")


def test_a_timeout_and_a_missing_executable_raise(tmp_path: Path, monkeypatch) -> None:
    environment(monkeypatch, tmp_path, [{"sleep": 5}, {"structured_output": _reply(ONE)}])
    with pytest.raises(GenerationFailed, match="longer than 0.5 s"):
        ClaudeCodeGenerator(timeout_s=0.5).generate("p", schema(), MODEL)
    with pytest.raises(GenerationFailed, match="not on PATH"):
        ClaudeCodeGenerator(executable="no-such-claude-here").generate("p", schema(), MODEL)


def test_the_directory_must_be_under_agentlint_home_and_empty(tmp_path: Path, monkeypatch) -> None:
    log = environment(monkeypatch, tmp_path, [{"structured_output": _reply(ONE)}])
    with pytest.raises(ValueError, match="under agentlint's home"):
        ClaudeCodeGenerator(tmp_path / "elsewhere")
    busy = home() / "busy"
    busy.mkdir()
    (busy / "AGENTS.md").write_text("rules")
    with pytest.raises(GenerationFailed, match="should be empty"):
        ClaudeCodeGenerator(busy).generate("p", schema(), MODEL)
    assert calls(log) == [] and (busy / "AGENTS.md").read_text() == "rules"  # refused, never cleared


def test_the_model_used_names_every_model_that_answered() -> None:
    assert model_used({"modelUsage": {"b": {}, "a": {}}}) == "a, b"
    assert model_used({"modelUsage": {}}) is None and model_used({}) is None and model_used({"modelUsage": []}) is None
