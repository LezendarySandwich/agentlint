"""The probe generator for Claude Code: the core ``Generator`` protocol, answered by one headless ``claude -p`` call
(docs/tech_impl/04-probe-generator.md, "The real generator"; decided 2026-10-04 as D14, settled by E5).

The call is the one E5 ran on this machine:

    claude -p <prompt> --bare --model haiku --tools "" --no-session-persistence --output-format json --json-schema <schema>

``--bare`` and ``--tools ""`` keep the configuration under test out of the call that writes its test inputs, and
``--no-session-persistence`` writes no transcript (E5: none was written; the harness made an empty project folder with
``memory/`` under ~/.claude/projects). It runs in an empty directory under ``AGENTLINT_HOME``, so the project it is
started from adds nothing either. The result is one JSON object: ``structured_output`` is the reply, ``modelUsage``
names the model that answered (E5: claude-haiku-4-5@20251001 for ``haiku``), ``is_error`` says whether the call
failed. Anything else — no executable, a timeout, output that isn't that object, an error result, no structured
output — raises GenerationFailed, never an empty reply. Output is decoded as UTF-8 with bad bytes replaced, so bytes
that aren't UTF-8 (on stdout or stderr) end as GenerationFailed too, never as a UnicodeDecodeError.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agentlint.probes.generator import GenerationFailed, Reply
from agentlint.store import home, private_dirs

MODEL = "haiku"  # the cheap model the generator asks for by default (D14)
TIMEOUT_S = 120  # E5 took 6 s; a call that takes twenty times that is not coming back
EXECUTABLE = "claude"


def command(prompt: str, schema: Mapping[str, Any], model: str, executable: str = EXECUTABLE) -> list[str]:
    """The command line, in the order E5 ran it."""
    return [executable, "-p", prompt, "--bare", "--model", model, "--tools", "", "--no-session-persistence",
            "--output-format", "json", "--json-schema", json.dumps(schema, separators=(",", ":"))]


def _result(stdout: str) -> dict:
    """The result object: the whole output, or else its last line that is a JSON object (a launcher may print a line
    of its own first)."""
    candidates = [stdout.strip()] + [line.strip() for line in reversed(stdout.splitlines()) if line.strip().startswith("{")]
    for text in candidates:
        try:
            parsed = json.loads(text)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise GenerationFailed(f"the generator's output is not a JSON result: {stdout.strip()[:200]!r}")


def model_used(result: Mapping[str, Any]) -> str | None:
    """The model that answered, from ``modelUsage``: its one key, or every key when several answered, so a fallback
    shows; None when the result doesn't say."""
    usage = result.get("modelUsage")
    names = sorted(str(name) for name in usage if str(name).strip()) if isinstance(usage, Mapping) else []
    return ", ".join(names) or None


class ClaudeCodeGenerator:
    """One headless call per ``generate``, in ``directory`` (default ``AGENTLINT_HOME/generate``), which must be
    empty: it is created when missing, and a directory holding anything is refused rather than cleared."""

    default_model = MODEL

    def __init__(self, directory: Path | None = None, *, executable: str = EXECUTABLE, timeout_s: float = TIMEOUT_S,
                 env: Mapping[str, str] | None = None) -> None:
        self.directory = directory if directory is not None else home() / "generate"
        root = home().resolve()
        if not self.directory.resolve().is_relative_to(root):
            raise ValueError(f"the generator's directory must be under agentlint's home ({root}): {self.directory}")
        self.executable, self.timeout_s, self.env = executable, timeout_s, env

    def _empty_directory(self) -> Path:
        private_dirs(self.directory)  # it, and agentlint's home when it is new, made 0700
        if any(self.directory.iterdir()):
            raise GenerationFailed(f"{self.directory} should be empty, so that nothing in it shapes the reply, but "
                                   "holds something; empty it and try again")
        return self.directory

    def generate(self, prompt: str, schema: Mapping[str, Any], model: str) -> Reply:
        cwd = self._empty_directory()
        try:
            done = subprocess.run(command(prompt, schema, model, self.executable), cwd=cwd, stdin=subprocess.DEVNULL,
                                  capture_output=True, encoding="utf-8", errors="replace",
                                  timeout=self.timeout_s,
                                  env=dict(self.env) if self.env is not None else None)
        except FileNotFoundError as error:
            raise GenerationFailed(f"{self.executable} is not on PATH: {error}") from error
        except subprocess.TimeoutExpired as error:
            raise GenerationFailed(f"the generator's call took longer than {self.timeout_s:g} s") from error
        except OSError as error:
            raise GenerationFailed(f"the generator's call could not start: {error}") from error
        try:
            result = _result(done.stdout)
        except GenerationFailed:
            if done.returncode != 0:
                raise GenerationFailed(f"the generator's call exited {done.returncode}: "
                                       f"{done.stderr.strip()[-300:]!r}") from None
            raise
        if result.get("is_error") is not False:
            raise GenerationFailed(f"the generator's call failed ({result.get('subtype', 'no subtype')}): "
                                   f"{str(result.get('result', ''))[:300]!r}")
        if "structured_output" not in result or result["structured_output"] is None:
            raise GenerationFailed("the generator's result has no structured output")
        return Reply(data=result["structured_output"], model_used=model_used(result), raw=done.stdout)
