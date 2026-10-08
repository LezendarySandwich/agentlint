"""A job's worker for the tests, run as a script (never imported): ``runner.worker.work`` with an adapter reading the
fake machine whose paths it is given, and the real Claude Code launcher — which finds the fake ``claude`` on PATH.
Arguments: <job id> <home> <managed settings> <install dir>. It is what ``python -m agentlint.runner`` does, with the
fake machine's adapter in place of this machine's."""

import os
import sys
from pathlib import Path

from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
from agentlint.adapters.claude_code.discovery import Paths
from agentlint.adapters.claude_code.launch import ClaudeCodeLauncher
from agentlint.runner import worker
from agentlint.store import connect

job_id, home, managed, install = sys.argv[1:5]
adapter = ClaudeCodeAdapter(Paths(Path(home), Path(managed), Path(install)))


def parts(harness):
    return (adapter, ClaudeCodeLauncher(grace_s=1)) if harness == "claude-code" else None


sys.exit(worker.work(job_id, connect=connect, parts=parts, environ=os.environ, wait=lambda seconds: None))
