"""What agentlint has recorded, for the live UI: a project's stored snapshots and what changed between two of them
(Configuration › What changed), and agentlint's diagnostics — its findings about itself (docs/tech_impl/11-ui.md).

Everything here is read from the agentlint database, never from the harness's disk, and never starts a reading: the
snapshots are those `check`, `show` and the UI's own page loads stored. The diff is ``report/diff.py``'s, as
`agentlint show --diff` prints it, so the page and the command apply the same rules. A request names its scope with
``?harness=&project=`` and only a harness and a project the server lists is read (``scope``, else ``ScopeError``); a
fingerprint is matched among that project's snapshots only, by any unique prefix. Diagnostics carry no project (the
table has none: they are about agentlint's own work, a target or a job), so every one is listed whatever the project.
"""

from __future__ import annotations

import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agentlint.config.model import ConfigSnapshot
from agentlint.harness import Adapter
from agentlint.report.diff import SnapshotDiff, always_on, diff, unseen, untraced_left_out
from agentlint.store import StoreError, diagnostics
from agentlint.store import snapshots as snapshot_store
from agentlint.store.diagnostics import Diagnostic
from agentlint.store.snapshots import Stored, UnknownSnapshot, UnreadableSnapshot
from agentlint.ui.server import Params, ScopeError, Unreadable

Connect = Callable[[], sqlite3.Connection]
Scope = Callable[[Params], tuple[Adapter, Path]]  # the harness and project a request names, checked (Catalogs.scope)
KEPT_SUMMARIES = 256  # a stored snapshot's line in the list, by project, fingerprint and when it was stored


class StoredRecords:
    """The ``Records`` the server answers /api/snapshots, /api/diff and /api/diagnostics from."""

    def __init__(self, scope: Scope, connect: Connect) -> None:
        self._scope = scope
        self._connect = connect
        # Reading a snapshot back takes ~0.1 s; its line can't change while its row doesn't (a re-reading of the same
        # configuration replaces the row, with a new time), so each is read back once.
        self._summaries: OrderedDict[tuple[str, str, str], dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    @contextmanager
    def _database(self) -> Iterator[sqlite3.Connection]:
        try:
            db = self._connect()
        except StoreError as error:
            raise Unreadable(str(error)) from error
        try:
            yield db
        except (StoreError, sqlite3.Error) as error:
            raise Unreadable(f"the agentlint database: {error}") from error
        finally:
            db.close()

    def _summary(self, db: sqlite3.Connection, project: str, item: Stored) -> dict[str, Any]:
        key = (project, item.fingerprint, item.taken_at.isoformat())
        with self._lock:
            if key in self._summaries:
                self._summaries.move_to_end(key)
                return self._summaries[key]
        try:
            snapshot = snapshot_store.load(db, project, item.fingerprint)
            summary = {"harness": snapshot.harness, **summary_data(snapshot)}
        except UnreadableSnapshot:  # stored by an agentlint whose format this one can't read back
            summary = {"harness": snapshot_store.harness_of(db, project, item.fingerprint), "harness_version": None,
                       "readable": False, "sources": None, "tokens": None}
        with self._lock:
            self._summaries[key] = summary
            while len(self._summaries) > KEPT_SUMMARIES:
                self._summaries.popitem(last=False)
        return summary

    def snapshots(self, params: Params) -> list[dict[str, Any]]:
        """The project's stored snapshots through this harness, the most recent first: each one's fingerprint, when it
        was stored (last read), its harness version, how many sources and its always-on tokens (``report/diff.py``'s
        figure; None for one this agentlint can't read back, which says so with ``readable``)."""
        adapter, project = self._scope(params)
        listed = []
        with self._database() as db:
            for item in snapshot_store.stored(db, str(project)):
                summary = self._summary(db, str(project), item)
                if summary["harness"] != adapter.name:
                    continue
                listed.append({"fingerprint": item.fingerprint, "taken_at": item.taken_at.isoformat(),
                               **{key: value for key, value in summary.items() if key != "harness"}})
        return listed

    def _resolve(self, db: sqlite3.Connection, adapter: Adapter, project: str, prefix: str) -> Stored:
        try:
            found = snapshot_store.resolve(db, project, prefix)
        except UnknownSnapshot as error:
            raise ScopeError(str(error).replace("; `agentlint show --snapshots` lists them", "")) from error
        if self._summary(db, project, found)["harness"] != adapter.name:
            raise ScopeError(f"the snapshot {prefix} wasn't read through {adapter.name}")
        return found

    def diff(self, params: Params) -> dict[str, Any]:
        """What changed from the snapshot ``from`` to ``to`` (none: the latest stored, the configuration as the page
        last read it) — ``report/diff.py``'s comparison, as `show --diff` prints it."""
        adapter, project = self._scope(params)
        if not params.get("from"):
            raise ScopeError("name the snapshot to compare from (from=<fingerprint>)")
        with self._database() as db:
            before = self._resolve(db, adapter, str(project), params["from"])
            if params.get("to"):
                after = self._resolve(db, adapter, str(project), params["to"])
            else:
                after = next(s for s in snapshot_store.stored(db, str(project))
                             if self._summary(db, str(project), s)["harness"] == adapter.name)
            try:
                result = diff(snapshot_store.load(db, str(project), before.fingerprint),
                              snapshot_store.load(db, str(project), after.fingerprint))
            except UnreadableSnapshot as error:
                raise Unreadable(f"{error}; they can't be compared") from error
        return diff_data(result, before, after)

    def diagnostics(self, params: Params) -> list[dict[str, Any]]:
        """Every diagnostic agentlint kept, the most recent first (``diagnostic_data``). The scope is checked as
        everywhere, though a diagnostic belongs to no project: the table has none."""
        self._scope(params)
        with self._database() as db:
            return [diagnostic_data(item) for item in diagnostics.listed(db)]


def summary_data(snapshot: ConfigSnapshot) -> dict[str, Any]:
    """A stored snapshot's line in the list, past its fingerprint and time (``SnapshotInfo`` in ui/src/contract.ts)."""
    return {"harness_version": snapshot.harness_version, "readable": True, "sources": len(snapshot.sources),
            "tokens": always_on(snapshot)}


def diff_data(result: SnapshotDiff, before: Stored, after: Stored) -> dict[str, Any]:
    """A ``SnapshotDiff`` as the UI reads it (``SnapshotDiff`` in ui/src/contract.ts). ``unexplained`` is the part of
    the always-on change no listed change accounts for — text seen on one side only, whose change in size isn't
    known — computed here, as `show --diff` computes it, so the page never re-states the rule."""

    def side(snapshot: ConfigSnapshot, stored: Stored) -> dict[str, Any]:
        return {"fingerprint": stored.fingerprint, "taken_at": stored.taken_at.isoformat(),
                "harness_version": snapshot.harness_version, "model": snapshot.model, "unseen": unseen(snapshot)}

    explained = sum(c.delta for c in result.changes if c.delta is not None and c.load.in_first_turn)
    return {
        "from": side(result.before, before),
        "to": side(result.after, after),
        "always_before": result.always_before,
        "always_after": result.always_after,
        "unexplained": result.always_after - result.always_before - explained,
        "untraced_left_out": untraced_left_out(result),
        "changes": [{"kind": c.kind, "source_id": c.source_id, "label": c.label, "load": c.load.value,
                     "before": c.before, "after": c.after, "delta": c.delta, "note": c.note} for c in result.changes],
        "entities_added": [{"kind": kind, "name": name} for kind, name in result.entities_added],
        "entities_removed": [{"kind": kind, "name": name} for kind, name in result.entities_removed],
    }


def diagnostic_data(item: Diagnostic) -> dict[str, Any]:
    """A diagnostic as the UI reads it (``DiagnosticInfo`` in ui/src/contract.ts): its kind (its producer's name for what
    happened), what it is about (a target, a job), the sentence for a person and when. Its details stay in the store."""
    return {"id": item.id, "kind": item.kind, "subject": item.subject, "message": item.message,
            "created": item.created.isoformat() if item.created else None}
