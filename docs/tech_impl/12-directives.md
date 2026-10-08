# 12 — Directives, contradictions and repeats

Decided with the user 2026-10-07 (decision log). The configuration's instruction text tells the agent to do and not
do things with skills, agents, tools, programs and files. Two sources can contradict each other ("don't use subagents
unless asked" against "always use agent X for exploration"), and the same directive can be said in several places
the agent reads every session. This component finds both.

**The model writes inputs, code judges.** A model reads each section of text and lists its directives in a fixed
form; code checks every one strictly and joins them. No model ever decides that two directives conflict
(non-negotiable 2). The findings are `suggestive`: a directive is a model's reading of a section, held to the exact
words that say it.

| Part | Where |
|---|---|
| The form | `src/agentlint/directives/model.py` — `Directive(polarity, thing, quote, when)`, `Polarity` |
| The prompt | `src/agentlint/directives/prompt.py` — `PROMPT_VERSION`, `MOST` |
| The check | `src/agentlint/directives/validate.py` — `schema()`, `check(data, text, known)` |
| Reading | `src/agentlint/directives/extract.py` — `readable`, `statuses`, `read_sources`, `occurrences` |
| Pairing | `src/agentlint/directives/pair.py` — `contradictions`, `repeats` |
| The store | `src/agentlint/store/directives.py` — one row per section fingerprint and prompt version |
| The checks | `src/agentlint/checks/directives.py` — `conflicting-directives`, `repeated-directive` |

## The form

```json
{"polarity": "must_not", "thing": "agent:*", "quote": "Never delegate to subagents", "when": "unless the user asks"}
```

- **polarity**: `must`, `must_not`, `prefer`, `avoid`. `must` and `prefer` are toward the thing, the others away.
  "Use X instead of Y" is two: prefer X, avoid Y.
- **thing**: `matters.THING`'s form — `skill:<name>` or `skill:*`, `agent:<name>` or `agent:*`, `tool:<name>`,
  `program:<name>`, `file:<path>` — the same vocabulary the tests' expectations use (`04-probe-generator.md`).
- **quote**: the words of the section that say it, exactly; at most 300 characters.
- **when**: when it applies, in at most twelve words, or null for always.

## The check

Strict, never repaired (as the generator's, `04`): a reply that isn't `{directives: [...], note?}` or holds more than
`MOST` (15) is rejected whole; a directive is thrown out when its polarity isn't on the menu, its thing doesn't fit
`THING`, it names a skill or agent the configuration doesn't have (`probes.generate.knows`; `skill:*` and `agent:*`
always fit), its **quote is not in the section** (compared with whitespace squashed: a line break may fall anywhere in
it), its **quote doesn't name its thing** (2026-10-08, `names_thing`: `agent:*` needs "agent", "subagent" or
"delegate" in the quote, `skill:*` "skill", a named thing its name as a word — a skill's or agent's without its
package, `-` and `_` alike — a file its path or file name), its `when` is too long, or it repeats one of the same
section. What is thrown out is a `directives_rejected`
diagnostic with each reason; a call that got no reply, `directives_failed`. Neither is ever a finding.

## Reading

**What can be read**: every source whose text reaches the agent (not `not_in_context`) and has sections — instruction
files, memory, hook output, the listings, managed policy, and skill and agent bodies. On the reference machine:
~2,400 sections, ~2.8M tokens, nearly all of it skill and agent bodies (measured 2026-10-07).

**When**: only when you choose (the user, 2026-10-07: "let's not make it automatic, let's make it triggerable"). You
select sources — all, or none — and a **reading** reads their sections. **Each section once**: a section's
fingerprint is its text's content hash (`Rule.content_hash`), stored with what was found (possibly nothing) under the
prompt version; a section whose fingerprint is stored is never read again, so a changed section is read and an
unchanged one never is. Sections that share a text are read once. A call that failed leaves its section unread, to
be read the next time it is selected or the reading is resumed. The reading runs 4 headless runs of the harness at a
time (`at_once`), with the generator the tests use (Claude Code: `claude -p --bare --model haiku --tools ""`,
`09-adapters.md`), and stops after the calls under way when you stop it.

## Reading jobs

A reading is a job, as a test is (the user, 2026-10-07: "jobs should be coarse and then granular"):

- **Planned at once** (`extract.plan_read`, `store/reads.py`): from the project as Read instructions last read it, the
  sections of the selected sources whose fingerprint isn't read yet, each once, stored with their text, where they come
  from and how they load — so its worker needs nothing else — and the skill and agent names a directive may name.
  Nothing to read: no job.
- **Run in a worker of its own**: `python -m agentlint.directives <read id>` (`directives/worker.py`), launched by the
  UI in its own session as a test's worker is, so it outlives the server; it takes the reading's lock (pid and start
  time), marks each section reading, then read (with how many directives) or failed (with why) as it goes, and logs to
  `jobs/<read id>.log`. One reading at a time per project.
- **Stop** is Ctrl-C to its process group: no new section starts, the calls under way finish (one Stop cut short is
  pending again, not failed), and the reading is stopped. **Resume** reads what is pending or failed; a section another
  reading read meanwhile counts as read.
- Its tables are created on first use, like the directives'; forget keeps them (configuration text, not transcripts),
  and a reading's log is kept with it.

`statuses` says per source how many of its sections are read (`read` of `sections`) and how many tokens the rest
hold: read, partly read (new or changed sections), not read, or stale.

## The reader's version

`prompt.READER_VERSION` is **set by hand** (the user, 2026-10-08): raise it whenever the prompt or the check changes
in a way that could change what a section gives. A section's directives are stored under the version that read them,
and **everything stored under an older version is stale**: no check uses it (`occurrences` loads only the current
version), Read instructions shows such a section as stale ("4 stale: read by an older version of the reader, to read
again", the summary naming the reader's version), and a reading reads it again like a section never read, replacing
the old row. Version 1 was the first prompt; version 2 (2026-10-08) the stricter prompt and the quote check.

The store table is created on first use, without a schema version: it is additive, and a version bump would make an
older agentlint still running (the UI you have open) refuse the store as soon as a newer process opened it.

## Pairing

Over every stored directive of the snapshot's sections (`occurrences`; each at the line its quote starts on):

- **A contradiction candidate** (`conflicting-directives`): **one pair of quotes**, one source toward a thing and
  another away from it — the same thing, or `skill:*` / `agent:*` against one of their kind. Any-of-a-kind against
  any-of-a-kind ("use subagents for research" / "don't use subagents for edits") only when one side has no `when`:
  two scopes are not a contradiction. A named thing against any of its kind is always one. One source qualifying
  itself ("use X, but not for Y") is not one. A skill or agent is compared by its name without a package's prefix, a program in lower case, a file
  without a leading `./`. `warn` when one side loads in every session, `info` when both load only when invoked. The
  detail quotes every place on both sides, with its `when`, and says that whether both apply at once is yours to
  judge (the user chose candidates only, not a test run).
- **A repeat** (`repeated-directive`): the same thing in the same direction in two or more sources that load in every
  session (`always`, `per_message`): the agent reads it more than once every session. `info`.

Both are identified by the thing(s) and direction, so a finding stays the same finding as more is read; with nothing
read yet neither check is evaluated, and a finding says how much is read when it isn't all.

**The first real reading** (the user, 2026-10-07, 153 of 2,585 sections, prompt version 1) made one candidate of
fourteen quotes the model had labelled `agent:*` — "AI generates inputs", "When in doubt, report nothing", "send them
in a single message", "it does not guess" — none about whether to use subagents. The user judged it noise. Prompt
version 2 asks only for directives to use or not use the thing, and leaves out how to behave once it is used, mere
mentions, limits and principles about AI; the quote check and the pairing rules above came with it, and
`tests/directives/test_core.py` keeps those fourteen quotes as a case that must make no candidate. Sections read
under version 1 are read again. How many real candidates the configuration holds is UNVERIFIED until a reading under
version 2.
