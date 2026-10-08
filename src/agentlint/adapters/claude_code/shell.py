"""The programs a shell command runs, in order: what a Bash call is compared by in the with/without comparison (decided
with the user 2026-10-05: ``ls; grep … | head`` is ls, grep, head; arguments and wording ignored).

A small reader of the shell's grammar, not a shell: it splits on the operators that start a new command (``;`` ``&``
``&&`` ``||`` ``|`` ``|&``, newlines, and the parentheses of a subshell) and takes each command's first word, skipping
``NAME=value`` assignments, redirections (``2>/dev/null``, ``>x``, ``<x``, ``2>&1``, ``&>x``) and the shell's own
keywords (``for``, ``do``, ``if``, ``!``, ``time``, ``{`` …). A path becomes its last part (``/usr/bin/grep`` is grep).
The programs inside ``$(…)`` and backticks count, where they appear; a ``for x in …`` header runs nothing; a comment
and a heredoc's body are not commands; arithmetic (``$((…))``) runs nothing. ``case`` arms are skipped whole. A
command it can't read (an unclosed quote or parenthesis) is its first word alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=")
_STARTS = frozenset({"do", "then", "else", "elif", "while", "until", "if", "!", "time", "{", "exec"})  # the next word runs
_ENDS = frozenset({"done", "fi", "}"})  # a compound command closes: what follows is an operator, or its redirections
_HEADERS = frozenset({"for", "select"})  # their words up to the next separator run nothing
_SEPARATORS = frozenset({";", "&", "&&", "||", "|", "|&", "\n", ";;", ";&", ";;&"})


class _Unreadable(ValueError):
    """An unclosed quote, substitution or parenthesis."""


@dataclass(frozen=True)
class _Token:
    kind: str  # "word", "op", "sub" (a command substitution's text) or "redirect"
    text: str


def programs(command: str) -> tuple[str, ...]:
    """The programs ``command`` runs, in the order they appear, repeats kept; () for an empty command."""
    try:
        return tuple(_programs(_tokens(command)))
    except _Unreadable:
        words = command.replace("(", " ").replace("{", " ").split()
        return (_basename(words[0]),) if words else ()


def _basename(word: str) -> str:
    return word.rstrip("/").rsplit("/", 1)[-1] or word


def _programs(tokens: list[_Token]) -> list[str]:
    found: list[str] = []
    start = True  # the next word is a command's first
    header = False  # inside a for/select header: its words run nothing
    case = 0  # inside a case statement, skipped whole
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if token.kind == "sub":
            found.extend(programs(token.text))
            continue
        if token.kind == "redirect":
            continue
        if token.kind == "op":
            if case:
                continue
            if token.text in _SEPARATORS or token.text == "(":
                start, header = True, False
            elif token.text == ")":
                start = False
            continue
        word = token.text
        if case:
            if word == "case":
                case += 1
            elif word == "esac":
                case -= 1
                start = False
            continue
        if header:
            if word == "do":  # `for x in a b do` on one line, without the semicolon
                header, start = False, True
            continue
        if not start:
            continue
        if word in _HEADERS:
            header = True
        elif word == "case":
            case = 1
        elif word in _STARTS:
            pass  # still at a command's start
        elif word in _ENDS:
            start = False
        elif _ASSIGNMENT.match(word):
            pass  # an assignment before the command (or alone): the next word may be the program
        else:
            # A command word that is only a substitution (`$(which python) x.py`) ran the substitution's programs,
            # counted where it was read; its arguments are never programs.
            if word and not word.startswith(("$(", "`")):
                found.append(_basename(word))
            start = False
    return found


def _tokens(text: str) -> list[_Token]:
    """The command split into words, operators, redirections and command substitutions (whose text is read again on
    its own). Raises ``_Unreadable`` for an unclosed quote, substitution or parenthesis."""
    tokens: list[_Token] = []
    word: list[str] = []
    in_word = False  # a word has begun (quotes can make an empty one)
    heredocs: list[tuple[str, bool]] = []  # delimiters waiting for the end of this line: (word, strip leading tabs)
    i, n = 0, len(text)

    def end_word() -> None:
        nonlocal word, in_word
        if in_word:
            tokens.append(_Token("word", "".join(word)))
        word, in_word = [], False

    def substitution(close: str, j: int) -> tuple[str, int]:
        """The text of $( … ) from j (just after the opening), and the index after its close; quotes inside respected."""
        depth, k = 1, j
        while k < n:
            c = text[k]
            if c == "\\":
                k += 2
                continue
            if c in "'\"":
                end = text.find(c, k + 1) if c == "'" else _close_double(text, k + 1)
                if end < 0:
                    raise _Unreadable(text)
                k = end + 1
                continue
            if close == ")" and c == "(":
                depth += 1
            elif c == close:
                depth -= 1
                if depth == 0:
                    return text[j:k], k + 1
            k += 1
        raise _Unreadable(text)

    while i < n:
        c = text[i]
        if c == "\\":
            if i + 1 < n and text[i + 1] == "\n":  # a line continued
                i += 2
                continue
            if i + 1 < n:
                word.append(text[i + 1])
                in_word = True
            i += 2
            continue
        if c == "\n":
            end_word()
            tokens.append(_Token("op", "\n"))
            i += 1
            if heredocs:
                i = _skip_heredocs(text, i, heredocs)
                heredocs = []
            continue
        if c in " \t\r":
            end_word()
            i += 1
            continue
        if c == "#" and not in_word:
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "'":
            end = text.find("'", i + 1)
            if end < 0:
                raise _Unreadable(text)
            word.append(text[i + 1:end])
            in_word = True
            i = end + 1
            continue
        if c == '"':
            end = _close_double(text, i + 1)
            if end < 0:
                raise _Unreadable(text)
            inner = text[i + 1:end]
            for sub in _substitutions(inner):
                tokens.append(_Token("sub", sub))
            word.append(inner)
            in_word = True
            i = end + 1
            continue
        if c == "$" and text.startswith("$((", i):
            _, i = substitution(")", i + 2)  # arithmetic: no command in it
            if i < n and text[i] == ")":
                i += 1
            word.append("$((…))")
            in_word = True
            continue
        if c == "$" and text.startswith("$(", i):
            inner, i = substitution(")", i + 2)
            tokens.append(_Token("sub", inner))
            in_word = True
            continue
        if c == "`":
            end = text.find("`", i + 1)
            if end < 0:
                raise _Unreadable(text)
            tokens.append(_Token("sub", text[i + 1:end]))
            in_word = True
            i = end + 1
            continue
        if c in "<>" or (c == "&" and text.startswith("&>", i)):
            fd = "".join(word)
            if in_word and fd.isdigit():  # `2>`: the digits are the redirection's, not a word
                word, in_word = [], False
            else:
                end_word()
            j = i
            while j < n and text[j] in "<>&|-":
                j += 1
            op = text[i:j]
            i = j
            if op.endswith("&") or (op.endswith("-") and "&" in op):  # `2>&1`, `>&-`: a descriptor follows, no file
                while i < n and (text[i].isdigit() or text[i] == "-"):
                    i += 1
                tokens.append(_Token("redirect", op))
                continue
            while i < n and text[i] in " \t":
                i += 1
            target: list[str] = []
            while i < n and text[i] not in " \t\n;&|()<>":
                if text[i] in "'\"":
                    end = text.find(text[i], i + 1) if text[i] == "'" else _close_double(text, i + 1)
                    if end < 0:
                        raise _Unreadable(text)
                    target.append(text[i + 1:end])
                    i = end + 1
                    continue
                target.append(text[i])
                i += 1
            if op.startswith("<<") and not op.startswith("<<<"):
                delimiter = "".join(target)
                heredocs.append((delimiter, op.startswith("<<-")))
            tokens.append(_Token("redirect", op + "".join(target)))
            continue
        if c in ";&|()":
            end_word()
            for op in (";;&", ";;", ";&", "&&", "||", "|&"):
                if text.startswith(op, i):
                    tokens.append(_Token("op", op))
                    i += len(op)
                    break
            else:
                tokens.append(_Token("op", c))
                i += 1
            continue
        word.append(c)
        in_word = True
        i += 1
    end_word()
    opened = sum(1 for t in tokens if t.kind == "op" and t.text == "(")
    closed = sum(1 for t in tokens if t.kind == "op" and t.text == ")")
    if opened > closed:  # an unclosed subshell; a case pattern's `a)` closes nothing it opened
        raise _Unreadable(text)
    return tokens


def _close_double(text: str, j: int) -> int:
    """The index of the double quote closing one opened just before j, or -1."""
    while j < len(text):
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == '"':
            return j
        j += 1
    return -1


def _substitutions(inner: str) -> list[str]:
    """The command substitutions inside a double-quoted string's text, in order."""
    found = []
    try:
        for token in _tokens_of_quoted(inner):
            found.append(token)
    except _Unreadable:
        pass
    return found


def _tokens_of_quoted(inner: str) -> list[str]:
    found, i, n = [], 0, len(inner)
    while i < n:
        if inner[i] == "\\":
            i += 2
            continue
        if inner.startswith("$((", i):
            close = inner.find("))", i + 3)
            if close < 0:
                raise _Unreadable(inner)
            i = close + 2
            continue
        if inner.startswith("$(", i):
            depth, k = 1, i + 2
            while k < n and depth:
                if inner[k] == "(":
                    depth += 1
                elif inner[k] == ")":
                    depth -= 1
                k += 1
            if depth:
                raise _Unreadable(inner)
            found.append(inner[i + 2:k - 1])
            i = k
            continue
        if inner[i] == "`":
            end = inner.find("`", i + 1)
            if end < 0:
                raise _Unreadable(inner)
            found.append(inner[i + 1:end])
            i = end + 1
            continue
        i += 1
    return found


def _skip_heredocs(text: str, i: int, heredocs: list[tuple[str, bool]]) -> int:
    """From the line after a heredoc's marker, past each body to its delimiter's line; the index after it."""
    n = len(text)
    for delimiter, tabs in heredocs:
        while i < n:
            end = text.find("\n", i)
            line = text[i:] if end < 0 else text[i:end]
            i = n if end < 0 else end + 1
            if (line.lstrip("\t") if tabs else line) == delimiter:
                break
    return i
