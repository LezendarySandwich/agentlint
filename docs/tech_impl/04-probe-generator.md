# 04 — Probe generator

Writes the situations we test with. This is one of exactly two places AI is used, and it is used
**generatively** — it writes inputs. It never evaluates anything (AGENTS.md non-negotiable 2). Every check on what
it writes is code.

A **situation** is a request a developer might type. A **target** is a line of the ranker's plan, an
`AblationTarget` (`03-ranker.md`): one rule, or a whole skill or hook no variant can take apart. The generator
writes situations for a target.

## Status

Built 2026-10-04, against a fake generator and a fake `claude` (no model has been called by the code; the call
itself was run by hand, E5 in `02-design.md`'s Verified Facts):

| Piece | Where | What it does |
|---|---|---|
| Generator interface | `src/agentlint/probes/generator.py` | `Generator.generate(prompt, schema, model) -> Reply(data, model_used, raw)`; `GenerationFailed` |
| Fake generator | `src/agentlint/probes/fake.py` | `FakeGenerator`: scripted replies and exceptions, every call recorded |
| Reply check | `src/agentlint/probes/validate.py` | `schema(limits)`, `situations(data, limits) -> Checked`, `text_problem` — strict, stdlib only |
| What matters | `src/agentlint/probes/matters.py` | `Matters`: the fixed menu, one value per situation |
| Contamination guard | `src/agentlint/probes/contamination.py` | `guard_for(target, entities)`, `Guard.hits(situation)` |
| The prompt | `src/agentlint/probes/prompt.py` | `prompt(target, most, kept)`, `target_text`, `PROMPT_VERSION` (2 since 2026-10-05) |
| Generating, the cache, review | `src/agentlint/probes/generate.py` | `generate(db, target, entities, generator, model, now=, regenerate=)`, `listed(db, target)`, `review(db, probe_id, status, now, edited_text)` |
| Probes and diagnostics tables | `src/agentlint/store/probes.py`, `store/diagnostics.py` | schema 13 |
| The real generator | `src/agentlint/adapters/claude_code/generator.py` | `ClaudeCodeGenerator`: the headless call settled by E5 (D14) |

Tests: `tests/probes/`, `tests/store/test_probes_store.py`, `tests/adapters/test_claude_code_generator.py`.

Built 2026-10-05, the backend of review in the UI (D17), against a `FakeGenerator`: `ui/testing.py` generates in
the background (`POST /api/situations/generate`), lists a target's situations with their review and what matters
(`GET /api/situations`), records keep / drop / edit (`POST /api/situations/<id>`), and a job takes only the KEPT
situations (D12; "Review" below, `11-ui.md` "Tests"). The review screen is built too (2026-10-05, Tests ›
Situations: each situation with what matters, keep / drop / edit). Since schema 16 (decided 2026-10-05) a situation
is written kept and 'pending' is no state: review is the exception, not a gate. Not built: any command (the
terminal surface is decided in the UI review).
## The generator interface

```python
@dataclass(frozen=True)
class Reply:
    data: object             # the reply parsed as JSON, unchecked
    model_used: str | None   # the model that answered, as the call reports it; None when it doesn't say
    raw: str                 # the reply as received, kept for the record

class Generator(Protocol):
    def generate(self, prompt: str, schema: Mapping[str, Any], model: str) -> Reply: ...
```

Core hands a generator a prompt, the JSON schema the reply must fit and the model to ask for. It never knows which
harness answers: the real generator, how it calls the harness CLI, and the default model belong to an adapter
(non-negotiable 6). A call that gets no reply — it failed, timed out, or answered with something that isn't JSON —
raises `GenerationFailed`; it never returns an empty reply, so a failed call can't read as "no situation fits".
`model_used` is recorded as the model that answered, so a fallback can't hide (D14).

`FakeGenerator` answers from a script in order: a `Reply` as is, an exception raised, any other value as the
reply's data. A call after the script runs out raises `GenerationFailed`, so a test can't loop on it. It lives in
the package, not the test suite, so nothing outside pytest imports a test module to get one.

## The real generator

`adapters/claude_code/generator.py`, `ClaudeCodeGenerator` (2026-10-04, D14, settled by E5): one call per
`generate`, the command line in the order E5 ran it —

```
claude -p <prompt> --bare --model haiku --tools "" --no-session-persistence --output-format json --json-schema <schema>
```

— with the user's own environment (their real home: it authenticates the way their terminal does), in
`AGENTLINT_HOME/generate`, which must be empty: created when missing, and a directory holding anything is refused,
never cleared. `--bare` and `--tools ""` keep the configuration under test out of the call that writes its test
inputs; `--no-session-persistence` writes no transcript (E5: none; the harness still makes an empty project folder
with `memory/` under the real `~/.claude/projects`). The result's `structured_output` is the reply's data and
`modelUsage` names the model that answered (several, comma-joined, so a fallback shows). A missing executable, a
timeout (120 s; E5 took 6 s), output that isn't a JSON result (a launcher's own line before it is skipped; output is
decoded as UTF-8 with bad bytes replaced, so bytes that aren't UTF-8 land here too, never as a decoding error), an
error result (`is_error` not false) or no structured output raise `GenerationFailed`. Tested against a fake `claude` first
on PATH (`tests/adapters/fake_claude_cli.py`); the code's call has not been run against the real one (UNVERIFIED).

## What is asked for

One call per target, one structured reply: a list of situations, each with what matters in it, and an optional note.

```python
schema(Limits(most=3)) == {
  "type": "object", "additionalProperties": False, "required": ["situations"],
  "properties": {
    "situations": {"type": "array", "maxItems": 3, "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "matters"],
        "properties": {"text": {"type": "string", "minLength": 10, "maxLength": 600},
                       "matters": {"anyOf": [  # one shape per kind; THING: matters.THING as a pattern
                           {"required": ["kind", "uses", "then"], "properties": {"kind": {"enum": ["order"]},
                                                                                 "uses": THING, "then": THING}},
                           {"required": ["kind", "uses"], "properties": {"kind": {"enum": ["which_tools"]},
                                                                         "uses": THING, "instead_of": THING}},
                           {"required": ["kind", "uses"], "properties": {"kind": {"enum": ["targets", "tool_present"]},
                                                                         "uses": THING}}]}}}},
    "note": {"type": ["string", "null"], "maxLength": 1000},
  },
}  # each shape also "type": "object", "additionalProperties": False
```

**One shape per kind** (2026-10-07): an `order` without `then`, or a `then` on any other kind, doesn't fit the schema at
all — before, the generator wrote an `order` with no `then` and the reply check could only drop the situation (a
"generator rejected" diagnostic). The reply check still enforces the same, as the backstop. Whether
`claude -p --json-schema` accepts `anyOf` and holds the reply to it is UNVERIFIED until a Write runs (or
`/tmp/agentlint-build/anyof-check.sh`): if it refused the schema, every Write would fail with "Call 1 got no reply".

There is no `fixture` field: every situation runs in the same empty directory (below).

### What matters

Each situation carries one choice from a small fixed menu (`probes/matters.py`, `Matters`) — what part of the agent's
actions the target should change in it (the user's idea, 2026-10-04) — **naming the thing it is about** (decided with
the user 2026-10-07: "we only need one menu and the agent can decide from it"; the bare tag, never checked against a
run, let situations through that no run of exercised):

```json
{"kind": "tool_present", "uses": "skill:*"}
{"kind": "which_tools", "uses": "skill:data:sql-query", "instead_of": "skill:analytics:sql-query"}
{"kind": "order", "uses": "program:acme", "then": "tool:Write"}
{"kind": "targets", "uses": "program:acme"}
```

| Kind | The prompt's and review's line | Names |
|---|---|---|
| `which_tools` | which tool, skill or agent the agent picks among those that could do the job | `uses`, optionally `instead_of` |
| `order` | the order the agent does things in | `uses` (first) and `then` |
| `targets` | which files or programs the agent reads, edits or runs | `uses` |
| `tool_present` | whether the agent does a particular thing at all | `uses` |

A thing is `skill:<name>` or `skill:*` (any skill), `agent:<name>` or `agent:*`, `tool:<name>`, `program:<name>` (a
program a shell command runs) or `file:<path>` (`matters.THING`). **A skill or agent must be one the configuration
has** (`generate.knows`: among its entities by name, a package's prefix optional) — any other is made up, and the
situation is rejected (`UNKNOWN_THING`); tools, programs and files the configuration can't list (a harness's own
tools, any program on the machine) are taken as written, and one no run ever does shows as not followed. It is
stored as JSON (`WhatMatters.stored`); a situation written before recorded the bare menu value and named nothing, and
store schema 17 deleted those (decided with the user 2026-10-07: Write again). **It is the situation's expectation,
and trusted** (the user, 2026-10-07): the test counts the runs with and without the target that did it, plain
matching, never a model's verdict (non-negotiable 2) — `07-analysis.md` "The expectation's strength". Review shows it beside the menu
line: "What matters: whether the agent does a particular thing at all: skill:*".

### `claim`, later

The same call can later also emit a **claim** — what the target asserts about behaviour — from the understanding it
already needs to write a situation that triggers it. The claim reuses the must-never predicate vocabulary
(`called(X)`, `not_called(X)`, `before(X, Y)`), with `X` constrained to the entity registry injected into the schema
at call time, so the model can't name an entity that doesn't exist. **`claim: null` is a first-class result**: the
target asserts nothing checkable against an action trace (a tone or reasoning rule), which drives the blindness
guard in `07-analysis.md`.

**Status: deferred past v3.** Until then the schema has no `claim` field, and a reply that includes one is rejected
as having a field the schema doesn't allow.

## Checking a reply

Every reply passes `validate.situations(data, limits)` before anything of it is kept. A reply fits or it doesn't;
**nothing is repaired** — no trimming, no truncating a long list to the count, no coercing a number to a string.

| Problem | Outcome |
|---|---|
| Not a JSON object; a field other than `situations` and `note`; `situations` missing or not a list; more situations than were asked for (`Limits.most`); `note` neither a string nor null, or longer than allowed | The **whole reply** is rejected |
| A situation that isn't an object with exactly the fields `text` and `matters`; text that isn't a string; shorter than `min_chars` once spaces are set aside (blank counts); longer than `max_chars` | **That situation** is rejected; the rest are checked |
| A situation whose `matters` isn't exactly one of the menu's values (no case folding, no trimming) | That situation is rejected (`BAD_MATTERS`) |
| A situation holding a control character other than a newline (LF, or CR immediately before LF) or a tab; a lone CR is rejected | That situation is rejected (`CONTROL_CHARACTER`) |
| A situation holding an invisible character: a format character (zero-width space, bidi override, byte-order mark), a line or paragraph separator (U+2028, U+2029), a surrogate, a private-use character, or any other Unicode default-ignorable code point (the combining grapheme joiner U+034F, variation selectors U+FE00–FE0F and U+E0100–E01EF, the Hangul fillers U+115F–1160, U+3164 and U+FFA0, U+17B4–17B5, U+180B–180F, tags U+E0000–E0FFF). An emoji's own joiners stay: a zero-width joiner between two symbols, a variation selector right after a non-ASCII symbol (or a keycap's digit before U+20E3), and the tags of a well-formed subdivision flag (U+1F3F4, then 2 to 6 tag digits U+E0030–E0039 or lowercase tag letters U+E0061–E007A, then the cancel tag U+E007F). Other unassigned code points stay, since a newer emoji may be one | That situation is rejected (`INVISIBLE_CHARACTER`): "search_files" with a U+200B or U+034F inside it would read as search_files to a reviewer, and probably to the agent, but would slip past the contamination guard. A variation selector after `_`, `/` or `=` is inside a name, so it is rejected. Any other run of tags after a black flag (capitals, a space, no cancel tag) spells ASCII a model reads and a person can't see, so it is rejected too |
| A situation that is the same request as an earlier kept one (`same_request`): the same text once case is folded, spacing collapsed, and sentence punctuation (`. , ! ? ; :`), quotes and backticks stripped from each word's edges. Symbols in or on a word count (`C` and `C++`, `src/a.py` and `src-a.py`, `f()` and `f` differ), and a text that would reduce to nothing is its own key | Rejected as a duplicate |

Kept situations are `Situation(text, matters)`, the text verbatim, in the reply's order (`Checked.texts` gives the
texts alone). `text_problem(text, limits)` judges one text by the same rules; review uses it for an edited text. Every rejection carries its `Reason` and the situation's
position (none for the whole reply), and `Checked.counts()` tallies them, so a generator that keeps missing shows up
as numbers, not as silence. An empty list with a note fits: that is the model saying no situation would trigger the
target. What an empty result leads to is D16 (below).

The schema handed to the model and this check say the same thing; this check is what decides. It uses the standard
library only.

## Contamination

A situation that names the thing it tests is **contaminated**. If the request says "use search_files to find X",
the agent uses search_files whether or not the rule exists, and the comparison measures nothing — it reads as a
confident "no effect". The prompt asks the model not to; `contamination.py` checks it in code. This is code checking
a generated input, not a model judging an output.

`guard_for(target, entities)` works out, once per target, what its situations must not contain. `entities` are the
configuration's (`ConfigSnapshot.entities`, tools included); the target's text is its rules' text.

| Term | From |
|---|---|
| The target's own entity | Every name (full, short, aliases) of an entity the target's source provides — the skill whose definition is the target — named in its text or not. An alias that is one ordinary word (a server's tool called `search`) is left out: nobody sees it alone |
| An entity the text names | Its full name and aliases, and its short name when that is more than an ordinary word: naming `data:sql-query` forbids `sql-query`, but naming `brain:status` doesn't forbid the word "status", and a span `status` names neither. A distinctive name (anything but letters, or a capital inside: `mcp__x__search_files`, `general-purpose`, `brain:load`, `WebFetch`) names its entity anywhere in the text, **fenced blocks included**, since a skill usually shows its tool calls in one. Any other name (`Read`) names it only in prose or a code span, never in a fenced block, where it is as likely a class or a variable |
| Code | A code span (`like this`, outside fenced blocks) that is a command, path, flag or identifier: one token that is more than an ordinary word (`docs/03-checklist.md`, `--dry-run`), or two to six words holding a symbol, digit or internal capital (`uv run pytest -q`, `Depends on D<number>`), or two to six plain words whose first word the text runs as a program (below). A lone ordinary word (`true`, `name`) is not a term, nor is a span of plain words otherwise (`depends on`, `limit reached`): that is prose in backticks, left to the copied-words check |
| Identifier | A snake_case identifier in the prose (`billing_client`), not in a fenced block: a fenced block is a whole program, not a list of names, so its code and identifiers are left to the copied-words check |
| Copied words | Any run of 6 consecutive words of the target's text, at least half of them more than function words |

Names are matched **by their shape**, so ordinary words don't misfire:

- A name holding anything but letters, or a capital inside it (`search_files`, `sql-query`, `WebFetch`), matches
  whatever its case, with `-` and `_` interchangeable.
- A capitalised word (`Read`, `Edit`) matches only in that case, and in prose not where a sentence starts, where a
  capital says nothing ("Read the README" doesn't name the Read tool; "open it with Read" does). A sentence starts at
  the text's start, after `.` `!` `?` or a colon, at a line's start, and at a list item's first word: after a bullet,
  `1.` `1)` `(1)`, or `a)` `(a)` at a line's start or after a sentence's end. This holds for the target's text and
  for the situation alike. In a code span a capital always counts.
- A plain lowercase word (`git`, `debug`) matches whatever its case, but the target's text never names an entity by
  one, even in a code span: on the machine this was measured on, `run` and `loop` are skill names as well as
  everyday words and subcommands.
- A lowercase hyphenated compound that is a real English word (a short list in the code: `general-purpose`,
  `deep-dive`, `code-review`, `follow-up`, `dry-run`, …; capitalised only at a sentence start) written plainly in
  prose where an English compound stands — after a determiner (*a*, *the*, *our*, *more*, …: "do a deep-dive") or
  right before another content word ("a general-purpose retry helper") — is the word, not the name. This holds for
  the target's text (so "a general-purpose library" names no agent) and for the situation. Written as a name it
  counts: in a code span or fenced block, straight after `/`, `@` or `=` (`/deep-dive`,
  `subagent_type=general-purpose`), between quotes, with `_` for `-`, after a verb that invokes (*use*, *run*,
  *invoke*, *call*, *spawn*, *ask*: "run deep-dive on it"), before a role word (*skill*, *agent*, *subagent*, *tool*,
  *command*, *plugin*: "the general-purpose agent"), or anywhere else in prose ("use deep-dive to find why"). A
  compound not on the list (`ci-autopilot`, `skill-creator`) is always the name, and the target's own entities'
  names are never read as words, whatever their shape: a skill's own `code-review` is a hit in "do a code-review".
  The machine this was measured on has 238 names of the form word-word.

A span of plain words is a command when its first word is a **program the text runs**: written in lowercase, it is
the first word of a line in a shell fenced block (`bash`, `sh`, `shell`, `zsh`, `console`), or after a `$` prompt in
any fenced block, or the first word of at least three different multi-word code spans (`sl status`, `sl commit`,
`sl amend`). A function word never is (a shell line can start with `for`; `no change`, `no news` and `no idea` say
nothing), nor is a capitalised one (`Depends on D123` is a commit trailer). A line of Python or Hack in its own fence
(`async def`, `return x`) runs no program.

An MCP tool goes by its own name too: the adapter gives `mcp__<server>__search_files` the alias `search_files`
(`01-config-reader.md`, Entity), so a target that names the tool forbids "use search_files", and a target that says
`search_files` alone names the tool.

`Guard.hits(situation)` returns each hit with its offsets and the term it matched (none for copied words); a hit
inside a longer one is left out. The terms and hits are what review would show beside a situation.

Known limits:

- A target's own entity with an ordinary-word name (a skill called `test`) makes every situation that uses the word
  a hit. That is the name the agent sees, so the hit stands.
- A target's own entity named by an English compound (an agent called `general-purpose`) makes every situation that
  uses the word a hit, as with an ordinary-word name.
- A compound not on the list used as an English word is a hit: "write a data-viz of signups" when the target's text
  names `data-viz`. A listed compound used as a name with neither cue is missed ("have general-purpose look into
  it"); one before a role word in its English sense ("a general-purpose tool") reads as the name. The copied-words
  check and review remain.
- A span of plain words that is a command whose program the text shows only once (`git log` alone) is no term.

The guard matches characters as written, so it relies on the reply check having rejected invisible characters: a
situation is checked for contamination only after `validate.situations` has kept it.

**Measured 2026-10-04** on this machine's configuration (871 entities, 920 targets, read with `AGENTLINT_HOME` set
to a throwaway directory): building every target's guard took 14–20 s across three runs, about 20 ms a target. Ten ordinary requests ("Find every place we
call the billing API in this repo.", "Read the error log and tell me what went wrong.", …) checked against all 920
guards gave 3 hits, all of them a target's own ordinary-word entity (`test`, `run`, `plan`). No model was involved.

**Measured again 2026-10-04** after distinctive names began to count in fenced blocks (same machine, same way, two
runs): the targets' guards name 135–146 more target/entity pairs than before (3,668 → 3,814 in one run), none fewer,
for example `general-purpose`, `notes:myself` and `mcp__plugin_acme_hub__knowledge_load` shown in a skill's fenced
example. Nine ordinary requests checked against all 920 guards gave the same 6 hits before and after: five a
target's own ordinary-word entity (`build`, `load`, `summary` twice, `test`) and one two-word code span (`depends
on`). Building every guard took about 20 s either way.

**Measured a third time 2026-10-04** after the compound, program and MCP-alias rules (same machine, same way: 871
entities, 920 targets; old and new guards compared on the same reading). Ordinary requests that the previous guard
flagged and this one doesn't: "Write a general-purpose retry helper for our HTTP client." (32 targets → 0), "Can you
do a deep-dive on why the cache misses?" (16 → 0), "Do a code-review of my last change before I push." (13 → 0),
"Keep going until the rate limit reached zero, then retry." (4 → 0, `limit reached`), "List what depends on the
cache module." (1 → 0), "Add a dry-run flag to the deploy script." (3 → 0), "Write a data-viz of last week's
signups." (7 → 0). The three own-entity ordinary-word hits (`load`, `test`, `Plan`) stand. Distinct multi-word code
terms made only of lowercase words fell from 403 to 228, nearly all of them commands (`sl status`, `gh pr create`,
`pkg build`). Named target/entity pairs rose from 4,689 to 4,977, mostly from the 217 MCP tools' aliases: "Use
search_files to find every TODO in src." is a hit for 30 targets (16 before, and then only as code). Written as a
name, a compound is still caught: "Run /deep-dive on the cache misses." 13 targets (17 before; 4 targets used
deep-dive only as an English word), "Spawn it with subagent_type=general-purpose." 30 (33). The miss: "Hand this to
the general-purpose agent." 0 (32). Building every guard took 17–22 s.

Since then the compound rule takes only listed English compounds and reads a compound after an invoking verb or
before a role word as the name; the target's own names are never read as words. Not re-measured (UNVERIFIED): "Hand
this to the general-purpose agent." should now be a hit where the run above gave 0, "Write a data-viz of last week's
signups." a hit again for the targets that name `data-viz`, and the code-review, deep-dive and general-purpose
requests above a hit for the targets that own those names.

A situation with any hit is dropped automatically, each drop a diagnostic (D16, "Generating" below).

## The prompt

`probes/prompt.py`, `PROMPT_VERSION = 3` (2026-10-07: what matters names its thing — "kind" from the menu and "uses",
written as skill:<name>, agent:<name>, tool:<name>, program:<name> or file:<path>, or skill:* / agent:*, with "then" for
order and an optional "instead_of" for which_tools; the names are for checking the agent's actions afterwards and the
request itself still names none; "A request whose runs never do "uses" tested nothing." Version 2, 2026-10-05: requests
answered only by acting; version 1, 2026-10-04, asked for any request that made the target relevant). The text is the target's rules' text (`target_text`, the same text the contamination guard reads),
where it comes from (its point's label and authority, never its path), when the agent sees it, that a request must be
one the agent can only answer by acting, the directory a situation runs in (D1: the project's configuration, no code),
the requirements, and the menu of what matters:

```
You are writing test inputs for a tool that checks a coding agent's configuration.

Here is one part of that configuration (a rule, or a whole skill, agent or hook):

---
{target's rules' text}
---

Where it comes from: {point label}, set at {authority} level.
When the agent sees it: {from the first message of every session | with every message | when what it defines is
invoked | when this holds: <condition> | never as text}.

Write up to {n} requests a developer might type to the coding agent that would put the agent in a situation where
this part of its configuration becomes relevant.

Each request must be one the agent can only answer by acting: by looking something up with a tool or command, running
a program, creating or changing a file, or using a skill or agent. Never a question it could answer from what it
already knows: no advice, no explanations, no comparisons. The tool compares what the agent does, so a request
answered in words alone tests nothing.

The agent works in a directory that holds the project's configuration (its instruction files and settings) and no
code: no source files, no git history. Each request must make sense there: something the agent can act on from
nothing (create a file, set something up, run a command, look something up), never something that needs existing code.

Requirements:
- Natural phrasing: a real person typing to a coding agent, not a test case.
- Never name this skill, agent, tool, command or hook, nor any tool or skill its text names, and never quote its
  wording. The request must make it relevant without pointing at it.
- Vary them: not {n} rewordings of one request.
- For each request, say in "matters" which part of the agent's actions this configuration should change, one of:
  - which_tools: ...   (the menu, with each value's meaning)
- If no request could make it relevant, return an empty list and say why in the note.
[top-up only: These requests are already written and kept; write different ones: - ...]
Reply with JSON only: {"situations": [{"text": "...", "matters": "..."}], "note": "..."}
```

**Why version 2** (decided with the user 2026-10-05): in the first real job (02-design's Verified Facts, and the
decision log's 'No action' row that day), 3 of the 6 situations got only written answers — no tool, skill or agent used in any run — and a test compares actions, never
words (non-negotiable 1), so a situation answered in words measures nothing: its screen reads "No action"
(`07-analysis.md`). Version 1 asked for anything that made the target relevant and offered "plan" and "explain" among
its examples; version 2 asks only for requests the agent can answer by acting, and names advice, explanations and
comparisons as what not to write. Whether it lowers the share of "No action" situations is UNVERIFIED until a job runs
situations written by it.

The "never name" line is a request; the contamination guard is the check. Changing the wording raises
`PROMPT_VERSION`, which every probe records and none is invalidated by (D15): situations written by version 1 stay,
kept, until their target is written again.

## Generating

`generate(db, target, entities, generator, model, now=, regenerate=False, limits=Limits())` — the Python entry point
the UI's `POST /api/situations/generate` calls ("Review"); there is no command (D17).

1. The cache key is `target_hash(target)`: the content hash of the target's text, and nothing else (D15). If the
   text has situations and `regenerate` is false, they are returned with no call (`Generated.cached`). Two targets
   with the same text share them.
2. A target with no text (only its size known) makes no call: a `generator_no_text` diagnostic.
3. The first call asks for `Limits.most` (your `situations_per_target`, 3 by default). Its reply passes the strict check; each kept situation that repeats
   one already kept is lost as a duplicate, and each that the guard finds any hit in is **dropped**, one
   `situation_contaminated` diagnostic each, with the situation and its hits. A call that gets no reply is a
   `generator_failed` diagnostic; a reply the check rejected, whole or in part, a `generator_rejected` one with the
   reasons.
4. **At most one top-up call**, and only when something was lost: the call failed, the reply or a situation was
   rejected, or a situation was dropped. It asks for the missing count only, and the prompt lists the situations
   already kept. A model that wrote fewer than asked with nothing lost is taken at its word: no top-up.
5. Nothing kept is a `generator_empty` diagnostic, carrying the notes: **a diagnostic, not a finding** (D16). A cheap
   model giving up is no evidence about the configuration (non-negotiables 2 and 3).
6. What was kept replaces the text's situations in one transaction with the diagnostics: probe id
   `probe_id(target_hash, text)` (16 hex digits of a SHA-256 of both), what matters, the model that answered *that*
   reply, `PROMPT_VERSION`, `now`, review `accepted` — kept — with no review time (`reviewed` NULL: nobody reviewed
   it, so "kept as written" stays apart from "kept by you" in the data). A regeneration that keeps nothing leaves the old situations, and
   its `generator_empty` diagnostic says so: a failed call never throws away reviewed situations.

An empty result is not cached: generating for that target again calls the model again.

`listed(db, target)` is the target text's situations in the order written; `review(db, probe_id, status, now,
edited_text=None)` records accepted / rejected / edited, and when (a keep too). An edited text must pass `text_problem` (the rules
a generated text passes) or `EditRefused`; whether it names its target is for review to show from the guard.

## Diagnostics

agentlint's findings about itself (D16): their own table (`store/diagnostics.py`: kind, subject — a target or job id
— message, data as JSON, created), never in `findings`, its counts, `--json` findings or an exit code. `keep_days`
prunes them; `forget` deletes them. The kind is its producer's name for what happened (`generate.Kind` for the
generator's); the store keeps it as text, so the runner's retries need no migration. The UI's Diagnostics page lists
them (built 2026-10-04, `11-ui.md`); `agentlint diagnostics` is not built.

## Where a situation runs

**Decided 2026-10-04 (user, `02-design.md` decision log):** every situation runs in an empty directory agentlint
owns, at a fixed path under `AGENTLINT_HOME`, never a random temporary directory, so `forget` deletes run results
with everything else. There is one directory and no fixture to choose, so neither the schema nor a probe has a
`fixture` field. Rules that only trigger with files present can't be measured in it — a known limit. Beside it goes
only the project's configuration, never its code (user, D1, 2026-10-04), and the prompt says so.

## What a probe is

A `probes` row (`store/probes.py`, schema 13), one per situation:

| Column | What |
|---|---|
| `probe_id` | 16 hex digits of SHA-256 over the target's text hash and the situation's text |
| `target_id` | the `AblationTarget.id` whose generation wrote it (the latest, when two targets share a text: a regeneration replaces every row for the text, under the caller's id) |
| `target_hash` | content hash of the target's text: the only cache key (D15) |
| `situation` | verbatim, as the generator wrote it |
| `matters` | a `Matters` value; used for no verdict |
| `model` | the model that answered, as the call reported it |
| `prompt_version`, `created` | recorded; neither invalidates anything (D15) |
| `review`, `edited_text`, `reviewed` | `accepted` (kept: how every situation is written) / `rejected` / `edited` — `pending` until schema 16, which made every pending row kept; the edited text exactly when edited (a CHECK); when a person last reviewed it, NULL for one kept as written |

`Probe.text` is what a run types: the edited text when there is one. A job freezes the situation's text in its plan
(`05-runner.md`), so regenerating never changes a job.

**There is no assertion.** Generated probes carry no expectation. The expectation is a universal property applied
by the analysis stage — "behavior should be self-consistent," "a rule should have measurable effect." Written once in
the engine, not per probe.

The `targets` link is the important field. It's what makes coverage measurable: *"we tested this target with three
situations and removing it changed nothing."* Without the link you have probes; with it you have evidence.

**An empty result** is a `generator_empty` diagnostic carrying the model's note (D16, "Generating" above), never a
finding.

## Review

**A generated situation is written kept** (decided with the user 2026-10-05, schema 16; was: pending until a person
reviewed it, D12 and D17): the user kept every situation of the first real test by hand, so review is the exception,
not a gate. You review where you choose to — drop one, edit one (with any contamination hits shown), or keep one
again — and only kept situations run, as before. A situation kept as written has no review time (`reviewed` NULL);
one a person kept has one, so the data tells "kept by default" from "kept by you" (the UI doesn't show it). Review
happens in the UI first (D17). **The backend is built** (2026-10-05, `ui/testing.py`, tested against a
`FakeGenerator` in `tests/test_ui_testing.py`):

- `POST /api/situations/generate {target_ids, harness?, project?, regenerate?}` takes 1 to 500 ids — as many as can be
  ticked, since **Write situations** sends every ticked target with none written in one request — reads the project,
  checks each id is one of its targets now (else 400), and runs `generate` for each, one after another, in a background
  thread, so the request returns at
  once (`{generating, project}`). It is refused (409) inside one of the harness's own sessions — the same check the
  worker makes (`VariantHomes.nested`; for Claude Code, `CLAUDECODE` set) — and for a target already being written.
  A generation that raises instead of returning is a `generator_error` diagnostic with its traceback; an empty,
  rejected or contaminated result is the diagnostic `generate` records, never a finding (D16).
- `GET /api/situations?target=&harness=&project=` lists the target's situations as kept for its text now — each one's
  id, text (the edit, when edited), the generated text, what matters and its menu line, its review in review's words
  (`kept`, `dropped`, `edited`), the model and the times (`reviewed` null for one kept as written) — whether some are
  being written now, and the
  target's last 20 diagnostics. It reuses the target text the last reading of that project found (the plan, a
  generation), so polling never reads the configuration again; a target never read is read once.
- `POST /api/situations/<probe id> {decision: keep|drop|edit, text?}` records `accepted`, `rejected` or `edited`
  and when (`generate.review`). An edit needs its text (1-2,000 characters) and must pass `text_problem` (else 400, "this text
  would be rejected if the generator wrote it"); whether it names its target comes back as `names_its_target` — the
  guard's hits, shown, never refused — when the server has read that target. An unknown id is 404.
- **A job runs only the situations kept** (D12): `accepted` and `edited`, never `rejected`
  (`ui/testing.job_plan`, `05-runner.md` "The worker").

Why review stays possible, given the generator is cheap and the probes carry no expectations: a bad probe wastes its
runs (3 per situation in phase 1, `RunCounts` in `03-ranker.md`) and produces a misleading "no effect" result, so a
glance that drops one is worth it. It is no longer a step every situation waits on.

## Caching and invalidation

Decided 2026-10-04 (D15): a target's situations stay valid until its text changes. The key is the target text's
hash alone; the model, the prompt version and the date are recorded but invalidate nothing, so a package install or
a model switch doesn't throw away reviewed situations. Regenerating happens only on request (`regenerate=True`).
Situations of a text no target has any more are not deleted (only `forget` deletes probes).

Decided: reviewed status does **not** carry across regeneration — not even for a situation written again word for
word.

## Count per target

Default 3: the `situations_per_target` setting (1–10, decided 2026-10-05), which the UI's generation asks for
(`Limits.most`) and the ranker counts (`RunCounts.situations` in `src/agentlint/rank/plan.py`); a test runs at most
that many kept situations of each ticked target, and fewer where fewer are kept. A target written before keeps the
situations it has until it is written again, so changing the setting changes no target's cached situations.
Rationale: one situation is a coin flip, and the cost scales linearly. Three gives enough variety that a "no effect"
verdict means something, without quadrupling the runs.

## Must-never probes are different

Hand-written, not generated, and they *do* carry assertions. See `docs/02-design.md` for the format.
They live in `~/.agentlint/probes/must-never/` and are version-controlled by the user.

They should be **adversarial** — written to tempt the violation. A rule that holds only when nothing
pulls against it isn't a control. The generator can *propose* adversarial probes for a safety rule,
but a human must approve each one before it counts as a check.

## CLI

None (2026-10-04): the terminal surface is decided in the UI review (D17). The UI's review comes first: its endpoints
and its screen are built (2026-10-05, "Review"; `11-ui.md` "Tests").
