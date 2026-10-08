"""The recorder: usage read from transcripts, kept, counted (docs/tech_impl/08-recorder.md)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentlint import cli
from agentlint.adapters.claude_code.transcripts import usage_turns
from agentlint.config.entity import Entity
from agentlint.config.model import Declared
from agentlint.config.conversation import TranscriptFile
from agentlint.config.usage import Turn, build_index, summarise
from agentlint.config.usage import UsageSummary
from agentlint.report.usage import render
from agentlint.store import archive, connect, home, retention
from agentlint.config.sessions import SessionHeader
from agentlint.store import usage as usage_store

from ..checks.test_core_checks import COMMAND, SKILL
from ..checks.test_findings_and_cli import PROJECT, FakeAdapter, declared
from .helpers import rows

NOW = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)


def turn(n: int, *entities: str, at: datetime = NOW - timedelta(days=1), session: str = "s1",
         interactive: bool = True, sidechain: bool = False) -> Turn:
    return Turn(SessionHeader(session, Path("/w"), "2.0", "m", at, interactive), n, at, "m", sidechain, entities)


def test_a_transcript_becomes_turns_of_entities_and_never_text(tmp_path: Path) -> None:
    def line(**record) -> str:
        return json.dumps({"sessionId": "s1", "cwd": "/w", "version": "2.0", "entrypoint": "cli",
                           "timestamp": "2026-10-01T09:00:00Z", **record})

    lines = [
        line(type="user", message={"content": "first prompt, secret words"}),
        line(type="assistant", message={"model": "claude-x", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "tool_use", "name": "Skill", "input": {"skill": "pkg:helper"}}]}),
        line(type="user", isSidechain=True, message={"content": "a subagent's prompt"}),
        line(type="assistant", isSidechain=True, message={"content": [{"type": "tool_use", "name": "Grep", "input": {}}]}),
        line(type="user", message={"content": [{"type": "tool_result", "content": "output"}]}),
        line(type="user", message={"content": "<command-name>/pkg:cmd</command-name><command-message>pkg:cmd</command-message>"}),
        line(type="assistant", message={"model": "<synthetic>", "content": [
            {"type": "tool_use", "name": "Agent", "input": {"subagent_type": "helper-agent"}}]}),
        line(type="user", isMeta=True, message={"content": "<command-name>expanded-skill</command-name>"}),
    ]
    path = tmp_path / "s1.jsonl"
    path.write_text("\n".join(lines) + "\n")
    first, second = usage_turns(path)
    assert set(first.entities) == {"tools/Bash", "tools/Skill", "skills/pkg:helper"}  # no Grep: a subagent's
    assert first.model == "claude-x" and first.header.interactive and not first.sidechain
    assert set(second.entities) == {"skills/pkg:cmd", "tools/Agent", "agents/helper-agent"}  # not the harness's own attachment
    assert second.model is None  # a synthetic message names no model
    assert "secret" not in repr(first)  # a turn never carries what was typed
    for entrypoint in ("sdk-cli", "sdk-ts", "claude-code-github-action"):
        path.write_text("\n".join(lines).replace('"entrypoint": "cli"', f'"entrypoint": "{entrypoint}"') + "\n")
        assert not usage_turns(path)[0].header.interactive  # run by a program: kept, not counted as your usage


def test_only_what_you_typed_starts_a_turn_and_no_other_text_is_kept(tmp_path: Path) -> None:
    def line(**record) -> str:
        return json.dumps({"sessionId": "s1", "cwd": "/w", "version": "2.0", "entrypoint": "cli",
                           "timestamp": "2026-10-01T09:00:00Z", **record})

    bash = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cat notes.md"}}
    lines = [
        line(type="user", origin={"kind": "human"}, message={"content": "look at my notes"}),
        line(type="assistant", message={"content": [bash, {"type": "tool_use", "name": "Agent", "input": {"prompt": "go"}},
                                                    {"type": "tool_use", "name": "Agent", "input": {"subagent_type": "fork"}}]}),
        line(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "t1",  # markup in a tool's output
                                                "content": "<command-name>/release --token=sk-SECRET</command-name>"}]}),
        line(type="user", origin={"kind": "task-notification"}, message={"content": "<task-notification>done</task-notification>"}),
        line(type="user", message={"content": "[Request interrupted by user]"}),
        line(type="user", message={"content": "<bash-input>pwd</bash-input>"}),
        line(type="user", isCompactSummary=True, message={"content": "This session is being continued..."}),
        line(type="system", subtype="local_command", commandRun={"command": "permissions", "args": ""}),
        line(type="user", message={"content": "<command-name>my bank password is hunter2</command-name>"}),  # not a name
    ]
    path = tmp_path / "s1.jsonl"
    path.write_text("\n".join(lines) + "\n")
    only, permissions = usage_turns(path)  # a command run with no line of its own is its own turn, not the prompt's
    assert set(only.entities) == {"tools/Bash", "tools/Agent", "agents/general-purpose"}
    assert permissions.entities == ("skills/permissions",)
    assert "SECRET" not in repr(only) + repr(permissions) and "hunter2" not in repr(only) + repr(permissions)


def test_a_command_older_versions_wrote_only_as_a_system_line_counts_as_its_own_turn(tmp_path: Path) -> None:
    def line(at: str, **record) -> str:
        return json.dumps({"sessionId": "s1", "cwd": "/w", "version": "2.1.266", "entrypoint": "cli",
                           "timestamp": f"2026-10-01T{at}Z", **record})

    markup = "<command-name>/{0}</command-name>\n<command-message>{0}</command-message>\n<command-args></command-args>"
    lines = [
        line("09:00:00", type="user", message={"content": "fix the test"}),
        line("09:00:05", type="assistant", message={"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}),
        line("10:00:00", type="system", subtype="local_command", content=markup.format("effort")),
        line("10:00:00", type="system", subtype="local_command", content="<local-command-stdout>Set</local-command-stdout>"),
        # typed while the agent was mid-reply: the command never reaches the model, the rest of the reply is the prompt's
        line("10:00:09", type="assistant", message={"content": [{"type": "tool_use", "name": "Grep", "input": {}}]}),
        line("11:00:00", type="system", subtype="local_command", content=markup.format("resume")),
        line("11:00:00", type="system", subtype="local_command", content="<local-command-stdout>x</local-command-stdout>",
             commandRun={"command": "resume", "args": ""}),
        line("12:00:00", type="user", message={"content": markup.format("clear")}),
        line("12:00:01", type="system", subtype="local_command", content="<local-command-stdout></local-command-stdout>",
             commandRun={"command": "clear", "args": ""}),
    ]
    path = tmp_path / "s1.jsonl"
    path.write_text("\n".join(lines) + "\n")
    assert [(t.at.hour, t.entities) for t in usage_turns(path)] == [
        (9, ("tools/Bash", "tools/Grep")), (10, ("skills/effort",)), (11, ("skills/resume",)), (12, ("skills/clear",))]


def test_a_prompt_typed_while_the_agent_worked_starts_its_own_turn(tmp_path: Path) -> None:
    def line(**record) -> str:
        return json.dumps({"sessionId": "s1", "cwd": "/w", "version": "2.0", "entrypoint": "cli",
                           "timestamp": "2026-10-01T09:00:00Z", **record})

    lines = [
        line(type="user", message={"content": "fix the test"}),
        line(type="assistant", message={"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}),
        line(type="attachment", attachment={"type": "queued_command", "prompt": "also update the changelog",
                                            "commandMode": "prompt", "origin": {"kind": "human"}, "humanTurn": True}),
        line(type="assistant", message={"content": [{"type": "tool_use", "name": "Skill", "input": {"skill": "changelog"}}]}),
        line(type="attachment", attachment={"type": "queued_command", "prompt": "<task-notification>x</task-notification>",
                                            "commandMode": "task-notification"}),
    ]
    path = tmp_path / "s1.jsonl"
    path.write_text("\n".join(lines) + "\n")
    first, second = usage_turns(path)
    assert set(first.entities) == {"tools/Bash"} and set(second.entities) == {"tools/Skill", "skills/changelog"}
    assert "changelog" not in repr(second).replace("skills/changelog", "")  # never what was typed


class Files:
    """Transcripts on a fake disk: bytes by key, appendable, rewritable, deletable."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.reads: list[tuple[str, int, int | None]] = []

    def listing(self, subagents: dict[str, str] | None = None) -> list[TranscriptFile]:
        subagents = subagents or {}
        return [TranscriptFile(key, len(data), float(len(data)), subagents.get(key, key), key in subagents)
                for key, data in self.data.items()]

    def read(self, key: str, offset: int = 0, length: int | None = None) -> bytes:
        if key not in self.data:
            raise FileNotFoundError(key)
        self.reads.append((key, offset, length))
        chunk = self.data[key][offset:]
        return chunk if length is None else chunk[:length]


def line(n: int, entity: str) -> bytes:
    return f"{n}|{entity}\n".encode()


def turns_from(key: str, data: bytes) -> list[Turn]:
    """A fake harness's reading rules: one turn per line `n|entity`."""
    found = []
    for raw in data.decode().splitlines():
        n, entity = raw.split("|")
        found.append(turn(int(n), entity, session=key))
    return found


def test_the_archive_keeps_every_transcript_and_reads_only_what_grew() -> None:
    db, disk = connect(), Files()
    disk.data = {"s1": line(1, "tools/Bash"), "agent-1": line(1, "tools/Grep")}
    assert archive.update(db, "h", disk.listing({"agent-1": "s1"}), disk.read) == ["s1", "agent-1"]
    assert archive.read(db, "agent-1") == line(1, "tools/Grep")  # subagents are kept too
    assert archive.update(db, "h", disk.listing({"agent-1": "s1"}), disk.read) == []  # unchanged: not read
    disk.reads.clear()
    disk.data["s1"] += line(2, "skills/pkg:a")
    archive.update(db, "h", disk.listing({"agent-1": "s1"}), disk.read)
    assert ("s1", 0, None) not in disk.reads and ("s1", 13, None) in disk.reads  # it only grew: just the new bytes
    assert archive.read(db, "s1") == line(1, "tools/Bash") + line(2, "skills/pkg:a")
    disk.data["s1"] = line(1, "tools/Edit")  # rewritten from the start
    archive.update(db, "h", disk.listing(), disk.read)
    assert archive.read(db, "s1") == line(1, "tools/Edit")
    del disk.data["s1"]  # the harness deleted it
    archive.update(db, "h", disk.listing(), disk.read)
    assert archive.read(db, "s1") == line(1, "tools/Edit") and [a.session_id for a in archive.sessions(db, "h")] == ["s1"]


def test_usage_is_derived_from_the_archive_and_again_when_the_rules_change() -> None:
    db, disk = connect(), Files()
    disk.data = {"s1": line(1, "tools/Bash") + line(2, "skills/pkg:a")}
    archive.update(db, "h", disk.listing(), disk.read)
    assert usage_store.derive(db, "h", "rules/1", turns_from) == 2
    del disk.data["s1"]  # gone from disk, still in the archive
    assert usage_store.derive(db, "h", "rules/1", turns_from) == 0  # nothing changed: nothing read
    better = lambda key, data: [t for t in turns_from(key, data) if t.entities != ("tools/Bash",)]
    assert usage_store.derive(db, "h", "rules/2", better) == 1  # new rules reach the whole kept history
    assert rows(db, "SELECT turn FROM observations") == [(2,)]


def test_a_transcript_that_vanishes_mid_read_is_skipped_and_retried() -> None:
    db, disk = connect(), Files()
    disk.data = {"s1": line(1, "tools/Bash")}
    listing = [*disk.listing(), TranscriptFile("gone", 10, 1.0, "gone")]
    assert archive.update(db, "h", listing, disk.read) == ["s1"]
    assert archive.read(db, "gone") is None  # not marked read: tried again next time


def test_forgetting_keeps_nothing_from_before_and_reads_none_of_it_back() -> None:
    db, disk = connect(), Files()
    disk.data = {"s1": line(1, "tools/Bash"), "idle": line(1, "tools/Read")}
    archive.update(db, "h", disk.listing(), disk.read)
    usage_store.derive(db, "h", "rules/1", turns_from)
    usage_store.forget(db, NOW)
    assert rows(db, "SELECT count(*) FROM observations") == [(0,)] and archive.read(db, "s1") == b""
    assert archive.sessions(db, "h") == [] and archive.find(db, "h", "s1") is None  # nothing kept: none listed
    disk.data["s1"] += line(2, "skills/pkg:a")  # the session carried on after you forgot
    archive.update(db, "h", disk.listing(), disk.read)
    assert archive.read(db, "s1") == line(2, "skills/pkg:a") and archive.read(db, "idle") == b""  # only what came after
    assert [a.session_id for a in archive.sessions(db, "h")] == ["s1"]  # listed again: it holds what came after


FORGET_KEEPS = "where it is, its session, how far it had got, when it last changed and a hash of its last bytes"


def test_forgetting_keeps_each_transcripts_bookkeeping_and_a_carried_on_sessions_later_turns() -> None:
    db, disk = connect(), Files()
    disk.data = {"/p/s1.jsonl": line(1, "tools/Bash")}
    archive.update(db, "h", disk.listing(), disk.read)
    usage_store.derive(db, "h", "rules/1", turns_from)
    usage_store.forget(db, NOW, "h", disk.listing(), disk.read)
    # Not nothing: per transcript, where it is, its session, how far it had got, when, and a hash of its end.
    assert rows(db, "SELECT key, session_id, bytes_read FROM archive_files") == [("/p/s1.jsonl", "/p/s1.jsonl", 13)]
    assert rows(db, "SELECT length(tail_sha256) > 0 FROM archive_files") == [(1,)]
    disk.data["/p/s1.jsonl"] += line(2, "skills/pkg:a")  # the session started before the forget and carried on
    archive.update(db, "h", disk.listing(), disk.read, forgotten_before=NOW)
    after = lambda key, data: [turn(int(n), e, at=NOW + timedelta(minutes=5), session=key)
                               for n, e in (raw.split("|") for raw in data.decode().splitlines())]
    assert usage_store.derive(db, "h", "rules/1", after) == 1  # usage keeps its turns after the cutoff
    assert rows(db, "SELECT session_id, turn FROM observations") == [("/p/s1.jsonl", 2)]


def test_the_docs_say_what_forget_keeps() -> None:
    root = Path(__file__).resolve().parents[2]
    for doc in (root / "README.md", root / "docs/tech_impl/08-recorder.md"):
        text = " ".join(doc.read_text().split())
        assert "keeps nothing from before" not in text and "(configuration or usage) uses an earlier session" not in text
        assert "how far it had got" in text and "turns taken after" in text  # bookkeeping kept; usage cut by turn


def test_keep_days_prunes_everything_but_what_is_current() -> None:
    db, disk = connect(), Files()
    disk.data = {"old": line(1, "tools/Bash")}
    archive.update(db, "h", [TranscriptFile("old", 6, (NOW - timedelta(days=400)).timestamp(), "old")], disk.read)
    usage_store.derive(db, "h", "rules/1", lambda key, data: [turn(1, "tools/Bash", at=NOW - timedelta(days=400))])
    assert retention.prune(db, NOW, keep_days=183) >= 2
    assert archive.read(db, "old") == b"" and rows(db, "SELECT count(*) FROM observations") == [(0,)]


def test_keep_days_leaves_a_transcript_the_harness_still_has_unread() -> None:
    db, disk = connect(), Files()
    disk.data = {"s1": line(1, "tools/Bash"), "s2": line(1, "tools/Read")}

    def listing(at: datetime) -> list[TranscriptFile]:  # s1 was written at `at`; s2 hasn't been since
        return [TranscriptFile(key, len(data), (at if key == "s1" else NOW - timedelta(days=10)).timestamp(), key)
                for key, data in disk.data.items()]

    archive.update(db, "h", listing(NOW - timedelta(days=10)), disk.read)
    usage_store.derive(db, "h", "rules/1", turns_from)
    retention.prune(db, NOW, keep_days=7)
    assert archive.read(db, "s1") == b"" and archive.totals(db) == (0, 0, 0)  # its bytes went
    disk.reads.clear()
    assert archive.update(db, "h", listing(NOW - timedelta(days=10)), disk.read) == [] and disk.reads == []  # not again
    assert usage_store.derive(db, "h", "rules/1", turns_from) == 0
    disk.data["s1"] += line(2, "skills/pkg:a")  # the old session carried on: only what is new is kept
    assert archive.update(db, "h", listing(NOW), disk.read) == ["s1"]
    assert archive.read(db, "s1") == line(2, "skills/pkg:a") and archive.read(db, "s2") == b""
    del disk.data["s2"]  # the harness deleted it: nothing kept, nothing left to remember
    archive.update(db, "h", listing(NOW), disk.read)
    assert rows(db, "SELECT key FROM archive_files ORDER BY key") == [("s1",)]


def test_forgetting_forgets_what_was_written_since_the_last_import_too() -> None:
    db, disk = connect(), Files()
    disk.data = {"s1": line(1, "tools/Bash")}
    archive.update(db, "h", disk.listing(), disk.read)
    disk.data["s1"] += line(2, "skills/before-forget")  # typed after the last import, before the forget
    disk.data["new"] = line(1, "skills/never-imported")  # a session agentlint hadn't read yet
    usage_store.forget(db, NOW, "h", disk.listing(), disk.read)
    assert archive.totals(db) == (0, 0, 0)
    disk.data["s1"] += line(3, "tools/Edit")
    disk.data["new"] += line(2, "tools/Grep")
    archive.update(db, "h", disk.listing(), disk.read, forgotten_before=NOW)
    assert archive.read(db, "s1") == line(3, "tools/Edit") and archive.read(db, "new") == line(2, "tools/Grep")
    disk.data["s1"] = line(9, "tools/Bash")  # rewritten after the forget: old and new can't be told apart
    archive.update(db, "h", disk.listing(), disk.read, forgotten_before=NOW)
    assert archive.read(db, "s1") == b""
    disk.data["s1"] += line(10, "tools/Read")  # what it gains from here is kept
    archive.update(db, "h", disk.listing(), disk.read, forgotten_before=NOW)
    assert archive.read(db, "s1") == line(10, "tools/Read")


def test_two_imports_at_once_keep_new_bytes_once() -> None:
    db, other, disk = connect(), connect(), Files()
    disk.data = {"s1": line(1, "tools/Bash")}
    archive.update(db, "h", disk.listing(), disk.read)
    usage_store.derive(db, "h", "rules/1", turns_from)
    disk.data["s1"] += line(2, "skills/pkg:a")
    raced = []

    def racing(key: str, offset: int = 0, length: int | None = None) -> bytes:
        if not raced:  # another agentlint imports between this one's look at the archive and its write
            raced.append(archive.update(other, "h", disk.listing(), disk.read))
        return disk.read(key, offset, length)

    assert archive.update(db, "h", disk.listing(), racing) == [] and raced == [["s1"]]
    assert archive.read(db, "s1") == disk.data["s1"]
    assert usage_store.derive(db, "h", "rules/1", turns_from) == 2


def test_a_forget_while_deriving_keeps_nothing_from_before_it() -> None:
    db, other, disk = connect(), connect(), Files()
    disk.data = {"s1": line(1, "tools/Bash")}
    archive.update(db, "h", disk.listing(), disk.read)

    def forgotten_meanwhile(key: str, data: bytes) -> list[Turn]:
        usage_store.forget(other, NOW)
        return turns_from(key, data)

    usage_store.derive(db, "h", "rules/1", forgotten_meanwhile)
    assert rows(db, "SELECT count(*) FROM observations") == [(0,)]


def test_an_interrupted_import_is_finished_by_the_next() -> None:
    db, disk = connect(), Files()
    disk.data = {"s1": line(1, "tools/Bash")}
    archive.update(db, "h", disk.listing(), disk.read)
    usage_store.derive(db, "h", "rules/1", turns_from)
    disk.data["s2"] = line(1, "skills/pkg:a")
    archive.update(db, "h", disk.listing(), disk.read)

    def interrupted(key: str, data: bytes) -> list[Turn]:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        usage_store.derive(db, "h", "rules/1", interrupted)
    assert archive.update(db, "h", disk.listing(), disk.read) == []  # nothing changed on disk
    assert usage_store.derive(db, "h", "rules/1", turns_from) == 1
    assert rows(db, "SELECT session_id FROM observations ORDER BY session_id") == [("s1",), ("s2",)]


def test_counting_is_yours_only_and_never_a_stored_rate() -> None:
    turns = [turn(1, "skills/pkg:a"), turn(2, "skills/pkg:a", "tools/Bash", at=NOW - timedelta(hours=1)),
             turn(3, "skills/pkg:b"), turn(4, "skills/pkg:a", interactive=False), turn(5, "skills/pkg:a", sidechain=True)]
    index = build_index(turns)
    a = index.entities["skills/pkg:a"]
    assert (a.invocations, index.turns_observed) == (2, 3)  # the headless and subagent turns don't count
    assert a.last_seen == NOW - timedelta(hours=1)  # its latest turn


def test_summary_matches_usage_to_this_configuration() -> None:
    entities = [Entity("skill:pkg:a", "pkg:a", SKILL, "f"), Entity("command:pkg:a", "pkg:a", COMMAND, "g"),
                Entity("skill:pkg:quiet", "pkg:quiet", SKILL, "f")]
    index = build_index([turn(1, "skills/pkg:a"), turn(2, "skills/clear")])
    summary = summarise(entities, index)
    assert [e.name for e, _ in summary.used] == ["pkg:a"]  # a skill and a command sharing a name: one choice
    assert [e.name for e in summary.unused] == ["pkg:quiet"] and summary.unmatched == {"skills/clear": 1}


def test_what_the_agent_chooses_between_is_decided_by_role_not_by_namespace() -> None:
    from agentlint.config.taxonomy import EntityKind, EntityRole

    playbook = EntityKind("test-harness", "playbook", "playbook", EntityRole.SKILL, "playbooks")
    helper = EntityKind("test-harness", "helper", "helper", EntityRole.AGENT, "helpers")
    tool = EntityKind("test-harness", "tool", "tool", EntityRole.TOOL, "functions")
    entities = [Entity("playbook:p", "p", playbook, "f"), Entity("helper:h", "h", helper, "f"),
                Entity("tool:t", "t", tool, "f")]
    index = build_index([turn(1, "playbooks/p", "functions/t", "functions/u", "playbooks/q", "agents/x")])
    summary = summarise(entities, index)
    assert [e.id for e, _ in summary.used] == ["playbook:p"] and [e.id for e in summary.unused] == ["helper:h"]
    assert summary.unmatched == {"playbooks/q": 1}  # not a tool's namespace, nor one nothing here is chosen from


def test_the_report_ships_the_summary_usage_prints() -> None:
    from agentlint.report.document import usage_data

    entities = [Entity("skill:pkg:a", "pkg:a", SKILL, "f"), Entity("command:pkg:a", "pkg:a", COMMAND, "g"),
                Entity("skill:pkg:quiet", "pkg:quiet", SKILL, "f")]
    data = usage_data(build_index([turn(1, "skills/pkg:a", "tools/Bash"), turn(2, "skills/clear")]), 30, entities)
    assert data["summary"] == {"used": [{"entity": "skill:pkg:a", "reference": "skills/pkg:a"}],
                               "unused": [{"entity": "skill:pkg:quiet", "reference": "skills/pkg:quiet"}],
                               "unmatched": {"skills/clear": 1}}
    assert usage_data(None, 30, entities) is None


class UsageAdapter(FakeAdapter):
    usage_reader_version = "test/2"

    def read_declared(self, cwd: Path) -> Declared:
        base = declared()
        helper = Entity("skill:pkg:helper", "pkg:helper", SKILL, "notes", "helps")
        quiet = Entity("skill:pkg:quiet", "pkg:quiet", SKILL, "notes", "never used")
        return Declared(base.harness, base.cwd, base.harness_version, base.model, base.sources, (helper, quiet))

    def transcripts(self):
        return [TranscriptFile("t1", 8, 1.0, "t1")]

    def read_transcript(self, key: str, offset: int = 0, length: int | None = None) -> bytes:
        return b"two turns"[offset:] if length is None else b"two turns"[offset:offset + length]

    def turns_from(self, key: str, data: bytes):
        now = datetime.now(UTC)
        return [turn(1, "skills/pkg:helper", at=now - timedelta(hours=2), session="t1"),
                turn(2, "skills/pkg:helper", "skills/model", at=now - timedelta(hours=1), session="t1")]


def test_cli_usage(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_adapter", UsageAdapter)
    assert cli.main(["usage", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "2 of your turns" in out and "USED  1 of 2 skills and agents" in out
    assert "2  skill pkg:helper" in out and "model ×1" in out  # built-ins kept apart
    assert cli.main(["usage", "--unused", "--cwd", str(PROJECT)]) == cli.EXIT_OK
    assert "pkg:quiet" in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert cli.main(["forget"]) == cli.EXIT_OK  # it deletes the archive too, so it asks
    assert "Nothing deleted" in capsys.readouterr().out
    with pytest.raises(SystemExit) as stopped:  # `usage --forget` went 2026-10-04: forget is the one way
        cli.main(["usage", "--forget"])
    assert stopped.value.code == cli.EXIT_TOOL_ERROR and "unrecognized arguments: --forget" in capsys.readouterr().err
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert cli.main(["forget"]) == cli.EXIT_OK
    said = capsys.readouterr().out
    assert "Forgot 2 turns" in said
    # It says what is true: of what came before, agentlint keeps only each transcript's bookkeeping (never "nothing");
    # no configuration reading uses an earlier session, but usage counts a carried-on session's later turns; the UI's
    # transcript reader still shows the sessions the harness itself keeps (storing nothing) — never "never read".
    assert "keeps nothing" not in said and FORGET_KEEPS in said
    assert "No reading of your configuration uses a session that started before now" in said
    assert "usage counts only turns taken after now, including those of a session that carries on" in said
    assert "transcript reader still shows the sessions your harness keeps" in said and "never read" not in said


def test_the_store_is_readable_by_you_only() -> None:
    connect().close()
    assert (home().stat().st_mode & 0o777, (home() / "agentlint.db").stat().st_mode & 0o777) == (0o700, 0o600)


def test_forgetting_leaves_nothing_readable_in_the_file() -> None:
    import zlib

    db, disk = connect(), Files()
    disk.data = {"zz-private": line(1, "skills/zz-private-skill") * 50}
    archive.update(db, "h", disk.listing(), disk.read)
    usage_store.derive(db, "h", "rules/1", turns_from)
    kept = zlib.compress(disk.data["zz-private"], archive.LEVEL)
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    usage_store.forget(db, NOW)
    db.close()
    raw = b"".join(path.read_bytes() for path in home().glob("agentlint.db*"))
    assert b"zz-private-skill" not in raw and kept not in raw  # the turns and the archived bytes, overwritten


def test_the_forget_cutoff_is_exact() -> None:
    db, disk = connect(), Files()
    usage_store.forget(db, NOW + timedelta(milliseconds=900))
    disk.data = {"s1": b"x"}
    archive.update(db, "h", disk.listing(), disk.read)
    usage_store.derive(db, "h", "rules/1", lambda key, data: [
        turn(1, "tools/Bash", at=NOW + timedelta(milliseconds=300)), turn(2, "tools/Read", at=NOW + timedelta(seconds=2))])
    assert rows(db, "SELECT turn FROM observations") == [(2,)]


def test_never_used_is_claimed_only_as_far_back_as_history_goes() -> None:
    quiet = Entity("skill:pkg:quiet", "pkg:quiet", SKILL, "f")
    index = build_index([turn(1, "tools/Bash")], window_start=NOW - timedelta(days=180), history_start=NOW - timedelta(days=10))
    text = render(UsageSummary([], [quiet], {}), index, days=180, unused=True, package_of={})
    assert f"NEVER USED since {(NOW - timedelta(days=10)).astimezone():%Y-%m-%d}" in text




def test_a_malformed_record_in_a_transcript_or_its_archived_copy_is_skipped_and_the_rest_kept(tmp_path: Path) -> None:
    from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
    from agentlint.adapters.claude_code.discovery import Paths

    def line(at: str, **record) -> bytes:
        return (json.dumps({"sessionId": "s1", "cwd": "/w", "version": "2.0", "entrypoint": "cli",
                            "timestamp": f"2026-10-01T{at}Z", **record}) + "\n").encode()

    adapter, db, disk = ClaudeCodeAdapter(Paths(tmp_path)), connect(), Files()
    disk.data = {"/p/-w/s1.jsonl": line("09:00:00", type="user", message={"content": "hi"})
                 + line("09:00:01", type="user", message={"role": "user", "content": [{"type": "text", "text": None}]})
                 + line("10:00:00", type="user", message={"content": "again"})}
    archive.update(db, "claude-code", disk.listing(), disk.read)
    assert usage_store.derive(db, "claude-code", "rules/1", adapter.turns_from) == 2
    del disk.data["/p/-w/s1.jsonl"]  # the harness deleted it: the archived copy is read from now on
    archive.update(db, "claude-code", disk.listing(), disk.read)
    assert usage_store.derive(db, "claude-code", "rules/2", adapter.turns_from) == 2
