"""``python -m agentlint.directives <read_id>``: the worker the UI launches to read instructions (``directives/worker.py``).
Internal — not a command a person types; the composition root builds the reading's generator."""

from __future__ import annotations

import sys

from agentlint.cli import run_read_worker

if __name__ == "__main__":
    sys.exit(run_read_worker(sys.argv[1:]))
