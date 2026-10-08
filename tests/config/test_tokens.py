"""Token estimates: a long text is counted once and remembered by its digest, never by the text itself."""

from __future__ import annotations

from agentlint.config import tokens


def test_a_long_text_is_counted_once_and_the_count_is_the_same(monkeypatch) -> None:
    text = "def f(x):\n    return x + 1  # a line of code\n" * 100
    fresh = round(tokens.count_pieces(text) * tokens.TOKENS_PER_PIECE)
    calls: list[int] = []
    counting = tokens.count_pieces
    monkeypatch.setattr(tokens, "_remembered", {})
    monkeypatch.setattr(tokens, "count_pieces", lambda t: calls.append(len(t)) or counting(t))
    assert tokens.estimate_tokens(text) == tokens.estimate_tokens(str(text[:-1] + text[-1])) == fresh
    assert calls == [len(text)]  # the second, an equal text in another object, was remembered
    assert all(isinstance(key, bytes) and len(key) == 16 for key in tokens._remembered)  # digests, not the texts
    tokens.estimate_tokens("short")
    tokens.estimate_tokens("short")
    assert calls == [len(text), 5, 5]  # a short text is cheaper to count than to remember


def test_what_is_remembered_stays_bounded(monkeypatch) -> None:
    monkeypatch.setattr(tokens, "_remembered", {})
    monkeypatch.setattr(tokens, "REMEMBERED", 3)
    for n in range(5):
        tokens.estimate_tokens(f"{n} " + "word " * tokens.REMEMBERED_FROM)
    assert len(tokens._remembered) == 3
