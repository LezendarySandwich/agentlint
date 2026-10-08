# Technical overview

## Component map

```
                   ┌──────────────────┐
                   │  ADAPTER         │  harness-specific, small
                   │  read_config()   │
                   │  run()           │
                   └────────┬─────────┘
                            │ Source[], Entity[], Trace
         ┌──────────────────┼──────────────────┐
         │                  │                  │
   ┌─────▼──────┐    ┌──────▼──────┐    ┌──────▼──────┐
   │ 01 CONFIG  │    │ 05 RUNNER   │    │ 08 RECORDER │
   │   READER   │    │             │    │ (no hook)   │
   └─────┬──────┘    └──────┬──────┘    └──────┬──────┘
         │                  │                  │
   ┌─────▼──────┐    ┌──────▼──────┐           │
   │ 02 STATIC  │    │ 06 TRACE    │           │
   │   CHECKS   │    │             │           │
   └─────┬──────┘    └──────┬──────┘           │
         │                  │                  │
         │           ┌──────▼──────┐           │
         │           │ 07 ANALYSIS │           │
         │           └──────┬──────┘           │
         │                  │                  │
         └──────┬───────────┴──────────────────┘
                │
         ┌──────▼──────┐        ┌─────────────┐
         │ 03 RANKER   │◄───────┤ usage data  │
         └──────┬──────┘        └─────────────┘
                │
         ┌──────▼──────┐
         │ 04 PROBE    │
         │   GENERATOR │
         └──────┬──────┘
                │ (feeds back into 05)
         ┌──────▼──────┐
         │ 10 FINDINGS │
         └──────┬──────┘
                │  same JSON
         ┌──────▼──────┐
         │ 11 UI       │  localhost server
         └─────────────┘
```

The UI is not a downstream afterthought — it consumes the same JSON the CLI emits at every stage,
and every feature reaches it, new ones first (decided 2026-10-04). See `11-ui.md`.

## Data flow

**Cheap path** (`agentlint check`, seconds, free):

```
adapter.read(cwd, N) → declared + sessions → assemble() → ConfigSnapshot ─┐
                     → evidence, for the adapter's own checks ────────────┼→ Reading → static checks → findings
file_facts(snapshot) → what the core checks ask of the filesystem ────────┘
```

One reading is self-contained: the disk is read afresh each time (an open UI's `r` sees an edit), and every
check judges only the `Reading` it is given (`02-static-checks.md`).

Disk is the truth; the last N sessions add only what disk can't know (hook output, the harness's
built-in text, the observed total). Nothing on this path executes the harness.

**Expensive path** (`agentlint test`, hours, metered):

```
Source[] + usage → ranker → rules worth testing
                 → probe generator → situations
                 → runner (variant A, variant B) → traces
                 → trace normaliser → canonical outcomes
                 → analysis → findings
```

## Repo layout

**Language: Python ≥3.12, managed with `uv`.** Decided 2026-10-02. Transcript parsing and small
statistics are where the work is, and there is no runtime-distribution requirement that would
justify a compiled language.

Dependencies are **stdlib-first**. PyYAML (frontmatter) is the only runtime dependency; `pytest` is
dev-only. Anything heavy — ML models, numpy, torch — must be an optional extra and must never be
imported on the `check` path or the recorder's ingest path, both of which must start fast.

```
uv sync                 # create .venv from uv.lock
uv run agentlint ...    # run the CLI
uv run pytest           # run the tests
```

`tests/test_boundaries.py` enforces the harness-agnostic core (`09-adapters.md`): core never
imports an adapter and never names a harness.

```
agentlint/
  AGENTS.md
  docs/
  ui/                    # 11 — the UI's source: TypeScript + React (Vite), built into src/agentlint/ui/shell.html
  src/agentlint/
    harness.py           # the adapter interface (core) — see 09
    adapters/            # concrete adapters only; nothing in core imports these
      claude_code/
    config/              # 01 — the model, formats, and assemble(); usage.py: 08's record and counting
    checks/              # 02 — static checks
    rank/                # 03 — the plan: ablation targets, filter, sort, budget
    probes/              # 04 — the generator interface, the reply check, the contamination guard
    runner/              # 05 — the job engine (engine.py, plan.py, status.py, lock.py), variant homes (variant.py) and
                         #      their verification (verify.py)
    trace/               # 06
    analysis/            # 07
    report/              # 10 — Finding type, renderers
    store/               # the agentlint database: schema, finding lifecycle, snapshots, usage history (08)
    ui/                  # 11 — the built shell + local server
      shell.html         #      built from ui/ by `npm run build`: one self-contained file
      server.py          #      the endpoints, the token, the kept texts
      catalog.py         #      the sessions it lists and reads back, by harness and project
      records.py         #      what agentlint stored: a project's snapshots and what changed between two
    cli.py
  tests/
```

There is no `fixtures/`: a situation runs in one empty directory at a fixed path under `AGENTLINT_HOME` (Open
Question 3, decided 2026-10-04).

## Settings

Zero setup: every command works with defaults. Your settings live in the agentlint database's `settings` table
(decided 2026-10-04; until then in `~/.agentlint/config.toml`): one row per setting you changed — its JSON value and
when — and none for a setting at its default, so a default can still improve unless you deliberately set it. A
command-line flag overrides the stored setting for one run (`settings.py`). `agentlint config` and the UI's Settings
screen (`11-ui.md`) read and write the same rows; `config unset`, and Reset in Settings, delete the row. Every value
is checked before it is written — a whole number in its range, a list of harness names — and nothing is written
otherwise. `forget` never deletes settings.

```
agentlint config list                       # every setting, its value, and whether you set it
agentlint config set harnesses a,b          # read only these harnesses (empty: every one found)
agentlint config get keep_days / unset sessions
```

**A config.toml from before is imported once** (`settings.migrate`, run by `main()` before every command). Each
setting it sets is stored as written unless the database already holds that setting — then the database's value is
kept, and the note names it (`INSERT OR IGNORE`; only a row actually written counts as imported). A value that can't
be used (out of range, the wrong type) is stored too, never dropped for its default: it is then a stored value that
can't be used, below, refused and shown until you change it — a dropped `keep_days` would have pruned at 183 days and
deleted what you asked to keep. A key that is no setting is left out and named. The file is then renamed
`config.toml.migrated` (`.2`, `.3` … if that exists) — never deleted — and one line on stderr says what was imported,
what can't be used, what the database kept, what was left out and where the file went ("it set none" only for a file
of comments). **The whole import holds the database's write lock** (`BEGIN IMMEDIATE`), from looking for the file to
renaming it: two agentlint processes starting at once — the scheduled `sync --quiet` while `agentlint ui` starts, on
the first run after upgrading — import it once; the second waits, finds no file and says nothing. A file gone by the
rename was moved by someone else: no note tells you to rename it (none at all when the database already held all it
set). **A file that doesn't parse** is left where it is, unrenamed, and nothing is imported from it: it may set
`keep_days`, so until it reads (fix it, or remove it) every command refuses, as at 1420dc1, except `config`, `forget`
and `ui`, and nothing is deleted by age. Fixed, it is imported on the next command — or, with `agentlint ui` running,
on the UI's next request, which tries the import again whenever there is a file (`StoreControl._retry_import`).
`config path` is
gone: there is no file.

**A stored value that can't be used** (written by hand, imported from a config.toml, or written by another version)
counts as unset — its default is in effect — and is named with why, naming its key once ("the stored keep_days must be a whole number from 1 to 36500", `settings.read`). Every command refuses then, as
it refused a broken file, except those that fix it or need no setting: `config` (`config list` marks it, `config get`
says why on stderr and exits 2 rather than print the default as its value, `config unset <key>` clears it), `forget`
— and `ui`, which starts anyway and opens on Settings, where the setting shows what is wrong. A key
this agentlint has no setting for is ignored: another version wrote it.

**Nothing is deleted on a fallback `keep_days`.** While the stored `keep_days` can't be used, or a config.toml can't be
read, `Settings.hold` says why, and every pruning path — `_import`'s and the UI's prune as it starts — checks it first
and deletes nothing by age; the UI says so at the top of Settings while it lasts (read again on every request, so fixing it there clears it without a restart). Only a `keep_days` you set, or the default when you
never set one, prunes.

**The first run** is a flag in the database (`state.welcomed`), no longer the moment `config.toml` was written. The
CLI's notice — what is read, what is kept and for how long, that nothing leaves the machine — and the UI's
first-run panel share it, so you see one of them, once: any command but `ui` prints the notice and sets the flag (one
`INSERT OR IGNORE`, so two commands at once print it once); `ui` leaves it to the page, which shows the panel and
sets the flag when you close it. Importing a `config.toml` sets it too: writing that file was the first run.

| Setting | Default | Range | What it decides |
|---|---|---|---|
| `harnesses` | `[]` | harness names | which harnesses to read; empty means every one agentlint finds (an open UI takes a change the next time it starts) |
| `sessions` | 10 | 0-1000 | recent sessions read for what disk can't show, by every command that reads the configuration — `rank` and `usage` too (`--sessions` overrides) |
| `usage_days` | 30 | 1-36500 | the usage window of `agentlint usage` and the UI (`--days` overrides) |
| `keep_days` | 183 | 1-36500 | everything older is deleted, except what is current |
| `situations_per_target` | 3 | 1-10 | how many situations the generator writes for each target, and the most kept ones a test runs of each ticked target. A test's size is the targets you tick × this × `runs_per_situation`, and that total is its run ceiling (decided 2026-10-05: it replaced `runs`, "max runs per test", 60, which was the ceiling and cut the plan; a stored `runs` row is ignored as another version's) |
| `runs_per_situation` | 3 | 3-20 | how many times a test runs each situation on the configuration as it is (the stability screen); fewer than 3 is never a screen (`MIN_RUNS`) |
| `runs_at_once` | 1 | 1-8 | how many test runs go at once (2026-10-07; `05-runner.md` "Concurrency"): each starts its own harness with every MCP server and hook, so more finishes sooner but loads the machine; read as each resume starts |
| `run_timeout_s` | 300 | 30-3600 | one test run's time limit, in seconds. E1's run took 53 s, 28 s of it one SessionStart hook; a timeout is an outcome (D7), so the limit sits well clear of a normal run, and a stuck run costs at most five minutes |
| `model` | `""` | a model's name | the model every run of a new test asks for (`--model`); empty means the model your own sessions use — the `model` your harness's settings set (for Claude Code the last settings layer that sets one, as `discovery.configured_model` reads it), else the model your latest interactive session recorded |
| `keep_tools` | `[]` | tool names | tools the adapter would remove from test runs as acting outside the machine (D6) that runs keep offering |
| `remove_tools` | `[]` | tool names | tools removed from every test run besides those the adapter removes (D6); a job freezes the list it ran with |

The last six are a test's (`settings.GROUPS["tests"]`, the UI's `test_settings`, shown at onboarding): a new job
removes the adapter's choice (`tool_verdicts`) less `keep_tools`, plus `remove_tools`. A tool name never starts with
`-` (the list is the last thing on a run's command line, after a flag that takes every word after it). The one-time
test onboarding (D18) is `state.onboarded`, set once when you accept it.

Each default and each range is written once, on the `Settings` field (`settings.py`), and `Settings` checks
its ranges when it is built. Stored values, `config set` and the UI's writes are held to them, and so are the flags: each flag's
argparse `type=` is built from its setting's range (`cli.bounded`), so `--sessions 1001` or
`--days ten` is a usage error (exit 2) naming the flag and the range, before anything is read. `rank --runs` is no
setting's flag: its budget is rank's own (`cli.RANK_RUNS`, 60; 1-100000, `--runs 0` a usage error too). The library
has no second copy of a default: `read_config(sessions=)`, `plan(budget=)` and `report_data(usage_days=)` take
the value as a required keyword argument (`plan(budget=None)`: no budget, as a test from the UI plans).

A `harnesses` setting that names none agentlint can read is a tool error (exit 2) saying so, for every command
except those that fix the setting or touch only what agentlint keeps: `config`, `suppress`, `unsuppress` and
`forget` (`usage --forget`, which was `forget` by another name, went 2026-10-04) — and `ui`, which starts and opens on
Settings (2026-10-04). `suppress <id>` checks the project first, so it refuses, reading and storing nothing, when the
setting leaves out the harness that check reads (2026-10-04; `suppress --list` and `unsuppress` still run).

`main()` resolves the flags and the settings once into a frozen `Options` (`settings.py`) — `sessions`,
`days`, the `settings` they came from, and `comparable` — and every command receives it; the parsed
arguments are never changed. A reading is "comparable" — recorded in the lifecycle and kept for `--diff` —
when it reads your `sessions` setting's number of sessions (`Options.comparable`, the one place that test is
written). The open UI reads the settings again on every request (`Options.reread`), keeping the flags it was
started with, so Settings and `config set` reach it and a flag still beats them.

Each command's parser names its function (`set_defaults(func=cmd_…)`), and `main()` calls it. A command that
can't go on raises `cli.ToolError`, which `main()` prints as one `agentlint: …` line and exits 2 for. Every command
opens the database through `cli.store()`, a context manager that closes it however the block ends and turns a
database that can't be opened (`StoreError`) or fails in use (`sqlite3.Error`, `OSError`) into a `ToolError`
(2026-10-04, design review 10.1; before, each command wrapped its own `connect()` in the same three handlers).

## State on disk

Everything local. Nothing uploads. Readable by you only: the database 0600 and every directory agentlint makes 0700,
at every level it makes — the home itself when it is new, `runs.noindex/` and each job's and run's directory under it, `jobs/`,
`generate/` — like the transcripts it copies from (`store.private_dirs`, the one way agentlint makes a directory: before
2026-10-05 `Path.mkdir(parents=True, mode=0o700)` gave the mode to the last level only, and `runs/` was made 0755);
deleted rows are overwritten, not left in the file. The test suite never opens it:
importing `tests` points `AGENTLINT_HOME` at a throwaway directory, under pytest or from a script that borrows a
test's helpers, and each test gets its own (`tests/__init__.py`, `tests/conftest.py`).

```
~/.agentlint/                  # AGENTLINT_HOME moves it
  agentlint.db                 # SQLite: everything agentlint accumulates (tables below)
  generate/                    # the empty directory the probe generator's call runs in (04, D14)
  runs.noindex/<job>/<run>/{home,work} # a run's variant home and working directory, deleted once its trace is read (05)
  jobs/<job>.log               # what a job's worker said, one line per run (05 "The worker")
  config.toml.migrated         # only if you had settings before 2026-10-04: the old file, imported once, kept
  emphasis.toml                # your marker overrides, merged over the shipped defaults
```

| Table | What | Lifetime |
|---|---|---|
| `analyses` | each `check`: project, time, fingerprint, the checks evaluated | `keep_days`, except each project's latest — tiny |
| `findings` | per project, every finding seen, open or closed, with what it rested on; an open one is kept while it stays open | `keep_days` after it was last seen, once closed |
| `suppressions`, `suppression_baselines` | your dismissals — global — and, per project, what each finding rested on there when hidden | until you unsuppress |
| `snapshots` | each comparable configuration read, per project and fingerprint — ~160 KB of metadata per new fingerprint (a harness update makes one per project); a later reading under the same fingerprint replaces it | `keep_days`, except each project's latest reading |
| `blobs` | each distinct source text a reading holds, stored once, compressed — what changed costs, what didn't is shared | while a kept reading refers to it: every prune deletes the rest, a text a replaced reading held included |
| `verdicts` (v2) | per project, each ablation target (a rule, or a whole skill or hook) → verdict and the fingerprint it was measured under; read by the ranker, written by the runner | `keep_days` after last measured, except a verdict measured under the fingerprint of the project's latest reading — tiny |
| `jobs` (v2) | per test you start: its header (project, fingerprint, harness version, model, run ceiling, timeout, the harness whose adapter runs it — schema 15), the plan frozen as JSON, its status, how many invocations it launched, the run key in flight and the lock (the runner's pid and that process's start time) — never how far it got, which is its runs | while open, whatever its age; once closed — complete, salvaged, or stopped at its run ceiling — `keep_days` after its last progress |
| `runs` (v2) | one row per run recorded, keyed (job, `<comparison>:<variant>:<n>`): session id, outcome, the raw trace as JSON, the harness version and model it was observed under, duration, cost estimate, start and finish — the only record of a job's progress | with its job |
| `probes` (v2) | one row per generated situation: its id (a hash of the target's text hash and the situation), the target it was written for, the target's text hash — the only cache key (D15) — the situation verbatim, what it expects (a menu choice naming a thing, as JSON; schema 17 deleted the rows from before, which named nothing), the model that answered, the prompt version, when, and its review (written accepted — kept — then rejected or edited by you, with the edited text and when; `pending` until schema 16) | until the target's text changes and its situations are regenerated on request; never by `keep_days` |
| `diagnostics` (v2) | agentlint's findings about itself (D16): kind, subject (a target or job id), a message, details as JSON, when — the generator wrote nothing, a situation named its own target and was dropped, runs retried. Never in `findings`, its counts, `--json` findings or an exit code | `keep_days` |
| `extractions` (v3) | extraction cache by content hash; not built | until the content changes |
| `observations` (v1) | usage derived from archived transcripts: one row per turn, the names of what it used | `keep_days` |
| `archive_chunks` | every transcript read — sessions and subagents — verbatim, compressed | `keep_days`, by the transcript's modification time |
| `archive_files` | per transcript: how far it was read (`bytes_read`), when (`mtime_epoch_s`), how many leading bytes went (`forgotten_bytes`), a hash of how it ended (`tail_sha256`), whether usage was derived from it — so one whose bytes went isn't read in again | while it has bytes kept or the harness still lists the transcript |
| `usage_meta` | the forget cutoff; the reading rules usage was last derived with, per harness | forever — tiny |
| `settings` | your settings: one row per setting you changed — key, JSON value, when — none for a setting at its default (`settings.py`) | until you unset it; never by `forget` or `keep_days` |
| `state` | agentlint's own bookkeeping, never a setting (`store/state.py`): `welcomed` (the first-run notice was shown, or the UI's panel closed), `onboarded` (the one-time test onboarding was accepted, D18) and `last_sync` (when `agentlint sync` last ran and what it came to) | forever — tiny; never by `forget` or `keep_days` |

`agentlint forget` — and the UI's Forget everything, through the same `forget.forget_everything` — deletes, whatever
their age, everything here that came from transcripts: `observations`,
`archive_chunks`, `snapshots`, `blobs`, `findings`, `jobs` with their `runs` (a run's trace is read from its
transcript, and a test's results live under agentlint's home so that forget deletes them — decided 2026-10-04; it
refuses while a job's runner is alive, 2026-10-04; the runs' directories under `runs.noindex/` go too, `05-runner.md`), `probes` and `diagnostics`; it keeps suppressions and their baselines, `analyses`,
`verdicts`, `settings` and `state`, and records its cutoff in `usage_meta` (`08-recorder.md`).

Three of these accumulate **across** jobs and are the long-lived value: `verdicts`, the findings
tables, and `snapshots` — kept `keep_days`, and what is current kept always (`store/retention.py`). Traces are bulky and disposable once their verdict is recorded. Coverage is
derived from verdicts and probes, never stored separately. Tables for components not yet built are
created when those components are.

The schema version is the database's `user_version`: a database written by a newer agentlint is
refused, not misread. A command's own writes land in one transaction, so a kill never leaves half a write;
importing transcripts, which comes first, commits one transcript at a time, so an interrupted import keeps
its progress and the next one derives usage from whatever it archived but hadn't yet read. Details in `10-reporting.md`.
The tables and columns are stated once, in `_SCHEMA` (`store/__init__.py`); every statement names its columns and
rows are read by column name, never by position. An older database is upgraded by `_MIGRATIONS`, and
`tests/store/test_schema.py` holds one upgraded from the first release's schema (70a0307, frozen there as `_V1`) to
the same tables, columns, STRICT, NOT NULL, CHECK clauses and indexes as a fresh one.
Every time kept as TEXT is written one way — ISO 8601 in UTC, to the microsecond (`store/_time.py`) — so
retention and the usage window can compare times as text; a time without a zone is refused, never taken as local.
The one exception is `archive_files.mtime_epoch_s`, a transcript file's modification time in Unix seconds, as its stat
gives it: it is compared with the file's next stat, not with stored text, and its name says the unit.

A run's directories — the empty directory the situation runs in, and the variant home — live at a fixed path under
`AGENTLINT_HOME`, never in a random temporary directory (decided 2026-10-04), so `forget` deletes them with
everything else; the layout is settled with variant construction (`05-runner.md`, decision D2). Variant homes are
copies of the config tree, rebuilt from `(fingerprint, target)` on demand: none is load-bearing, and a resumed job
reconstructs what it needs. Every write is checked to land under `AGENTLINT_HOME`; the user's real config is never
modified.

### Units of work

`run` < `comparison` < `phase` < `job`. A **run** is one agent invocation; a **comparison** is one
`(situation, ablation target)` pair across both variants — the target a rule, or a whole skill or hook
(`03-ranker.md`) — and is the atomic unit that produces a finding; a
**job** is one test you start — by hand, resumable; an overnight budget is a later layer on top — with a frozen
plan. Defined in `05-runner.md`.

## Conventions

- **Everything is keyed on content hash.** Sources, rules, probes, extraction results. Recomputation
  is cheap and caching is automatic.
- **Every artifact that came from a run carries a config fingerprint.** Comparing across fingerprints
  without explicitly intending to is a bug; the code should refuse.
- **Findings are a single shared type** (see `10-reporting.md`) regardless of which stage produced
  them. Static checks and behavioral tests emit the same shape.
- **No stage mutates another stage's output.** Pipeline, not shared state.

## Build order

Follow `docs/03-checklist.md`. The short version: 01 and 02 first and they must be useful alone;
08 early because there's no past to recover; 03–07 after.
