# 02 — Static checks

Free, fast, no AI, no agent runs. These must be useful on their own — v0 ships with nothing else.

**Precision is the whole game here.** These are the first findings a user ever sees. One false
positive and they stop trusting the tool. Every check below should be defensible line by line.

## The checks

Each check has one ID, and that same string is used in three places:

- the `check` field on every `Finding` — written once, where the check is declared (`Check("broken-reference",
  ...)`): a check makes its findings with `Check.finding(...)`, which stamps `check=self.id` (2026-10-04)
- CLI selection — `agentlint check --only trigger-overlap,broken-reference`
- what the user reads in terminal output; the UI shows it made readable (`broken-reference` is "Broken reference",
  `checkName` in ui/src/format.ts), with the id itself in tooltips and in the finding's detail (2026-10-04)

So it has to be legible on its own. A numbered code (`C4`) would require a lookup table that only
exists in this file, which is useless to someone reading CLI output.

**Check IDs are a public API.** Renaming one breaks saved `--only` flags and CI invocations. Don't
rename casually; if you must, keep the old name as an alias.

| ID | Kind | What it finds | Where |
|---|---|---|---|
| `token-budget` | cost | What's spending your always-on context, grouped by how it gets there | core |
| `package-footprint` | cost | What each package adds to every session, and how much would leave context if disabled | Claude Code adapter |
| `listing-budget` | delivery | Skills reaching the model as a bare name, why (the budget formula), and the fixes | Claude Code adapter |
| `duplicate-name` | collision | Entities sharing a name: identical copies, drifted copies, or namesakes | core |
| `trigger-overlap` | collision | Nearly identical descriptions under different names — active (both described) or latent | core |
| `broken-reference` | broken | Provable references to things that don't exist | core + adapter |
| `unavailable-reference` | broken | Instructions naming tools that are denied or absent | Claude Code adapter |
| `interrupted-start` | delivery | Sessions whose skill listing never reached the model | Claude Code adapter |
| `untraced-injection` | delivery | Delivered text that traces to nothing configured | core |
| `hook-health` | health | Hooks that fail, time out, or slow session start | Claude Code adapter |
| `definition-size` | cost | Invocable definitions large enough to matter | core |
| `emphasis-density` | emphasis | Sources shouting far more than the rest (suggestive) | core |
| `tested-expectation` | behaviour | Tested targets that didn't make the agent do what their situations expect — every run with them, none without (measured) | core |
| `tested-consistency` | behaviour | Tested targets whose runs with them didn't use the same tools, skills and agents (measured) | core |
| `conflicting-directives` | collision | One source says to use a thing, another not to: candidates for you to judge (suggestive; `12-directives.md`) | core |
| `repeated-directive` | cost | The same directive in several sources that load in every session (suggestive; `12-directives.md`) | core |

**Everything surfaced is a `Finding`** (decided 2026-10-02) — costs and heuristics included — described by
three independent enums: `Severity` (info / warn / error), `FindingKind` (cost / collision / broken / delivery /
health / emphasis / behaviour) and `Confidence` (certain / measured / suggestive). Static checks are `certain`,
except heuristics, which are `suggestive` and say so. Output order: severity, then check, then the order the
check emitted — each check puts its most important finding first (loudest, most similar).

**A check that lacks its input says so.** It raises `NotEvaluated(reason)` — no recent sessions, no session
from this project, too few sources to compare — which is not the same as finding nothing: the check is listed
as not evaluated and its earlier findings keep their state. A check that lacks a session raises `NoSession`
(`in_project=True` when it needs one in this project's directory); when the reading left out sessions from before
you last forgot, the run reports it as "no session since you forgot (date)" instead. Two finer statements: a check whose evidence
covers only some of its earlier findings returns `Partial(findings, judged)` (hook-health can judge only
hooks that ran in the sessions read), and a check whose findings can vanish with nothing they rest on changed
— a ranking, or members only sessions show — is `needs_change` (emphasis-density, trigger-overlap,
duplicate-name), so such a finding is closed only when one of its own inputs changed (`10-reporting.md`,
"States"). A check that judges by something other than the text of the sources its findings rest on is
`by_text=False` (hook-health: recorded runs), so a source whose text no session showed doesn't keep its findings open.

**Adapters contribute checks** that need harness knowledge (a listing's budget formula, transcript structure,
permission syntax) through `Adapter.checks()`. They emit the same `Finding` type and share the same `--only`
namespace. Core checks stay harness-agnostic.

**A check is a pure function of what it is given** (decided 2026-10-03). `Check.run` takes one `Reading`
(`config/reading.py`, made by `read_config`): the `ConfigSnapshot`, the filesystem facts the core checks need
(`FileFacts`: which relative links in an instruction file point at nothing, which entity files are symlinks —
taken by `checks/files.py` as the configuration is read, keyed by the source's `Provenance.file`; a source that is one
part of a file, with an `anchor`, has no links or symlink of its own to judge, and one in no file is never looked
up — before 2026-10-04 a label was passed to `Path(...).exists()` and relied on not naming a file in the process's
working directory), and the adapter's own `evidence` from the same read
(Claude Code's: the hooks configured and the packages installed, the sessions read and the hook runs they
recorded, and which `${CLAUDE_PLUGIN_ROOT}` paths are missing). A check never stats a file, opens a transcript
or reads state an earlier reading left on the adapter, so it judges exactly the reading it is handed — the
same reading run twice gives the same findings, whatever the disk did in between — and a reading with no
history gives adapter checks no sessions rather than another project's. A path the reading didn't look at is
never reported. One exception, outside the configuration being judged: emphasis-density reads your own
marker file (`~/.agentlint/emphasis.toml`) when it runs. **Test results join the reading the same way**
(2026-10-07): `Reading.tests` is what the store's tests said of the snapshot's targets, taken as the configuration
is read (`runner/tested.py`, by `cli._read` and the suppress path), never looked up while a check runs; a reading
that took none (`tests` None: the harness's runs can't be read here) evaluates neither behaviour check.

---

## token-budget

Not a "check" so much as the number nobody has.

**Algorithm:** group the always-on outcome's own attribution (`snapshot.always_on.attribution`, computed once in
assemble: what the anchor session delivered when a session reported a total, otherwise each always-on source's and
untraced text's latest observation) by how it gets there — never a second sum over the sources, so the breakdown,
the attributed figure and the remainder count the same tokens: the harness's own prompt and tool definitions, listings the
harness composes, instruction files, instruction text inside settings, package definitions, hook output (once
per session, and every message). The finding rests on every declared always-on source, observed or not.

**Output:** one `info` finding whose title states the total exactly as far as the data allows — observed and
reconciled against the anchor session's own first turn ("44,280 tokens every session (observed)", with how much
was attributed), observed but overcounted, or, with no clean session in this project, "up to ≈N tokens per
session (estimated)", saying that each source counts what it last delivered, possibly in different sessions:

```
token-budget   Always-on context: up to ≈50,092 tokens per session (estimated)
               ≈ 24,423  listings the harness composes from what is installed
               ≈ 19,155  the harness's own prompt and tool definitions
               ≈  4,204  hook output, once per session
               ≈  2,107  instruction files
               …
```

**No threshold, no pass/fail.** This is information. The ranking is the finding. `checks/token_budget.py`.

## package-footprint (Claude Code adapter)

Users disable packages, not listing lines. The adapter splits the skill, agent and deferred-tool listings
into one source per package (lossless — the parts sum to exactly the delivered text), and this check ranks
packages by what they add to every session: listing entries, MCP tool names, hook output. It says how much
would actually **leave context** if the package were disabled: while the skill listing is at its cap (within
10% of its budget), a package's share of it is handed to other skills' descriptions rather than removed; below
the cap it leaves with the package. "At its cap" is listing-budget's rule: near the budget *and* cutting
descriptions. It rests on every declared always-on package source, observed or not, so
the session window can't change what it rests on. One `info` finding.
On the reference machine: 44 packages, ≈24k tokens; one package adds ≈4.1k, mostly MCP tool names.

## listing-budget (Claude Code adapter)

The harness caps the skill listing at `contextWindow × charsPerToken × skillListingBudgetFraction` characters
(VERIFIED in the 2.1.285 bundle; 30,000 for a 1M-context model, 8,000 for the 200k default). Entries past the
cap reach the model as a bare name, so the model — told to pick a skill when "the task at hand is one a listed
skill covers" — has only the name to go on. The check parses the delivered listing (exact text, from sessions)
and counts described versus name-only entries per package — only lines naming an entity the session recorded as
listed, since a description can hold `- ` bullets of its own. It fires only when the delivered listing is within
10% of its budget: below that, a bare name is one with no description to give, not one the cap cut. It names the
two fixes: disable unused packages, or raise the fraction (stating what each +0.01 costs in tokens, from the
listing's own tokens-per-character). One `warn` finding. "The cap is cutting" — within 10% of the budget and some
listed skill shown by name only — is one rule shared with package-footprint, so the two never disagree. On the
reference machine: 436 of 505 entries name-only, the listing at 29,973 of 30,000 characters.

## duplicate-name

**Algorithm:** group entities by namespace (entities the agent chooses from one list: skills and slash
commands share one) and short name — the name without what provides it, so `data:sql-query` and
`analytics:sql-query` collide. Each harness's entity kinds say how names are prefixed (`:` for Claude Code's
package entities), so core assumes no convention. One name declared twice in one package (a skill and a command)
is one choice for the agent and counts once; the member the agent was shown stands for it. Report groups with
more than one distinct name. Implemented in `checks/duplicate_name.py`.

Each group is **described mechanically**, from the files behind its members, because the right fix differs:

| Description | Test | Action |
|---|---|---|
| identical copies | every declaration is the same file content as another | remove all but one |
| two or more sets of identical copies | every declaration is a copy of another, in more than one set of copies | remove the extra copies in each set |
| N of M are identical copies | some declarations are copies of each other, the rest differ | remove the extra copies, then compare what remains — one may have drifted |
| same description, different bodies | identical descriptions, no two declarations' files identical | probably drifted copies: keep one, or rename one if they really do different jobs |
| different descriptions | otherwise — drifted copy or unrelated namesake; we don't guess which | rename so names say what each does, or remove one |

A **declaration** is a name in one scope — one package, the user's, the project's (its authority). Copies are
judged between declarations, not names: a user skill and a project skill of one name are two declarations that can
drift apart, so when the user's is a package's copy and the project's has drifted the group is "2 of 3 declarations
are identical copies" — the word "declarations" appears only when a name is declared in more than one scope, so M
is never mistaken for the member count — and never "identical copies" with advice to delete the one file that
differs. Descriptions are compared over the same declarations.

A member known only from a session — a harness built-in, a package not found on disk — has no file of its own,
so it is never counted as a copy, and a group with such members and one description is "same description", with
no claim about bodies nobody read. A declaration counts as a copy when any entity behind it matches another
declaration's file, so a twin the agent wasn't shown still counts (here, a `divine` command symlinked to a plugin's,
beside its workflow namesake in the same package). Every entity behind the group is one of the finding's
**members** (Finding output, below), a name declared more than once once per entity, so a copy mark
(`identical_to`, the entity id of a member it matches) sits on the very file that matches, leaving the drifted one
unmarked. Members are listed by name, each name's entities together, ordered by kind. Since 2026-10-04 the finding's
`detail` says only what the exposure means; the terminal prints the members, one line each — name, kind and where
it sits (its package, else its scope), what the agent saw, the copy mark, a symlink, the description — so two of one
kind under one name read `skill (user)` and `skill (project)`, and a mark pointing at such a name carries the place
too: `(identical to deploy (user))`. Before, the text listed them, a name declared twice under a "declared N times
under this name:" line with only the description of the one the agent was shown; now each declaration shows its
own. The locations are every source with a file behind the group, once each. A group of mixed kinds is
counted as "entries" rather than mis-pluralised. A member only sessions show can leave the session window, or be
filtered out by a harness update, with nothing changed, so the check is `needs_change`: a group is closed only when
one of its members' files changed or went away.

**Exposure** comes from what the agent saw (`Visibility`, below): **active** (`warn`) when it was shown two or
more members, by name or with a description; **latent** (`info`) when it wasn't; **unknown** (`warn`, "exposure
unknown") when no recent session shows what it saw — the collision is certain even when its exposure isn't. A
copy that is a symlink is flagged — whatever installed it may recreate it, so "remove it" may not stick. Identity
is the namespace and short name, so removing one member does not make the group a new finding.

**Reference machine:** 39 active and 6 latent groups, including the expected 4× `sql-query` — "3 of 4 are
identical copies", acme-tools' having drifted — 4× `visualize`, and 5 entries named `feedback` (one
of them a skill and a command under the same name).

**What the agent saw.** Sessions report a `Visibility` per entity — `described`, `name_only` (a listing cap, or
a deferred tool), `not_shown` (hidden from the agent, though a user may still invoke it) — taken from the latest
interactive session that showed the listings. Core checks use it without parsing any harness's listing format.

## trigger-overlap

Two entities whose descriptions are so alike that choosing between them is a coin flip. Built:
`checks/trigger_overlap.py`.

**Algorithm (lexical, decided 2026-10-02):**
1. One entity per (list, name): a skill and a command sharing a name are one choice, and the one the agent
   was shown stands for both. Harness built-ins take their descriptions from the delivered listing.
2. TF-IDF over each entity's short name and description — sublinear term frequency, smoothed IDF over all
   descriptions, unit-length vectors. No model, no cache, deterministic.
3. Cosine similarity between every two entities chosen from the same list (skills with commands, agents with
   agents): ~600 entities, about a second.
4. Skipped: pairs sharing a short name (duplicate-name reports those) and pairs within one package, which
   are usually a deliberate family — role variants of one tracker here. `overlaps(same_package=True)`
   includes them.

**Threshold: 0.8, from labels (VERIFIED 2026-10-02).** Sixteen cross-package pairs spread evenly over
0.27–0.69, shown shuffled with scores hidden, were all judged distinguishable by the user — including two
experiment-search skills sharing most of their wording (0.69) and two Google Drive health checks (0.54).
Shared wording is not a shared job. The threshold therefore sits above every labelled pair (nothing on the
reference machine falls between 0.69 and 0.88), and what the check reports is near-copies: the same
description installed under two names. Here, 2 — two Metrics skills with word-for-word identical descriptions
(0.99) and two brainstorming skills differing by one phrase (0.88). If labels on another machine find
same-job pairs below 0.8, lower it there.

**Exposure:** active (`warn`) when the agent was shown both descriptions, latent (`info`) when it wasn't, and
unknown (`warn`) when no recent session shows what it saw, each member's `seen` then null (`[not known]`); both here are
latent, as most skills reach the model name-only. The pair are the finding's two members (Finding output, below); `detail` keeps the shared wording and
what the exposure means (since 2026-10-04; it listed the pair before). A finding rests on both members' sources. Confidence `suggestive`. Similarity weighs words by how common they are across all descriptions, so
the check is `needs_change`: a pair that drops below the threshold while neither description changed was
outweighed, not fixed.

**Output:**
```
trigger-overlap   data:metrics and acme-tools:metrics_cli have nearly identical descriptions (0.99) (latent)
                  data:metrics  skill (data@acme-market)  [name only]  "Official Metrics skill. Load Metrics UI URLs, …"
                  acme-tools:metrics_cli  skill (acme-tools@acme-market)  [name only]  "Official Metrics …"
                  Shared wording: metrics, discover, table, queries, craft, joins
                  Latent: the agent wasn't shown both descriptions in the latest session; if it is, it has …
                  → If they are copies, remove the one you don't use; if not, rewrite one description
                    to say when to use it rather than the other.
```

## broken-reference

Rules that name something that doesn't exist. Pure decay, and mechanically certain.

**This check must have zero false positives.** Hand-verify every hit before v0 ships. If a category
produces any false positive, drop that category rather than tuning it — a broken-reference report
that's sometimes wrong is worthless.

Provable kinds only (decided 2026-10-02). One public id with three contributors — core for links, the adapter
for what depends on how its harness names, invokes and installs things:

| Reference | Detected | Resolved against | Where |
|---|---|---|---|
| Relative markdown link | `[text](path)` outside code, to a path inside the file's own directory | the filesystem, as read with the configuration | core |
| Namespaced entity | `pkg:name` as an inline code span, `Skill('…')` or `subagent_type: …` — the last two inside code blocks too, where they are what the agent copies | entity registry: the namespace exists, the name doesn't | Claude Code adapter |
| Package path | `${CLAUDE_PLUGIN_ROOT}/…` in a hook command, or after an interpreter (`python3`, `bash`, …) in package text | the package root, as read with the configuration | Claude Code adapter |

Not flagged, because each produced false positives on real data or can't be proven from configuration:

- bare `pkg:name` mentions in prose — feature-request titles ("Add /brain:search for…"), names an entity
  declares for itself;
- links leaving the file's directory (`../`), absolute paths, placeholders such as `[title](url)`, and anything
  inside code — a fence opens at any indent, as code in list items does, and closes only with a fence no deeper
  than 3 spaces past where it opened, so a fence shown inside an outer code block doesn't end it;
- `${CLAUDE_PLUGIN_ROOT}` paths in prose — optional files ("if it is present"), instructions *about* paths,
  documentation examples;
- shell commands (`which` on a binary) — the answer depends on the PATH of the moment;
- tools — those are `unavailable-reference`, a distinct finding.

Hook commands that test for their file first (`[ -f "$0" ] && …`, `|| exit 0`) are `info`: they skip quietly
rather than fail. "Did you mean" is offered only for a near-certain match (similarity ≥ 0.9, e.g.
`code_search` → `code-search`); otherwise the closest name is mentioned without a recommendation.

On the reference machine (VERIFIED 2026-10-02, every hit checked by hand on disk): 9 findings, no false
positives — six dangling `pkg:name` references (three are `_` written for `-`), one linked example file that
isn't shipped, and two scripts that instructions run but that aren't in their packages.

## emphasis-density

Proxy for alert fatigue, and a good signal that an author was fighting something they couldn't
test. Built: `checks/emphasis.py`, defaults in `src/agentlint/data/emphasis.toml`.

### The marker table is data, not code

Markers live in a shipped TOML file (`data/emphasis.toml`) that users extend. Never a hardcoded
regex — different orgs shout differently, and this list will grow forever. Each marker has an `id`,
a `pattern` (Python regex, matched against prose with code removed), a `weight`, a `scale` (`flat`
by default) and a `unit` — what a run's length counts, `chars` (default) or `words`.

The shipped defaults were chosen on the reference machine's 619 prose sources (2026-10-02) and differ
from this doc's first sketch where real text proved it noisy:

| id | pattern (abridged) | weight | scale | why it is shaped so |
|---|---|---|---|---|
| `must`, `never` | `\bMUST\b`, `\bNEVER\b` | 2 | flat | as sketched |
| `do-not` | `DO NOT`, `DON'T` | 2 | flat | the most common shout here (145), only half-counted by a caps run |
| `alarm-word` | `CRITICAL`, `IMPORTANT`, `MANDATORY`, `ALWAYS`, `ONLY`, `STOP`, `REQUIRED`, `WARNING` | 1 | flat | 900+ uses; a bare capital `NOT` was left out — 1,570 hits, mostly SQL |
| `exclamation` | `!+` directly after a word | 1 | superlinear | a bare `!+` matched `<!--` comments and `!=` |
| `shouting-tag` | `<(EXTREMELY_)IMPORTANT>` | 4 | flat | as sketched |
| `not-negotiable` | "no exceptions", "this is not negotiable", … | 4 | flat | as sketched |
| `caps-run` | three or more capitalised words in a row | 1 | linear, in words | two-word runs were mostly acronyms and SQL (`GROUP BY` ×26, `REST API`, `PKG ACL`) |

**Merge semantics:** defaults load first, then `~/.agentlint/emphasis.toml` is merged on top, field by
field. A new `id` adds a marker (after the defaults); reusing an `id` overrides only the fields it sets;
`enabled = false` disables one. Adding a marker never requires editing the defaults. A malformed file —
bad TOML, not UTF-8, unreadable (no permission), unknown field, bad regex, a pattern that matches empty text, an
unknown scale or unit — stops
`agentlint check` with a tool error (exit 2) naming the file and the marker, rather than being half-applied; so does
an unknown top-level table (`[[markers]]` for `[[marker]]`) or an `enabled` that isn't `true` or `false`, which
would otherwise be ignored without a word, or switching off an id that isn't a shipped marker. Every marker is
checked, switched off or not.

### Run length

```python
SCALES = {
    "flat":        lambda n: 1.0,          # length irrelevant
    "linear":      lambda n: n,            # n× as loud
    "superlinear": lambda n: 2 * n - 1,    # 1, 3, 5, 7 — escalation penalised, bounded
}
```

The pattern matches a **run**, and the scale function is applied to its length.

| Text | Matches | Score |
|---|---|---|
| `fix this!` | one run of 1 | 1.0 |
| `fix this!!` | one run of 2 | 3.0 |
| `fix this!!!` | one run of 3 | 5.0 |
| `fix a! fix b! fix c!` | three runs of 1 | 3.0 |

`a!!!` and `a! b! c!` are different things: one person escalating versus three ordinary emphases.
Counting `!` characters would score them identically, which is why the regex captures `!+` rather
than `!`. Same logic covers consecutive capitalised words, counted in words — one capped word is
normal, a whole capped sentence is not. (This table is a test.)

### Scoring

```python
score = sum(marker.weight * SCALES[marker.scale](run_length(m)) for each match m) / (words / 100)
```

Over the source's body — frontmatter is listing metadata, not what an invocation delivers — with fenced code
(at any indent) and code spans removed. Where markers overlap, the text scores once: the highest-scoring match
wins, and runs counted in words (capitals) are measured over what is left, so "PKG ACL MUST" is one `MUST`,
not a three-word shout as well: a run of capitals is found in the text as written (so `API!!! KEY TOKEN` is no
run) and counts only the words no other marker scored. Normalised per 100 words so a long source isn't
penalised for being long. Sources under **50 words** are not scored — one
marker swings them. The comparison population is every prose source that reaches the model (instruction
files, entity definitions, observed hook output, managed instructions); with fewer than 20 scored, the
check says nothing.

**Report comparatively, never absolutely.** Flagged: the top decile — the loudest 10%, rounded up, with ties
at the boundary included — and louder than the median source, so a population of equals flags nobody. The check is `needs_change`: a source that drops out while unchanged was outranked, not
fixed. The comparison is stated as a
rank ("the 3rd loudest of your 600 sources") or a share ("louder than 96%"), next to the median source's
score — not as "N× the median": the median here is 0.1 points per 100 words, which made ratios like 88×
that overstate.

**One finding per source** in the top decile, each saying when the source is in context — every session,
with every message, or when invoked — loudest first. (A two-tier form, always-on sources individually and
invocable definitions summarised in one finding, was tried and dropped for consistency; user decision,
2026-10-02.)

### Output names the drivers

A bare score isn't actionable. Each finding lists the distinct matched texts, most points first:

```
emphasis-density   hook: SessionStart · power-engineer@acme-market is the 4th loudest of your 591
                   sources, and in context every session  [suggestive]
                   8.5 emphasis points per 100 words; your median source scores 0.1.
                   Driven by: "SKILL APPLIES TO YOUR TASK" ×1, "<EXTREMELY-IMPORTANT>" ×1, MUST ×2, …
```

On the reference machine (VERIFIED 2026-10-02): 60 findings of 591 sources — among them the SessionStart hooks
of power-engineer (`<EXTREMELY_IMPORTANT>`, "This is not negotiable"), acme and catalog_lookup. Scoring the
body only removed findings that came entirely from description text. Every finding is `info`, `suggestive`.

Use this primarily as a *suspicion* input to the ranker (`03-ranker.md`) rather than as a
standalone finding — on its own it's `SUGGESTIVE` confidence at best. `emphasis.measure()` gives the
ranker each source's loudness without the presentation.

## untraced-injection

Delivered text that traces to nothing configured — counted in the budget, never fingerprinted
(`01-config-reader.md`). One `info` finding per distinct text at an injection point (identity: the point and the
whitespace-normalised text), merged across the labels it arrived under (hook events, matchers), with how often
and when it is delivered. Read from this project's sessions only, so with none it is not evaluated. On the reference machine: an ≈85-token reminder injected with
every message that no file on disk contains. `checks/delivery.py`.

## definition-size

Invocable definitions (loaded on invoke) of at least 10,000 tokens, as one ranked `info` finding. These are
definition sizes on disk; how much reaches the model when invoked is not measured until an invocation is
recorded, and the finding says so. On the reference machine: 63 definitions over 10k tokens, the largest
≈90k. `checks/delivery.py`.

## interrupted-start (Claude Code adapter)

Sessions whose first submission was abandoned before any reply — a cancelled prompt, or `/model` / `/effort`
and a resend — and whose final submission carried no skill listing: the harness drops the abandoned
submission's attachments and does not send the listing again, so the agent has no list of skills for the whole
session (VERIFIED 2026-10-02: the reference session's own context lacked it). One `warn` finding naming the
sessions, one line each: its start in local time (as every other time the CLI and the UI show), its id, project
and harness version. On the reference machine: 2 of the last 10.

## hook-health (Claude Code adapter)

From every recorded hook execution in the last N sessions (whole sessions, not just the first turn): failures
(non-zero exit, with the most common stderr line), runs killed at their timeout, and SessionStart hooks with a
median of 5s or more. Precision rules, so it doesn't cry wolf:

- **No rates.** The harness records failures and timeouts, but only some successful runs — none at all for many
  PreToolUse and UserPromptSubmit hooks — so how often a hook ran without failing isn't known. Findings give
  counts and sessions ("killed at its timeout 10 times in 2 sessions"), never "N of M runs".
- **Isolated** failures — fewer than 3 — are `info` ("could be transient", and the advice is to check whether
  it happens again). The advice always follows the grade: a live failure gets "fix or remove the hook" (or, for
  a timeout, "make it faster"), and one no longer configured gets "nothing configured runs it now; if it still
  fails, find what enables it at launch" — never advice to fix what isn't there.
- A failing command **no configuration declares now** is `warn` only if it failed in one of the two most recent
  sessions ("something enables it at launch"); otherwise `info` ("Last seen <date>; … probably already resolved",
  the date of its last failure in local time, as every other time shown — UTC before review round 12).
- Timeouts that **cluster** — most within ten minutes — say they point at a busy machine rather than the hook.
- Cancellations without a timeout are skipped: they reflect the user (Esc, resubmitting), not the hook.
- It judges only what it saw: a hook that didn't run in the sessions read is neither failing nor fixed, so its
  earlier finding stays open; one no longer configured at all counts as gone. It judges by recorded runs, not by
  what a hook printed (`Check.by_text=False`), so a hook whose output no session showed doesn't keep open a finding
  the runs say is gone (`10-reporting.md`, rule 2).

**What a finding rests on and points at** (2026-10-04; before, every hook-health finding rested on nothing and
pointed nowhere, so a row showed its package only inside the title). A finding rests on (`sources`) and points at
(`locations`, one per hook) the hooks configured now that run its command **for one of the events its failing runs
fired under, with a matcher that accepts one of those runs' match targets** (the transcript names each run
`<Event>:<target>`, e.g. `PostToolUse:Bash`; the tracer filters by the same matcher, `_matcher_allows`). Each configured hook entry is a source (`claude-code:hook:<owner>:<file name>#<JSON pointer>`), so the
row shows that hook's package and the file that declares it (`plugin.json`, `hooks.json`, `settings.json`), and the
detail its JSON pointer. Several packages may declare one command (`${CLAUDE_PLUGIN_ROOT}/hooks/start.sh`): the
finding rests on every one of them, since the transcript records the command unexpanded and can't say which ran.
Entries the matcher already tells apart are not several: one package declaring one command for PostToolUse on
`Write|Edit|Read` and again on `Bash` (llm-rules does) has a `PostToolUse:Bash` timeout rest on the `Bash` entry only.
The title's owner comes from the same entries — one package by name, several as "one of …" — so title and cells
never disagree, and the title names the events of the failing runs only (before, every event the command ran under:
"A PostToolUse/PreToolUse/SessionStart hook was killed … 1 time" for one PreToolUse timeout).

- **The same command under another event is not the hook that ran.** A hook fires only for the event it is
  declared under, so one declared for SessionStart never produced a UserPromptSubmit run — the configuration
  changed since, or something else enables it. Precision over recall: such a finding rests on nothing and points
  nowhere, it is graded as not configured, and the title says "(declared now only for SessionStart)", the detail
  "Not in your configuration files for UserPromptSubmit". Likewise the same command under the same event with a
  matcher that rejects every failing run: "(declared now only for PostToolUse on Write|Edit|Read)", the detail
  "Not in your configuration files for PostToolUse on Bash". A run recorded with no target is accepted by every
  matcher, as in the tracer. With no configured hook running the command at all, the
  title says "(not declared in your configuration now)", as before. A module hook (handlers registered in code)
  names no command, so it is never matched — before, a run recorded without a command matched every module hook.
- **No line.** A `Location`'s line is a line of its source's *text*, and a hook source's text is what the hook
  printed (what `/api/texts` serves and an excerpt would quote), not its JSON declaration — so a line in
  `plugin.json` would quote the hook's output. The JSON pointer (the source's anchor) already names the entry
  exactly, and every renderer shows it after the file (`plugin.json#/hooks/UserPromptSubmit/0/hooks/0`), so
  hook-health locations carry no line and the UI shows no excerpt for them.
- Suppressions armed before this change rested on nothing; one doesn't lapse when its finding starts resting on a
  hook that was configured when it was armed, and takes that hook up as its baseline from then on
  (`10-reporting.md`, "Suppressions").

On this machine (2026-10-04, `check --json` into a throwaway store): 27 of the 29 hook-health findings rest on and
point at configured hooks (28 hooks of 14 packages; one SessionStart command is declared by three packages); the
other two are commands no configured hook has under any event — a script since removed from a package, and a
package whose directory is gone — both info, "no longer configured". Against the code before, the 171 finding ids
and every other field were the same, but for the one title above. Filtering by matcher changed none of these
numbers (re-run 2026-10-04): no finding here had a second entry the matcher rules out.

All 78 timeouts recorded on the reference machine ran past their limit (genuine), and several clustered during
heavy machine load. Found there: hook scripts that no longer exist, hooks of a package whose directory is missing,
and SessionStart hooks killed at their timeout.

## unavailable-reference (Claude Code adapter)

Instructions that name a tool the agent cannot use — the doc's "exists but unavailable in this environment",
kept separate from broken references. Scans instruction files, hook output, managed text and entity bodies
(never settings, code or harness listings) for `mcp__…` names and for harness tool names used as tools
("the Grep and Glob tools"). A capitalised word only counts if it is a known harness tool name — every name
recent sessions delivered, plus documented historical ones (`Grep`, `Glob`, `Task`, `MultiEdit`, …) — so
"AI tools" never matches; lists split only on whole-word "and"/"or" (so `SlashCommand` stays whole), and a tool
name never ends on sentence punctuation. A failed server is matched by its exact name. Says nothing when no
session shows which tools were delivered.

| Case | Severity |
|---|---|
| denied by a permission rule in any settings layer, where the instruction asks for it | `warn`, quoting the sentence — a mention inside a prohibition agrees with the rule and is skipped, judged within the clause holding the name: "never/avoid/instead of" count only before it ("never use X", not "use X instead of curl"), "blocked/denied/disabled" anywhere ("X is blocked here"); clauses break only at ";", "so" and "but" — never at commas or dashes, so "never use A, B or C" stays one prohibition |
| harness tool name not delivered (renamed or removed) | `warn` if the text is in context every session, else `info` |
| tool missing from a server that otherwise connected (plugin or user-configured) | `warn` — a stale tool name |
| tool from a server that isn't configured, or failed to connect | `info`, one finding per server — optional tools written for other setups |

On the reference machine: a SessionStart hook tells the agent to use a web-search tool that managed policy denies,
and to prefer search over "the Grep and Glob tools", neither of which this harness provides.

## tested-expectation and tested-consistency (behaviour, from tests)

Decided with the user 2026-10-07 ("any delta short of perfect is a finding"). The evidence is each target's
**latest decided test** of what its situations expect (`07-analysis.md`, "The expectation's strength"): the newest
of the project's jobs of this harness (the 30 newest are looked through) whose runs decided every situation of the
target — a job still running, or with too few readable runs, is passed over for an older decided one. A test counts
only while the target's text is the text its situations were written for: a situation's id is a hash of the target
text's hash and the situation, so it is checked against the store's situation, or recomputed from the plan's text
when the situation was regenerated away; an edited target has no test until it is tested again. A test from before
expectations, or of a target no variant could remove (no runs without it), says nothing here.

- **`tested-expectation`**, one finding per target whose result isn't `works`, its weakest situation's verdict in the
  title: "not followed" ("the agent didn't do what it is for"), "backfires", "adds nothing" — `warn` — or
  "partial" — `info`. The detail names the test's date and every situation: what it expects, the runs with the target
  that did it, then without ("never run without" for one not followed, with what the runs did instead), the
  strength and the verdict. Figures: situations falling short, mean strength. It points at the target (the section's
  first line, or the whole source).
- **`tested-consistency`**, `info`, one finding per target with situations whose runs with it didn't use the same
  tools, skills and agents (D10's screen): the agent can't make up its mind there. It names those situations.

Both are `behaviour` / `measured`, identified by the target's id — so a finding stays the same finding across tests,
and a later test that says `works` (or whose runs agreed) closes it — and `by_text=False`: judged by recorded runs.
VERIFIED 2026-10-07 on a copy of the user's store (deleted after): the power-engineer SessionStart hook's three real
situations, each run 3 times on 7 Oct, given the expectation `skill:*` in a format-6 plan built from those runs,
gave one `warn` — not followed in 3 of 3, what the runs did instead listed (no action; `shell:head`, `shell:ls`, …).

## Deferred: declared-vs-delivered

Comparing what the config files say should load against what a transcript shows actually loaded.
**Not in v0.** Rationale is in the deferred list in `docs/03-checklist.md` — the short version is
that its accuracy is a direct proxy for how correct the config reader is, and a reader bug
presents as a false positive, which v0 cannot afford.

## Finding output

All checks emit the shared `Finding` type (`report/finding.py`, see `10-reporting.md`), made by the check's own
`Check.finding`, which fills in `check`:

```python
LISTING_BUDGET.finding(                  # Check("listing-budget", ...): check="listing-budget", the public id
    kind=FindingKind.DELIVERY,
    severity=Severity.WARN,
    confidence=Confidence.CERTAIN,       # MEASURED only for behavioural findings, and always for them
    title="436 of 505 skills reach the model as a bare name",
    detail="...",                        # what we did and what we saw
    discriminator="skill-listing",       # identity within the check
    sources=(...), locations=(Location(source_id, line), ...),  # where it points: a source by id, a line in its text
    figures=(Figure("name-only", 436, "entries", "entry"), ...),   # machine-readable numbers; the unit, and its form for 1
    suggested_action="...",
    members=(...),                       # what it sets side by side, as data; () for most checks
)
```

A location names a source by id and, optionally, a line within that source's text — never a path or a label —
so every renderer joins it to the snapshot's source by id and shows that source's file, or its label for text no
file holds (`10-reporting.md`). Discriminators never use locations, so this changed no finding's id (2026-10-04).

`members` is for a finding that sets entities side by side — duplicate-name's namesakes, trigger-overlap's pair.
Each `Member` names the entity (id, name, kind label), the source that provides it, where that sits (`scope`, an
authority; `package`), what the agent saw of it (`seen`, a `Visibility`, null when no session shows it), its
whole description, and, for duplicate-name, `identical_to` (another member's entity id) and `symlink`. The same
facts are never written twice: `detail` keeps why the finding matters, and every renderer — the terminal's member
lines, the UI's detail — draws the members from this one list (decided 2026-10-04, the redesign's Q3 (a)). A member
is listed once, and a copy mark points only at another member.

Identity is `sha256(check | discriminator)` — the check decides what makes a finding *this* finding (the
shared name, the entity pair, the source and the reference). Never membership, wording or numbers: removing
one of four same-named entities must not read as "1 fixed, 1 new".

## CLI

```
agentlint check                 # all checks
agentlint check --only trigger-overlap,broken-reference
agentlint check --json
agentlint check --fail-on warn  # exit nonzero, for CI
```
