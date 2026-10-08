"""`agentlint ui`: the report's shell, served on this machine with the report read fresh on every load
(docs/tech_impl/11-ui.md).

It answers its fixed endpoints and nothing else: the shell, the report and its texts (those of the last few scopes
read, by harness and project; asking for them never starts a reading), what agentlint recorded (a project's stored
snapshots and what changed between two, and its diagnostics, read from its database), and your sessions, listed and read back by id (the
transcript reader). Each names its scope with ``?harness=&project=``, and only a harness and a project
the server lists is ever read (else 400). Never a file by path. Every request must carry
the token made for this run, and name this server as its host: a page elsewhere can neither guess the
token nor reach the server under its own name (DNS rebinding). It listens on 127.0.0.1 only. What can't be read
is answered 500 with why (``Unreadable``); anything else raised is a bug, whose traceback goes to stderr before its 500
(``log_message`` stays silent: the request line can carry the token).

Writes are POSTs to the ``Control``'s fixed endpoints, and every one must pass, in this order: this server named as
its host (403); no token in the query — a write takes it from the ``X-Agentlint-Token`` header only, so an address
pasted or logged can't make one (403); that header's token (403); an ``Origin``, when the browser sends one, that is
this server (403); ``Content-Type: application/json`` (415) — so a cross-origin page can only send it after a CORS
preflight, and there is no OPTIONS handler to answer one (501); a ``Content-Length`` (411) of at most ``MAX_BODY``
bytes (413); a body that is one JSON object (400). The control then checks every value and refuses what it won't do
with ``Refused`` — its status and reason. Every refusal is JSON: ``{"error": why}``.
"""

from __future__ import annotations

import gzip
import hmac
import json
import re
import secrets
import sys
import threading
import traceback
from collections import OrderedDict
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlsplit

HOST = "127.0.0.1"
TOKEN_HEADER = "X-Agentlint-Token"
_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
_PAGE_POLICY = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; "
                "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

Params = dict[str, str]  # the request's query: the scope it asks for (harness, project)
Build = Callable[[Params], tuple[dict[str, Any], dict[str, str]]]  # the report for a scope, and its texts
KEPT_TEXTS = 4  # the texts of this many scopes' latest readings are kept: each is megabytes
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
MAX_BODY = 64 * 1024  # bytes of a write's body: every write is a few small fields
_REF_MAX = 200  # characters of a subagent reference: what it means is the adapter's, how long it may be the server's


class ScopeError(ValueError):
    """A scope the server won't read: a harness or a project not among those it lists. Answered 400."""


class Unreadable(Exception):
    """What a request asked for couldn't be read — the agentlint database, a transcript gone mid-read, a reading that
    failed: the message says why. Answered 500 with it; anything else raised is a bug, answered 500 after its traceback
    is printed to stderr."""


class Refused(Exception):
    """A write the control won't do — a value out of range, a field it doesn't take, Forget while a job runs: answered
    with ``status`` and the reason, in words a person can act on."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status


class Control(Protocol):
    """What the UI can read about agentlint itself and change (``ui/control.py``'s ``StoreControl``). Each write takes
    the request's JSON object and returns the answer; what it won't do it refuses with ``Refused``."""

    def settings(self) -> dict[str, Any]:
        """GET /api/settings: every setting, the daily sync, the archive's totals, the version, the first run."""
        ...

    def set_setting(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/settings: ``{key, value}`` or ``{key, reset: true}``."""
        ...

    def schedule(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/schedule: ``{on}``."""
        ...

    def forget(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/forget: ``{confirm: "forget"}``."""
        ...

    def welcomed(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/welcomed: ``{}`` — the first-run panel was closed."""
        ...

    def suppressions(self) -> dict[str, Any]:
        """GET /api/suppressions: every dismissal across projects, with its state in each."""
        ...

    def suppress(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/suppress: ``{finding_id, project, reason}``, and the scope's ``harness`` — the project is checked
        first, as `agentlint suppress` checks it."""
        ...

    def unsuppress(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/unsuppress: ``{finding_id}``."""
        ...


class Tests(Protocol):
    """Tests from the UI (``ui/testing.py``'s ``Testing``): the plan, situations, onboarding, jobs and results. Reads
    take the request's query, writes its JSON object; what it won't do it refuses with ``Refused``."""

    def plan(self, params: Params) -> dict[str, Any]: ...

    def tick(self, body: dict[str, Any]) -> dict[str, Any]: ...

    def situations(self, params: Params) -> dict[str, Any]: ...

    def onboarding(self, params: Params) -> dict[str, Any]: ...

    def jobs(self, params: Params) -> dict[str, Any]: ...

    def results(self, job_id: str) -> dict[str, Any]: ...

    def generate(self, body: dict[str, Any]) -> dict[str, Any]: ...

    def review(self, probe_id: str, body: dict[str, Any]) -> dict[str, Any]: ...

    def accept(self, body: dict[str, Any]) -> dict[str, Any]: ...

    def create_job(self, body: dict[str, Any]) -> dict[str, Any]: ...

    def stop(self, job_id: str, body: dict[str, Any]) -> dict[str, Any]: ...

    def resume(self, job_id: str, body: dict[str, Any]) -> dict[str, Any]: ...

    def salvage(self, job_id: str, body: dict[str, Any]) -> dict[str, Any]: ...


class NoControl:
    """A server with nothing to change: every write, and the settings, are 404."""

    def settings(self) -> dict[str, Any]:
        raise Refused(404, "this server has no settings")

    def set_setting(self, body: dict[str, Any]) -> dict[str, Any]:
        raise Refused(404, "nothing here")

    schedule = forget = welcomed = suppress = unsuppress = set_setting

    def suppressions(self) -> dict[str, Any]:
        raise Refused(404, "this server has no dismissals")


class Catalog(Protocol):
    """The sessions the server answers for, each request naming its scope (``ui/catalog.py``'s ``Catalogs``)."""

    def scopes(self, params: Params) -> dict[str, Any]:
        """Every harness listed, and the chosen one's projects."""
        ...

    def sessions(self, params: Params) -> list[dict[str, Any]]:
        """The scope's sessions, without their contents."""
        ...

    def session(self, params: Params, session_id: str) -> dict[str, Any] | None:
        """One of the harness's sessions by id, readable; None if there is none."""
        ...

    def subagents(self, params: Params, session_id: str, ref: str) -> list[dict[str, Any]] | None:
        """A session's subagents behind a subagent reference, without contents; None when the reference isn't one
        the harness's adapter writes."""
        ...

    def subagent(self, params: Params, session_id: str, ident: str) -> dict[str, Any] | None:
        """One subagent's transcript, readable; None if there is none."""
        ...


class NoSessions:
    """A catalog with nothing in it: a server that only serves reports."""

    def scopes(self, params: Params) -> dict[str, Any]:
        return {}

    def sessions(self, params: Params) -> list[dict[str, Any]]:
        return []

    def session(self, params: Params, session_id: str) -> dict[str, Any] | None:
        return None

    def subagents(self, params: Params, session_id: str, ref: str) -> list[dict[str, Any]] | None:
        return None

    def subagent(self, params: Params, session_id: str, ident: str) -> dict[str, Any] | None:
        return None


class Records(Protocol):
    """What agentlint has recorded, each request naming its scope (``ui/records.py``'s ``StoredRecords``)."""

    def snapshots(self, params: Params) -> list[dict[str, Any]]:
        """The project's stored snapshots, the most recent first."""
        ...

    def diff(self, params: Params) -> dict[str, Any]:
        """What changed between two of them (``from``, and ``to`` or the latest)."""
        ...

    def diagnostics(self, params: Params) -> list[dict[str, Any]]:
        """agentlint's findings about itself, the most recent first: never findings, never counted with them."""
        ...


class UIServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, build: Build, shell_html: str, port: int = 0, *, catalog: Catalog | None = None,
                 control: Control | None = None, records: Records | None = None, tests: Tests | None = None) -> None:
        super().__init__((HOST, port), _Handler)
        self.build = build
        self.tests = tests  # None: a server that runs no tests, which answers 404 for every test endpoint
        self.catalog: Catalog = catalog or NoSessions()
        self.control: Control = control or NoControl()
        self.write_lock = threading.Lock()  # one write at a time
        self.records = records  # None: a server of reports only, which answers 404 for what it never recorded
        self.shell_html = shell_html
        self.token = secrets.token_urlsafe(24)
        self.hosts = {f"{HOST}:{self.server_port}", f"localhost:{self.server_port}"}
        self.origins = {f"http://{host}" for host in self.hosts}  # a page this server served
        self.texts: OrderedDict[tuple[str, str], dict[str, str]] = OrderedDict()  # (harness, project) -> texts, oldest first
        self.texts_lock = threading.Lock()  # its own: a reading holds `lock` for the whole reading
        self.lock = threading.Lock()  # one reading at a time

    def keep_texts(self, data: dict[str, Any], texts: dict[str, str]) -> None:
        """Keep a reading's texts under the scope the reading says it is of — its snapshot's harness and project,
        so the default scope and the same one named explicitly are one key — evicting the oldest past KEPT_TEXTS."""
        snapshot = data.get("snapshot") or {}
        if not (snapshot.get("harness") and snapshot.get("cwd")):
            return
        with self.texts_lock:
            key = (snapshot["harness"], snapshot["cwd"])
            self.texts.pop(key, None)
            self.texts[key] = texts
            while len(self.texts) > KEPT_TEXTS:
                self.texts.popitem(last=False)

    def kept_texts(self, harness: str, project: str) -> dict[str, str] | None:
        with self.texts_lock:
            return self.texts.get((harness, project))

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.server_port}/?token={self.token}"


_WRITES = {"/api/settings": "set_setting", "/api/schedule": "schedule", "/api/forget": "forget",
           "/api/welcomed": "welcomed", "/api/suppress": "suppress", "/api/unsuppress": "unsuppress"}  # path -> Control's
_READING_WRITES = {"/api/suppress", "/api/jobs", "/api/situations/generate"}  # these read a project first: a
# reading, so they wait for one under way
_TEST_WRITES = {"/api/situations/generate": "generate", "/api/onboarding": "accept", "/api/jobs": "create_job",
                "/api/plan/ticked": "tick",  # ticking reads nothing: it sizes the job from the last reading
                "/api/directives/read": "read_directives"}  # planned from the last reading GET /api/directives kept
_JOB_ACTION = re.compile(r"/api/jobs/([A-Za-z0-9][A-Za-z0-9._-]{0,99})/(stop|resume|salvage)")
_JOB_RESULTS = re.compile(r"/api/jobs/([A-Za-z0-9][A-Za-z0-9._-]{0,99})/results")
_READ_ACTION = re.compile(r"/api/reads/([A-Za-z0-9][A-Za-z0-9._-]{0,99})/(stop|resume)")
_READ_DETAIL = re.compile(r"/api/reads/([A-Za-z0-9][A-Za-z0-9._-]{0,99})")
_SITUATION = re.compile(r"/api/situations/([0-9a-f]{16})")
_TEST_READS = {"/api/plan": "plan", "/api/situations": "situations", "/api/onboarding": "onboarding",
               "/api/jobs": "jobs", "/api/directives": "directives", "/api/reads": "reads"}
_READING_READS = {"/api/plan", "/api/onboarding", "/api/directives"}  # each reads the project: one reading at a time


def _no_constant(name: str) -> None:
    raise ValueError(f"{name} isn't JSON")


class _Handler(BaseHTTPRequestHandler):
    server: UIServer

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if self.headers.get("Host") not in self.server.hosts:
            return self._send(403, b"agentlint: wrong host\n", "text/plain")
        token = parse_qs(url.query).get("token", [""])[0] or self.headers.get(TOKEN_HEADER, "")
        if not hmac.compare_digest(token.encode(), self.server.token.encode()):
            return self._send(403, b"agentlint: this needs the address agentlint ui printed\n", "text/plain")
        if url.path == "/":
            return self._send(200, self.server.shell_html.encode(), "text/html; charset=utf-8", page=True)
        params = {key: values[0] for key, values in parse_qs(url.query).items() if key != "token"}
        self._handle(lambda: self._answer(url.path, params))

    def do_POST(self) -> None:
        url = urlsplit(self.path)
        if self.headers.get("Host") not in self.server.hosts:
            return self._json(403, {"error": "wrong host"})
        if "token" in parse_qs(url.query, keep_blank_values=True):  # never in the address for a write: addresses are pasted and logged
            return self._json(403, {"error": "a write takes the token in the X-Agentlint-Token header only"})
        if not hmac.compare_digest(self.headers.get(TOKEN_HEADER, "").encode(), self.server.token.encode()):
            return self._json(403, {"error": "this needs the token agentlint ui made, in the X-Agentlint-Token header"})
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.origins:
            return self._json(403, {"error": "a write must come from a page this server served"})
        if self.headers.get_content_type() != "application/json":
            return self._json(415, {"error": "a write's body must be JSON (Content-Type: application/json)"})
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length") is None:
            return self._json(411, {"error": "a write must say its length (Content-Length)"})
        try:
            length = int(self.headers["Content-Length"])
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            return self._json(413 if length > MAX_BODY else 400,
                              {"error": f"a write's body must be at most {MAX_BODY:,} bytes"})
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"), parse_constant=_no_constant)
        except (UnicodeDecodeError, ValueError):
            body = None
        if not isinstance(body, dict):
            return self._json(400, {"error": "a write's body must be one JSON object"})
        self._handle(lambda: self._write(url.path, body))

    def _test_write(self, path: str) -> Callable[[dict[str, Any]], dict[str, Any]] | None:
        """The test write a path names, or None. A server built without tests refuses each with 404."""
        tests = self.server.tests
        if path in _TEST_WRITES:
            name, args = _TEST_WRITES[path], ()
        elif match := _JOB_ACTION.fullmatch(path):
            name, args = match.group(2), (match.group(1),)
        elif match := _SITUATION.fullmatch(path):
            name, args = "review", (match.group(1),)
        elif match := _READ_ACTION.fullmatch(path):
            name, args = f"{match.group(2)}_read", (match.group(1),)
        else:
            return None
        if tests is None:
            def refuse(body: dict[str, Any]) -> dict[str, Any]:
                raise Refused(404, "this server runs no tests")
            return refuse
        return lambda body: getattr(tests, name)(*args, body)

    def _write(self, path: str, body: dict[str, Any]) -> None:
        test_write = self._test_write(path)
        if test_write is not None:
            with self.server.write_lock:
                if path not in _READING_WRITES:
                    return self._json(200, test_write(body))
                with self.server.lock:
                    return self._json(200, test_write(body))
        if path not in _WRITES:
            return self._json(404, {"error": "nothing here"})
        write = getattr(self.server.control, _WRITES[path])
        with self.server.write_lock:
            if path not in _READING_WRITES:
                return self._json(200, write(body))
            with self.server.lock:  # it reads the configuration, as a report does: one reading at a time
                return self._json(200, write(body))

    def _handle(self, answer: Callable[[], None]) -> None:
        """Answer, turning what went wrong into a JSON error that says why."""
        try:
            return answer()
        except Refused as error:
            return self._json(error.status, {"error": str(error)})
        except ScopeError as error:
            return self._json(400, {"error": str(error)})
        except Unreadable as error:  # the page says what went wrong; the server keeps serving
            return self._json(500, {"error": str(error)})
        except ConnectionError:  # the page went away mid-answer: no one to tell
            return None
        except Exception as error:  # a bug: where, to the terminal running the server, then 500; the server carries on
            # The traceback holds code and exception messages, never the token: it reaches no further than do_GET's
            # and do_POST's checks above, and the parameters and bodies handed on never carry it.
            sys.stderr.write(traceback.format_exc())
            return self._json(500, {"error": f"{type(error).__name__}: {error} — unexpected; the terminal running "
                                             "agentlint ui shows where"})

    def _answer(self, path: str, params: Params) -> None:
        if path == "/api/settings":
            return self._json(200, self.server.control.settings())
        if path == "/api/suppressions":
            return self._json(200, self.server.control.suppressions())
        if path == "/api/report":
            with self.server.lock:
                data, texts = self.server.build(params)
                self.server.keep_texts(data, texts)
            return self._json(200, data)
        if path == "/api/texts":  # never starts a reading: only what one already read
            if not (params.get("harness") and params.get("project")):
                return self._json(400, {"error": "name the harness and project whose texts you want"})
            texts = self.server.kept_texts(params["harness"], params["project"])
            if texts is None:
                return self._json(404, {"error": "this project's texts aren't kept: it hasn't been read, or not among the last few read"})
            return self._json(200, texts)
        if path in _TEST_READS or _JOB_RESULTS.fullmatch(path) or _READ_DETAIL.fullmatch(path):
            tests = self.server.tests
            if tests is None:
                return self._json(404, {"error": "this server runs no tests"})
            if match := _JOB_RESULTS.fullmatch(path):
                return self._json(200, tests.results(match.group(1)))
            if path not in _TEST_READS and (match := _READ_DETAIL.fullmatch(path)):
                return self._json(200, tests.read_detail(match.group(1)))
            read = getattr(tests, _TEST_READS[path])
            if path in _READING_READS:
                with self.server.lock:
                    return self._json(200, read(params))
            return self._json(200, read(params))
        records = self.server.records
        if path in ("/api/snapshots", "/api/diff", "/api/diagnostics") and records is not None:  # from the store only
            answer = {"/api/snapshots": records.snapshots, "/api/diff": records.diff, "/api/diagnostics": records.diagnostics}
            return self._json(200, answer[path](params))
        catalog = self.server.catalog
        if path == "/api/scope":
            return self._json(200, catalog.scopes(params))
        if path == "/api/sessions":
            return self._json(200, catalog.sessions(params))
        parts = [unquote(part) for part in path.split("/")[3:]] if path.startswith("/api/sessions/") else []
        if len(parts) == 1 and _ID.fullmatch(parts[0]):
            conversation = catalog.session(params, parts[0])
            return self._json(200, conversation) if conversation else self._json(404, {"error": "no such session"})
        if len(parts) == 2 and parts[1] == "subagents" and _ID.fullmatch(parts[0]):
            ref = params.get("ref", "")
            listed = catalog.subagents(params, parts[0], ref) if 0 < len(ref) <= _REF_MAX else None
            return self._json(200, listed) if listed is not None else self._json(400, {"error": "not a subagent reference"})
        if len(parts) == 3 and parts[1] == "subagents" and _ID.fullmatch(parts[0]) and _ID.fullmatch(parts[2]):
            conversation = catalog.subagent(params, parts[0], parts[2])
            return self._json(200, conversation) if conversation else self._json(404, {"error": "no such subagent"})
        return self._send(404, b"agentlint: nothing here\n", "text/plain")

    def _json(self, status: int, payload: object) -> None:
        body = gzip.compress(json.dumps(payload).encode("ascii"), mtime=0)  # ASCII: any text survives
        self._send(status, body, "application/json", encoding="gzip")

    def _send(self, status: int, body: bytes, kind: str, *, encoding: str | None = None, page: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        if encoding:
            self.send_header("Content-Encoding", encoding)
        for name, value in _HEADERS.items():
            self.send_header(name, value)
        if page:
            self.send_header("Content-Security-Policy", _PAGE_POLICY)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass  # the request line carries the token: never printed
