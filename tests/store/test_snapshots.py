from __future__ import annotations

import json
import zlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from agentlint import cli
from agentlint.config.assemble import assemble
from agentlint.config.entity import Entity
from agentlint.config.model import Declared
from agentlint.config.serialize import snapshot_data, to_data
from agentlint.config.taxonomy import Load
from agentlint.report.diff import diff, render, untraced_left_out
from agentlint.store import connect
from agentlint.store.snapshots import UnknownSnapshot, UnreadableSnapshot, load, previous, resolve, save, stored, text
from agentlint.ui.records import diff_data

from ..checks.test_core_checks import SKILL
from ..config.helpers import HARNESS, PROJECT, delivery, file_source, observable_source, ref, session
from .helpers import rows

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def snapshot(rules: str, *, skill: bool = False, hook_says: str = "hello", version: str = "2.0"):
    sources = [file_source("rules", rules), observable_source("hook")]
    entities = []
    if skill:
        sources.append(file_source("skill", "---\ndescription: drafts notes\n---\nbody", load=Load.ON_INVOKE))
        entities.append(Entity("skill:notes", "notes", SKILL, "skill", "drafts notes"))
    declared = Declared(HARNESS, PROJECT, version, "m", tuple(sources), tuple(entities))
    return assemble(declared, [session(ref(1, version=version), delivery("hook", hook_says))])


def test_texts_are_stored_once_and_come_back_intact() -> None:
    db = connect()
    first = snapshot("Run the tests.")
    save(db, first, T0)
    save(db, first, T0 + timedelta(minutes=1))  # the same reading again: replaces, adds no text
    later = snapshot("Run the tests. Keep commits small.")
    save(db, later, T0 + timedelta(hours=1))
    assert rows(db, "SELECT count(*) FROM blobs") == [(3,)]  # two rules texts + one hook output, shared
    assert [s.fingerprint for s in stored(db, str(PROJECT))] == [later.fingerprint, first.fingerprint]
    assert load(db, str(PROJECT), first.fingerprint) == first  # read back as the model, texts and all
    assert to_data(load(db, str(PROJECT), later.fingerprint)) == to_data(later)
    (data,) = db.execute("SELECT data FROM snapshots WHERE fingerprint = ?", (first.fingerprint,)).fetchone()
    assert b"Run the tests." not in zlib.decompress(data)  # the text is kept once, in blobs
    assert text(db, first.source("rules").content.content_hash) == "Run the tests."
    assert previous(db, str(PROJECT), later.fingerprint).fingerprint == first.fingerprint
    assert resolve(db, str(PROJECT), first.fingerprint.removeprefix("sha256:")[:10]).fingerprint == first.fingerprint
    with pytest.raises(UnknownSnapshot):
        resolve(db, str(PROJECT), "ffffffffff")


def test_diff_names_every_kind_of_change() -> None:
    db = connect()
    before = snapshot("Run the tests.", hook_says="hello")
    after = snapshot("Run the tests. NEVER push to main.", skill=True, hook_says="hello, and a lot more text now")
    save(db, before, T0)
    save(db, after, T0 + timedelta(hours=1))
    result = diff(load(db, str(PROJECT), before.fingerprint), load(db, str(PROJECT), after.fingerprint))
    kinds = {c.source_id: c.kind for c in result.changes}
    assert kinds == {"rules": "changed", "skill": "added", "hook": "output"}  # the hook's config didn't change
    assert result.entities_added == (("skill", "notes"),) and result.entities_removed == ()
    assert result.always_after > result.always_before
    shown = render(result, T0, text_of=lambda r: text(db, r))
    assert "CHANGED — configuration that differs" in shown and "+Run the tests. NEVER push to main." in shown
    assert "SAME CONFIGURATION" in shown and "added 1 skill: notes" in shown
    reverse = diff(load(db, str(PROJECT), after.fingerprint), load(db, str(PROJECT), before.fingerprint))
    assert {c.source_id: c.kind for c in reverse.changes}["skill"] == "removed"


def test_an_entity_id_in_a_new_form_is_not_an_entity_added_and_removed() -> None:
    db = connect()
    old = snapshot("Run the tests.", skill=True)
    renamed = replace(old, entities=tuple(replace(e, id="skill:user:notes") for e in old.entities))
    save(db, old, T0)
    result = diff(load(db, str(PROJECT), old.fingerprint), renamed)
    assert result.entities_added == () and result.entities_removed == ()


def test_only_the_harness_changed_and_an_unreadable_format() -> None:
    db = connect()
    old, new = snapshot("same", version="2.0"), snapshot("same", version="2.1")
    save(db, old, T0)
    save(db, new, T0 + timedelta(hours=1))
    before, after = load(db, str(PROJECT), old.fingerprint), load(db, str(PROJECT), new.fingerprint)
    shown = render(diff(before, after), T0)
    assert "harness 2.0 → 2.1" in shown and "No source changed." in shown
    stored_as = json.dumps({**snapshot_data(old), "schema_version": 0}).encode()
    db.execute("UPDATE snapshots SET data = ? WHERE fingerprint = ?", (zlib.compress(stored_as), old.fingerprint))
    with pytest.raises(UnreadableSnapshot, match="format 0"):
        load(db, str(PROJECT), old.fingerprint)


def test_cli_show_diff(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from ..checks.test_findings_and_cli import FakeAdapter, declared

    monkeypatch.setattr(cli, "_adapter", FakeAdapter)
    args = ["show", "--cwd", str(PROJECT), "--diff"]
    assert cli.main(args) == cli.EXIT_OK
    assert "No earlier configuration" in capsys.readouterr().out

    class Changed(FakeAdapter):
        def read_declared(self, cwd):
            base = declared()
            return Declared(base.harness, base.cwd, base.harness_version, base.model,
                            (*base.sources, file_source("extra", "One more rule.")), base.entities)

    monkeypatch.setattr(cli, "_adapter", Changed)
    assert cli.main(args) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "ADDED" in out and "instructions file" in out
    assert cli.main(["show", "--cwd", str(PROJECT), "--snapshots"]) == cli.EXIT_OK
    assert capsys.readouterr().out.count("harness") == 2
    assert cli.main(["show", "--cwd", str(PROJECT), "--diff", "ffffffffff"]) == cli.EXIT_TOOL_ERROR


def test_diff_explains_observation_changes_and_never_invents_numbers() -> None:
    from agentlint.config.taxonomy import InjectionPoint, Mechanism

    listing_point = InjectionPoint(HARNESS, "listing", "skill listing", Mechanism.HARNESS_GENERATED)
    listing = observable_source("listing", point=listing_point)
    tool = Entity("tool:Bash", "Bash", SKILL, "listing", "runs commands")  # known only from a session

    def reading(version: str, *, seen: bool, extra_hook: bool = False):
        sources = [file_source("rules", "Run the tests."), observable_source("hook"), listing]
        if extra_hook:
            sources.append(observable_source("new-hook", declared="command: cat big.md"))
        declared = Declared(HARNESS, PROJECT, version, "m", tuple(sources), ())
        if not seen:
            return assemble(declared, [])
        nudge = delivery(None, "REMINDER: be brief", label="hook output — not traced")
        sess = session(ref(1, version=version), delivery("hook", "hello there"), delivery("listing", "- Bash"), nudge)
        from dataclasses import replace as _replace
        return assemble(declared, [_replace(sess, entities=(tool,))])

    db = connect()
    before, after = reading("2.0", seen=True), reading("2.1", seen=False, extra_hook=True)  # a harness update
    save(db, before, T0)
    save(db, after, T0 + timedelta(hours=1))
    result = diff(load(db, str(PROJECT), before.fingerprint), load(db, str(PROJECT), after.fingerprint))
    assert result.entities_removed == ()  # Bash wasn't uninstalled; no session has run on 2.1 yet
    kinds = {(c.kind, c.source_id): c for c in result.changes}
    assert kinds[("output", "hook")].note == "no longer observed"
    assert kinds[("output", "hook")].delta is None  # its size now is unknown, not zero
    assert not any(c.kind == "untraced" for c in result.changes)  # no session from this project now: can't tell
    added = kinds[("added", "new-hook")]
    assert added.delta is None  # an always-on hook whose output no session has shown: unknown, not +0
    shown = render(result, T0)
    assert "       ?  " in shown and "leaves out always-on sources whose text no session showed" in shown
    assert "is text seen only then or only now" in shown and "leaves out untraced text" in shown
    assert untraced_left_out(result) and not result.untraced_compared  # the page notes it on the same rule
    assert diff_data(result, stored(db, str(PROJECT))[1], stored(db, str(PROJECT))[0])["untraced_left_out"] is True


def test_untraced_text_left_out_is_noted_only_when_a_side_has_some() -> None:
    """With no session from the project on either side, untraced text isn't compared — but there is none to leave out,
    so neither show --diff nor the page's What changed notes it."""
    def reading(rules: str):
        return assemble(Declared(HARNESS, PROJECT, "2.0", "m", (file_source("rules", rules),), ()), [])

    db = connect()
    before, after = reading("Run the tests."), reading("Run the tests first.")
    save(db, before, T0)
    save(db, after, T0 + timedelta(hours=1))
    result = diff(load(db, str(PROJECT), before.fingerprint), load(db, str(PROJECT), after.fingerprint))
    assert not result.untraced_compared and not untraced_left_out(result)
    assert "untraced" not in render(result, T0)
    newer, older = stored(db, str(PROJECT))
    assert diff_data(result, older, newer)["untraced_left_out"] is False


def test_untraced_text_under_several_labels_is_accounted_for() -> None:
    def reading(labels: tuple[str, ...]):
        declared = Declared(HARNESS, PROJECT, "2.0", "m", (file_source("rules", "Run the tests."),), ())
        nudges = [delivery(None, "REMINDER: be brief", label=label) for label in labels]
        return assemble(declared, [session(ref(1), *nudges)])

    db = connect()
    before, after = reading(("PreToolUse hook", "PostToolUse hook")), reading(("PreToolUse hook",))
    save(db, before, T0)
    save(db, replace(after, fingerprint="sha256:" + "a" * 64), T0 + timedelta(hours=1))
    result = diff(load(db, str(PROJECT), before.fingerprint), load(db, str(PROJECT), "sha256:" + "a" * 64))
    [change] = [c for c in result.changes if c.kind == "untraced"]
    assert change.note == "under 1 label, was 2" and change.delta == result.always_after - result.always_before


def test_a_change_with_an_unknown_side_has_no_number() -> None:
    from agentlint.report.diff import Change

    assert Change("changed", "s", "l", Load.ALWAYS, 100, None, None, None).delta is None
    assert Change("output", "s", "l", Load.ALWAYS, None, 50, None, "r", "newly observed").delta is None
    assert Change("added", "s", "l", Load.ALWAYS, None, None, None, None).delta is None
    assert Change("untraced", "s", "l", Load.ALWAYS, None, 50, None, "r", "now delivered").delta == 50  # compared: known


def test_a_text_replaced_under_the_same_fingerprint_goes_by_keep_days() -> None:
    """Hook output is not in the fingerprint: a later reading replaces the row, and the text it dropped is pruned."""
    from agentlint.store import retention

    db = connect()
    first, later = snapshot("Run the tests.", hook_says="cwd /projects/other"), snapshot("Run the tests.", hook_says="bye")
    assert first.fingerprint == later.fingerprint
    save(db, first, T0)
    save(db, later, T0 + timedelta(minutes=1))  # replaces the row: nothing refers to the first hook output now
    retention.prune(db, T0 + timedelta(days=400), keep_days=1)  # deletes no reading: each project keeps its latest
    assert sorted(text(db, h) for (h,) in rows(db, "SELECT hash FROM blobs")) == ["Run the tests.", "bye"]
    assert load(db, str(PROJECT), later.fingerprint) == later


def test_a_reading_from_before_a_forget_is_never_stored_after_it() -> None:
    """You forgot while the configuration was being read: its sessions began before the cutoff, so it isn't stored."""
    from agentlint.store import usage
    from agentlint.store.findings import record

    from .helpers import ran

    db = connect()
    reading = snapshot("Run the tests.", hook_says="Primary working directory: /projects/secret")
    usage.forget(db, reading.sessions[0].started_at + timedelta(seconds=1))
    with pytest.raises(usage.Forgotten):
        save(db, reading, T0 + timedelta(days=1))
    with pytest.raises(usage.Forgotten):
        record(db, project=str(PROJECT), results=ran([]), snapshot=reading, at=T0 + timedelta(days=1), write=True, note=None)
    assert rows(db, "SELECT count(*) FROM blobs") == [(0,)] and stored(db, str(PROJECT)) == []
