"""A reading's worker for the tests, run as a script (never imported): ``directives.worker.work`` with a generator that
finds one directive wherever a section says "Read the notes", after FAKE_READ_SLEEP seconds a call (default 0).
Argument: <read id>. It is what ``python -m agentlint.directives`` does, with a scripted generator in place of claude."""

import os
import sys
import time
from types import SimpleNamespace

from agentlint.directives import worker
from agentlint.probes.generator import Reply
from agentlint.store import connect


class Scripted:
    def generate(self, prompt, schema, model):
        time.sleep(float(os.environ.get("FAKE_READ_SLEEP", "0")))
        section = prompt.split("---\n", 2)[1]
        found = [{"polarity": "must", "thing": "tool:Read", "quote": "Read the notes", "when": None}] \
            if "Read the notes" in section else []
        return Reply({"directives": found}, "fake-model", "{}")


parts = SimpleNamespace(adapter=SimpleNamespace(nested=lambda environ: None), generator=Scripted(), generator_model="haiku")
sys.exit(worker.work(sys.argv[1], connect=connect, parts=lambda harness: parts, environ=os.environ))
