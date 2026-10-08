"""The generator interface and its scripted fake: what core may assume about any generator."""

from __future__ import annotations

import pytest

from agentlint.probes.fake import Call, FakeGenerator
from agentlint.probes.generator import GenerationFailed, Generator, Reply
from agentlint.probes.validate import schema


def test_the_fake_answers_in_order_and_records_every_call() -> None:
    reply = Reply({"situations": []}, "scripted", '{"situations": []}')
    fake = FakeGenerator([{"situations": [{"text": "Find the billing calls."}]}, reply])
    generator: Generator = fake
    first = generator.generate("prompt one", schema(), "cheap")
    assert first.data == {"situations": [{"text": "Find the billing calls."}]}
    assert first.model_used == "fake-model"
    assert first.raw == '{"situations": [{"text": "Find the billing calls."}]}'
    assert generator.generate("prompt two", schema(), "cheap") is reply
    assert fake.calls == [Call("prompt one", schema(), "cheap"), Call("prompt two", schema(), "cheap")]
    assert fake.remaining == 0


def test_a_scripted_exception_is_raised_and_an_empty_script_fails_rather_than_answering() -> None:
    fake = FakeGenerator([GenerationFailed("model unavailable")])
    with pytest.raises(GenerationFailed, match="unavailable"):
        fake.generate("p", schema(), "cheap")
    with pytest.raises(GenerationFailed, match="no answer left for call 2"):
        fake.generate("p", schema(), "cheap")
    assert len(fake.calls) == 2


def test_malformed_and_unreported_replies_pass_through_unchecked() -> None:
    fake = FakeGenerator(["not an object", None], model_used=None)
    assert fake.generate("p", schema(), "m").data == "not an object"
    reply = fake.generate("p", schema(), "m")
    assert reply.data is None and reply.raw == "null" and reply.model_used is None


def test_a_reply_never_claims_a_blank_model() -> None:
    with pytest.raises(ValueError):
        Reply({}, "  ", "{}")
