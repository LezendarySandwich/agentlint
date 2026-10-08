"""Builders for core-model test data. Uses a fictional harness so tests never depend on a real one."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentlint.config.content import Content, NotObserved, content_hash, file_text
from agentlint.config.formats import ContentFormat
from agentlint.config.model import Ablation, AblationPolicy, Activation, Provenance, Source
from agentlint.config.sessions import Delivery, SessionDeliveries, SessionHeader
from agentlint.config.taxonomy import Authority, InjectionPoint, Load, Mechanism
from agentlint.config.tokens import TokenCount, TokenMethod

HARNESS = "test-harness"
PROJECT = Path("/work/project")
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

INSTRUCTIONS = InjectionPoint(HARNESS, "instructions", "instructions file", Mechanism.INSTRUCTION_FILE)
SETTINGS = InjectionPoint(HARNESS, "settings", "settings file", Mechanism.CONFIG_FILE)
INJECTED = InjectionPoint(HARNESS, "session_start_output", "session-start output", Mechanism.HOOK_OUTPUT)
PROMPT = InjectionPoint(HARNESS, "system_prompt", "system prompt", Mechanism.HARNESS_BUILTIN)


def ref(hours: int, project: Path = PROJECT, version: str = "2.0") -> SessionHeader:
    return SessionHeader(f"s{hours}", project, version, "model-x", T0 + timedelta(hours=hours))


def file_source(
    source_id: str,
    text: str,
    *,
    fmt: ContentFormat = ContentFormat.MARKDOWN,
    load: Load = Load.ALWAYS,
    authority: Authority = Authority.USER,
    point: InjectionPoint = INSTRUCTIONS,
) -> Source:
    content = file_text(fmt, text)
    return Source(
        id=source_id,
        provenance=Provenance(point, Path(f"/home/u/{source_id}"), authority),
        activation=Activation(load),
        declared_hash=content.content_hash,
        content=content,
        ablation=AblationPolicy(Ablation.SPAN_EXCISION),
    )


def observable_source(
    source_id: str,
    *,
    point: InjectionPoint = INJECTED,
    load: Load = Load.ALWAYS,
    authority: Authority = Authority.USER,
    declared: str = "command: echo hi",
) -> Source:
    """A source whose text only a session can show, declared with its configuration."""
    content: Content = NotObserved(0, "only a session can show this text")
    in_no_file = point.mechanism in (Mechanism.HARNESS_BUILTIN, Mechanism.HARNESS_GENERATED)
    return Source(
        id=source_id,
        provenance=(Provenance(point, None, authority) if in_no_file else  # configured as an entry in a settings file
                    Provenance(point, Path("/home/u/settings.json"), authority, anchor=f"/hooks/{source_id}")),
        activation=Activation(load),
        declared_hash=content_hash(declared),
        content=content,
        ablation=AblationPolicy.blocked("test")
        if point.mechanism is Mechanism.HARNESS_BUILTIN
        else AblationPolicy(Ablation.SETTINGS_DISABLE),
    )


def delivery(source_id: str | None, text: str, *, point: InjectionPoint = INJECTED, label: str = "output",
             load: Load = Load.ALWAYS) -> Delivery:
    return Delivery(point, source_id, ContentFormat.PLAIN, text, load, label)


def session(at: SessionHeader, *deliveries: Delivery, total: int | None = None) -> SessionDeliveries:
    observed = TokenCount(total, TokenMethod.OBSERVED) if total is not None else None
    return SessionDeliveries(at, tuple(deliveries), observed)
