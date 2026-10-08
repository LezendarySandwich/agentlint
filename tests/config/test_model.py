"""Every core class rejects inconsistent states at construction."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from agentlint.config.content import NotObserved, ObservedText, SizeOnly, content_hash, file_text, observed_text
from agentlint.config.formats import ContentFormat
from agentlint.config.locators import LineRange, MarkdownSection, Whole
from agentlint.config.model import (
    Ablation,
    AblationPolicy,
    Activation,
    Attribution,
    Declared,
    Entity,
    NotReconciled,
    Overcounted,
    Provenance,
    Reconciled,
    Rule,
    Source,
    Untraced,
)
from agentlint.config.sessions import SessionDeliveries, SessionHeader
from agentlint.config.taxonomy import Authority, EntityKind, EntityRole, InjectionPoint, Load, Mechanism
from agentlint.config.tokens import TokenCount, TokenMethod, estimated

from .helpers import HARNESS, INSTRUCTIONS, PROJECT, file_source, observable_source, ref

ESTIMATED, OBSERVED, DERIVED = TokenMethod.ESTIMATED, TokenMethod.OBSERVED, TokenMethod.DERIVED


@pytest.mark.parametrize("value", [-1, 1.5, True, "3"])
def test_token_count_rejects_non_counts(value: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        TokenCount(value, ESTIMATED)  # type: ignore[arg-type]


def test_injection_point_validates_identity() -> None:
    with pytest.raises(ValueError):
        InjectionPoint("Bad Harness", "x", "x", Mechanism.CONFIG_FILE)
    with pytest.raises(ValueError):
        InjectionPoint(HARNESS, "Not-Snake", "x", Mechanism.CONFIG_FILE)
    with pytest.raises(ValueError):
        InjectionPoint(HARNESS, "x", "  ", Mechanism.CONFIG_FILE)
    with pytest.raises(TypeError):
        InjectionPoint(HARNESS, "x", "x", "config_file")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        EntityKind(HARNESS, "skill", "skill", EntityRole.SKILL, "Bad Namespace")


def test_text_content_must_be_consistent_with_its_text() -> None:
    with pytest.raises(ValueError, match="ESTIMATED"):
        file_text(ContentFormat.PLAIN, "x").__class__(ContentFormat.PLAIN, "x", content_hash("x"), TokenCount(1, OBSERVED))
    with pytest.raises(ValueError, match="content_hash"):
        file_text(ContentFormat.PLAIN, "x").__class__(ContentFormat.PLAIN, "x", content_hash("y"), estimated("x"))


def test_observed_text_needs_sessions_in_order() -> None:
    with pytest.raises(ValueError, match="NotObserved"):
        observed_text(ContentFormat.PLAIN, "x", (), 3)
    with pytest.raises(ValueError, match="exceed"):
        observed_text(ContentFormat.PLAIN, "x", (ref(1), ref(2)), 1)
    with pytest.raises(ValueError, match="most recent first"):
        observed_text(ContentFormat.PLAIN, "x", (ref(1), ref(2)), 2)
    ok = observed_text(ContentFormat.PLAIN, "x", (ref(2), ref(1)), 5)
    assert isinstance(ok, ObservedText) and not hasattr(ok, "excise")


def test_size_only_and_not_observed() -> None:
    with pytest.raises(ValueError, match="OBSERVED or DERIVED"):
        SizeOnly(TokenCount(10, ESTIMATED), "no text")
    with pytest.raises(ValueError):
        SizeOnly(TokenCount(10, DERIVED), " ")
    assert SizeOnly(TokenCount(10, DERIVED), "harness built-in").tokens.value == 10
    with pytest.raises(ValueError):
        NotObserved(-1, "x")
    with pytest.raises(ValueError):
        NotObserved(0, "")


def test_activation_and_ablation_invariants() -> None:
    with pytest.raises(ValueError):
        Activation(Load.CONDITIONAL)
    assert Activation(Load.CONDITIONAL, "before shell commands").condition
    with pytest.raises(ValueError):
        AblationPolicy(Ablation.NONE)
    with pytest.raises(ValueError):
        AblationPolicy(Ablation.FILE_REMOVAL, blocked_reason="why?")
    assert AblationPolicy.blocked("managed settings always apply").mechanism is Ablation.NONE
    assert Load.PER_MESSAGE.in_first_turn and not Load.NOT_IN_CONTEXT.in_first_turn


def test_source_invariants() -> None:
    good = file_source("a", "# A\n")
    with pytest.raises(ValueError, match="exactly that file"):
        Source(good.id, good.provenance, good.activation, content_hash("other"), good.content, good.ablation)
    with pytest.raises(ValueError, match="sha256"):
        Source(good.id, good.provenance, good.activation, "md5:abc", good.content, good.ablation)
    with pytest.raises(ValueError, match="itself"):
        Source(good.id, good.provenance, good.activation, good.declared_hash, good.content,
               AblationPolicy(Ablation.FILE_REMOVAL, linked_sources=("a",)))
    with pytest.raises(ValueError, match="anchor"):  # a place inside no file
        Provenance(INSTRUCTIONS, None, Authority.USER, anchor="/hooks/0")
    with pytest.raises(ValueError, match="anchor"):
        Provenance(INSTRUCTIONS, Path("/home/u/settings.json"), Authority.USER, anchor=" ")
    with pytest.raises(TypeError, match="Path"):  # a string is not a file: core never parses one
        Provenance(INSTRUCTIONS, "/home/u/CLAUDE.md", Authority.USER)  # type: ignore[arg-type]
    assert good.provenance.harness == HARNESS


def test_rules_come_only_from_text() -> None:
    source = file_source("notes", "## One\na\n## Two\nb\n")
    rules = source.rules()
    assert [r.id for r in rules] == ["notes#section:/One", "notes#section:/Two"]
    assert rules[1].text == "## Two\nb\n" and (rules[1].start_line, rules[1].end_line) == (3, 4)
    assert all(r.tokens.method is ESTIMATED for r in rules)
    assert observable_source("out").rules() == ()


def test_declared_rejects_inconsistent_bundles() -> None:
    a, b = file_source("a", "x"), file_source("b", "y")
    kind = EntityKind(HARNESS, "skill", "skill", EntityRole.SKILL, "listing")
    with pytest.raises(ValueError, match="duplicate"):
        Declared(HARNESS, PROJECT, None, None, (a, a), ())
    with pytest.raises(ValueError, match="unknown sources"):
        Declared(HARNESS, PROJECT, None, None, (a, b), (Entity("e", "e", kind, "missing"),))
    with pytest.raises(ValueError, match="duplicate entity ids"):  # one id for two entities: a namesake lost
        Declared(HARNESS, PROJECT, None, None, (a, b), (Entity("e", "e", kind, "a"), Entity("e", "e", kind, "b")))
    with pytest.raises(ValueError, match="belongs to"):
        Declared("other-harness", PROJECT, None, None, (a,), ())
    observed = Source("o", a.provenance, a.activation, a.declared_hash, observed_text(ContentFormat.PLAIN, "x", (ref(1),), 1),
                      a.ablation)
    with pytest.raises(ValueError, match="from sessions"):
        Declared(HARNESS, PROJECT, None, None, (observed,), ())


def test_reconciliation_states_are_internally_consistent() -> None:
    total = TokenCount(100, OBSERVED)
    sixty = (Attribution("a", 50), Attribution(None, 10, "hook output"))
    reconciled = Reconciled(total, sixty, ref(1))
    assert (reconciled.attributed, reconciled.unattributed) == (60, TokenCount(40, DERIVED))  # derived, never stored
    with pytest.raises(ValueError):
        Reconciled(TokenCount(100, ESTIMATED), sixty, ref(1))
    with pytest.raises(ValueError, match="Overcounted"):
        Reconciled(total, (Attribution("a", 101),), ref(1))
    assert Overcounted(total, (Attribution("a", 130),), ref(1)).excess == 30
    with pytest.raises(ValueError):
        Overcounted(total, (Attribution("a", 100),), ref(1))
    assert NotReconciled((Attribution("a", 5),), "no sessions").attributed == 5
    assert NotReconciled((), "no sessions").attributed == 0
    with pytest.raises(ValueError, match="why"):
        NotReconciled((), " ")


def test_an_estimated_total_is_refused_by_both_outcomes_that_rest_on_one() -> None:
    guess = TokenCount(100, ESTIMATED)
    for outcome, tokens in ((Reconciled, 50), (Overcounted, 150)):
        with pytest.raises(ValueError, match="never an estimate"):
            outcome(guess, (Attribution("a", tokens),), ref(1))
    assert Overcounted(TokenCount(100, DERIVED), (Attribution("a", 150),), ref(1)).excess == 50


def test_a_rule_computes_what_follows_from_its_text_and_refuses_backward_lines() -> None:
    rule = Rule("notes", MarkdownSection(("One",)), "## One\na\n", 1, 2)
    assert rule.id == "notes#section:/One"
    assert (rule.tokens, rule.content_hash) == (estimated("## One\na\n"), content_hash("## One\na\n"))
    for start, end in ((0, 1), (3, 2)):
        with pytest.raises(ValueError, match="forward"):
            Rule("notes", Whole(), "x", start, end)
    with pytest.raises(ValueError, match="source_id"):
        Rule(" ", Whole(), "x", 1, 1)


def test_locators_refuse_inverted_or_negative_values() -> None:
    for start, end in ((0, 2), (-1, 2), (3, 2)):
        with pytest.raises(ValueError, match="forward"):
            LineRange(start, end)
    assert LineRange(2, 2).end == 2
    with pytest.raises(ValueError, match="occurrence"):
        MarkdownSection(("A",), occurrence=-1)
    for level in (0, 7):
        with pytest.raises(ValueError, match="heading level"):
            MarkdownSection(("A",), until_level=level)


def test_untraced_text_says_what_it_arrived_as() -> None:
    text = observed_text(ContentFormat.PLAIN, "x", (ref(1),), 1)
    with pytest.raises(ValueError, match="label"):
        Untraced(INSTRUCTIONS, " ", Load.ALWAYS, text)


def test_an_attribution_names_a_source_or_untraced_text() -> None:
    with pytest.raises(ValueError):
        Attribution(None, 5)  # untraced text needs its label
    with pytest.raises(ValueError):
        Attribution("a", 5, "label")
    with pytest.raises(ValueError):
        Attribution("a", -1)


def test_session_records() -> None:
    with pytest.raises(ValueError):
        SessionHeader("s", "/p", "1", "m", datetime(2026, 1, 1))  # naive datetime
    with pytest.raises(ValueError):
        SessionDeliveries(ref(1), (), TokenCount(5, ESTIMATED))


def test_a_snapshot_holds_each_entity_id_once_after_sessions_merge_in() -> None:
    from dataclasses import replace

    from agentlint.config.assemble import assemble

    a = file_source("a", "x")
    kind = EntityKind(HARNESS, "skill", "skill", EntityRole.SKILL, "listing")
    snap = assemble(Declared(HARNESS, PROJECT, None, None, (a,), (Entity("e", "e", kind, "a"),)), [])
    with pytest.raises(ValueError, match="duplicate entity ids"):
        replace(snap, entities=(*snap.entities, Entity("e", "e", kind, "a")))
    with pytest.raises(ValueError, match="duplicate source ids"):
        replace(snap, sources=(a, a))


def test_a_snapshot_names_only_entities_its_sources_provide() -> None:
    from dataclasses import replace

    from ..contract_data import every_shape

    snapshot = every_shape()
    stray = replace(snapshot.entities[0], id="stray", provided_by="missing")
    with pytest.raises(ValueError, match="unknown sources"):
        replace(snapshot, entities=(*snapshot.entities, stray))
