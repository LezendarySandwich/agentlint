"""``python -m agentlint.runner <job_id>``: the worker the UI launches to run or resume one test job
(``runner/worker.py``). Internal — not a command a person types; the composition root builds the job's harness."""

from __future__ import annotations

import sys

from agentlint.cli import run_worker

if __name__ == "__main__":
    sys.exit(run_worker(sys.argv[1:]))
