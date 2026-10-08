"""Reading sections for their directives (12-directives.md, "Reading"): the sections of the sources you select, each
read once — a section whose fingerprint (its text's content hash) is stored under this prompt version is never read
again — checked strictly and stored, possibly with no directive at all. A call that failed, or a directive the check
threw out, is a diagnostic, never a finding (D16).
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from agentlint.config.model import ConfigSnapshot, Rule, Source
from agentlint.config.taxonomy import Load
from agentlint.directives.pair import Occurrence
from agentlint.directives.prompt import READER_VERSION, prompt
from agentlint.directives.validate import check, schema
from agentlint.probes.generator import GenerationFailed, Generator
from agentlint.report.show import label
from agentlint.store import diagnostics as diagnostic_store
from agentlint.store import directives as directive_store
from agentlint.store import reads
from agentlint.store.diagnostics import Diagnostic

FAILED, REJECTED = "directives_failed", "directives_rejected"  # diagnostic kinds
EVERY_SESSION = (Load.ALWAYS, Load.PER_MESSAGE)
_LOADS = {Load.ALWAYS: "in every session, from the first message", Load.PER_MESSAGE: "with every message",
          Load.ON_INVOKE: "only when what it defines is invoked", Load.CONDITIONAL: "only when a condition holds"}


@dataclass(frozen=True)
class Section:
    source: Source
    rule: Rule

    @property
    def fingerprint(self) -> str:
        return self.rule.content_hash


def readable(snapshot: ConfigSnapshot) -> list[Source]:
    """The sources that can be read for directives: text that reaches the agent, with sections to read."""
    return [s for s in snapshot.sources if s.activation.load is not Load.NOT_IN_CONTEXT and s.rules()]


def sections(sources: Iterable[Source]) -> list[Section]:
    return [Section(source, rule) for source in sources for rule in source.rules()]


@dataclass(frozen=True)
class SourceStatus:
    """How much of a source has been read: sections whose fingerprint is stored under the reader's version, of all its
    sections. A source read before whose text changed since is partly or not read: only its changed sections need
    reading. ``stale``: sections read only under an older version of the reader — to read again."""

    source: Source
    name: str
    sections: int
    read: int
    tokens: int  # of the sections not read: what reading it would cost
    stale: int = 0


def statuses(db: sqlite3.Connection, snapshot: ConfigSnapshot) -> list[SourceStatus]:
    sources = readable(snapshot)
    every = sections(sources)
    known = directive_store.load(db, (s.fingerprint for s in every), READER_VERSION)
    versions = directive_store.versions(db, (s.fingerprint for s in every))
    defined = snapshot.entities_by_source
    found = []
    for source in sources:
        mine = sections([source])
        unread = [s for s in mine if s.fingerprint not in known]
        found.append(SourceStatus(source, label(source, defined), len(mine), len(mine) - len(unread),
                                  sum(s.rule.tokens.value for s in unread),
                                  sum(versions.get(s.fingerprint, READER_VERSION) < READER_VERSION for s in unread)))
    return found


def occurrences(db: sqlite3.Connection, snapshot: ConfigSnapshot) -> tuple[list[Occurrence], int, int]:
    """Every stored directive of the snapshot's sections, where it is; with how many sections were read, of all."""
    every = sections(readable(snapshot))
    known = directive_store.load(db, (s.fingerprint for s in every), READER_VERSION)
    found = [Occurrence(d, section.source.id, section.rule.id, line_of(d.quote, section.rule),
                        section.source.activation.load in EVERY_SESSION)
             for section in every if section.fingerprint in known for d in known[section.fingerprint].directives]
    return found, sum(s.fingerprint in known for s in every), len(every)


def line_of(quote: str, rule: Rule) -> int:
    """The line of the source a quote starts on: its section's first line, plus the lines before it in the section
    (a quote matches whatever whitespace the text has between its words)."""
    found = re.search(r"\s+".join(re.escape(word) for word in quote.split()), rule.text)
    return rule.start_line + (rule.text[:found.start()].count("\n") if found else 0)


def plan_read(db: sqlite3.Connection, snapshot: ConfigSnapshot, source_ids: Sequence[str], *, read_id: str,
              harness: str, model: str, now: datetime) -> int:
    """Plan a reading of the selected sources (``store/reads.py``): each of their sections whose fingerprint isn't read
    yet, once — sections that share a text are one. Returns how many sections it holds (0: everything is read)."""
    wanted = set(source_ids)
    chosen = [s for s in readable(snapshot) if s.id in wanted]
    known = directive_store.load(db, (s.fingerprint for s in sections(chosen)), READER_VERSION)
    defined = snapshot.entities_by_source
    todo: dict[str, reads.Planned] = {}
    for section in sections(chosen):
        if section.fingerprint in known or section.fingerprint in todo:
            continue
        todo[section.fingerprint] = reads.Planned(
            section.fingerprint, section.source.id, label(section.source, defined), section.rule.id,
            section.rule.start_line, section.rule.text, label(section.source, defined),
            _LOADS.get(section.source.activation.load, ""))
    names = sorted(_names(snapshot))
    reads.create(db, read_id=read_id, project=str(snapshot.cwd), harness=harness, model=model, sources=len(chosen),
                 known=names, sections=list(todo.values()), now=now)
    return len(todo)


def _names(snapshot: ConfigSnapshot) -> set[str]:
    """The skill and agent names a directive may name, as ``knows`` reads them, written as things."""
    found = set()
    for entity in snapshot.entities:
        role = entity.kind.role.value
        if role in ("skill", "agent"):
            for name in (entity.name, *entity.aliases):
                found.update((f"{role}:{name}", f"{role}:{name.rsplit(':', 1)[-1]}"))
    return found


def _known(names: Iterable[str]) -> Callable[[str], bool]:
    have = set(names)

    def known(thing: str) -> bool:
        kind, _, name = thing.partition(":")
        if kind in ("skill", "agent"):
            return name == "*" or thing in have or f"{kind}:{name.rsplit(':', 1)[-1]}" in have
        return True

    return known


def run_read(connect: Callable[[], sqlite3.Connection], read_id: str, generator: Generator, *, at_once: int = 4,
             stop: threading.Event | None = None, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
    """Read the reading's sections still pending or failed, ``at_once`` calls at a time, each marked reading, then read
    (its directives stored by fingerprint) or failed, as it comes back. One another reading read meanwhile is read
    already. ``stop``: no new section starts once it is set; those under way finish."""
    stop = stop or threading.Event()
    db = connect()
    try:
        read = reads.load(db, read_id)
        if read is None:
            raise LookupError(f"no reading {read_id}")
        reads.settle(db, read_id)
        queue = reads.sections(db, read_id, statuses=(reads.Section.PENDING, reads.Section.FAILED))
        done = directive_store.load(db, (s.planned.fingerprint for s in queue), READER_VERSION)
        for state in [s for s in queue if s.planned.fingerprint in done]:
            reads.mark(db, read_id, state.seq, reads.Section.READ, directives=len(done[state.planned.fingerprint].directives))
        queue = [s for s in queue if s.planned.fingerprint not in done]
    finally:
        db.close()
    allowed = _known(read.known)
    lock = threading.Lock()

    def one(state: reads.SectionState) -> None:
        planned = state.planned
        db = connect()
        try:
            reads.mark(db, read_id, state.seq, reads.Section.READING)
            try:
                reply = generator.generate(prompt(planned.text, planned.place, planned.loads), schema(), read.model)
            except GenerationFailed as error:
                if stop is not None and stop.is_set():  # stopped mid-call (Stop reaches the call too): not a failure
                    reads.mark(db, read_id, state.seq, reads.Section.PENDING)
                    return
                reads.mark(db, read_id, state.seq, reads.Section.FAILED, message=str(error)[:500] or "no reply")
                diagnostic_store.add(db, Diagnostic(FAILED, planned.source_id, f"Reading a section of "
                                                    f"{planned.source_name} got no reply: {error}",
                                                    {"section": planned.section, "read": read_id}), now())
                return
            checked = check(reply.data, planned.text, allowed)
            directive_store.save(db, planned.fingerprint, READER_VERSION, checked.directives, reply.model_used, now())
            reads.mark(db, read_id, state.seq, reads.Section.READ, directives=len(checked.directives))
            if checked.rejected:
                diagnostic_store.add(db, Diagnostic(
                    REJECTED, planned.source_id, f"The check threw out {len(checked.rejected)} of what the model "
                    f"listed for a section of {planned.source_name}.", {"section": planned.section, "read": read_id,
                    "rejected": [{"reason": r.reason.name, "index": r.index, "detail": r.detail}
                                 for r in checked.rejected]}), now())
        finally:
            db.close()

    def worker() -> None:
        while True:
            if stop is not None and stop.is_set():
                return
            with lock:
                if not queue:
                    return
                state = queue.pop(0)
            one(state)

    threads = [threading.Thread(target=worker, name=f"agentlint-directives-{n}", daemon=True)
               for n in range(max(1, at_once))]
    for thread in threads:
        thread.start()
    try:
        for thread in threads:
            while thread.is_alive():
                thread.join(timeout=0.2)  # in short waits, so Ctrl-C reaches this thread at once
    except BaseException:
        stop.set()  # Ctrl-C or a stop signal: nothing new starts, and the calls under way are waited for
        for thread in threads:
            thread.join()
        raise
