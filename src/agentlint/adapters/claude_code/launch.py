"""Running one situation with Claude Code, headless, in a variant home (docs/tech_impl/05-runner.md "The executor",
09-adapters.md "Running").

The command line, VERIFIED in parts by E1 and E5 (2026-10-04, a plain terminal):

    claude -p <situation> --session-id <uuid> --output-format json --model <model> [--disallowedTools <tool>…]

run with ``HOME`` set to the variant home, the working directory the run's empty one, and
``CLAUDE_CODE_VERSION_OVERRIDE`` set to the harness version the job pins (D5, "Pinning is enough"): the launcher
runs exactly that version, downloading it when it lacks it — VERIFIED 2026-10-04 by E7 (asked for 2.1.284 where the
default was 2.1.285; the launcher printed "Downloading native claude_code 2.1.284 from Manifold…" and the transcript
recorded ``version`` 2.1.284). Every launch of a job, resumes included, runs the version pinned when the job was
created, so a launcher update between resumes changes nothing. The model is always given (``--model``). E1: a fresh variant home
authenticated through the managed AI Gateway with nothing copied but configuration (D19: no credentials are ever
copied), ``--session-id`` was honoured, and the transcript was written under the variant home at
``.claude/projects/<slug of the cwd>/<session id>.jsonl``, recording the version, model and permission mode. The ``-p``
JSON result carries ``session_id``, ``is_error``, ``subtype``, ``num_turns``, ``duration_ms``, ``total_cost_usd``,
``permission_denials`` and ``result`` — and no tool calls: the trace is read from the transcript (``trace.py``).

**No permission mode is overridden** (D6 final, 2026-10-04): a run follows the org's guardrails as your own sessions
do. Overriding it would change nothing that matters: the org's guardrails PreToolUse hook answers "allow" for in-project
writes and shell commands, and a hook's allow beats any mode (E8b, VERIFIED 2026-10-04 with ``dontAsk``). What is
enforced instead is that **every MCP tool that acts outside the machine is removed** from every run, the same in every
variant: the job's frozen list (``Launch.removed_tools``, chosen by ``acting.py`` or your override) is passed as
``--disallowedTools`` with whole tool names, the flag last so its variadic list takes nothing else. A whole-tool deny
removes the tool from what the model is offered, whatever a hook would answer — VERIFIED 2026-10-04 (E8c) for built-in
tools (``--disallowedTools Write Bash``); that an ``mcp__…`` name is removed the same way is UNVERIFIED. Each tool call
the result's ``permission_denials`` names by its ``tool_use_id`` is marked denied in the trace; the shape of those
entries is UNVERIFIED (none were seen yet).

**What is infrastructure** (D7): an API error the harness wrote into the transcript in place of a model reply
(``isApiErrorMessage``) is never an outcome, with one exception — ``invalid_request`` that no reason or text marks as
an authentication failure (a safeguard refusal, a request the model rejected). Classified from the record's ``error``
code, its ``apiError`` reason and its text, as the 2.1.285 binary's error classifier writes them (read 2026-10-04 at
about byte 183,594,000 of /usr/local/bin/claude_code/native/claude):

- **pause at once** (``AuthFailed``; retrying can't fix it) on the codes in ``PAUSED`` (``authentication_failed``,
  ``billing_error``, ``verification_required``, ``account_on_hold``, ``oauth_org_not_allowed``), on any reason in
  ``PAUSED_REASONS`` whatever the code — a gateway sign-in required or expired is written ``unknown`` with
  ``gateway_signin_required`` / ``gateway_session_expired``; a provider's credentials ``unknown`` or
  ``authentication_failed`` with ``provider_credentials``; a rejected credential header or a disabled key
  ``invalid_request`` with ``invalid_credential_header``, ``org_disabled_credential`` or ``api_key_auth_disabled`` —
  and on ``invalid_request`` whose text is the gateway's failure to authenticate upstream (``GATEWAY_AUTH``: that one
  carries no reason, and this machine signs in through the gateway); ``model_not_found`` pauses too (the job's model
  isn't served: ``HarnessFailed``);
- **retry** (``Retry``, at most 3 times, then the job pauses) on everything else: ``server_error``, ``rate_limit``,
  ``cloud_credential_error`` (credentials that couldn't be loaded, which the binary marks transient), and any code
  not recognised, ``unknown`` and an unreadable one included — precision over recall: an error nobody has classified
  must not pass for the agent's behaviour.

Seen in local transcripts (VERIFIED 2026-10-04): only ``server_error`` (46) and ``invalid_request`` (2), each with no
``apiError``; whether a reason reaches the transcript is UNVERIFIED, which is why the gateway text is matched too.
Every other ending is an outcome: ``ok``; ``error`` or ``error:<subtype>`` when the result says ``is_error``;
``crash`` when the harness exited without a result; ``timeout`` when the time limit killed it — a timeout with an
API error recorded is infrastructure, without one it is behaviour. The run gets its own
process group: at the time limit it is sent SIGTERM, then SIGKILL after ``GRACE_S``; after a normal exit, whatever it
left running (a background hook) is sent SIGTERM, so nothing of one run outlives it into the next. A run that exits
before writing any transcript is neither: ``HarnessFailed`` pauses the job, since nothing it did can be seen.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from agentlint.adapters.claude_code.trace import read_trace
from agentlint.runner.engine import Paused, Retry
from agentlint.runner.execute import Launch, Launched, record_running
from agentlint.trace.model import ApiError, Trace, TraceMissing

CLI = "claude"
# D7/D19: API errors only you can fix — pause the job at once. By code, by reason whatever the code, by the text of
# the gateway's upstream authentication failure (written as invalid_request with no reason).
PAUSED = frozenset({"authentication_failed", "billing_error", "verification_required", "account_on_hold",
                    "oauth_org_not_allowed"})
PAUSED_REASONS = frozenset({"gateway_signin_required", "gateway_session_expired", "provider_credentials",
                            "invalid_credential_header", "org_disabled_credential", "api_key_auth_disabled"})
GATEWAY_AUTH = "The gateway could not authenticate with its upstream provider"
UNSERVED = frozenset({"model_not_found"})  # the job's model isn't served here: pause, retrying can't fix it
OUTCOME = frozenset({"invalid_request"})  # a refusal of the request itself, unless marked as auth above
GRACE_S = 10  # after the time limit: SIGTERM, then this long before SIGKILL
_DROPPED_ENV = ("CLAUDE_CONFIG_DIR",)  # would point the harness at a configuration other than the variant home's
VERSION_ENV = "CLAUDE_CODE_VERSION_OVERRIDE"  # D5: the launcher runs (and downloads if needed) this version (E7)


class AuthFailed(Paused):
    """The harness couldn't authenticate from the variant home. No credentials are copied into it (D19): it
    authenticates through what the machine's managed settings provide, so this needs looking at outside agentlint."""


class HarnessFailed(Paused):
    """The harness exited without writing a transcript: nothing the run did can be seen, so it is no outcome."""


def command(executable: str, launch: Launch, session_id: str) -> list[str]:
    """The command line: no permission mode (D6 final: the org's guardrails govern, E8b), the job's model, and the
    job's removed tools denied whole (E8c), last, since the flag takes every word after it."""
    line = [executable, "-p", launch.situation, "--session-id", session_id, "--output-format", "json",
            "--model", launch.model]
    if launch.removed_tools:
        line += ["--disallowedTools", *launch.removed_tools]
    return line


def _result(stdout: bytes) -> dict[str, Any] | None:
    """The JSON result: the last line of stdout that parses as a JSON object."""
    for line in reversed(stdout.decode("utf-8", errors="replace").strip().splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _denied(trace: Trace, result: dict[str, Any] | None) -> Trace:
    """The trace with every call the result's ``permission_denials`` names marked denied (and an error)."""
    denials = result.get("permission_denials") if result else None
    ids = {d.get("tool_use_id") for d in denials or [] if isinstance(d, dict) and isinstance(d.get("tool_use_id"), str)}
    if not ids:
        return trace
    return replace(trace, actions=tuple(replace(a, denied=True, error=True) if a.call_id in ids else a
                                        for a in trace.actions))


def _paused(error: ApiError) -> bool:
    return error.code in PAUSED or error.reason in PAUSED_REASONS or (
        error.code in OUTCOME and GATEWAY_AUTH in error.text)


def _named(error: ApiError) -> str:
    return error.code + (f" ({error.reason})" if error.reason else "") if error.code else "an unreadable code"


def classify(errors: tuple[ApiError, ...]) -> Paused | Retry | None:
    """What the run's API errors make it (D7): a pause, a retry, or None — no API error, or only refusals of the
    request itself, so the run is an outcome."""
    if auth := [e for e in errors if _paused(e)]:
        return AuthFailed(
            f"the run couldn't authenticate ({', '.join(sorted({_named(e) for e in auth}))} in its transcript). A "
            "variant home gets no credentials (D19): it signs in through what this machine's managed settings provide "
            f"(here, a gateway whose sign-in can expire). Check that a plain `{CLI} -p` works in a terminal, then "
            "resume the job.")
    if unserved := [e for e in errors if e.code in UNSERVED]:
        return HarnessFailed(f"the job's model isn't served here ({', '.join(sorted({_named(e) for e in unserved}))} "
                             "in the transcript): start a job with a model this machine's harness can use.")
    if infrastructure := [e for e in errors if e.code not in OUTCOME]:
        return Retry(f"API error in the transcript: {', '.join(sorted({_named(e) for e in infrastructure}))}")
    return None


def _tail(stderr: bytes, limit: int = 400) -> str:
    text = stderr.decode("utf-8", errors="replace").strip()
    return text[-limit:] if text else "nothing on stderr"


class ClaudeCodeLauncher:
    """Runs one situation with the ``claude`` CLI found on PATH (or ``executable``)."""

    def __init__(self, executable: str | None = None, *, grace_s: float = GRACE_S) -> None:
        self.executable = executable
        self.grace_s = grace_s

    def _executable(self) -> str:
        found = self.executable or shutil.which(CLI)
        if found is None:
            raise HarnessFailed(f"no {CLI} executable on PATH")
        return found

    def launch(self, launch: Launch) -> Launched:
        session_id = str(uuid.uuid4())
        env = {k: v for k, v in os.environ.items() if k not in _DROPPED_ENV and k != VERSION_ENV}
        env.update(HOME=str(launch.home), PWD=str(launch.workdir))
        if launch.harness_version:
            env[VERSION_ENV] = launch.harness_version  # the job's pin, on every launch (D5)
        # Output goes to unlinked temporary files, not pipes: a process the run leaves behind holding them open can
        # neither block the wait nor make a finished run look timed out.
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            process = subprocess.Popen(command(self._executable(), launch, session_id), cwd=launch.workdir, env=env,
                                       stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
            timed_out = False
            try:
                record_running(launch.record, process.pid)  # so a killed runner's successor can stop it
                process.wait(timeout=launch.timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._stop(process)
            except BaseException:
                self._stop(process)
                raise
            else:
                _signal_group(process.pid, signal.SIGTERM)  # what the finished run left running is the run's own
            out.seek(0)
            err.seek(0)
            stdout, stderr = out.read(), err.read()
        result = _result(stdout)
        try:
            trace: Trace | None = _denied(read_trace(launch.home, session_id), result)
        except TraceMissing:
            trace = None
        failure = classify(trace.api_errors if trace is not None else ())
        if failure is not None:
            raise failure
        cost = result.get("total_cost_usd") if result else None
        cost = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else (
            trace.cost.cost_usd if trace is not None and trace.cost is not None else None)
        if timed_out:
            return Launched("timeout", session_id, trace, cost)
        if trace is None:
            raise HarnessFailed(f"{CLI} exited with status {process.returncode} and wrote no transcript for session "
                                f"{session_id}: {_tail(stderr)}")
        if result is None:
            return Launched("crash", session_id, trace, cost)
        if result.get("is_error"):
            subtype = result.get("subtype")
            named = isinstance(subtype, str) and subtype and subtype != "success"
            return Launched(f"error:{subtype}" if named else "error", session_id, trace, cost)
        return Launched("ok", session_id, trace, cost)

    def _stop(self, process: subprocess.Popen[bytes]) -> None:
        """Stop the run and everything it started (its own process group): SIGTERM, then SIGKILL after the grace."""
        _signal_group(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=self.grace_s)
        except subprocess.TimeoutExpired:
            _signal_group(process.pid, signal.SIGKILL)
            process.wait()


def _signal_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def run_test(db, *, project: Path, model: str, adapter=None, launcher: ClaudeCodeLauncher | None = None,
             **options: Any):
    """Start or resume a job against this machine's Claude Code: ``runner.execute.run_test`` with this adapter and
    launcher (the Python entry point until ``agentlint test`` exists)."""
    from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
    from agentlint.runner import execute

    return execute.run_test(db, adapter or ClaudeCodeAdapter(), launcher or ClaudeCodeLauncher(), project=project,
                            model=model, **options)
