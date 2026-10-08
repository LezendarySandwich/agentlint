"""Re-measure TOKENS_PER_PIECE from local Claude Code sessions. Read-only.

Two independent measurements, both against the harness's exact usage numbers:

1. First turns of clean sessions: observed first-call input vs pieces of everything the session
   recorded delivering. This is config-like text (listings, tool schemas, hook output).
2. Tool-result deltas: between consecutive model calls whose only additions were tool results
   (plus delivered reminders), input grows by the previous reply's output tokens — thinking
   included, it stays in context within a tool loop — plus the added text.

Run:  uv run python scripts/calibrate_tokens.py
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from agentlint.adapters.claude_code.transcripts import main_transcripts, parse_session
from agentlint.config.tokens import TOKENS_PER_PIECE, count_pieces

PROJECTS = Path.home() / ".claude" / "projects"
NOT_SENT = {"hook_success", "hook_cancelled", "hook_non_blocking_error", "hook_system_message",
            "async_hook_response", "prompt_snapshot"}


def first_turn_ratios() -> list[float]:
    ratios = []
    for path in main_transcripts(PROJECTS, 1000):
        s = parse_session(path)
        if s.interrupted_start or not (s.first_turn_input and s.system_prompt and s.tool_definitions):
            continue
        files = {f.path: f.content for f in s.instruction_files}
        texts = [s.system_prompt, s.tool_definitions, s.skill_listing or "", s.agent_listing or "",
                 s.deferred_tools or "", *files.values(), *s.session_context,
                 *(h.text for h in s.hook_outputs), s.first_turn_user_text]
        ratios.append(s.first_turn_input / sum(count_pieces(t) for t in texts))
    return ratios


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, dict) and block.get("type") == "tool_result":
            parts.append(_text(block.get("content")))
    return "\n".join(parts)


def tool_result_ratios(min_chars: int = 400) -> list[float]:
    ratios = []
    for path in PROJECTS.glob("*/*.jsonl"):
        previous, texts, dirty = None, [], False
        for raw in path.open("rb"):
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if record.get("isSidechain"):
                continue
            kind = record.get("type")
            if kind == "attachment":
                attachment = record.get("attachment") or {}
                rendered = record.get("rendered")
                if rendered:
                    texts.append("".join(part.get("content", "") for part in rendered if isinstance(part, dict)))
                elif attachment.get("type") not in NOT_SENT:
                    dirty = True
            elif kind == "user":
                content = (record.get("message") or {}).get("content")
                if isinstance(content, list) and content and all(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                ):
                    texts.append(_text(content))
                else:
                    dirty = True
            elif kind == "assistant":
                message = record.get("message") or {}
                usage = message.get("usage") or {}
                total = sum(int(usage.get(k) or 0) for k in
                            ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
                if previous and message.get("id") != previous["id"]:
                    text = "\n".join(texts)
                    added = total - previous["output"]
                    if not dirty and len(text) >= min_chars and added > 0 and count_pieces(text):
                        ratios.append((total - previous["total"] - previous["output"]) / count_pieces(text))
                    texts, dirty = [], False
                if not previous or message.get("id") != previous["id"]:
                    previous = {"id": message.get("id"), "total": total, "output": 0}
                previous["output"] = int(usage.get("output_tokens") or 0)
    return [r for r in ratios if r > 0]


def summarise(name: str, ratios: list[float]) -> None:
    if len(ratios) < 2:
        print(f"{name}: not enough data ({len(ratios)})")
        return
    q1, _, q3 = statistics.quantiles(ratios, n=4)
    print(f"{name}: n={len(ratios)}  median {statistics.median(ratios):.3f}  IQR [{q1:.3f}, {q3:.3f}]")


if __name__ == "__main__":
    print(f"current TOKENS_PER_PIECE = {TOKENS_PER_PIECE}")
    summarise("first turns of clean sessions", first_turn_ratios())
    summarise("tool-result deltas           ", tool_result_ratios())
