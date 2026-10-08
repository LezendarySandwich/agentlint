"""An Executor that runs nothing: each run returns at once, or after a delay, with an outcome a function chooses.

For tests and for exercising the engine without a harness — never a stand-in for a real run's result. ``log``, when
given, gets one line per launch, written and flushed before the run "starts", so a process watching it knows a key
is in flight (the kill-and-resume test, which SIGKILLs a runner mid-run).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from agentlint.runner.engine import Retry, RunRequest, RunResult


class FakeExecutor:
    def __init__(self, outcome: Callable[[RunRequest], str] = lambda request: "ok", *, delay_s: float = 0.0,
                 log: Path | None = None, retry: Callable[[RunRequest, int], bool] = lambda request, attempt: False,
                 harness_version: str = "fake/1", model: str = "fake-model") -> None:
        self.outcome, self.delay_s, self.log, self.retry = outcome, delay_s, log, retry
        self.harness_version, self.model = harness_version, model
        self.launched: list[str] = []  # every key launched, in order, retries included

    def run(self, request: RunRequest) -> RunResult:
        key = request.run.key
        self.launched.append(key)
        if self.log is not None:
            with self.log.open("a") as out:
                out.write(key + "\n")
                out.flush()
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.retry(request, sum(k == key for k in self.launched)):
            raise Retry(f"fake infrastructure failure on {key}")
        return RunResult(self.outcome(request), [{"fake": key}], session_id=f"fake-{request.job_id}-{key}",
                         harness_version=self.harness_version, model=self.model, cost_usd=None)
