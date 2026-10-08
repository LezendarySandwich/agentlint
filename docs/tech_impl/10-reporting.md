# 10 — Reporting

One finding type for every stage. Static checks and behavioral tests emit the same shape, so the
renderer doesn't care where a finding came from.

## The Finding type

`report/finding.py` is the definition; this is its shape:

```python
@dataclass(frozen=True)
class Finding:
    check: str                      # public check id: "trigger-overlap", later "unstable", "backfires"
    kind: FindingKind               # COST | COLLISION | BROKEN | DELIVERY | HEALTH | EMPHASIS | BEHAVIOUR
    severity: Severity              # INFO | WARN | ERROR
    confidence: Confidence          # CERTAIN | MEASURED | SUGGESTIVE
    title: str                      # one line, specific
    detail: str                     # what we did and what we saw
    discriminator: str              # what makes this finding itself, within its check
    sources: tuple[str, ...] = ()   # source ids it rests on
    locations: tuple[Location, ...] = ()   # Location(source_id, line): a source by id, a line in its text
    figures: tuple[Figure, ...] = ()       # the numbers it rests on, machine-readable
    suggested_action: str | None = None    # an action, or None when there honestly isn't one
    members: tuple[Member, ...] = ()       # entities it sets side by side, as data (duplicate-name, trigger-overlap)

    id = sha256(f"{check}|{discriminator}")[:16]   # property: see "Stable identity"
```

A `Figure` is a number the finding rests on: its `label`, `value`, `unit` ("runs", "tokens", "percent") and
`unit_one`, the unit for a value of exactly 1 ("run", "token", "percent"), so a renderer writes "1 run" without
guessing an English plural (review round 11; before, the UI printed "timeouts 1 runs"). Every check gives both.
Additive: `--json`'s `schema_version` stays 3 and the report's 1.

A `Member` is one entity a finding sets beside others: its id, name and kind label, the source that provides it,
where that sits (`scope`, an authority; `package`), what the agent saw of it (`seen`, null when no session shows
it), its description, and duplicate-name's copy mark (`identical_to`, another member's entity id) and `symlink`
(`02-static-checks.md`, Finding output). The facts live there once: `detail` says why the finding matters, and the
terminal and the UI both draw the members from the list (2026-10-04; before, `detail` listed them as text).

A `Location` names the source a finding points into by id, and a line within that source's text (1-based, or None
for the source as a whole) — never a path or a label (decided 2026-10-04, design review 6.1). Every renderer joins it
to the snapshot's source by id: the terminal prints `at` and the source's file, with its anchor after a `#`
(`report/paths.place`), or the source's label for text no file holds, or the id itself for a source the reading
doesn't have; the UI links it to that source. The ranker matches a proven break to a section by source id too
(`03-ranker.md`). Before, `Location.path` held a path or a label and was joined to sources by string equality,
which the UI could only do by splitting at `#`. `--json` and the report write `{"source_id": …, "line": …}` where
they wrote `{"path": …, "line": …}`; `schema_version` is 3.

Each finding validates itself: `MEASURED` confidence goes with behavioural findings and only those, a member is
listed once, and a copy mark points only at another member. What a
finding was measured under (fingerprint, time) belongs to the analysis that produced it, recorded in the
database, not to the finding.

### Confidence levels

This distinction must survive into the UI. It's the difference between a tool people trust and a
tool that burns someone once.

| Level | Meaning | Who emits |
|---|---|---|
| `CERTAIN` | Mechanically true. A referenced file does not exist. | Static checks |
| `MEASURED` | Observed under stated conditions. "No effect across 4 situations × 10 runs." | Behavioral tests |
| `SUGGESTIVE` | A pattern worth looking at, not a claim. High emphasis density. | Heuristics |

Static checks are `CERTAIN`, except heuristics (emphasis-density, trigger-overlap), which are `SUGGESTIVE` and
say so. Nothing is ever `CERTAIN` on the basis of behavioral evidence — the system is probabilistic and the
report must not pretend otherwise.

## Writing findings

Rules for the text, because this is where a precision-first tool is won or lost:

**Say what you did, not just what you concluded.**

> ✗ "This rule does nothing."
> ✓ "Removed it and behavior was identical across 4 situations × 10 runs. 1 further situation was
>    unmeasurable (unstable baseline)."

**Name the specific thing.**

> ✗ "Skill selection is ambiguous."
> ✓ "6/10 runs chose `data:sql-query`, 4/10 chose `analytics:sql-query`."

**A test of expectations may say "works"** (decided with the user 2026-10-07): each situation names what its
target should make the agent do, the situations are trusted, and "works" means every run with the target did it and
none without — the intent is the expectation, counted, never judged. The behaviour findings (`tested-expectation`,
`02-static-checks.md`) say every count they rest on, situation by situation.

**Never let "changes behaviour" read as "works."** For a test without expectations, until `matches_intent` ships, a measured effect
means the rule does *something* — not that it does the right thing. See `07-analysis.md` for the
verdict vocabulary and the full label table.

> ✗ "This rule is working — it changed behaviour in 7 of 10 runs."
> ✓ "Removing it changed behaviour on 2 of 3 situations. Whether that is the change the rule
>    intends is not measured."

**Give an action, or admit there isn't one.**

> ✓ "Narrow one description, or remove one."
> ✓ "No action available — managed settings can't be removed, so we can't verify this rule binds."

**Never print a hash where a diff would do.** Compute the position where traces diverged and show
it.

## Persistence and lifecycle

Timestamped dumps would be wrong. A finding persists across readings, and can be deliberately accepted: without
identity a dismissal can't follow it, and the report becomes an undifferentiated pile every run.

### Stable identity

```python
@property
def id(self) -> str:  # Finding.id, report/finding.py
    return sha256(f"{self.check}|{self.discriminator}").hexdigest()[:16]
```

Each check puts what makes a finding itself into the discriminator: the entity pair, the broken
reference, the source id. Deliberately **excluded**: timestamps, scores, token counts, ordering — and
the member list, so removing one of four duplicates is the same finding, not "1 fixed, 1 new".

### States

Per project — the directory the configuration was read for, because project files change the
findings. On every recorded analysis, diff the computed set against the project's open findings by id. One
vocabulary throughout — SQL, Python and docs (`FindingStatus` in `store/__init__.py`, `SuppressionState` in
`store/suppressions.py`):

| Situation | Status | Shown as |
|---|---|---|
| present now | `open` | shown |
| open before, absent now, and this analysis could have seen it | `closed` | **not shown** — see below |
| present now, id in suppressions, nothing it rests on changed | `open`; suppression `active` | hidden, counted (`Tracked.suppressed`) |
| present now, id in suppressions, what it rests on changed | `open`; suppression `lapsed` | shown, flagged |
| suppressed, but no check finds it in that project now | suppression `moot` | in `suppress --list` only |

**Neither "fixed" nor "new" is shown anywhere** — not in the UI, the terminal or `--json` (decided with the user
2026-10-03, "fixed" first, then "new": "similarly brittle"). Judging what disappeared or appeared from presence and
absence proved brittle: four review rounds found false fixes, and a falsely closed finding comes back as "new".
The store still opens and closes findings — suppressions arm against them per project and are moot where they are
closed — but nothing derived from that reaches the user; there is no record of transitions (schema 8 dropped the
`history` table, which nothing read). A view of what changed in the configuration itself is future scope. Absence
closes a finding only when this analysis could have seen it. A finding absent now is **closed** only if all of
these hold:

1. **Its check judged it** (`CheckResults.judges`). The check was evaluated — not deselected by `--only`, not `NotEvaluated` for
   lack of input (no recent sessions, no session from this project, too few sources) — and, if its
   evidence covers only some of its earlier findings, this one was in scope. hook-health judges a hook
   only if it ran in the sessions read, or is no longer configured at all: a hook that simply didn't
   fire is not a closed hook.
2. **Nothing it rested on went unobserved.** A source still configured whose text no recent session
   showed (a hook's output) keeps it open. A source that is gone doesn't: removing it is how such
   findings get closed. A check that judges by something other than its sources' text says so
   (`Check.by_text=False`, `CheckResults.not_by_text`) and is exempt: hook-health rests on the hooks that ran a
   failing command but judges by their recorded runs, so what a hook printed has no bearing on whether its
   failures stopped (2026-10-04, when hook-health findings began resting on hooks).
3. **For a check whose findings can vanish with nothing they rest on changed, one of its own inputs
   changed** (`needs_change`). emphasis-density (the top decile of your sources) and trigger-overlap
   (similarity weighted by how common words are across all descriptions) can drop a finding because
   *other* sources changed; duplicate-name can lose a group because a member only sessions show left the
   session window, or a harness update filtered it out. Unchanged, such a finding was outranked or out of
   view, not gone.

A finding's **inputs** are the sources of configuration it rests on, with their declared hashes. Sources
the harness makes — its listings, its own prompt — carry no configuration (their declared hash never
changes) and whether a finding involves them depends on what recent sessions showed, so they are not
inputs. The exception is a package's share of a listing: it is declared from disk, one per installed
package, so it is an input — it is how a newly installed package joins what token-budget, package-footprint
and listing-budget rest on, and how its removal shows. Being configured already, a share never lapses a
suppression by joining or leaving a finding as the sessions read change.

`record()` derives everything these rules need from the check results and the configuration reading it is given —
each finding's inputs, the sources unobserved, every source's declared hash — so no caller can leave a guard out
and have it default to closing (design review 2.1; before, four optional arguments did exactly that). Its only
other arguments are the project, the time, and whether to write — a reading not written carries a note saying
why, and a written one carries none.

And only readings that are comparable are recorded at all (`Options.comparable`): `check` or `show` with a
`--sessions` other than your `sessions` setting sees different evidence, so it neither updates the lifecycle nor stores a snapshot — `check`
says so on its summary line (and suppressions still hide what they hide), `show` in a note on stderr.

VERIFIED 2026-10-02 on this machine: normal → `--history 0` → normal, and normal → `--history 2` →
normal, closed nothing. The review that prompted rules 1 and 3 reproduced 13 hook-health findings
falsely closed by `--history 2` and an emphasis finding closed by adding louder sources elsewhere. (The flag was
`--history` then; it is `--sessions` since 2026-10-04, and `--history` went the same day.)

```
agentlint check — claude-code 2.1.285 · ~/projects/agentlint · 171 findings (9 error, 54 warn, 108 info)
```

### Suppressions

A precision-first tool needs a mute button, or the one false positive you can't fix nags forever
and the user mutes the whole tool instead.

A suppression is **global** — dismissing a finding id dismisses it in every project — but what a finding
rests on can differ by project: the always-on budget includes each project's own instruction files. So
each project keeps a **baseline**, `{source id: declared hash}` for the sources the finding rested on
there: armed by `suppress` in every project where the finding is open (against each project's latest stored
configuration), and in any other project the first time a check sees the suppressed finding there.

```
agentlint suppress <finding-id> --reason "..."     # any unique prefix of 4+ characters; reason required
agentlint suppress <finding-id> --cwd <project> --reason "..."   # the command the UI and report offer
agentlint suppress --list                          # each with its reason; per project active, lapsed, or moot
agentlint unsuppress <finding-id>
```

`suppress` first checks the project named by `--cwd` (default: here) exactly as `check` would — every check, the
sessions setting, the snapshot stored and the analysis recorded, nothing printed — then, in one transaction, arms
every project where the finding is open. That project is where you saw the finding, and it is checked every time,
not only when no check has recorded the id: the UI records the lifecycle only when a page loads (and never at
another `--sessions`), so the finding may be recorded only in another project, closed here, or recorded here before
an edit the UI is already showing. Without that check the project you suppressed it from would get no baseline, and a project without one
never lapses — the finding would stay hidden there however its sources changed. If that check doesn't produce the
finding and no check has recorded it anywhere, `suppress` says so and exits 2. When the `harnesses` setting leaves out
the harness that check would read, `suppress <id>` reads nothing, stores nothing and suppresses nothing: it says so
and exits 2, as every reading command does (`suppress --list` and `unsuppress` touch only the store and still run;
2026-10-04 — before, it read and stored that harness's configuration and sessions). The command the UI offers always
names its project with `--cwd`, so it works from any terminal, for someone who has only ever run `agentlint ui`.

**From the UI** (2026-10-04) a finding is dismissed with **Dismiss**, its reason required: `POST /api/suppress
{finding_id, project, reason, harness}` does what `suppress <id> --cwd <project>` does — the same quiet check of that
project, with the scope's harness and your stored settings (refused, 409, nothing read, when the harnesses setting
now leaves the harness out, a stored setting can't be used, or a config.toml can't be read — where `main()` turns
`suppress` away; under `ui --sessions N` the check still reads your `sessions` setting's count, and a refusal for want
of the finding says so), then the
same `suppress` — and refuses, with why, a finding never recorded nor found there now (404) and one found only in
other projects (409: dismiss it from a project where it is found; the CLI dismisses it anyway, the UI dismisses only
what it shows). It takes a whole id, never a prefix. **Show again** is `POST /api/unsuppress {finding_id}`, and the
Dismissed screen lists `GET /api/suppressions` — `suppress --list`'s states per project (`11-ui.md`, "Views › 3.
Findings", "Views › 8. Dismissed").

Each project is armed against what the finding rests on there now: the sources it last rested on, at the hashes its
latest stored configuration declares. A finding's row is only as fresh as the last check that produced it, while a
later `check --only`, or one where its check couldn't tell, stores newer configuration; pairing the row's older
hashes with that configuration would mark a suppression "inputs changed, shown again" the moment it is made. So
suppressing again re-arms every project where the finding is open against what it rests on there now; the message
counts those projects, not ones where a baseline remains from before the finding closed, and names the finding as
the `--cwd` check just found it.

**Suppressions lapse when their inputs change.** If a source the finding rests on in a project is
edited, the finding resurfaces there — flagged `previously dismissed, inputs changed`, not as `new` —
and stays hidden elsewhere. "Edited" is judged against the project's **configuration**: something in the
baseline is declared differently now, or is gone — or the finding now rests on a source the configuration
didn't have when the baseline was armed (each baseline records every source configured then): a new instruction
file or hook in the budget, a newly installed package, a third namesake. A source that was already configured
never lapses it by joining or leaving the finding, as sources do when the sessions read show different things —
a namesake only sessions show, a listing switching between a skill and a command of the same name — while
editing an unobserved hook's configuration does. `suppress --list` makes
the same comparison, against each project's latest stored configuration and what the finding last rested on
there, and says per project whether the suppression is active, lapsed (shown again), or moot because the
finding is no longer found there — the last check that ran its check didn't produce it: closed, kept open only
because that check couldn't tell (a hook's output no session has shown since), or closed and since deleted by
retention. Only a finding the last check produced can be "shown again". Silently keeping the suppression would hide a genuinely different problem;
silently dropping it would nag. Showing the history is the honest middle. Findings that aggregate many
sources (token-budget, package-footprint) rest on every declared source of their kind, observed or not,
so a session window that happens to show different hooks can't lapse them. A finding that rests on no
source (a session whose skill listing never arrived, say) stays suppressed until unsuppressed. A baseline armed
while its finding rested on nothing — every hook-health finding before 2026-10-04 — is filled in, once, when the
finding first rests on sources that were all configured when it was armed: they join without lapsing it (the rule
above), and that recorded check writes them, at their hashes then, as the baseline (keeping what was configured when
it was armed), so editing them afterwards lapses it as it would one suppressed today. A finding that comes to rest on
a source configured since lapses instead, and its baseline is left as it was: suppress again to re-arm.
VERIFIED 2026-10-02:
token-budget suppressed in this repository stayed hidden in a second project with its own `CLAUDE.md`
(before, it resurfaced there as "inputs changed").

### The store

Tables in the agentlint database (`00-overview.md`, "State on disk"); `store/findings.py`. `analyses` holds one
row per recorded check, `findings` the latest of each finding per project (open or closed), `suppressions` your
dismissals (global) and `suppression_baselines` what each rested on per project, with every source configured there
when it was hidden. Their columns are stated once, in `_SCHEMA` in `store/__init__.py`, commented there.

`keep_days` prunes closed findings and old analyses (`00-overview.md`, "State on disk"). `check` stores its
snapshot and records the analysis in one transaction, and so does the report the UI loads (`cli._report`, since
2026-10-04: before, it read the lifecycle without recording it, so someone who only used the UI had no findings'
lifecycle and no snapshots for What changed). Both record only a reading at the `sessions` setting, the reading
being comparable with stored ones; another `--sessions` reads the lifecycle and records nothing, saying why — `check`
in its terminal output, `check --json` as `recorded: false`, the UI's report as `not_recorded` (the note, shown on
Findings and What changed, and printed once when `agentlint ui --sessions N` starts). Reading an
unchanged configuration again adds nothing: its snapshot replaces the row of the same fingerprint, no finding opens
or closes, and an analysis of the same fingerprint by the same checks as the project's latest moves that row to now
instead of adding one — only the latest analysis that ran a check is ever read, so nothing is lost, and a page
reloaded all day doesn't pile up rows. Schema 8 (2026-10-03) renamed the status `fixed` to
`closed` — the product claims no fixes — and dropped the `history` table of transitions, which nothing read once
"new" and "fixed" left the product. Schema 2 (2026-10-02) moved suppression inputs into per-project baselines; opening a schema-1
database migrates it, dropping the old inputs, which were armed against whichever project last saw the
finding, to be re-armed on first sight. Traces additionally carry
`CANON_VERSION` (`06-trace.md`); a mismatch must refuse to compare rather than compare across
normalisation rules.

## Terminal output

Grouped by severity, most actionable first. The header gives the counts; a second line appears only when there
is something to say about you — how many you suppressed, or why a reading wasn't recorded. Errors and warnings
print in full; **info prints one line each**,
ending in a short id to suppress by — `--detail` prints everything. The terminal is the quick look and
the CI gate; the UI is where findings are explored. Then **NOT EVALUATED** (checks that lacked their
input, and what they lacked). Suppressed findings are
hidden and counted on the summary line (`· 3 suppressed`); one whose inputs changed since it was
dismissed is shown with `[dismissed before; inputs changed]`. `--json` carries every finding, with
`suppressed` and `suppression_lapsed`, plus `not_evaluated`. Changed 2026-10-04: that key was
`dismissed_before_inputs_changed`; it now has the name of the field it comes from (`Tracked.suppression_lapsed`,
which was `Tracked.lapsed`), in `--json` and in the report alike, so a script reading the old key must change.

A finding with members prints them first, one line each, from `Finding.members` — name, kind and where it sits
(its package id, else its scope), `[described]`/`[name only]`/`[not shown]`/`[not known]`, the copy mark, a symlink,
and the description (150 characters) — then `detail`. Changed 2026-10-04: duplicate-name and trigger-overlap wrote
these lines into `detail` before, so `--json`'s `detail` for them is now only why it matters, and each finding
gains `members` (`[]` for the other checks). Ids did not change: they hash the check and discriminator only.

```
agentlint check — claude-code 2.1.285 · ~/projects/agentlint · 171 findings (9 error, 54 warn, 108 info)

ERROR
  broken-reference       References to codesearch:code_search, which doesn't exist in codesearch
                         codesearch is installed, but has nothing named codesearch:code_search;
                         invoking it would fail. Did you mean codesearch:code-search?
                         at ~/.claude/skills/deep-research/SKILL.md:94
                         → Change it to codesearch:code-search.
                         id …
WARN
  duplicate-name         4 skills named "sql-query" — identical copies
                         data-agent:sql-query  skill (data-agent@acme-market)  [name only]  (identical to data:sql-query)  "Query SQL/Hive data, …"
                         data:sql-query  skill (data@acme-market)  [name only]  (identical to data-agent:sql-query)  "Query SQL/Hive data, …"
                         ...
                         The agent was shown 4 of them (0 with a description), so asking for "sql-query" leaves it to pick one.
  ...
INFO
  definition-size        63 invocable definitions are over 10,000 tokens  · a48d4d71
  duplicate-name         2 skills named "conveyor" — different descriptions (latent)  · 54d5c42c
  ...
                         (one line each; --detail prints them in full)
```
(Abridged from a real run on the reference machine, 2026-10-02: 611 lines, down from ~1,100 in full. The
duplicate-name member lines are from 2026-10-04, after members became data.)

## Coverage view

The honest status display. Never "green" — always "here's what we've looked at."

```
agentlint status

CONFIG      fingerprint a3f9…  412 skills  44,290 always-on tokens
MEASURED    38 of 206 testable rules  (18%)
            last run 2026-10-04, $4.82 spent

  effect          21 rules   behave measurably differently when removed
  no-effect        9 rules   deletion candidates — 14,200 tokens total
  unmeasurable     8 rules   unstable baselines
  never measured 168 rules

STALE        6 rules measured under an older config — re-run recommended
```

The "deletion candidates — 14,200 tokens total" line is the payoff. That's the number that makes
someone act.

## Relationship to the UI

The terminal output above is the fallback, not the primary surface. The main consumer of
everything in this doc is the UI (`11-ui.md`), which renders the same `Finding` objects.

One data contract, two renderers:

| Renderer | Source of data |
|---|---|
| Terminal | `Finding[]` in-process |
| Live UI | the report document (`report/document.py` `report_data`), fetched fresh from `/api/report?harness=&project=` for the scope chosen (`11-ui.md`) |

The offline HTML report (`agentlint report --html`, the same document baked into the shell) was dropped 2026-10-04:
the live UI shows everything, and a file holding the configuration's full text invites sharing. There is no separate
serialisation path for the UI: it reads one document, whose `check.findings` are each
`Finding` serialised as it is, plus what the finding's lifecycle adds — its `id`, `suppressed`, and
`suppression_lapsed` (named `dismissed_before_inputs_changed` before 2026-10-04). If a field is in neither, the UI can't show it — so add it here, not there.

`harness_label` is how the user knows the harness the report is of — its adapter's `label` (built 2026-10-03, so the
breadcrumb names it before the server's list of harnesses answers; core never names a harness, the adapter does). It is
additive: `schema_version` stays 1, and a reader of an older report falls back to `snapshot.harness`.

`home_dir` is the user's home directory (Python's `Path.home()`; built 2026-10-04, review round 11). The UI
writes a path in the `~` form only when it is that directory or under it (`home` in `ui/src/format.ts`), so a
project in `/Users/Shared` or another user's directory reads as it is, and the copied suppress command's `--cwd`
names the project the shell will find. Before, the UI took the first `/Users/<x>` or `/home/<x>` of the path
itself as the home. It is generic (a machine fact, not a harness's) and stays on the machine like every path in
the report; `schema_version` stays 1, as the server serves the shell that reads it.

`not_recorded` is the finding lifecycle's note when the reading was neither stored nor recorded — `agentlint ui`
started with a `--sessions` other than your `sessions` setting — and null when it was (built 2026-10-04, after a
UI at another number of sessions recorded nothing and said nothing, while What changed promised that the next
reading would be stored). The UI shows it as written, on Findings and What changed; `schema_version` stays 1.

The CLI and the labels in the report (`report.labels`, built with `paths.place`) keep the same rule:
`paths.display()` writes `~` only where a path starts with the home directory — the directory itself or a path
under it — and never for a match inside another path (`/Volumes/bak/Users/sam/x`) or a longer name
(`/Users/samuel/x`), which stay whole. In a line of text (a finding's detail) a path starts at the line's start or
after a space, quote or bracket. Before review round 12 it replaced the home path anywhere in the text, so a label
could read `~uel/x` while the UI's Where row read the path whole.

`package_labels` maps each package a source in the snapshot shipped in (`provenance.package`) to how the user knows
it — the adapter's `package_label` (`09-adapters.md`; built 2026-10-04). The ids are the harness's own and core never
takes one apart, so a short name comes only from here. The UI reads it through `packageName` (`ui/src/format.ts`),
which shows the id itself when there is no label or when two packages share one, and puts the id in the tooltip
wherever a short name stands in for it. Also additive: an older report has none, and the ids show whole.

A rule the report applies, it states once, so no reader re-states it: `loads` gives each load its
`in_first_turn` (whether text loaded that way reaches the agent before your first words — `Load.in_first_turn`).
Each entry of `contexts` lists the sources and the untraced text that pass that one rule, and carries
`observed_total` whenever that session is the one the always-on total was observed in, reconciled or overcounted.
`report/diff.py` reads the same rule from `Load`, and the UI reads `loads`: `inFirstTurn` in `ui/src/model.ts` is
a lookup of `in_first_turn`, never a copy of the rule.

## Exports

```
agentlint check --json
agentlint export --findings <path>     # derived findings only
```

**Never export traces or recorder logs.** Those contain real work content. The export command
should refuse, not warn, if asked to include them. This is a non-negotiable in `AGENTS.md`.

## CI use

```
agentlint check --fail-on error
```

Exit codes: `0` clean, `1` findings at or above the threshold, `2` tool error — including any exception agentlint
did not expect, which prints its traceback and exits 2, so a crash never reads as findings (`130` on Ctrl-C),
and any flag outside its setting's range (`--sessions`, `--days`, `--runs`; `00-overview.md`, "Settings"),
which argparse refuses with exit 2 before anything is read.
Suppressed findings never
fail a build; one whose suppression lapsed (its inputs changed) is shown again and counts.

Behavioral tests are deliberately **not** suitable for CI gating in v1 — they take hours and cost
money. If that changes later, gate on a small must-never subset only.
