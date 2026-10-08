"""The trace of one run: its tool calls in order, the hooks that ran, and the facts of the run (06-trace.md).

Everything is kept as the transcript recorded it — a call's whole input, its message and call ids — so a later
choice of projection (Open Question 2) needs no re-run. A trace is JSON-shaped (``to_dict`` / ``from_dict``) because
the runner stores it as one value per run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class TraceMissing(LookupError):
    """No transcript for this run: its trace can't be read. Never an empty trace — an empty action list would be an
    outcome of its own ("did nothing") and read as a behaviour."""


class TraceEmpty(TraceMissing):
    """A transcript exists but says nothing readable about a run: no record could be read, or none says which harness
    version or entry point wrote it (a zero-byte file, one whose only line is torn). Raised, never read as an empty
    trace, for the same reason as ``TraceMissing``; a ``TraceMissing``, so whoever handles a missing trace handles
    this one too."""


@dataclass(frozen=True)
class Action:
    """One tool call the agent made, in the order it made them."""

    seq: int  # its position in the run's actions, from 0
    tool: str  # the tool's name, as the harness names it
    input: dict[str, Any] = field(hash=False)  # as given, whole: JSON-shaped
    message_id: str | None  # the model message that made it: calls sharing one were made together
    call_id: str | None  # the harness's id for the call, which its result names
    uses: tuple[str, ...]  # what it used, as usage references (namespace/name): the tool, a skill, an agent
    answered: bool  # a result for it was recorded
    error: bool  # its result was an error (a denial included)
    denied: bool  # the harness refused to run it: a permission decision, not the tool failing
    subagent_ref: str | None = None  # where the subagent work it started is recorded, when it started any


@dataclass(frozen=True)
class FiredHook:
    """One hook execution the run's transcript recorded with an outcome. Kept beside the actions, not among them: a
    hook runs because the configuration says so, not because the agent chose it.

    ``Trace.fired`` is not every hook that ran: a hook the harness records only by its output (context it added, a
    message it showed) or by an asynchronous response has no outcome record and is absent — for the one adapter so
    far, prompt-submit hooks unless cancelled, and async hooks (06 "fired")."""

    event: str
    name: str  # the harness's name for the firing, e.g. "<Event>:<target>"
    command: str  # as configured, unexpanded
    outcome: str  # "ok" | "error" | "timeout" | "cancelled"
    call_id: str | None = None  # the tool call it ran for


@dataclass(frozen=True)
class ApiError:
    """A model call that failed, which the harness recorded in place of a reply: not the agent acting. Which of these
    are infrastructure and which an outcome is the launcher's call (D7), from all three parts — a harness can write
    one code for causes that need different handling, telling them apart by ``reason`` or only by ``text``."""

    code: str  # the harness's error category, "" when it wrote none readable
    reason: str | None = None  # its finer reason, when it gave one
    transient: bool | None = None  # whether the harness itself marked it temporary
    text: str = ""  # the message it showed in place of the reply, cut to TEXT_LIMIT characters

    TEXT_LIMIT = 500

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "reason": self.reason, "transient": self.transient, "text": self.text}

    @classmethod
    def from_dict(cls, data: Any) -> ApiError:
        if isinstance(data, str):  # a trace stored before reasons were kept: its code alone
            return cls(data)
        transient = data.get("transient")
        return cls(str(data.get("code") or ""), data.get("reason"), transient if isinstance(transient, bool) else None,
                   str(data.get("text") or ""))


@dataclass(frozen=True)
class RunCost:
    """The run's closing cost record, as the harness wrote it."""

    cost_usd: float | None
    duration_ms: int | None
    api_duration_ms: int | None


@dataclass(frozen=True)
class Trace:
    session_id: str
    harness_version: str | None  # the version the transcript records
    models: tuple[str, ...]  # every model that answered, in the order they first did; synthetic messages name none
    headless: bool  # run by a program, not a person
    actions: tuple[Action, ...]
    fired: tuple[FiredHook, ...]
    api_errors: tuple[ApiError, ...] = ()  # each failed model call the harness recorded in its place
    cost: RunCost | None = None  # None when the transcript has no closing cost record (the run didn't finish)

    @property
    def model(self) -> str | None:
        """The model that answered first, when one did."""
        return self.models[0] if self.models else None

    def invoked(self) -> frozenset[str]:
        """Every usage reference any of its actions used."""
        return frozenset(ref for action in self.actions for ref in action.uses)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "harness_version": self.harness_version,
            "models": list(self.models),
            "headless": self.headless,
            "actions": [
                {"seq": a.seq, "tool": a.tool, "input": a.input, "message_id": a.message_id, "call_id": a.call_id,
                 "uses": list(a.uses), "answered": a.answered, "error": a.error, "denied": a.denied,
                 "subagent_ref": a.subagent_ref}
                for a in self.actions
            ],
            "fired": [{"event": h.event, "name": h.name, "command": h.command, "outcome": h.outcome,
                       "call_id": h.call_id} for h in self.fired],
            "api_errors": [e.to_dict() for e in self.api_errors],
            "cost": None if self.cost is None else {"cost_usd": self.cost.cost_usd, "duration_ms": self.cost.duration_ms,
                                                    "api_duration_ms": self.cost.api_duration_ms},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Trace:
        cost = data.get("cost")
        return cls(
            session_id=data["session_id"],
            harness_version=data.get("harness_version"),
            models=tuple(data.get("models") or ()),
            headless=bool(data.get("headless")),
            actions=tuple(
                Action(a["seq"], a["tool"], dict(a.get("input") or {}), a.get("message_id"), a.get("call_id"),
                       tuple(a.get("uses") or ()), bool(a.get("answered")), bool(a.get("error")), bool(a.get("denied")),
                       a.get("subagent_ref"))
                for a in data.get("actions") or ()
            ),
            fired=tuple(FiredHook(h["event"], h["name"], h["command"], h["outcome"], h.get("call_id"))
                        for h in data.get("fired") or ()),
            api_errors=tuple(ApiError.from_dict(e) for e in data.get("api_errors") or ()),
            cost=None if cost is None else RunCost(cost.get("cost_usd"), cost.get("duration_ms"),
                                                   cost.get("api_duration_ms")),
        )
