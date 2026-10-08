"""Which MCP tools act outside the machine, so that a test run is never offered them (D6 final, decided 2026-10-04;
docs/tech_impl/09-adapters.md "Running").

Test runs follow the org's guardrails as your own sessions do, and no permission mode is overridden: the guardrails'
PreToolUse hook answers "allow" for in-project writes and shell commands, and a hook's allow beats any mode (E8b). What
can be enforced is removing a whole tool — a whole-tool deny takes the tool out of what the model is offered, whatever
a hook would answer (E8c) — so every MCP tool that acts outside the machine (posts, books, sends, publishes, deletes…)
is removed from every run, the same in the baseline and the "without" variant.

Which ones is decided from each tool's own name — the part after ``mcp__<server>__`` — split into words
(``calendar_book_beta`` -> calendar, book, beta; ``getUser`` -> get, user):

- a word that is a verb that writes or acts (``ACTING``, with its simple inflections: ``books``, ``booked``,
  ``booking``) removes the tool, whatever else the name holds — verbs often used as nouns (run, log, review, change,
  store, record, drain) included, since a read word is as often a noun (``change_status``, ``store_info``,
  ``run_query``) and must not keep a tool whose verb acts;
- otherwise a read verb (``READING``, exact words only) keeps it;
- otherwise the name can't be told, and the tool is removed — leaning toward removing, since a read tool wrongly
  removed only makes both variants a little less like your sessions, while an acting tool wrongly kept could act for
  real.

Built-in tools are not classified here (the guardrails govern them). The names classified are the MCP tools the
configuration is known to offer: those recent sessions' deferred-tool listings (and tool definitions) recorded — a
server never seen in a session is not known, and so not removed (UNVERIFIED how often that happens; see 05-runner.md).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from agentlint import harness

MCP_PREFIX = "mcp__"

# Verbs that write or act. Any of them in a tool's name removes it.
ACTING = frozenset({
    # D6's list
    "book", "send", "post", "publish", "upsert", "delete", "create", "update", "cancel", "decline", "accept", "add",
    "write", "edit", "submit", "approve", "merge", "push", "kill", "revert", "toggle", "enable", "disable", "start",
    "stop", "abort", "schedule", "renew", "resolve",
    # verbs as often nouns in a read tool's name, counted as acting all the same: kept out, they lost to a read word
    # used as a noun (change_status, store_info, run_query were kept), and a read tool wrongly removed costs little
    "change", "store", "record", "run", "drain", "log", "review", "flush",
    # more of the same kind (file, page and link are left out: rarely the verb of a tool's name, and such a name is
    # removed anyway unless a read verb is in it)
    "reschedule", "remove", "put", "patch", "insert", "set", "save", "upload", "execute", "exec", "deploy", "land",
    "comment", "reply", "forward", "respond", "rsvp", "invite", "share", "move", "rename", "archive", "close",
    "assign", "unassign", "ban", "unban", "block", "unblock", "trigger", "restart", "rerun", "retry", "rollback",
    "modify", "apply", "release", "sync", "import", "emit", "notify", "mark", "react", "reject", "dismiss", "transfer",
    "pay", "purchase", "grant", "revoke", "mutate", "drop", "truncate", "clear", "reset", "purge", "destroy",
    "terminate", "suspend", "resume", "pause", "invoke", "escalate", "ack", "acknowledge", "subscribe", "unsubscribe",
    "follow", "unfollow", "upvote", "downvote", "vote", "claim", "lock", "unlock", "register", "unregister",
    "provision", "allocate", "reserve", "migrate", "rotate", "replace", "overwrite", "append", "attach", "detach",
    "connect", "disconnect", "install", "uninstall", "configure", "fork", "generate", "draft", "compose", "join",
    "leave", "kick", "mute", "unmute",
})
# Verbs (and the nouns tools are named for) that only read. Exact words only; an acting word wins over them.
READING = frozenset({"get", "list", "search", "read", "load", "info", "status", "query", "fetch", "show", "find",
                     "describe", "lookup", "view"})

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SEPARATORS = re.compile(r"[^A-Za-z0-9]+")


ToolVerdict = harness.ToolVerdict  # what the classifier says of one MCP tool: removed or kept, and why — the word that
# decided it, or that no word could ("can't tell"). Core's type: the onboarding screen and the Settings override read it.


def own_name(name: str) -> str | None:
    """An MCP tool's own name: ``mcp__<server>__<tool>`` -> ``<tool>``. None for anything that isn't an MCP tool."""
    if not name.startswith(MCP_PREFIX):
        return None
    server, _, tool = name[len(MCP_PREFIX):].partition("__")
    return tool if server and tool.strip("_") else None


def words(tool: str) -> list[str]:
    """A tool name's words, lowercased: split on anything but letters and digits, and between camelCase humps."""
    return [w.lower() for part in _SEPARATORS.split(tool) for w in _CAMEL.split(part) if w]


def _forms(word: str) -> set[str]:
    """A word and the verb it may be an inflection of: books, booked, booking -> book; schedules, scheduled,
    scheduling -> schedule; pushes -> push."""
    forms = {word}
    if word.endswith("s") and len(word) > 3:
        forms.add(word[:-1])
        if word.endswith("es"):
            forms.add(word[:-2])
    if word.endswith("ed") and len(word) > 4:
        forms.update({word[:-2], word[:-1]})
        if len(word) > 5 and word[-3] == word[-4]:  # stopped -> stop
            forms.add(word[:-3])
    if word.endswith("ing") and len(word) > 5:
        forms.update({word[:-3], word[:-3] + "e"})
        if len(word) > 6 and word[-4] == word[-5]:  # stopping -> stop
            forms.add(word[:-4])
    return forms


def verdict(name: str) -> ToolVerdict | None:
    """Whether the MCP tool ``name`` is removed from test runs, and why; None for a name that isn't an MCP tool."""
    tool = own_name(name)
    if tool is None:
        return None
    found = words(tool)
    for word in found:
        if acting := sorted(_forms(word) & ACTING):
            return ToolVerdict(name, True, f"acts: {acting[0]}")
    for word in found:
        if word in READING:
            return ToolVerdict(name, False, f"reads: {word}")
    return ToolVerdict(name, True, f"can't tell from {tool!r}: removed")


def verdicts(names: Iterable[str]) -> tuple[ToolVerdict, ...]:
    """The verdict on every MCP tool among ``names``, sorted by name, each once."""
    return tuple(v for v in (verdict(name) for name in sorted(set(names))) if v is not None)


def acting_tools(names: Iterable[str]) -> tuple[str, ...]:
    """The MCP tools among ``names`` that a test run is never offered: those that act, and those that can't be told."""
    return tuple(v.name for v in verdicts(names) if v.removed)
