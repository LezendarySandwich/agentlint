"""A fake ``claude`` executable for the executor's tests: run as a script (copied onto PATH as ``claude``), never
imported. It behaves as the real CLI was observed to on 2026-10-04 (E1, E5): ``-p <situation> --session-id <uuid>
--output-format json`` writes the run's transcript under ``$HOME/.claude/projects/<slug of the cwd>/<session id>.jsonl``
and prints one JSON result (``session_id``, ``is_error``, ``subtype``, ``num_turns``, ``duration_ms``,
``total_cost_usd``, ``permission_denials``, ``result``).

What each invocation does is taken, one entry per invocation, from the JSON list in ``$FAKE_CLAUDE_QUEUE`` ("ok" once
it is empty): ``ok``, ``server_error`` / ``rate_limit`` / ``auth`` (an API error recorded in the transcript),
``api:<code>[:<apiError>]`` (any API error record the 2.1.285 binary writes: its ``error`` and, when given, its
``apiError`` reason), ``gateway_auth`` (the gateway's upstream authentication failure, which the binary writes as
``invalid_request`` with no reason and a fixed text), ``refusal`` (an ``invalid_request`` refusing the request), ``error``
(a result with is_error), ``deny`` (a call refused, named in permission_denials), ``crash`` (a transcript, no result,
exit 1), ``no-transcript`` (exit 1, nothing written), ``sleep:<s>`` (the prompt written, then a pause, then ok),
``hang:<s>`` (a pause before anything is written), ``version:<v>`` and ``model:<m>`` (an ok run recording those,
whatever version was asked for), the version recorded otherwise being ``$CLAUDE_CODE_VERSION_OVERRIDE`` when set (as
the launcher runs it, E7), else ``$FAKE_CLAUDE_VERSION`` or 9.9.9,
``linger`` (an ok run that leaves a process behind holding its stdout, its pid in ``$FAKE_CLAUDE_LOG.linger``).
Every invocation appends one JSON line to ``$FAKE_CLAUDE_LOG``: its arguments, HOME, cwd, pid, the version override it
was given, the tools ``--disallowedTools`` named, what it did, and what the run's home and directory held.
"""

import json
import os
import re
import sys
import time
from pathlib import Path


def arg(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None


def disallowed(argv):
    """The tools ``--disallowedTools`` names: every word after it up to the next flag."""
    if "--disallowedTools" not in argv:
        return None
    rest = argv[argv.index("--disallowedTools") + 1:]
    return [w for w in rest[:next((i for i, w in enumerate(rest) if w.startswith("--")), len(rest))]]


def take():
    queue = os.environ.get("FAKE_CLAUDE_QUEUE")
    if not queue or not Path(queue).exists():
        return "ok"
    items = json.loads(Path(queue).read_text() or "[]")
    if not items:
        return "ok"
    Path(queue).write_text(json.dumps(items[1:]))
    return items[0]


def main():
    argv = sys.argv[1:]
    situation, session = arg(argv, "-p"), arg(argv, "--session-id")
    model = arg(argv, "--model") or "claude-opus-5-5"
    # The launcher runs the version CLAUDE_CODE_VERSION_OVERRIDE asks for (E7); unset, its default.
    version = os.environ.get("CLAUDE_CODE_VERSION_OVERRIDE") or os.environ.get("FAKE_CLAUDE_VERSION", "9.9.9")
    behaviour = take()
    home, cwd = Path(os.environ["HOME"]), os.getcwd()
    user_md = home / ".claude" / "CLAUDE.md"
    log = os.environ.get("FAKE_CLAUDE_LOG")
    if log:
        with open(log, "a") as out:
            out.write(json.dumps({"argv": argv, "home": str(home), "cwd": cwd, "pid": os.getpid(), "behaviour": behaviour,
                                  "version_override": os.environ.get("CLAUDE_CODE_VERSION_OVERRIDE"),
                                  "disallowed": disallowed(argv),
                                  "agents_md": os.path.exists(os.path.join(cwd, "AGENTS.md")),
                                  "user_md": user_md.read_text() if user_md.exists() else None}) + "\n")
            out.flush()
    if behaviour == "linger":
        import subprocess
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])  # inherits stdout
        Path(log + ".linger").write_text(str(child.pid))
    if behaviour.startswith("hang:"):
        time.sleep(float(behaviour.split(":", 1)[1]))
    if behaviour == "no-transcript":
        print("boom: could not start", file=sys.stderr)
        return 1
    if behaviour.startswith("version:"):
        version = behaviour.split(":", 1)[1]
    if behaviour.startswith("model:"):
        model = behaviour.split(":", 1)[1]
    path = home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", cwd) / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)

    def line(kind, **fields):
        return {"type": kind, "sessionId": session, "cwd": cwd, "version": version, "isSidechain": False,
                "entrypoint": "sdk-cli", "timestamp": "2026-10-04T12:00:00.000Z", "permissionMode": "default",
                **fields}

    def write(records):
        with open(path, "a") as out:
            for record in records:
                out.write(json.dumps(record) + "\n")

    write([{"type": "queue-operation", "sessionId": session},
           line("user", message={"role": "user", "content": situation})])
    if behaviour.startswith("sleep:"):
        time.sleep(float(behaviour.split(":", 1)[1]))
    api_error = {"server_error": ("server_error", None, "API Error: the response stopped arriving"),
                 "rate_limit": ("rate_limit", None, "API Error: Request rejected (429)"),
                 "auth": ("authentication_failed", None, "Please run /login"),
                 "gateway_auth": ("invalid_request", None, "Authentication error \u00b7 The gateway could not "
                                  "authenticate with its upstream provider \u2014 contact your gateway administrator"),
                 "refusal": ("invalid_request", None, "API Error: the request was refused")}
    if behaviour.startswith("api:"):
        code, _, reason = behaviour[4:].partition(":")
        api_error[behaviour] = (code, reason or None, f"API Error: {code}")
    denials = []
    if behaviour in api_error:
        code, reason, text = api_error[behaviour]
        record = line("assistant", isApiErrorMessage=True, error=code, message={
            "id": "msg_e", "role": "assistant", "model": "<synthetic>", "content": [{"type": "text", "text": text}]})
        if reason:
            record["apiError"] = reason
        write([record])
        result = {"is_error": True, "subtype": "success", "result": "API Error", "num_turns": 1}
    else:
        tool = {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {"command": "ls"}}
        answer = {"type": "tool_result", "tool_use_id": "tu_1", "content": "AGENTS.md", "is_error": False}
        if behaviour == "deny":
            answer = {**answer, "content": "Claude requested permissions to use Bash, but you haven't granted it yet.",
                      "is_error": True}
            denials = [{"tool_name": "Bash", "tool_use_id": "tu_1", "tool_input": {"command": "ls"}}]
        write([line("assistant", message={"id": "msg_1", "role": "assistant", "model": model, "content": [tool]}),
               line("user", message={"role": "user", "content": [answer]}),
               line("assistant", message={"id": "msg_2", "role": "assistant", "model": model,
                                          "content": [{"type": "text", "text": "Done."}]}),
               {"type": "cost-state", "sessionId": session, "totalCostUSD": 0.01, "totalDuration": 900,
                "totalAPIDuration": 400}])
        result = {"is_error": behaviour == "error", "subtype": "error_max_turns" if behaviour == "error" else "success",
                  "result": "Done.", "num_turns": 2}
    if behaviour == "crash":
        return 1
    print(json.dumps({"type": "result", "session_id": session, "duration_ms": 900, "total_cost_usd": 0.01,
                      "permission_denials": denials, **result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
