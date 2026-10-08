"""A generator that answers from a script: for tests and for building the generation path without any model.

It lives in the package, not the test suite, so nothing outside pytest ever imports a test module to get one.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from agentlint.probes.generator import GenerationFailed, Reply


@dataclass(frozen=True)
class Call:
    prompt: str
    schema: Mapping[str, Any]
    model: str


class FakeGenerator:
    """Returns its scripted answers in order. An answer is a Reply (returned as is), an exception (raised), or any
    other value, which becomes a Reply whose data is that value and whose raw text is its JSON. Every call is
    recorded; a call after the script runs out raises GenerationFailed, so a test never loops on a fake."""

    def __init__(self, answers: Iterable[object] = (), model_used: str | None = "fake-model") -> None:
        self._answers = deque(answers)
        self._model_used = model_used
        self.calls: list[Call] = []

    def generate(self, prompt: str, schema: Mapping[str, Any], model: str) -> Reply:
        self.calls.append(Call(prompt, schema, model))
        if not self._answers:
            raise GenerationFailed(f"the fake generator has no answer left for call {len(self.calls)}")
        answer = self._answers.popleft()
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, Reply):
            return answer
        return Reply(data=answer, model_used=self._model_used, raw=json.dumps(answer))

    @property
    def remaining(self) -> int:
        return len(self._answers)
