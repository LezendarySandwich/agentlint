"""assemble(): disk is the truth; recent sessions add only what disk cannot know."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from agentlint.config.assemble import assemble
from agentlint.config.content import NotObserved, ObservedText
from agentlint.config.formats import ContentFormat
from agentlint.config.model import Declared, NotReconciled, Overcounted, Reconciled
from agentlint.config.taxonomy import Authority, Load
from agentlint.config.tokens import TokenMethod
from agentlint.harness import read_config

from .helpers import HARNESS, PROJECT, PROMPT, SETTINGS, delivery, file_source, observable_source, ref, session

OTHER = Path("/work/elsewhere")


def declared(*, version: str | None = "2.0", **overrides: object) -> Declared:
    sources = (
        file_source("user-notes", "## Style\nBe brief.\n"),
        file_source("project-notes", "## Build\nUse make.\n", authority=Authority.PROJECT),
        file_source("settings", '{"a": 1}', fmt=ContentFormat.JSON, load=Load.NOT_IN_CONTEXT, point=SETTINGS),
        observable_source("start-output"),
        observable_source("project-output", authority=Authority.PROJECT, load=Load.PER_MESSAGE),
        observable_source("prompt", point=PROMPT, authority=Authority.MANAGED),
    )
    base = Declared(HARNESS, PROJECT, version, "model-x", sources, ())
    return replace(base, **overrides) if overrides else base


def tokens(snapshot, source_id: str) -> int:
    return snapshot.source(source_id).tokens.value


def test_without_sessions_everything_observable_stays_not_observed() -> None:
    snap = assemble(declared(), [])
    for source_id in ("start-output", "project-output", "prompt"):
        content = snap.source(source_id).content
        assert isinstance(content, NotObserved) and content.sessions_examined == 0
        assert content.reason == "only a session can show this text"
    assert isinstance(snap.always_on, NotReconciled)
    assert snap.always_on.attributed == tokens(snap, "user-notes") + tokens(snap, "project-notes")  # settings excluded
    assert snap.untraced == () and snap.sessions == ()


def test_sources_learn_from_the_sessions_that_apply_to_them() -> None:
    old_here = session(ref(1), delivery("start-output", "old"), delivery("prompt", "SP", point=PROMPT), total=900)
    newer_elsewhere = session(ref(2, project=OTHER), delivery("start-output", "newer"))
    newest_here_old_version = session(
        ref(3, version="1.9"), delivery("start-output", "newest"), delivery("prompt", "SP-1.9", point=PROMPT), total=950
    )
    snap = assemble(declared(), [old_here, newer_elsewhere, newest_here_old_version])

    start = snap.source("start-output").content  # user-level: any session counts
    assert isinstance(start, ObservedText)
    assert start.text == "newest"
    assert [r.session_id for r in start.seen_in] == ["s3", "s2", "s1"] and start.sessions_examined == 3

    prompt = snap.source("prompt").content  # harness built-in: same harness version only
    assert isinstance(prompt, ObservedText) and prompt.text == "SP"
    assert [r.session_id for r in prompt.seen_in] == ["s1"] and prompt.sessions_examined == 2

    project_output = snap.source("project-output").content  # project-scoped: this project only
    assert isinstance(project_output, NotObserved)
    assert project_output.sessions_examined == 2
    assert project_output.reason == "not delivered in any of the 2 sessions examined"

    assert [r.session_id for r in snap.sessions] == ["s3", "s2", "s1"]


def test_total_comes_from_latest_session_here_on_this_version() -> None:
    here = session(ref(1), delivery("start-output", "hello"), total=500)
    wrong_version = session(ref(2, version="1.9"), total=999)
    elsewhere = session(ref(3, project=OTHER), total=111)
    snap = assemble(declared(), [here, wrong_version, elsewhere])
    assert isinstance(snap.always_on, Reconciled)
    assert snap.always_on.session.session_id == "s1"
    assert snap.always_on.observed_total.value == 500
    assert snap.always_on.unattributed.method is TokenMethod.DERIVED
    assert snap.always_on.attributed + snap.always_on.unattributed.value == 500


def test_overcount_is_reported_not_hidden() -> None:
    snap = assemble(declared(), [session(ref(1), total=1)])
    assert isinstance(snap.always_on, Overcounted)
    assert snap.always_on.excess == snap.always_on.attributed - 1


def test_unknown_harness_version_accepts_any_session() -> None:
    snap = assemble(declared(version=None), [session(ref(1, version="9.9"), delivery("prompt", "SP", point=PROMPT), total=800)])
    assert isinstance(snap.source("prompt").content, ObservedText)
    assert isinstance(snap.always_on, (Reconciled, Overcounted))


def test_untraced_deliveries_are_counted_but_never_fingerprinted() -> None:
    first = session(ref(1), delivery(None, "mystery", label="gated output"))
    second = session(ref(2), delivery(None, "mystery v2", label="gated output"), delivery("ghost", "traced to nothing", label="ghost"))
    snap = assemble(declared(), [first, second])
    by_label = {u.label: u for u in snap.untraced}
    assert set(by_label) == {"gated output", "ghost"}  # unknown source ids count as untraced too
    assert by_label["gated output"].content.text == "mystery v2"
    assert [r.session_id for r in by_label["gated output"].content.seen_in] == ["s2", "s1"]
    baseline = assemble(declared(), [])
    assert snap.fingerprint == baseline.fingerprint and snap.config_hash == baseline.config_hash
    assert snap.always_on.attributed > baseline.always_on.attributed


def test_untraced_text_keeps_its_id_across_readings_and_through_json() -> None:
    from agentlint.config.serialize import from_data, snapshot_data

    one = assemble(declared(), [session(ref(1), delivery(None, "first", label="gated output"), delivery(None, "x", label="ghost"))])
    two = assemble(declared(), [session(ref(2), delivery(None, "x", label="ghost"), delivery(None, "changed", label="gated output"))])

    def ids(snap) -> dict[str, str]:
        return {u.label: u.id for u in snap.untraced}

    assert ids(one) == ids(two)  # by where and under what label it arrived: not its text, not its place in the list
    assert len(set(ids(one).values())) == 2 and all(len(i) == 12 for i in ids(one).values())
    data = snapshot_data(one)
    assert [u["id"] for u in data["untraced"]] == [u.id for u in one.untraced]  # written for the UI, computed again on reading
    assert from_data(data).untraced == one.untraced


def test_untraced_ignores_other_projects() -> None:
    snap = assemble(declared(), [session(ref(1, project=OTHER), delivery(None, "theirs", label="x"))])
    assert snap.untraced == ()


def test_several_deliveries_in_one_session_are_joined() -> None:
    snap = assemble(declared(), [session(ref(1), delivery("start-output", "part one"), delivery("start-output", "part two"))])
    assert snap.source("start-output").content.text == "part one\npart two"


def test_fingerprint_tracks_declared_config_only() -> None:
    base = assemble(declared(), [session(ref(1), delivery("start-output", "a"))])
    other_observation = assemble(declared(), [session(ref(1), delivery("start-output", "something else"))])
    assert other_observation.fingerprint == base.fingerprint

    edited = declared()
    edited = replace(edited, sources=(file_source("user-notes", "## Style\nBe thorough.\n"), *edited.sources[1:]))
    assert assemble(edited, []).config_hash != base.config_hash

    upgraded = assemble(declared(version="2.1"), [])
    assert upgraded.config_hash == base.config_hash and upgraded.fingerprint != base.fingerprint


def test_read_config_uses_no_sessions_when_sessions_is_zero() -> None:
    from agentlint.harness import AdapterRead

    class FakeAdapter:
        name = HARNESS
        asked: list[int] = []

        def read(self, cwd: Path, sessions: int, since=None) -> AdapterRead:
            FakeAdapter.asked.append(sessions)
            return AdapterRead(declared(), (session(ref(1), delivery("start-output", "hi")),), evidence="from this read")

    reading = read_config(FakeAdapter(), PROJECT, sessions=0)
    assert isinstance(reading.snapshot.source("start-output").content, NotObserved)  # even from an adapter that over-reads
    assert reading.evidence == "from this read"  # the adapter's own evidence, passed on untouched
    assert isinstance(read_config(FakeAdapter(), PROJECT, sessions=10).snapshot.source("start-output").content, ObservedText)
    assert FakeAdapter.asked == [0, 10]


def test_reconciliation_uses_the_anchor_sessions_own_deliveries() -> None:
    anchor = session(ref(1), delivery("start-output", "short"), total=10_000)
    later = session(ref(2), delivery("start-output", "a much longer text " * 200))  # no total
    snap = assemble(declared(), [anchor, later])
    assert snap.source("start-output").content.text.startswith("a much longer")  # display: latest
    assert isinstance(snap.always_on, Reconciled) and snap.always_on.session.session_id == "s1"
    from agentlint.config.tokens import estimate_tokens
    files = tokens(snap, "user-notes") + tokens(snap, "project-notes")
    assert snap.always_on.attributed == files + estimate_tokens("short")  # reconciled: the anchor's text
    parts = dict((a.source_id, a.tokens) for a in snap.always_on.attribution)
    assert parts["start-output"] == estimate_tokens("short")  # source by source, the anchor's own delivery
    assert snap.always_on.attributed == sum(parts.values())  # the sum is derived from the attribution


def test_untraced_text_is_attributed_by_its_label_in_the_anchor_session() -> None:
    from agentlint.config.tokens import estimate_tokens
    anchor = session(ref(1), delivery(None, "stray text", label="mystery"), delivery(None, "more", label="mystery"),
                     total=10_000)
    snap = assemble(declared(), [anchor])
    untraced = [a for a in snap.always_on.attribution if a.source_id is None]
    assert [(a.label, a.tokens) for a in untraced] == [("mystery", estimate_tokens("stray text") + estimate_tokens("more"))]


def test_observed_entities_merge_without_overriding_declared() -> None:
    from agentlint.config.entity import Entity
    from agentlint.config.taxonomy import EntityKind, EntityRole
    from .helpers import HARNESS
    kind = EntityKind(HARNESS, "tool", "tool", EntityRole.TOOL, "tools")
    base = declared()
    base = replace(base, entities=(Entity("tool:Mine", "Mine", kind, "user-notes", "declared"),))
    seen = session(ref(1), total=None)
    seen = replace(seen, entities=(
        Entity("tool:Mine", "Mine", kind, "prompt", "observed"),  # declared wins
        Entity("tool:Bash", "Bash", kind, "prompt"),
        Entity("tool:Orphan", "Orphan", kind, "no-such-source"),  # must name a declared source
    ))
    other_version = replace(session(ref(2, version="1.0")), entities=(Entity("tool:Old", "Old", kind, "prompt"),))
    snap = assemble(base, [seen, other_version])
    assert {e.id: e.description for e in snap.entities} == {"tool:Mine": "declared", "tool:Bash": None}


def test_observations_prefer_interactive_sessions_and_entities_union() -> None:
    from agentlint.config.entity import Entity
    from agentlint.config.taxonomy import EntityKind, EntityRole
    kind = EntityKind(HARNESS, "tool", "tool", EntityRole.TOOL, "tools")
    interactive = replace(session(ref(1), delivery("prompt", "interactive prompt", point=PROMPT)),
                          entities=(Entity("tool:Ask", "Ask", kind, "prompt"), Entity("tool:Bash", "Bash", kind, "prompt")))
    headless_ref = replace(ref(2), interactive=False)
    headless = replace(session(headless_ref, delivery("prompt", "headless prompt", point=PROMPT)),
                       entities=(Entity("tool:Bash", "Bash", kind, "prompt"),))
    snap = assemble(declared(), [interactive, headless])
    assert snap.source("prompt").content.text == "interactive prompt"  # newer headless run doesn't win
    assert [r.session_id for r in snap.source("prompt").content.seen_in] == ["s2", "s1"]  # still most recent first
    assert {e.id for e in snap.entities} == {"tool:Ask", "tool:Bash"}  # a headless run can't hide a tool


def test_visibility_comes_from_the_latest_interactive_session() -> None:
    from agentlint.config.entity import Visibility
    old = replace(session(ref(1)), visibility=(("skill:x", Visibility.DESCRIBED),))
    headless = replace(session(replace(ref(3), interactive=False)), visibility=(("skill:x", Visibility.NAME_ONLY),))
    newer = replace(session(ref(2)), visibility=(("skill:x", Visibility.NOT_SHOWN),))
    snap = assemble(declared(), [old, headless, newer])
    assert snap.seen_as("skill:x") is Visibility.NOT_SHOWN and snap.seen_as("skill:unknown") is None


def test_read_config_drops_a_session_from_before_the_forget_cutoff_and_checks_say_why() -> None:
    from agentlint.checks import Check, NoSession, run_checks
    from agentlint.harness import AdapterRead

    class OverReading:  # an adapter that ignores the cutoff: core still drops what began before it
        name = HARNESS

        def read(self, cwd: Path, sessions: int, since=None) -> AdapterRead:
            return AdapterRead(declared(), (session(ref(1), delivery("start-output", "hi")),))

    cutoff = ref(1).started_at + timedelta(seconds=1)
    reading = read_config(OverReading(), PROJECT, sessions=10, forgotten_before=cutoff)
    assert reading.snapshot.sessions == () and isinstance(reading.snapshot.source("start-output").content, NotObserved)

    def needs_a_session(r) -> list:
        raise NoSession("no recent sessions to read")

    def needs_one_here(r) -> list:
        raise NoSession("no recent session in this project", in_project=True)

    checks = [Check("a", "s", needs_a_session), Check("b", "s", needs_one_here)]
    said = run_checks(reading, checks).not_evaluated
    assert said == {"a": f"no session since you forgot ({cutoff.astimezone():%Y-%m-%d %H:%M})",
                    "b": f"no session in this project since you forgot ({cutoff.astimezone():%Y-%m-%d %H:%M})"}
    never_forgot = read_config(OverReading(), PROJECT, sessions=10)
    assert run_checks(never_forgot, checks).not_evaluated["a"] == "no recent sessions to read"
    assert read_config(OverReading(), PROJECT, sessions=0, forgotten_before=cutoff).forgotten_before is None  # none read


def test_untraced_text_is_grouped_and_named_by_its_key_not_its_label() -> None:
    """An adapter whose label changes between readings gives a key that doesn't: the text keeps one id, its sessions
    are counted together, and it shows the latest session's label."""
    from agentlint.config.serialize import from_data, snapshot_data

    older = replace(delivery(None, "Today is 2026-10-04", label='"Today is 2026-10-04" (2 candidate hooks)'), key="k")
    newer = replace(delivery(None, "Today is 2026-10-05", label='"Today is 2026-10-05" (3 candidate hooks)'), key="k")
    one = assemble(declared(), [session(ref(1), older)])
    [u] = assemble(declared(), [session(ref(2), newer), session(ref(1), older)]).untraced
    assert u.id == one.untraced[0].id and u.label == newer.label and u.content.text == "Today is 2026-10-05"
    assert [r.session_id for r in u.content.seen_in] == ["s2", "s1"]
    assert from_data(snapshot_data(one)).untraced == one.untraced  # the key is written, so the id is the same on reading
