"""Catching a situation that names what it tests (04 "Contamination").

A situation that says "use search_files to find X" gets search_files used whether or not the rule exists, so the
comparison it feeds measures nothing and reads as a confident "no effect". The prompt asks the model not to do
this; this module checks it in code. It is code checking a generated input, never a model judging an output
(AGENTS.md non-negotiable 2). Whether a hit drops the situation or only marks it in review is decided elsewhere
(D16); both use what this finds.

From the target's text and the configuration's entities, ``guard`` works out what a situation must not contain:

- every name of the target's own entities (the skill or agent whose definition is the target), whatever its shape;
- the names of an entity the target's text names: its full name and aliases, and its short name when that is more
  than an ordinary word (sql-query, but not status: a span `status` doesn't name pkg:status, and naming pkg:status
  doesn't forbid the word status). A distinctive name (below) names its entity in a fenced block too, since a skill
  shows its tool calls there; any other name only in prose or a code span;
- what the target's text marks as code (`like this`, outside fenced blocks) when it is a command, path, flag or
  identifier: one token that is more than an ordinary word, a span holding a symbol, digit or internal capital, or a
  span of plain words whose first word the text runs as a program (`sl status`, but not `depends on`); and
  snake_case identifiers in its prose (a fenced block is a whole program, not a list of names);
- and any run of ``PHRASE_WORDS`` consecutive words copied from the target's text, at least half of them more than
  function words.

Names are matched by their shape, so ordinary words don't misfire. A name holding anything but letters, or a capital
inside it (search_files, sql-query, docs/03-checklist.md, WebFetch), matches whatever its case, with - and _
interchangeable. A capitalised word (Read, Edit) matches only in that case and not where a sentence starts, where a
capital says nothing: the text's start, after . ! ? or a colon, a line's start, or a list item's first word after
1. 1) (1) a) or a bullet (in a code span a capital always counts). A plain lowercase word (git, debug) matches whatever
its case, but the target's text never names an entity by one, even in a code span: on this machine `run` and `loop`
are skill names and also everyday words and subcommands, and a guard that forbade "run" would drop most situations.
A lowercase hyphenated compound that is a real English word (general-purpose, deep-dive: the short list
``_ENGLISH_COMPOUNDS``) written plainly in prose where an English compound stands, after a determiner ("do a
deep-dive") or right before another content word ("a general-purpose retry helper"), is the word, not the name, in
the target's text and in the situation alike; in code, after / @ or =, between quotes, after a verb that invokes
(use, run, invoke, call, spawn, ask) or before a word that says what it is (skill, agent, subagent, tool, command,
plugin) it is the name. Any other compound (ci-autopilot, skill-creator) is always the name, and the target's own
entities' names are never read as words, whatever their shape.

Known limits: a target's own entity with an ordinary-word name (a skill called debug), or with an English compound for
a name (an agent called general-purpose), makes every situation that uses the word a hit; that is the name the agent
would see, so the hit stands and review shows it. A compound not on the list used as an English word ("write a
data-viz of signups", when the target's text names data-viz) is a hit too. A listed one used as a name with neither
cue ("have general-purpose look into it") is missed, and the copied-words check and review remain; one before a role
word in its English sense ("a general-purpose tool") reads as the name.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from functools import cache

from agentlint.config.entity import Entity
from agentlint.config.markdown import blank_code
from agentlint.rank.plan import AblationTarget

PHRASE_WORDS = 6
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_SNAKE = re.compile(r"(?<![\w-])[A-Za-z][A-Za-z0-9]*(?:_+[A-Za-z0-9]+)+(?![\w-])")
_WORD = re.compile(r"\w+")
_MAX_CODE_WORDS = 6  # a longer code span is a command line, left to the verbatim-phrase check
_SPANS_FOR_PROGRAM = 3
_FUNCTION_WORDS = frozenset("""
a an the and or but nor so if then than that this these those it its is are was were be been being am do does did
to of in on at by for with from into onto as about over under up down out off not no yes all any each every some
i you he she we they me him her us them my your our their mine yours what which who whom whose when where why how
can could will would shall should may might must have has had just also only very too more most such own same
there here s t don doesn isn aren
""".split())


class TermKind(Enum):
    OWN_ENTITY = "the target's own entity"
    NAMED_ENTITY = "an entity the target's text names"
    CODE = "what the target's text marks as code"
    IDENTIFIER = "an identifier in the target's prose"


@dataclass(frozen=True)
class Term:
    text: str  # the name as the configuration or the target's text spells it
    kind: TermKind
    entity_id: str | None = None  # for the two entity kinds


@dataclass(frozen=True)
class Hit:
    text: str  # as the situation spells it
    start: int  # offsets into the situation
    end: int
    term: Term | None  # None: a run of words copied from the target's text

    @property
    def copied(self) -> bool:
        return self.term is None


def _distinctive(name: str) -> bool:
    return bool(re.search(r"[^A-Za-z]|[a-z][A-Z]", name))


def _plain_lowercase(name: str) -> bool:
    return name.isalpha() and name.islower()


# A list item's marker: 1. 1) (1) a) (a), at a line's start or after a sentence's end, or a number after a space.
_LIST_MARKER = re.compile(r"(?:(?:^|\n|[.!?:;]\s)[ \t]*\(?[A-Za-z]|(?:^|\s)\(?\d{1,3})[.)]$")


def _sentence_start(text: str, offset: int) -> bool:
    """Where a capital says nothing: the text's start, after a sentence's end or a colon, at a line's start, or as a
    list item's first word (after 1. 1) a) or a bullet)."""
    before = text[:offset].rstrip(" \t\"'(*_>#`-")
    return not before or before[-1] in ".!?:\n" or _LIST_MARKER.search(before) is not None


# Lowercase words joined by single hyphens: in prose, as likely an English compound (general-purpose, deep-dive) as a
# name. A capital at a sentence start says nothing, so General-purpose there is the same.
_COMPOUND = re.compile(r"[a-z]+(?:-[a-z]+)+")
_QUOTES = "\"'\u2018\u2019\u201c\u201d"
_INVOKES = "/@="  # /deep-dive, @general-purpose, subagent_type=general-purpose
_DETERMINERS = frozenset("""
a an the this that these those my our your their his her its some any each every no another such one more most less
very
""".split())
# Real English compounds that are also plausible names: only these can be read as the word in prose.
_ENGLISH_COMPOUNDS = frozenset("""
general-purpose deep-dive follow-up built-in read-only well-known up-to-date end-to-end step-by-step one-off
long-running high-level low-level real-time open-source dry-run sign-off check-in double-check hands-on code-review
""".split())
_ROLE_WORDS = frozenset("""
skill skills agent agents subagent subagents tool tools command commands plugin plugins
""".split())  # "the ci-autopilot skill", "a general-purpose agent": the name of a thing of that kind
_INVOKING_VERBS = frozenset("""
use uses used using run runs ran running invoke invokes invoked invoking call calls called calling spawn spawns
spawned spawning ask asks asked asking
""".split())  # "run ci-autopilot loop", "ask general-purpose": the name of what is invoked
_WORD_BEFORE = re.compile(r"([A-Za-z]+)[ \t]+$")
_WORD_AFTER = re.compile(r"[ \t]+([A-Za-z]+)\b")


def _as_english(text: str, match: re.Match[str], prose: str) -> bool:
    """Whether a lowercase hyphenated compound stands where an English compound does, so it is read as the word,
    not the name: a real English compound (``_ENGLISH_COMPOUNDS``), written plainly in prose (not in code, not after
    / @ or =, not between quotes), not after a verb that invokes nor before a role word, and either after a
    determiner ("do a deep-dive") or right before another content word ("general-purpose retry helper")."""
    start, end = match.span()
    word = match.group(0)
    if not (_COMPOUND.fullmatch(word) or (_COMPOUND.fullmatch(word[0].lower() + word[1:])
                                          and _sentence_start(text, start))):
        return False
    if word.casefold() not in _ENGLISH_COMPOUNDS:
        return False
    if prose[start] != text[start]:
        return False  # in a code span or fenced block
    before, after = text[start - 1:start], text[end:end + 1]
    if before and (before in _INVOKES or (before in _QUOTES and after and after in _QUOTES)):
        return False
    previous = _WORD_BEFORE.search(text[:start])
    following = _WORD_AFTER.match(text, end)
    previous_word = previous.group(1).casefold() if previous else ""
    following_word = following.group(1).casefold() if following else ""
    if previous_word in _INVOKING_VERBS or following_word in _ROLE_WORDS:
        return False
    return previous_word in _DETERMINERS or bool(following_word and following_word not in _FUNCTION_WORDS)


class _Matcher:
    """How one name is found in a text, by its shape (the module docstring's rules)."""

    def __init__(self, name: str) -> None:
        body = "".join(r"[-_]" if c in "-_" else r"\s+" if c.isspace() else re.escape(c) for c in name.strip())
        self.case_sensitive = not _distinctive(name) and not _plain_lowercase(name)
        self.capitalised = self.case_sensitive and name[:1].isupper() and name[1:].islower()
        flags = 0 if self.case_sensitive else re.IGNORECASE
        self.pattern = re.compile(rf"(?<![\w-]){body}(?![\w-])", flags)
        self.hyphenated = bool(re.fullmatch(r"[A-Za-z]+(?:[-_][A-Za-z]+)+", name.strip()))

    def find(self, text: str, *, code: bool = False, prose: str | None = None,
             english: bool = True) -> Iterable[re.Match[str]]:
        """In code (``code``: the text is a span), every match counts. Otherwise a capital says nothing where a
        sentence starts, and a lowercase hyphenated compound used as an English word says nothing (``_as_english``;
        ``prose`` is the text with its code blanked, worked out here when not given), unless ``english`` is off: a
        target's own entity's name is never read as a word."""
        for match in self.pattern.finditer(text):
            if code:
                yield match
            elif self.capitalised and _sentence_start(text, match.start()):
                continue
            elif english and "-" in match.group(0) and self.hyphenated:
                prose = blank_code(text) if prose is None else prose
                if not _as_english(text, match, prose):
                    yield match
            else:
                yield match


@cache
def _matcher(name: str) -> _Matcher:
    """One matcher per name for the whole process: a plan's targets all check the same entities' names."""
    return _Matcher(name)


def _folded(text: str) -> str:
    return text.casefold().replace("_", "-")


def _key(name: str) -> str:
    """Two spellings of one name, as far as matching goes, share a key."""
    return "=" + name if _matcher(name).case_sensitive else _folded(" ".join(name.split()))


def _names(entity: Entity) -> list[str]:
    """Every name it goes by: what its own definition forbids. An alias that is one ordinary word (a server's tool
    called search) is left out: nobody sees it alone, and as a word it is everywhere."""
    aliases = (a for a in entity.aliases if not _ordinary(a))
    return list(dict.fromkeys(n for n in (entity.name, entity.short_name, *aliases) if n.strip()))


def _naming(entity: Entity) -> list[str]:
    """The names that say this entity and no namesake: what naming it means, and what naming it forbids."""
    return [n for n in _names(entity) if n != entity.short_name or n == entity.name or not _ordinary(n)]


def _ordinary(word: str) -> bool:
    """A single word of letters with no capital inside it: Read, status, debug."""
    return word.isalpha() and not re.search(r"[a-z][A-Z]", word)


class Guard:
    """What one target's situations must not contain, worked out once, then checked against each situation."""

    def __init__(self, text: str, terms: Iterable[Term], phrase_words: int = PHRASE_WORDS) -> None:
        if phrase_words < 2:
            raise ValueError("phrase_words must be at least 2")
        found: dict[str, Term] = {}
        for term in terms:
            found.setdefault(_key(term.text), term)  # the first reason given for a name is the one kept
        self.terms = tuple(found.values())
        self._matchers = [(_matcher(t.text), t) for t in self.terms]
        self._phrase_words = phrase_words
        words = [w.casefold() for w in _WORD.findall(text)]
        self._runs = {tuple(words[i:i + phrase_words]) for i in range(len(words) - phrase_words + 1)}

    def hits(self, situation: str) -> tuple[Hit, ...]:
        """Every forbidden name in the situation and every copied run of words, in order; a hit inside a longer
        one (sql-query inside data:sql-query) is left out."""
        prose = blank_code(situation)
        found = [Hit(m.group(0), m.start(), m.end(), term)
                 for matcher, term in self._matchers
                 for m in matcher.find(situation, prose=prose, english=term.kind is not TermKind.OWN_ENTITY)]
        found += self._copied(situation)
        found.sort(key=lambda h: (h.start, -h.end))
        kept: list[Hit] = []
        for hit in found:
            if not any(k.start <= hit.start and hit.end <= k.end for k in kept):
                kept.append(hit)
        return tuple(kept)

    def contaminated(self, situation: str) -> bool:
        return bool(self.hits(situation))

    def _copied(self, situation: str) -> list[Hit]:
        tokens = list(_WORD.finditer(situation))
        words = [t.group(0).casefold() for t in tokens]
        n = self._phrase_words
        covered = [False] * len(tokens)
        for i in range(len(tokens) - n + 1):
            window = tuple(words[i:i + n])
            if window in self._runs and sum(w not in _FUNCTION_WORDS for w in window) * 2 >= n:
                covered[i:i + n] = [True] * n
        hits, i = [], 0
        while i < len(tokens):
            if not covered[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(tokens) and covered[j + 1]:
                j += 1
            start, end = tokens[i].start(), tokens[j].end()
            hits.append(Hit(situation[start:end], start, end, None))
            i = j + 1
        return hits


def guard(text: str, entities: Iterable[Entity], own: Iterable[Entity] = (),
          phrase_words: int = PHRASE_WORDS) -> Guard:
    """The guard for a target whose text is ``text``. ``entities`` are the configuration's (its tools included);
    ``own`` are the entities the target itself defines, forbidden even where its text never names them."""
    terms: list[Term] = []
    for entity in own:
        terms += [Term(name, TermKind.OWN_ENTITY, entity.id) for name in _names(entity)]
    without_fences = blank_code(text, spans=False)
    spans = [m.group(1).strip() for m in _CODE_SPAN.finditer(without_fences)]
    prose = blank_code(text)
    folded = _folded(text)
    for entity in entities:
        names = _naming(entity)
        if any(_named(name, folded, text, prose, spans) for name in names):
            terms += [Term(name, TermKind.NAMED_ENTITY, entity.id) for name in names]
    programs = _programs(text, without_fences, spans)
    terms += [Term(span, TermKind.CODE) for span in spans if _code_term(span, programs)]
    terms += [Term(m.group(0), TermKind.IDENTIFIER) for m in _SNAKE.finditer(prose)]
    return Guard(text, terms, phrase_words)


def guard_for(target: AblationTarget, entities: Iterable[Entity], phrase_words: int = PHRASE_WORDS) -> Guard:
    """The guard for a plan target: its rules' text, and the entities its source provides as its own."""
    entities = tuple(entities)
    text = "\n\n".join(rule.text for rule in target.rules)
    own = [e for e in entities if e.provided_by == target.source.id]
    return guard(text, entities, own, phrase_words)


def _named(name: str, folded: str, text: str, prose: str, spans: list[str]) -> bool:
    """Whether the target's text names this: a distinctive name anywhere, fenced blocks included (a skill shows its
    tool calls in them); any other name in a code span or in prose, never in a fenced block, and never when it is a
    plain lowercase word. ``folded`` is the whole text case-folded, - for _: a name not in it can't match, which
    skips the regex."""
    if not any(c.isspace() for c in name.strip()) and _folded(name.strip()) not in folded:
        return False
    if _plain_lowercase(name):  # `run` is as likely a subcommand as the skill called run
        return False
    matcher = _matcher(name)
    if _distinctive(name):  # mcp__x__search_files, sql-query, WebFetch: no word of prose or code looks like it
        return next(iter(matcher.find(text, prose=prose)), None) is not None  # general-purpose as a word: no
    if any(next(iter(matcher.find(span, code=True)), None) for span in spans):
        return True
    return next(iter(matcher.find(prose)), None) is not None


def _code_term(span: str, programs: frozenset[str]) -> bool:
    """A command, path, flag or identifier: not a lone ordinary word (`true`, `name`), nor a whole command line. A span
    of several plain words is a command only when its first word is a program the text runs elsewhere (`sl status`
    beside `sl commit`); otherwise it is prose in backticks (`depends on`, `limit reached`)."""
    words = span.split()
    if not any(c.isalpha() for c in span):
        return False
    if len(words) == 1:
        return len(span) >= 3 and not _ordinary(span)
    if len(words) > _MAX_CODE_WORDS:
        return False
    return not all(_ordinary(w) for w in words) or words[0] in programs


_PROMPT = re.compile(r"^[ \t]*\$[ \t]+([a-z][\w.-]*)[ \t]+\S", re.MULTILINE)
_COMMAND = re.compile(r"^[ \t]*([a-z][\w.-]*)[ \t]+\S", re.MULTILINE)
_SHELL_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*(?:bash|sh|shell|zsh|console|terminal)\b[^\n]*\n(.*?)^[ \t]*\1",
                          re.MULTILINE | re.DOTALL)


def _programs(text: str, without_fences: str, spans: list[str]) -> frozenset[str]:
    """Words the text runs as programs, written in lowercase as commands are (`Depends on D123` is a commit trailer,
    not a program): the first word of a line in a shell fenced block, or after a $ prompt in any fenced block (a line
    of Python or Hack starts with `return` or `class`), or the first word of at least ``_SPANS_FOR_PROGRAM``
    different multi-word code spans (`sl status`, `sl commit`, `sl amend`; but `next friday` and `next monday` say
    nothing); never a function word (a shell line can start with for or if, a span with no)."""
    fenced = "\n".join(line for line, kept in zip(text.split("\n"), without_fences.split("\n")) if line != kept)
    found = {m.group(1) for m in _PROMPT.finditer(fenced)}
    found |= {m.group(1) for block in _SHELL_FENCE.finditer(text) for m in _COMMAND.finditer(block.group(2))}
    starts = Counter(span.split()[0] for span in set(spans) if len(span.split()) > 1 and span[:1].islower())
    found |= {w for w, n in starts.items() if n >= _SPANS_FOR_PROGRAM}
    return frozenset(found - _FUNCTION_WORDS)
