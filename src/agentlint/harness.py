"""The adapter interface: how a harness plugs into the core (docs/tech_impl/09-adapters.md).

An adapter is three parts, which a second harness implements separately and core reads separately:
- ``ConfigReader`` — the configuration on disk with what recent sessions show was delivered, and the checks only
  this harness can make. Required: it is what `check`, `show`, `ui` and `rank` stand on.
- ``TranscriptArchive`` — the transcripts agentlint archives and derives usage from (08). Optional: a harness that
  keeps no transcripts lists none, and the rest is never asked.
- ``ConversationReader`` — the live UI's transcript reader (11): sessions listed and read back, and their
  subagents. Optional: a harness with nothing readable lists nothing and finds nothing.
``Adapter`` is all three, the one type the composition root registers (``cli.ADAPTERS``); core takes the narrowest
part it uses (``read_config`` a ``ConfigReader``). Core owns the merge. A harness with no session history returns
no sessions, and everything only a session could show stays NotObserved. Each reading is self-contained: what the
adapter's checks need comes back from the same read, and nothing is kept on the adapter for the next one.

The methods for behavioural testing are added in v2, as the runner needs them: ``TraceReader`` reads one run's trace
(06) under the home the adapter was given, and ``VariantHomes`` says what a variant home copies of the harness's home
and what removing a source takes out of it (05-runner.md "Variants"). Running comes with the runner.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from agentlint.checks import Check
from agentlint.checks.files import file_facts
from agentlint.config.assemble import assemble
from agentlint.config.conversation import Conversation, SessionListing, TranscriptFile
from agentlint.config.model import ConfigSnapshot, Declared, Source
from agentlint.config.reading import Reading
from agentlint.config.usage import Turn
from agentlint.config.sessions import SessionDeliveries
from agentlint.trace.model import Action, Trace
from agentlint.trace.terms import ActionTerms
from agentlint.runner.variant import CopyPlan, EntryCut, Placement


@dataclass(frozen=True)
class AdapterRead:
    """One pass over a harness's disk and transcripts. Core merges the first two; the third is for the
    adapter's own checks, taken in the same pass — core never looks inside it."""

    declared: Declared
    sessions: tuple[SessionDeliveries, ...] = ()
    evidence: object = None


class ConfigReader(Protocol):
    """The configuration a harness has in effect, and what only it can check. Every adapter is one."""

    name: str  # lowercase-kebab harness id
    label: str  # how the user knows it

    def installed(self) -> bool:
        """Whether this harness is on this machine: something of it on disk to read. Read from disk, never by running it."""
        ...

    def version(self) -> str | None:
        """The installed harness's version, read from disk (never by running it); None when it can't be told."""
        ...

    def read(self, cwd: Path, sessions: int, since: datetime | None = None) -> AdapterRead:
        """Everything configured in ``cwd``, from disk as it is now, with what the ``sessions`` most recent
        sessions show the harness delivered (none when 0) — of those that started at or after ``since`` (when you
        last forgot), in the sessions and in the evidence alike; one whose start can't be told is left out then.
        No AI, no agent runs, and nothing kept for the next read: two reads see the disk as it was at each.
        A harness with no session history returns no sessions: that is a whole reader, not a broken one."""
        ...

    def checks(self) -> list[Check]:
        """Checks that need this harness's knowledge; they emit the same Finding type and judge a Reading,
        finding what they need beyond the snapshot in its ``evidence``. Optional: [] when there are none."""
        ...

    def package_label(self, package_id: str) -> str:
        """How the user knows the package ``package_id`` (a ``Provenance.package``): its short name, from what the
        harness's ids mean; the id itself when the adapter can't tell. Core never takes a package id apart."""
        ...


class TranscriptArchive(Protocol):
    """The transcripts the archive keeps and usage is derived from (08). Optional: a harness without transcripts
    returns [] from ``transcripts()``, and the other methods are then never called."""

    usage_reader_version: str  # changes whenever this adapter reads usage differently: everything archived is read again

    def transcripts(self) -> list[TranscriptFile]:
        """Every transcript the harness keeps, sessions and subagents, for the archive (08)."""
        ...

    def read_transcript(self, handle: str, offset: int = 0, length: int | None = None) -> bytes:
        """A transcript's bytes from ``offset`` — ``length`` of them, or the rest."""
        ...

    def turns_from(self, handle: str, data: bytes) -> list[Turn]:
        """The turns a session transcript holds, with the entities each used — never what was typed."""
        ...


class ConversationReader(Protocol):
    """The transcript reader (11): sessions listed without their contents, read back by id — from the harness's file
    or the archive's copy alike — and the subagents a session started. Optional: a harness with nothing readable
    lists [] and finds None; one without subagents writes no ``subagent_ref``, so ``is_subagent_ref`` is False and
    the other subagent methods are never called."""

    def list_conversations(self) -> list[SessionListing]:
        """Every session transcript the harness keeps, without its contents, for the transcript reader."""
        ...

    def read_conversation(self, session_id: str) -> Conversation | None:
        """One session, readable — found by id among those listed, never by a path from outside."""
        ...

    def conversation_from(self, handle: str, data: bytes, *, parts: bool = True) -> Conversation:
        """A session transcript, readable — from the harness's file or the archive's copy alike — with a ``Briefing``
        wherever the transcript records what the harness gave the model (the start, each compaction, a changed
        prompt), each part traced to the configuration source it is, when the adapter can tell. ``parts=False``: the
        same items, each briefing without its parts and ``texts`` empty — for a listing that reads only how many items
        there are and the first prompt, at a fraction of the cost."""
        ...

    def prepare(self, project: Path) -> None:
        """Start, off the request path, what reading this project's transcripts will need (such as the discovery of
        its configuration that briefings are traced against), and return at once. A no-op for a harness that needs
        nothing."""
        ...

    def listing_from(self, handle: str, data: bytes) -> SessionListing | None:
        """A session transcript's listing entry, without its contents — from the harness's file or the archive's copy
        alike; None when nothing in it says when it ran (then it holds no prompt and no reply)."""
        ...

    def subagent_id(self, handle: str) -> str:
        """The id the transcript reader shows and asks for a subagent transcript by (``/api/sessions/<id>/subagents/
        <subagent id>``), from its handle. Core never takes a handle apart."""
        ...

    def is_subagent_ref(self, ref: str) -> bool:
        """Whether ``ref`` is a subagent reference this adapter writes (an ``Item.subagent_ref``) — the check on one
        a request hands back; core never reads its grammar."""
        ...

    def belongs(self, ref: str, handle: str, read: Callable[[], bytes]) -> bool:
        """Whether a subagent transcript is one an item's ``subagent_ref`` points to; ``read`` gives its bytes, should
        its handle alone not settle it."""
        ...


class TraceReader(Protocol):
    """What one run of the agent did (06): its trace, read from the transcript the harness wrote for it under the
    home the adapter was given — a run's own home, under HOME substitution. For the runner; not every adapter is
    one."""

    def trace(self, session_id: str) -> Trace:
        """The run's trace, found by session id among the transcripts the harness keeps. Raises ``TraceMissing`` when
        there is none, when the one there can't be read (an ``OSError`` opening or partway through it, chained), or
        when it recorded nothing readable (``TraceEmpty``): never an empty trace, nor a partial one."""
        ...

    def action_terms(self, action: Action) -> ActionTerms:
        """What one action of a run did, in core's words (``trace/terms.py``): its kind and what it acted on — the
        programs a shell command runs, the path a file tool read or changed, the skill or agent it used. What the
        with/without comparison compares (07-analysis.md, "Effect of a rule")."""
        ...


@dataclass(frozen=True)
class ToolVerdict:
    """What the adapter says of one tool a test run could be offered: removed from every run (D6) or kept, and why —
    in words a person reads at onboarding and in Settings."""

    name: str  # as the harness names it
    removed: bool
    why: str


class VariantHomes(Protocol):
    """How a run gets its own copy of the harness's home (05). Optional until the runner needs it: a harness without
    it can be read and checked, never run."""

    def home_dir(self) -> Path:
        """The home this adapter reads: what a variant home copies."""
        ...

    def version(self) -> str | None:
        """The installed harness's version, read from disk (``ConfigReader.version``): what a new job pins, so that
        every launch of the job runs it (D5). None when it can't be told: no job can start then."""
        ...

    def copy_plan(self) -> CopyPlan:
        """What of the home a variant copies: the configuration, never history, transcripts or caches."""
        ...

    def at_home(self, home: Path) -> ConfigReader:
        """A reader of the same machine with ``home`` as the home. Reading through it never reads the home this
        adapter reads by default (``home_dir``), except where the copied configuration names a path there that the
        harness, too, would read there (one it maps into the substituted home is read in ``home``), or the directory
        read from lies under it. The runner reports the files of the sources read from there
        (``runner.verify``, escapes), not every file opened on the way, such as a package's manifest."""
        ...

    def removal(self, source: Source) -> tuple[Path, ...]:
        """For a source removed whole (``Ablation.FILE_REMOVAL``): the paths under ``home_dir`` to delete for it to be
        gone, a directory where the harness finds the entity by its directory. () when it can't say."""
        ...

    def placements(self, project: Path, home: Path, workdir: Path) -> tuple[Placement, ...]:
        """What a run in the empty directory ``workdir`` with ``home`` as its home gets copied of ``project`` beside the
        home (D1): the project's configuration — never its code — and whatever the harness keeps for the project under
        the home, placed where the harness will look for ``workdir``'s. Computed, not copied: core builds them."""
        ...

    def entry_cut(self, source: Source) -> EntryCut | None:
        """For a source that is one entry of a JSON file the harness may read from several copies (a package's hook,
        D3): every copy and how to find the entry in one. None for anything else. Raises TargetNotSupported for such
        an entry no copy a variant holds is read for (the variant reads it where it is, outside the copy)."""
        ...

    def acting_tools(self, reading: Reading) -> tuple[str, ...]:
        """The tools the configuration in ``reading`` is known to offer — what any session it read recorded, whatever
        harness version that session ran (a tool's name doesn't depend on it), not only the snapshot's entities — that
        act outside the machine (posting, booking, sending, deleting…), by the harness's own names for them, to be
        removed from every test run (D6): a tool the adapter can't tell apart from one is among them. () when none."""
        ...

    def tool_verdicts(self, reading: Reading) -> tuple[ToolVerdict, ...]:
        """Every tool ``acting_tools`` judged, removed or kept and why, sorted by name: for the onboarding screen and
        the Settings override. ``acting_tools`` is exactly the removed ones."""
        ...

    def nested(self, environ: Mapping[str, str]) -> str | None:
        """Why a test run, or the generator's call, can't be started from a process with this environment — it runs
        inside one of the harness's own sessions, which can't start another — in a sentence a person can act on; None
        when it can. Decided from the environment alone, never by trying."""
        ...

    def telemetry(self) -> str:
        """The onboarding's telemetry line (D18), in a sentence or two: a test run is a real session of this harness, so
        the harness's own telemetry — named, as this machine runs it — may see the situations; agentlint sends
        nothing."""
        ...

    def unplaced(self, project: Path, workdir: Path) -> tuple[str, ...]:
        """What the configuration placed in ``workdir`` would use of ``project`` that wasn't placed — a project hook
        or MCP server running a file of the project's code — one sentence each: a run would behave differently from the real
        configuration there, which a reading of sources can't show. () when nothing."""
        ...


class Adapter(ConfigReader, TranscriptArchive, ConversationReader, Protocol):
    """A harness's adapter: all three parts, the optional ones answering "nothing" where the harness has none."""


def read_config(adapter: ConfigReader, cwd: Path, *, sessions: int, forgotten_before: datetime | None = None) -> Reading:
    """The configuration in effect in ``cwd``: what disk declares, with what the last ``sessions`` sessions showed,
    and everything the checks judge it by, from this one read.

    No default: the number is the caller's `sessions` setting (or a flag over it), never a second default here.
    ``forgotten_before`` is the store's forget cutoff: no session that started before it is used — the adapter
    leaves them out of its read, and a session it returned anyway is dropped here."""
    if sessions < 0:
        raise ValueError("sessions must be non-negative")
    read = adapter.read(cwd, sessions, forgotten_before)
    kept = tuple(s for s in read.sessions if forgotten_before is None or s.header.started_at >= forgotten_before)
    snapshot = assemble(read.declared, kept if sessions else ())
    return Reading(snapshot, file_facts(snapshot), read.evidence, forgotten_before if sessions else None)
