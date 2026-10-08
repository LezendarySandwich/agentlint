"""The live UI's sessions: what `agentlint ui` lists and reads back, and the scope a request names
(docs/tech_impl/11-ui.md, "Technical shape").

A ``SessionCatalog`` is one harness's sessions — those the harness keeps, then those only agentlint's archive still
holds — listed without their contents, read back by id, and their subagents. It keeps three caches, each bounded: the
listings of archived-only sessions, by handle and bytes read, held only while still listed (decompressing an
archived transcript is the cost they save); parsed subagent transcripts, one parse per transcript at its latest
size, the ``kept_subagents`` most recently viewed; and subagents' list entries (a title and a count, read from a
parse without the briefings' parts), one per transcript at its latest size, the ``KEPT_OUTLINES`` most recent.

``Catalogs`` is what the server reads through: every harness listed, each with its catalog, and the one rule that
a request reads only a harness and a project agentlint lists (else ``ScopeError``, answered 400). A database or a
transcript that can't be read mid-request is ``Unreadable``, answered 500 with why.
"""

from __future__ import annotations

import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentlint.config.conversation import Conversation, Prompt, SessionListing
from agentlint.config.serialize import to_data
from agentlint.config.sessions import same_dir
from agentlint.harness import Adapter
from agentlint.store import StoreError, archive
from agentlint.ui.server import Params, ScopeError, Unreadable

KEPT_SUBAGENTS = 64  # an open UI keeps this many subagent transcripts parsed, the most recently viewed, each at one size
KEPT_OUTLINES = 4096  # and this many subagents' list entries (a title and a count), each at one size
Connect = Callable[[], sqlite3.Connection]  # opens the agentlint database


class SessionCatalog:
    """One harness's sessions, live and archived, for the transcript reader."""

    def __init__(self, adapter: Adapter, connect: Connect, *, kept_subagents: int = KEPT_SUBAGENTS) -> None:
        self.adapter = adapter
        self._connect = connect
        self._kept_subagents = kept_subagents
        self._listings: dict[tuple[str, int], SessionListing | None] = {}  # archived-only, by (handle, bytes read)
        self._parsed: OrderedDict[str, tuple[int, Conversation]] = OrderedDict()  # handle -> (size, parse), last viewed last
        self._outlines: OrderedDict[str, tuple[int, str | None, int]] = OrderedDict()  # handle -> (size, title, entries)
        self._lock = threading.Lock()  # requests are served on threads: the bookkeeping is one at a time, parsing is not

    def _database(self):
        try:
            return self._connect()
        except StoreError as error:
            raise Unreadable(str(error)) from error

    def listings(self) -> list[dict[str, Any]]:
        """Every session, the harness's own first and then those only the archive holds (``archived_only``), the most
        recently ended first, without their contents."""
        live = {listing.header.session_id: listing for listing in self.adapter.list_conversations()}
        listed = [to_data(listing) | {"archived_only": False} for listing in live.values()]
        db = self._database()
        try:
            kept_now: dict[tuple[str, int], SessionListing | None] = {}
            for kept in archive.sessions(db, self.adapter.name):
                if kept.session_id in live:
                    continue
                key = (kept.handle, kept.bytes_read)
                with self._lock:
                    known = key in self._listings
                    listing = self._listings.get(key)
                if not known:
                    data = archive.read(db, kept.handle)
                    listing = self.adapter.listing_from(kept.handle, data) if data else None
                kept_now[key] = listing
                if listing is not None:
                    listed.append(to_data(listing) | {"archived_only": True})  # the harness deleted it; agentlint kept it
        except sqlite3.Error as error:
            raise Unreadable(f"the agentlint database: {error}") from error
        finally:
            db.close()
        with self._lock:
            self._listings = kept_now  # forgotten, pruned or grown since: never served from memory
        return sorted(listed, key=lambda listing: listing["ended_at"], reverse=True)

    def projects(self, here: Path, listed: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """The projects to choose from: ``here``, then every one with sessions, the most recently used first —
        from ``listed`` (this catalog's ``listings()``) when the caller has them, so one request lists once."""
        listed = self.listings() if listed is None else listed
        projects: dict[str, dict[str, Any]] = {str(here): {"path": str(here), "sessions": 0, "last": None}}
        for listing in listed:
            where = listing["header"]["project"]
            if not where:
                continue
            path = next((p for p in projects if same_dir(p, where)), where)
            entry = projects.setdefault(path, {"path": path, "sessions": 0, "last": None})
            entry["sessions"] += 1
            entry["last"] = max(filter(None, [entry["last"], listing["ended_at"]]), default=None)
        others = sorted((p for p in projects.values() if p["path"] != str(here)), key=lambda p: p["last"] or "",
                        reverse=True)
        return [projects[str(here)], *others]

    def conversation(self, session_id: str) -> dict[str, Any] | None:
        """One session, readable: the harness's file while it has it, else the archive's copy; None if neither."""
        conversation = self.adapter.read_conversation(session_id)
        if conversation is None:
            db = self._database()
            try:
                handle = archive.find(db, self.adapter.name, session_id)
                data = archive.read(db, handle) if handle else None
            except sqlite3.Error as error:
                raise Unreadable(f"the agentlint database: {error}") from error
            finally:
                db.close()
            conversation = self.adapter.conversation_from(handle, data) if handle and data else None
        return to_data(conversation) if conversation else None

    def _subagent_files(self, session_id: str) -> dict[str, Callable[[], bytes]]:
        """A session's subagent transcripts: live where the harness still has them, else archived. handle -> its bytes"""
        found: dict[str, Callable[[], bytes]] = {
            f.handle: (lambda handle=f.handle: self.adapter.read_transcript(handle))
            for f in self.adapter.transcripts() if f.subagent and f.session_id == session_id}
        db = self._database()
        try:
            for kept in archive.subagents(db, self.adapter.name, session_id):
                if kept.handle not in found:
                    data = archive.read(db, kept.handle)
                    found[kept.handle] = lambda data=data: data
        except sqlite3.Error as error:
            raise Unreadable(f"the agentlint database: {error}") from error
        finally:
            db.close()
        return found

    def _parse(self, handle: str, read: Callable[[], bytes]) -> Conversation:
        """A subagent transcript parsed once per size: a size it grew past is let go, never kept beside the new one,
        and past ``kept_subagents`` the least recently viewed goes, so an open UI watching running agents stays bounded."""
        try:
            data = read()
        except OSError as error:  # gone or unreadable since it was listed
            raise Unreadable(f"a subagent transcript: {error}") from error
        with self._lock:
            kept = self._parsed.get(handle)
        if kept is None or kept[0] != len(data):
            kept = (len(data), self.adapter.conversation_from(handle, data))
        with self._lock:
            self._parsed.pop(handle, None)
            self._parsed[handle] = kept  # re-inserted last: the order is the order last viewed
            while len(self._parsed) > self._kept_subagents:
                self._parsed.popitem(last=False)
        return kept[1]

    def subagents(self, session_id: str, ref: str) -> list[dict[str, Any]] | None:
        """The subagents a session's ``subagent_ref`` points to, without contents; None when ``ref`` isn't one this
        harness's adapter writes."""
        if not self.adapter.is_subagent_ref(ref):
            return None
        listed = []
        for handle, read in self._subagent_files(session_id).items():
            if not self.adapter.belongs(ref, handle, read):
                continue
            title, entries = self._outline(handle, read)
            listed.append({"id": self.adapter.subagent_id(handle), "title": title, "entries": entries})
        return sorted(listed, key=lambda s: s["id"])

    def _outline(self, handle: str, read: Callable[[], bytes]) -> tuple[str | None, int]:
        """A subagent's list entry, its title and how many items it has: from its parse when one is kept at its size,
        else from a parse without the briefings' parts (``parts=False``: no discovery, no counting), kept apart."""
        try:
            data = read()
        except OSError as error:  # gone or unreadable since it was listed
            raise Unreadable(f"a subagent transcript: {error}") from error
        with self._lock:
            parsed, kept = self._parsed.get(handle), self._outlines.get(handle)
        if parsed is not None and parsed[0] == len(data):
            conversation = parsed[1]
        elif kept is not None and kept[0] == len(data):
            return kept[1], kept[2]
        else:
            conversation = self.adapter.conversation_from(handle, data, parts=False)
        asked = next((item.text for item in conversation.items if isinstance(item, Prompt)), "").strip()
        title = conversation.title or (asked.splitlines()[0][:160] if asked else None)  # the harness's name first
        with self._lock:
            self._outlines.pop(handle, None)
            self._outlines[handle] = (len(data), title, len(conversation.items))
            while len(self._outlines) > KEPT_OUTLINES:
                self._outlines.popitem(last=False)
        return title, len(conversation.items)

    def subagent(self, session_id: str, ident: str) -> dict[str, Any] | None:
        """One of a session's subagent transcripts, readable, by the id ``subagents`` lists it under."""
        for handle, read in self._subagent_files(session_id).items():
            if self.adapter.subagent_id(handle) == ident:
                return to_data(self._parse(handle, read))
        return None


class Catalogs:
    """Every harness the UI lists, each with its catalog, and the scope a request names (``?harness=&project=``):
    only a harness and a project listed here is ever read."""

    def __init__(self, catalogs: list[SessionCatalog], here: Path) -> None:
        if not catalogs:
            raise ValueError("a UI lists at least one harness")
        self._catalogs = {catalog.adapter.name: catalog for catalog in catalogs}  # in the order they are offered
        self.here = here
        self.harnesses = [{"name": c.adapter.name, "label": c.adapter.label, "version": c.adapter.version()}
                          for c in catalogs]  # read once, as the server starts

    def catalog(self, params: Params) -> SessionCatalog:
        """The harness the request names, or the first listed."""
        wanted = params.get("harness") or next(iter(self._catalogs))
        if wanted not in self._catalogs:
            raise ScopeError(f"not a harness agentlint lists here: {wanted}")
        return self._catalogs[wanted]

    def _project(self, catalog: SessionCatalog, params: Params, listed: list[dict[str, Any]] | None = None) -> str:
        wanted = params.get("project") or str(self.here)
        if wanted not in [p["path"] for p in catalog.projects(self.here, listed)]:  # never a path from the request
            raise ScopeError(f"not a project agentlint knows: {wanted}")
        return wanted

    def scope(self, params: Params) -> tuple[Adapter, Path]:
        """The harness and project a report is read for."""
        catalog = self.catalog(params)
        return catalog.adapter, Path(self._project(catalog, params))

    def scopes(self, params: Params) -> dict[str, Any]:
        """Every harness listed, and the chosen one's projects."""
        catalog = self.catalog(params)
        return {"harnesses": self.harnesses, "harness": catalog.adapter.name, "projects": catalog.projects(self.here),
                "default": str(self.here), "home_dir": str(Path.home())}

    def sessions(self, params: Params) -> list[dict[str, Any]]:
        catalog = self.catalog(params)
        listed = catalog.listings()  # once: the project is checked against the same listing
        project = self._project(catalog, params, listed)
        catalog.adapter.prepare(Path(project))  # its sessions are about to be read: get ready while you choose one
        return [listing for listing in listed if same_dir(listing["header"]["project"], project)]

    def session(self, params: Params, session_id: str) -> dict[str, Any] | None:
        return self.catalog(params).conversation(session_id)

    def subagents(self, params: Params, session_id: str, ref: str) -> list[dict[str, Any]] | None:
        return self.catalog(params).subagents(session_id, ref)

    def subagent(self, params: Params, session_id: str, ident: str) -> dict[str, Any] | None:
        return self.catalog(params).subagent(session_id, ident)
