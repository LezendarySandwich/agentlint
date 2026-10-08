"""A fake ``claude`` executable, put first on PATH by a test: it behaves as the real one was observed to (E1, E5).

It logs each call's argv and working directory, answers from a scripted list of replies, and writes a transcript under
``$HOME/.claude/projects/<slug of cwd>/<session id>.jsonl`` unless ``--no-session-persistence`` is given, when it only
makes that project folder with an empty ``memory/`` (E5). A reply is a dict:

- ``{"structured_output": ..., "models": [...]}`` — a success result, ``modelUsage`` keyed by the models
- ``{"error": "..."}`` — an error result (``is_error`` true), exit 1
- ``{"stdout": "...", "stderr": "...", "exit": n}`` — exactly that output
- ``{"stdout_hex": "fffe", "stderr_hex": "ff", "exit": n}`` — exactly those bytes, which need not be UTF-8
- ``{"sleep": s}`` — sleep, then answer the next reply in the script
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

SCRIPT = r'''
import json, os, re, sys, time, uuid
from pathlib import Path

argv = sys.argv[1:]
log = Path(os.environ["FAKE_CLAUDE_LOG"])
calls = log.read_text().splitlines() if log.exists() else []
with log.open("a") as out:
    out.write(json.dumps({"argv": argv, "cwd": os.getcwd()}) + "\n")
replies = json.loads(Path(os.environ["FAKE_CLAUDE_REPLIES"]).read_text())
reply = replies[len(calls)] if len(calls) < len(replies) else {"error": "the fake has no reply left"}
if "sleep" in reply:
    time.sleep(reply["sleep"])
    reply = replies[len(calls) + 1]
session = argv[argv.index("--session-id") + 1] if "--session-id" in argv else str(uuid.uuid4())
project = Path(os.environ["HOME"]) / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", os.getcwd())
if "--no-session-persistence" in argv:
    (project / "memory").mkdir(parents=True, exist_ok=True)
else:
    project.mkdir(parents=True, exist_ok=True)
    prompt = argv[argv.index("-p") + 1]
    (project / f"{session}.jsonl").write_text(json.dumps({"type": "user", "sessionId": session, "version": "2.1.285",
        "message": {"role": "user", "content": prompt}}) + "\n")
if "stdout_hex" in reply:
    sys.stdout.buffer.write(bytes.fromhex(reply["stdout_hex"]))
    sys.stderr.buffer.write(bytes.fromhex(reply.get("stderr_hex", "")))
    sys.exit(reply.get("exit", 0))
if "stdout" in reply:
    sys.stdout.write(reply["stdout"])
    sys.stderr.write(reply.get("stderr", ""))
    sys.exit(reply.get("exit", 0))
result = {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 3619, "num_turns": 1,
          "result": "", "session_id": session, "total_cost_usd": 0.01, "permission_denials": [], "stop_reason": "end_turn"}
if "error" in reply:
    result.update(subtype="error_during_execution", is_error=True, result=reply["error"])
    print(json.dumps(result))
    sys.exit(1)
result["structured_output"] = reply["structured_output"]
result["modelUsage"] = {m: {"inputTokens": 10, "outputTokens": 20} for m in reply.get("models", ["claude-haiku-4-5@20251001"])}
sys.stderr.write("Using AI Gateway (Vertex upstream)\n")
print(json.dumps(result))
'''


def install(directory: Path, replies: list[dict]) -> tuple[Path, Path]:
    """Write the fake ``claude`` into ``directory`` with its script of replies; (its bin directory, its call log)."""
    bin_dir = directory / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    executable = bin_dir / "claude"
    executable.write_text(f"#!{sys.executable}\n" + SCRIPT)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    (directory / "replies.json").write_text(json.dumps(replies))
    return bin_dir, directory / "calls.jsonl"


def calls(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def environment(monkeypatch, directory: Path, replies: list[dict]) -> Path:
    """Put the fake first on PATH, HOME at ``directory/home``; the call log."""
    bin_dir, log = install(directory, replies)
    (directory / "home").mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HOME", str(directory / "home"))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_REPLIES", str(directory / "replies.json"))
    return log
