# Build checklist

**Last updated:** 2026-10-04

Status markers — see `AGENTS.md` for the full convention.

| Marker | Meaning |
|---|---|
| `[ ]` | Not started |
| `[~]` | In progress — needs a `Status:` line |
| `[x]` | Done — needs a `Done:` date and a pointer |
| `[!]` | Blocked — needs a `Blocked:` line |
| `[-]` | Dropped — needs a `Dropped:` line |

---

## Milestone 0 — Groundwork

- [x] Confirm headless execution works
      Done: 2026-10-01. `claude -p "..." --output-format json`. See Verified Facts in
      `docs/02-design.md`.
- [x] Confirm config isolation mechanism
      Done: 2026-10-01. HOME substitution verified working.
- [x] Confirm whether JSON output carries tool calls
      Done: 2026-10-01. **It does not.** Trace must come from the session transcript or
      `--output-format stream-json`.
- [x] Survey `claude plugin eval` for reuse
      Done: 2026-10-01. Decision: not building on it. Rationale in `docs/02-design.md`.
- [x] Decide repo language and layout
      Done: 2026-10-02. Python ≥3.12 + uv, stdlib-first, PyYAML the only runtime dependency.
      `pyproject.toml`, `src/agentlint/`, rationale in `tech_impl/00-overview.md` "Repo layout"
      and the decision log. Core-vocabulary rule enforced by `tests/test_boundaries.py`.
- [x] Resolve Open Question 1 — rule granularity
      Done: 2026-10-01. Not one global choice — granularity and excision are per content format,
      dispatched as methods on `Content`. See the table in `tech_impl/01-config-reader.md`.
- [x] Resolve Open Question 2 — trace normalisation rule
      Done: 2026-10-04 (user). Two matching rules, one per claim (decision log, D10). The exact normalisation follows
      the noise study (Trace, "Normalisation per Open Question 2").
- [x] Resolve Open Question 3 — fixtures
      Done: 2026-10-04 (user). An empty directory at a fixed path under `AGENTLINT_HOME`, so `forget` deletes
      the results. Open: what of the project's own configuration is copied beside it (v2 readiness D1).
- [ ] Resolve Open Question 6 — can one package or entity be disabled through the harness's own settings
      Deferred 2026-10-04 (user) to UI-3, when the Disable button is built. The schema's switches are in Verified
      Facts; the runner removes things by editing the variant's copy instead (D3).

---

## Milestone v0 — See it and check it

*No AI, no agent runs, seconds to execute. Must be useful on its own.*

### Config reader

- [x] Claude Code adapter: enumerate settings layers (managed / user / project / local)
      Done: 2026-10-02, plus the add-dir layer the launcher uses for its preset.
      `adapters/claude_code/discovery.py`; resolves all 51 enabled packages on this machine.
- [x] Claude Code adapter: enumerate `CLAUDE.md` hierarchy
      Done: 2026-10-02 — plus the `AGENTS.md` fallback, managed `claudeMd`, rules dirs and the
      memory index. `adapters/claude_code/instructions.py`; delivered paths match in all 9 local
      sessions that record them.
- [x] Claude Code adapter: enumerate plugins, skills, agents from both plugin dirs
      Done: 2026-10-02 — packages resolve through the marketplace, not "both plugin dirs" (the
      cache dir is not what loads). `adapters/claude_code/entities.py`; verified by the listing
      oracle (skills/commands/workflows 493/506, agents 45/50, residual explained).
- [x] Claude Code adapter: extract skill/agent frontmatter (name, description, tools)
      Done: 2026-10-02. Strict YAML with a lenient fallback matching the harness
      (`entities.read_frontmatter`).
- [x] Claude Code adapter: recover tool/skill/agent listings from a transcript
      (for entities only knowable at runtime)
      Done: 2026-10-02. `transcripts.py` + `adapter.py`: system prompt, tool list, listings,
      instruction files and session context become session deliveries; tools and built-in
      entities become `Session.entities`. Abandoned first submissions handled.
- [x] Claude Code adapter: recover hook injections from a transcript
      Done: 2026-10-02. `hooks.py` traces 92.8% of delivered hook text to its configured entry on
      17 sessions; the rest becomes `Untraced` with its candidates.
- [x] Emit `Source` records (provenance / activation / content / ablation) per
      `tech_impl/01-config-reader.md`
      Done: 2026-10-02. `ClaudeCodeAdapter.read_declared` (`adapters/claude_code/adapter.py`):
      826 sources on this machine; end-to-end tests in `tests/adapters/test_claude_code_adapter.py`.
- [x] `Content` subtypes per format, with `rules()` and `excise()` + `validate()`
      Done: 2026-10-02. `Content = FileText | ObservedText | SizeOnly | NotObserved`
      (`src/agentlint/config/content.py`); format handlers verified on every config artifact on this
      machine — 1,309 frontmatter files (4,385 excisions valid), 2,340 markdown files (exact
      partition at both granularities), 24 JSON files (239 excisions round-trip).
- [x] `Locator` types: `Whole`, `MarkdownSection`, `LineRange`, `KeyPath`
      Done: 2026-10-02, plus `Body`; `MarkdownSection` gained `occurrence` and `until_level`.
      `src/agentlint/config/locators.py`, stable `locator_key()` used in rule ids.
- [x] Rule extraction per format (markdown sections, JSON key paths, frontmatter keys)
      Done: 2026-10-02. `Source.rules()` (`src/agentlint/config/model.py`) over the verified
      region extraction; rule id = `<source id>#<locator key>`.
- [x] Emit `Entity` registry
      Done: 2026-10-02. Disk entities (skills, commands, workflows, agents) plus session-only
      entities (tools, MCP tools, binary built-ins): 619 on this machine.
- [x] Token counting per source
      Done: 2026-10-02. Piece-based estimator, `TOKENS_PER_PIECE = 1.22`, calibrated and verified
      against two independent sets of exact usage numbers (6 clean first turns, 463 tool-result
      deltas) — `src/agentlint/config/tokens.py`, `scripts/calibrate_tokens.py`, method in
      `tech_impl/01-config-reader.md` "Token counting".
- [x] Unattributed-token accounting (observed − accounted = harness built-in)
      Done: 2026-10-02. Reconciled against the anchor session's own deliveries; on a clean real
      session 99.6% attributed (160 of 44,280 tokens unattributed). `config/assemble.py`.
- [x] Config fingerprint computation
      Done: 2026-10-02. `config/assemble.py`; on real data stable across independent reads and
      independent of how many sessions are read; changes when a declared file changes.
- [x] `agentlint show` — print the assembled config, ranked by token cost
      Done: 2026-10-02. `--json`, `--always-only`, `--rules`, `--all`; `--diff`, `--text` and
      `--snapshots` since snapshots landed (Persistence). `src/agentlint/cli.py`, `report/show.py`,
      `report/diff.py`, `config/serialize.py`; ~2s here.
- [ ] The reader honours the harness's own off switches — `skillOverrides` (`off`, `user-invocable-only`,
      `name-only`), `claudeMdExcludes`, `disableBundledSkills`, `disableAllHooks` at every layer (today only
      managed), `disabledMcpjsonServers` — so a skill turned off in `/skills` isn't counted as delivered
      (found 2026-10-04 with Open Question 6; none is set on this machine, so today's readings are unaffected;
      the behaviour is the schema's description, UNVERIFIED until a run shows it)

### Static checks

Decided 2026-10-02: everything surfaced is a `Finding` (severity + kind + confidence); adapters
contribute harness-specific checks emitting the same type; broken-reference checks provable kinds
only; trigger-overlap is lexical over all installed descriptions, active vs latent, with the
threshold set from the user's labels.

- [x] Finding output format (shared by all stages)
      Done: 2026-10-02. `report/finding.py`: `Severity`, `FindingKind`, `Confidence`, `Figure`,
      `Location`; identity = check + discriminator. Check registry in `checks/__init__.py`.
- [x] `agentlint check` — run all checks, print findings
      Done: 2026-10-02. `cli.py`: `--only`, `--json`, `--fail-on`, `--list`; exit 0/1/2. All 12 checks
      together in ~3 s here; finding identities asserted unique across contributors.
- [x] Token budget by source, ranked — plus each package's always-on footprint
      Done: 2026-10-02. `checks/token_budget.py` (core) and `package-footprint` in
      `adapters/claude_code/checks.py`, over per-package listing sources (lossless split).
- [x] Duplicate entity names across namespaces — identical copies vs drifted copies vs namesakes
      Done: 2026-10-02. `checks/duplicate_name.py`, active/latent from observed `Visibility`;
      39 active + 5 latent groups here, incl. 4× sql-query.
- [x] Trigger-description overlap — lexical over all installed descriptions, active vs latent;
      threshold from the user's labels
      Done: 2026-10-02. `checks/trigger_overlap.py`; 16 labelled pairs (0.27–0.69) all distinguishable,
      so the threshold is 0.8 and the check reports near-copies — 2 here.
- [x] Broken references — provable kinds only: missing script paths in hook/command configs,
      `pkg:name` missing from an installed package, files a skill references in its own directory
      Done: 2026-10-02. Core `checks/references.py` (links, entity references) plus the adapter's
      package paths in `adapters/claude_code/checks.py`, one public id. 9 hits here, all true.
- [x] Unavailable references — instructions naming tools that are denied or absent
      Done: 2026-10-02. `adapters/claude_code/checks.py`; finds the denied web-search instruction
      and the absent Grep/Glob tools here.
- [x] Emphasis density per source
      Marker table ships as `data/emphasis.toml`, never hardcoded. Weighted markers,
      run-length scaling (`!` vs `!!!`), merge semantics so users extend without
      copying defaults. Output must name the drivers, not just the score. No "× median" when the
      median is 0; a minimum word count; code excluded.
      Done: 2026-10-02. `checks/emphasis.py`; defaults tuned on real text, one finding per loud
      source (59 here, loudest first). Wheel ships the data file.
- [x] listing-budget — skills reaching the model as a bare name, why (the budget formula), and fixes
      Done: 2026-10-02. `adapters/claude_code/checks.py`; on this machine 436 of 505 name-only.
- [x] interrupted-start — sessions whose skill listing never reached the model
      Done: 2026-10-02. `adapters/claude_code/checks.py`; 2 of the last 10 sessions here.
- [x] untraced-injection — delivered text with no traceable origin, its frequency and cost
      Done: 2026-10-02. `checks/delivery.py`.
- [x] hook-health — hooks that fail, time out, or slow session start
      Done: 2026-10-02. `adapters/claude_code/checks.py` over `transcripts.hook_records`; precision
      rules for isolated, stale and clustered failures.
- [x] hook-health findings rest on and point at the configured hook that ran the failing command, so a row shows its
      package and file (user, 2026-10-04: the package appeared only inside the title)
      Done: 2026-10-04. `adapters/claude_code/checks.py` `_running` (command, event and matcher, as the tracer
      matches); `Check.by_text`; suppression baselines filled once. Here 27 of 29 findings now rest on their hook
      (28 hooks, 14 packages); the other 2 run commands nothing configured declares. Not yet re-measured: the
      11-ui.md count of rows with a package and file. Follow-up: a hook's declared hash leaves out its timeout,
      so raising a timeout doesn't lapse a suppressed timeout finding.
- [x] definition-size — invocable definitions large enough to matter
      Done: 2026-10-02. `checks/delivery.py`; 63 over 10k tokens here.
### Persistence

Decided 2026-10-02: everything agentlint accumulates lives in one SQLite database
(`~/.agentlint/agentlint.db`), not a tree of JSON files; finding lifecycle is per project,
suppressions are global; snapshots store each distinct source text once. User-edited files
(`config.toml`, `emphasis.toml`) stay TOML; exports stay JSON.

- [x] Stable `finding_id` — from check + discriminator (sources dropped, see decision log); never
      from timestamp, score, or ordering
      Done: 2026-10-02. `Finding.id` in `report/finding.py`; persisting it is the items below.
- [x] The agentlint database — schema, `user_version` (a newer database is refused), one
      transaction per command so a kill never leaves half a write; `AGENTLINT_HOME` overrides
      Done: 2026-10-02. `store/__init__.py`, schema v1; newer, foreign and corrupt files refused;
      tests get their own home (`tests/conftest.py`). Schema 2 since the review (per-project suppression baselines).
- [-] Findings + append-only history, per project (`findings`, `history` tables)
      Dropped: 2026-10-03. Nothing read the history of transitions, so schema 8 dropped the `history` table
      (`store/__init__.py`, migration 7→8). The `findings` table stays: open/closed bookkeeping that suppressions
      arm against (`store/findings.py`, `tech_impl/10-reporting.md`).
- [-] Lifecycle states: new / recurring / fixed, with the "3 new, 12 recurring, 2 fixed" summary
      line — scoped to the checks that ran, and a check that lacks its input (no sessions) says so
      rather than marking its findings fixed
      Dropped: 2026-10-03 (user). "Fixed", then "new", proved brittle — four review rounds found false fixes —
      so neither is shown anywhere and there is no summary line; findings are only open or closed, never shown as
      either (`tech_impl/10-reporting.md`). A check that lacks its input still says so (`NotEvaluated`).
- [x] Compact terminal output: errors and warnings in full, info one line each, `--detail` for all
      Done: 2026-10-02. `report/terminal.py`; 611 lines here instead of ~1,100.
- [x] Suppressions with reason and `input_hashes`, and the `suppressed` state (hidden, counted)
      Done: 2026-10-02. `store/suppressions.py`, global table; suppressed findings never fail
      `--fail-on`.
- [x] Suppressions lapse on input change — resurface as "previously dismissed, inputs changed",
      not as `new`
      Done: 2026-10-02. `Suppression.lapsed`; tested (verifying it for real would mean editing real
      config, which we don't).
- [x] `agentlint suppress` / `--list` / `unsuppress`
      Done: 2026-10-02. `cli.py`; any unique id prefix of 4+ characters, reason required. VERIFIED
      here: suppressed the Metrics trigger-overlap finding, saw it hidden and counted, unsuppressed.
- [x] Snapshots in the database, each distinct source text stored once — and `show --diff`
      Done: 2026-10-02. `store/snapshots.py`, `report/diff.py`; `--text`, `--snapshots`. VERIFIED on a
      throwaway project with the real adapter; 676 texts, 2.9 MB compressed, ~160 KB per new snapshot.

- [x] Fix what the 2026-10-02 adversarial review confirmed (six reviewers over static checks and
      persistence; 49 reports → 45 distinct → 37 confirmed by at least 2 of 3 skeptics, 8 refuted)
      Done: 2026-10-02. Four commits: lifecycle and suppressions, check precision and the core
      boundary, the diff, doc corrections; each fix has a test, each verified on this machine. A second
      workflow re-tested every fix (33 of 37 outright) and hunted regressions; the 4 partial fixes, 7 new
      issues and 2 regressions it confirmed are fixed in a fifth commit, the 3 a third round found in
      that commit in a sixth, and the 4 a fourth round found in the sixth in a seventh.

### UI-0 — static report (ships with v0)

Decided 2026-10-02: the UI is a TypeScript + React project in `ui/`, built into one self-contained HTML
file shipped inside the package (users need no Node); the report carries every source's text, compressed.

- [x] UI project and shell — `ui/` (TypeScript, React, Vite, Vitest), built to one self-contained
      `src/agentlint/ui/shell.html` with no external references; a source hash in the shell, checked by
      a Python test, so the shipped shell can't drift from its code
      Done: 2026-10-02. 233 KB shell; `tests/test_ui_shell.py` agrees with the build's hash.
- [x] Config view — everything loaded, ranked by token cost, searchable
      Done: 2026-10-02. `ui/src/ConfigView.tsx`; text search over a lowercase index built once.
- [x] Filter by authority / load condition / package
      Done: 2026-10-02. Same view; component tests.
- [x] Drill into a source: raw text, entities it provides, locations
      Done: 2026-10-02. `ui/src/SourceDetail.tsx`; texts unpacked by the browser's DecompressionStream.
- [x] Mark non-ablatable sources visibly
      Done: 2026-10-02. 🔒 with the reason as its title.
- [x] Findings view — grouped by severity, confidence shown
      Done: 2026-10-02. `ui/src/FindingsView.tsx`, with new / fixed / suppressed / not evaluated.
- [x] `agentlint report --html <path>` — bakes JSON into the shell
      Done: 2026-10-02. `report/html.py`; 5.5 MB for this machine, written atomically, owner-only.
- [x] Seen working in a real browser — this environment can't start one; open a report and confirm
      Done: 2026-10-02. VERIFIED by the user in their browser: it opens and renders — "but doesn't have
      structure", hence the redesign below.
- [x] Redesign to the UI principles (`tech_impl/11-ui.md`): colour and icons over words, a flow from an
      overview to detail, obvious hints, Linear-like simplicity, keyboard-first
      Done: 2026-10-03. Sidebar, overview, findings by check, configuration by load, detail beside the list,
      keys, URL routing, `?` legend (`tech_impl/11-ui.md` "As built"); 8 component tests; the real report renders
      in jsdom. Seen in a real browser: needs the user.
      Successor: the Quiet-ledger redesign (decided 2026-10-03), in Milestone v1 below.

### Exit criteria for v0

- [x] Runs on the reference machine in under 10 seconds
      Done: 2026-10-02. `check` ~3.4 s, `report` ~4 s, `show` ~2 s here.
- [x] Surfaces the four `sql-query` skills without being told to look
      Done: 2026-10-02. duplicate-name: "4 skills named "sql-query" — 3 of 4 are identical copies".
- [!] Reports the always-on token budget and it matches the ~44k observed
      Blocked: needs a session in this project that wasn't an interrupted start — both recent ones here
      were, so token-budget honestly reports an estimate. The next ordinary session here unblocks it.
      Earlier, before interrupted starts were detected, it reconciled 44,280 observed / 44,120 attributed.
- [x] Zero false positives on broken-reference check (hand-verify every hit)
      Done: 2026-10-02. 9 of 9 hits verified by hand on disk; re-verified after the review moved the
      entity part into the adapter and widened fences (same 9, all still missing on disk). Re-verify
      whenever the check changes.
- [ ] A new user can open the report and understand their config without reading docs
      (needs the user: open `agentlint ui` and judge — the offline report file is dropped, 2026-10-04)

---

## Milestone v1 — The recorder

Decided 2026-10-02 (user): usage is read from the transcripts the harness already writes — no hook, nothing
installed (`tech_impl/08-recorder.md`). Since 2026-10-03 (user) every transcript read is also archived verbatim —
prompts, replies, tool calls — until `keep_days` or `agentlint forget`; a usage record still holds names only.

- [-] Hook script — writes one record per turn
      Dropped: 2026-10-02. No hook: usage comes from the harness's own transcripts.
- [x] Usage ingestion — the adapter turns each transcript turn into a record (session, turn, time, project,
      harness version, model, interactive, sidechain, entities used); incremental; into `observations`
      Done: 2026-10-03. `adapters/claude_code/transcripts.py` `turns_from`; `store/archive.py` `update` keeps the
      transcripts and `store/usage.py` `derive` turns them into records, both via `cli.py` `_import`, run by
      `check`, `show`, `report` and `usage`. Here: 18 transcripts, 319 turns in 0.22 s; unchanged ones skipped.
      Round 6: a turn starts only at what you typed (265 of 320); names only, never other text.
- [-] `agentlint record --enable` / `--disable` — install and remove the hook
      Dropped: 2026-10-02. Nothing to install; ingestion runs whenever agentlint reads the configuration.
- [-] `--no-prompt-text` privacy mode
      Dropped: 2026-10-02. Then, prompt text was never stored, so there was no mode to choose. Since 2026-10-03
      every transcript is archived verbatim by decision (`02-design.md` decision log); what stops it being kept is
      `keep_days` and `agentlint forget`, not a mode.
- [x] Retention — rows older than six months deleted; `agentlint usage --forget` clears the history
      Done: 2026-10-03. `store/retention.py` `prune` (the `keep_days` setting, default 183) and `store/usage.py`
      `forget`; forgetting records a cutoff, so transcripts still on disk aren't read back in.
      `tests/store/test_usage.py`.
- [x] `EntityUsage` / `UsageIndex` — counts and window, never a stored rate
      Done: 2026-10-03. `config/usage.py`; only your turns count (interactive, not a subagent's).
- [-] `estimated_relevance()` for sources, returning an `Estimate` with sample size
      Done: 2026-10-03. `config/usage.py`; nothing consumes it until the ranker.
      Dropped: 2026-10-04. Nothing ever called it; the ranker uses observed usage only and applies no relevance
      discount. Deleted with `Estimate` in the design review (7.4).
- [x] `agentlint usage` — which entities you actually use, ranked
      Done: 2026-10-03. `report/usage.py`, `--days`, `--unused`, `--forget`. Here (after round 6): 265 turns in
      30 days, 2 of 618 skills and agents used; "never used since" the oldest kept turn.

### UI-1 — live server (ships with v1)

- [x] `agentlint ui` — localhost server, random port, token-gated, opens browser
      Done: 2026-10-03. `ui/server.py`, `cli.cmd_ui`; also a Host check against DNS rebinding.
      `tests/test_ui_server.py`.
- [x] JSON endpoints: `/api/config`, `/api/context`, `/api/findings`
      Done: 2026-10-03, as `/api/report` and `/api/texts` — one report document for the file and the server;
      context, findings and usage are blocks of it (decision log).
- [x] Same HTML shell as the static report, data fetched instead of baked
      Done: 2026-10-03. `ui/src/main.tsx` `Live`; `r` reads again.
- [x] Context view — what the last session actually loaded, incl. hook injections
      Done: 2026-10-03. `ui/src/ContextView.tsx` from the report's `context` (`report/html.py` `context_data`).
- [x] Entity view — all skills/agents with usage frequency, sortable by "never used"
      Done: 2026-10-03. `ui/src/EntitiesView.tsx`: used first, never used folded, a never-used filter.
- [-] Refuse to serve traces or recorder logs
      Dropped: 2026-10-03 (user). The recorder stores no text and traces don't exist yet; and the user wants the
      live UI to show their own transcripts (next item). The server answers only its fixed endpoints; a report
      file never carries conversation content.
- [x] Transcript reader — your sessions, readable (user, 2026-10-03: "I usually struggle to see old transcripts
      easily myself"). Live UI only, looked up by session id among the transcripts the adapter lists, never in a
      report file; prompts, replies, tool calls folded, skills, agents and hooks linked to their sources.
      Done: 2026-10-03. `config/conversation.py`, `adapters/claude_code/conversation.py`, `/api/sessions`,
      `ui/src/SessionsView.tsx`; the largest transcript here (21.5 MB) reads in 0.6 s. Needs the user's eyes.
- [x] The transcript reader shows what the agent was given before the first prompt, and again after each compaction
      or wherever the harness changed its prompt — folded, each part with its tokens and exact recorded text (user,
      2026-10-04: "sessions are not showing the system prompt")
      Done: 2026-10-04. Briefings: core `config/conversation.py` (`Briefing`, parts, `Conversation.texts` once per
      conversation), Claude Code's reader (`adapters/claude_code/conversation.py`, the same first-turn parser as the
      configuration), `ui/src/SessionsView.tsx`. At the top of every session and subagent transcript, after each
      compaction and where the prompt changed; 2.1.266/2.1.267 say not recorded. Here 867de26a's start reads ≈38.7k
      tokens. Needs the user's eyes.

### Onboarding, settings and history (decided with the user 2026-10-03)

- [x] Record the decisions (decision log, checklist, AGENTS.md non-negotiable 4 wording)
      Done: 2026-10-03. `docs/02-design.md` decision log (seven entries), this section, `AGENTS.md`.
- [x] "Fixed" removed from the UI, the terminal and `--json`
      Done: 2026-10-03. `report/terminal.py`, `cli.py`, `report/html.py`, the UI; `suppress --list` says "no longer
      found in"; a finding still closes internally. `10-reporting.md` "Fixed is not shown anywhere".
- [x] UI: sections start folded; an open one shows at most 10 rows and "show all"
      Done: 2026-10-03. `ui/src/fold.ts`, every grouped view; component tests.
- [x] UI: one tooltip at a time (an icon inside a chip showed both)
      Done: 2026-10-03. `ui/src/ui.tsx` `Tip`: the innermost hovered wins; icons inside chips are bare.
- [x] UI: a "what this is" line for sources the harness writes itself (listings, its prompt), worded by the adapter
      Done: 2026-10-03. `InjectionPoint.description` (core, not part of equality); Claude Code's wording in
      `adapters/claude_code/points.py`; a ⓘ on the row, the sentence in the detail.
- [x] `~/.agentlint/config.toml` written with commented defaults on first run; `agentlint config set/get/unset/list`;
      flags override per run; `harnesses` picks which adapters run
      Done: 2026-10-03. `settings.py`, `cli.cmd_config`; `tests/test_settings.py`. A file that doesn't read back is
      never written; a harnesses setting naming nothing agentlint reads stops a command with what to do.
- [x] Transcript archive — every transcript, main sessions and subagents, compressed in the store
      Done: 2026-10-03. `store/archive.py` (schema 6); here 1,511 transcripts, 676 MB → 179 MB in 6.9 s.
- [x] `keep_days` (default 183) prunes everything time-based except what is current
      Done: 2026-10-03. `store/retention.py`, run by every command that reads.
- [x] Usage re-derived from the archive when the reading rules change
      Done: 2026-10-03. `store/usage.py` `derive`, the adapter's `usage_reader_version` (named `usage_rules` until 2026-10-04).
- [x] Imports commit per transcript: an interrupted first import keeps its progress
      Done: 2026-10-03. `archive.update`, `derive`; a command's own writes stay one transaction.
- [x] `agentlint sync` imports now; `--daily` installs a scheduler job outside the harness; `--off` removes it
      Done: 2026-10-03. `schedule.py` (launchd, cron), `cli.cmd_sync`, `agentlint forget`; tested with a fake
      scheduler, the generated LaunchAgent linted (`plutil -lint`), never loaded here.
- [x] UI scope: harness, then project; sessions per project; the sidebar's details from the scope
      Done: 2026-10-03. `cli.cmd_ui` scopes/build/sessions, `/api/scope`, `ui/src/Sidebar.tsx` `ScopePicker`; a
      project's own sessions are read first. Here: 6 projects listed; another project's report in 3.4 s. Labelled
      and the harness always shown after the user couldn't find it; Session context picks among recent sessions.
      Successor: the home's harness and project lists and the breadcrumb, in the Quiet-ledger redesign below.
- [x] The transcript reader shows archived sessions the harness has deleted, and their subagents
      Done: 2026-10-03. Deleted sessions listed and read from the archive; an Agent call (`agentId`) or Workflow run
      (`runId`) opens to the subagents it started, nested in the call. `adapters/claude_code/conversation.py`
      `link_of`, `belongs`; `ui/src/SessionsView.tsx` `Subagents`.
- [x] First run: a one-time notice of what was read and what is kept
      Done: 2026-10-03. `cli.main`, on stderr, when `config.toml` is first written; `tests/test_settings.py`.
- [x] Settings move into the database, with a Settings screen in the UI; `agentlint config` reads and writes the same;
      `forget` never deletes them; an existing `config.toml` is migrated once (user 2026-10-04)
      Done: 2026-10-04. Schema 14 (`settings`, `state`); `config.toml` imported once under the write lock and renamed `.migrated`; `config list/get/set/unset` on the database; `forget` never deletes settings. Known edge: an unreadable `config.toml` fixed in place while the UI runs stays reported until imported or the UI restarts (fixed in wave 2b).
- [x] Review the terminal surface with the UI as the only way to interact
      Done: 2026-10-04 (user). The UI is the primary interface and gets every feature; the CLI stays for developers,
      minus what the changes made meaningless. Decision log; `tech_impl/11-ui.md` "The UI as the primary interface".

### The UI as the primary interface (decided with the user 2026-10-04)

- [x] UI writes: POST endpoints, the token in a header only, Host and Origin checked, JSON only, a capped body, values
      checked on the server; no `--allow-edit`
      Done: 2026-10-04. `ui/server.py`: token from the header only, Host and Origin checked, JSON only, 64 KB cap, values checked on the server; every refusal tested.
- [x] Settings screen: every setting, the daily-sync switch and its last result, archive totals, the version, and
      Privacy › Forget everything (a dialog plus typing `forget`; refused while a job runs)
      Done: 2026-10-04. `ui/src/Settings.tsx`: each setting with its default and Reset, Daily sync and its last result, archive totals, the version, Forget everything (a dialog plus typing `forget`; refused while a job runs).
- [x] Dismiss / Show again on a finding with a required reason, and a Dismissed screen across projects
      Done: 2026-10-05. POST `/api/suppress` and `/api/unsuppress` (what `suppress` does; a reason required), GET
      `/api/suppressions`, `ui/src/FindingDetail.tsx` (Dismiss, `s`), the Dismissed screen (under Findings, no key). Its
      last fixes were checked by their own tests and the full suites, not by an independent verifier (stalled).
- [x] Configuration › What changed: a project's snapshots and the diff from the previous one
      Done: 2026-10-04. GET `/api/snapshots`, `/api/diff` (`report/diff.py`, as `show --diff`), `ui/src/ChangesView.tsx`; the UI's reading now records what `check` records (no noise on reload; `not_recorded` says why when it can't).
- [x] Tests › Plan, Jobs and Situations: start (onboarding once), progress by polling, stop (Ctrl-C to the worker's
      process group), resume, salvage; a worker process that survives the server; keeping a Mac awake while a job runs,
      said on the page
      Done: 2026-10-05. `#/tests` (key 8; Settings is key 9): the plan, situations written and reviewed, jobs started in a worker process (`python -m agentlint.runner <job>`) that outlives the server, polled progress, Stop by process group, Resume, Salvage, caffeinate said on the page, and results as the stability screen by the set-of-tools rule (D10). Against a fake claude; the first real job needs a plain terminal.
- [x] A test's size follows what you pick: ticked targets × situations per target × runs per situation, the total and
      the time shown, the total the job's ceiling; 'max runs per test' goes (user 2026-10-05)
      Done: 2026-10-05. `situations_per_target`, ticks kept per project (POST `/api/plan/ticked`), POST `/api/jobs` takes
      the targets; `rank` keeps its own `--runs`. Follow-ups the same day: `config list`'s value column fits the
      longest value; while ticks are being saved, after one is refused, or while the plan is read again for one, the
      size says so and Start is disabled with that reason (`Tests.tsx` `Unsized`).
- [x] Tests › Results: every test of the project, newest first, each opening its results as one table — a row per
      situation grouped by target, the shared caveat once, a row opening to each run's actions (user 2026-10-05)
      Done: 2026-10-06. `ui/src/Results.tsx`: `#/results` under Tests in the sidebar (no key), the list from GET
      `/api/jobs` (the project in view's), one test at `#/results/<job>` from GET `/api/jobs/<id>/results` — a group
      row per target, Situation / Runs / Result / Used, a row opening in place to what matters and each run's actions;
      `#/tests/<job>` opens it there. The API: each situation's target by name and what matters, each run's seconds and
      actions (`{tool, what, uses}`, the run's directories as `.` and `~`, 60 a run, then a count); each job's
      standing, targets, situations and screens by state (50 jobs of 20 runs well under 200 ms).
- [x] Tests with far less text, and situations kept by default and folded under each target (user 2026-10-05)
      Done: 2026-10-06. Schema 16: a situation is written kept (`reviewed` NULL until a person reviews it), pending
      rows became kept, 'pending' is gone everywhere. Tests: explanations in the headings' and Start's tooltips, Not
      tested folded, one folded line per target (its name is the fold, review words, Write again), only the open job
      (finished ones are Results'). `Tests.tsx`, `Situations.tsx`, `Jobs.tsx`.
- [x] Diagnostics page, never mixed with findings
      Done: 2026-10-04. GET `/api/diagnostics`, `ui/src/DiagnosticsView.tsx` (key 7); never in Findings, its count or the overview.
- [x] One-time first-run panel in the UI (the trigger moves from `config.toml` being written to a database flag)
      Done: 2026-10-04. `state.welcomed`, shared with the CLI's notice: `ui` leaves it to the panel, other commands print the notice.
- [x] CLI clean-up: remove `config path`, `usage --forget` and the hidden `--history`; `config list/get/set/unset`
      read and write the database
      Done: 2026-10-04. Wave 1 (`config path`, `config` on the database) and wave 2a (`usage --forget`, `--history`).
      Last touched 2026-10-04.
- [x] Drop the offline HTML report: `report --html`, the shell's embedded-report mode, and the docs that describe it
      (UI-0, `10-reporting.md` "Exports")
      Done: 2026-10-04. `report --html`, the shell's embedded slots and the page's embedded mode removed; the report document is `report/document.py`, the shell loader `ui/shell.py`; the JSON contract and golden fixture stay.
- [x] README: install from GitHub, the first run, the flow
      Done: 2026-10-03. `README.md`. A built wheel installs into a fresh environment and `check` and `report` run
      from elsewhere (VERIFIED: the shell and emphasis.toml ship in it); installing from GitHub is UNVERIFIED — the
      repository has no remote yet.

### UI redesign: Quiet ledger (approved by the user 2026-10-03)

Direction a of three researched and mocked up; the home is the two-column one. The build plan follows
`docs/02-design.md` (decision log, 2026-10-03), one commit per item, verified against a copy of the store.

- [x] Record the Quiet-ledger decisions and open this section
      Done: 2026-10-03. `docs/02-design.md` decision log (eight entries, one of them the defaults taken while the
      user was away, to confirm), this section, a pointer in `tech_impl/11-ui.md`.
- [x] The UI server lists every installed harness agentlint reads, and reads through the chosen one
      Done: 2026-10-03. `cli.ADAPTERS` (the registry), `Adapter.installed()` and `version()`; every endpoint takes
      `?harness=`, an unlisted one gets 400; `/api/scope` lists every installed harness the setting allows with its
      projects; `tests/test_boundaries.py` also scans `ui/src`; `tests/test_ui_server.py`. A second real harness:
      UNVERIFIED (this machine has one adapter; tested with fakes).
- [x] Read again re-reads the configuration from disk
      Done: 2026-10-03. With the design review's "one reading's evidence": the adapter keeps no disk scan between
      readings, so `r` re-reads (`Adapter.read`, `config/reading.py`); a test reads, edits a file, reads again with
      one adapter.
- [x] Texts are kept per harness and project
      Done: 2026-10-03. `ui/server.py` keeps the texts of the last four scopes read, keyed by the reading's own
      harness and project; `/api/texts?harness=&project=` answers 404 for a scope not read, and never starts a
      reading; the UI asks for its own scope's; `tests/test_ui_server.py`.
- [x] Quiet-ledger tokens: warm greys, the system font, an ink accent, severity hues, four sizes, a 4 px scale,
      three radii; contrast pinned by a test
      Done: 2026-10-03. `ui/src/styles.css` one light and dark token block from the approved mockup (warm greys,
      system font, ink cursor and focus, severity hues, sizes 12/13/15/20, 4 px steps, radii 2/4/6/50%); contrast and
      the scales pinned by `ui/src/tokens.test.ts`. Seen in a real browser: needs the user.
- [x] One glyph per meaning: about 20 icons at 14/16 px, no heading icons, Sparkles retired
      Done: 2026-10-03. `ui/src/ui.tsx` `MARK` (21 marks), drawn severities, `NAV_ICON` and `ROLE_ICON`; no heading
      icons, the sparkle retired, the `?` legend from the same tables; `ui/src/glyphs.test.ts`.
- [x] Reading never blanks the page: the frame first, a 2 px line, seconds past 10 s, the view dimmed on `r`,
      errors inside the frame
      Done: 2026-10-03. `ui/src/Live.tsx` (reaches the server through an `api` object; an answer to anything but the
      latest request is dropped), `ui/src/Loading.tsx` (the frame and a static skeleton), the 2 px line and seconds
      past 10 s, the view dimmed on `r`, errors inside the frame; `ui/src/Live.test.tsx` with a fake api and fake
      timers. Seen in a real browser: needs the user.
- [x] The home: a harness list and a project list, keys, the default project read at once, Back and reload
      Done: 2026-10-03. `ui/src/Home.tsx` (home-3: the wordmark and the harness list on the left, the project filter
      and list on the right) on one list component, `ui/src/PickList.tsx` (a listbox, never a select or disabled,
      current row by a 2 px ink bar); the default project read as the home appears; choosing pushes the scope, Back
      returns home, a reload stays; `ui/src/Live.test.tsx`. Seen in a real browser: needs the user.
- [x] The breadcrumb `<harness> / <project> ▾`: `p` switches in place; a report file shows it static; the
      sidebar's count is neutral
      Done: 2026-10-03. `ui/src/Sidebar.tsx`: "<harness label> / <project> ▾" from data, tooltips naming Harness and
      Project; `p` opens the project list in place (a panel, keys isolated) and a switch keeps the view dimmed under
      the line; `0` or the wordmark returns home; a report file shows it static (`harness_label` in the report); the
      findings count is neutral; `tests/report/test_html.py`, `ui/src/App.test.tsx`. Seen in a real browser: needs the
      user.
- [x] Three groups (Yours, Installed, Harness) in one blue ramp; listings drawn split by their parts
      Done: 2026-10-03. `ui/src/model.ts` `GROUPS`/`GROUP_OF` and one attribution function `splitOf` for the bar and
      every cost row; `ui/src/Budget.tsx`; a listing drawn by its parts (its own part counts as Harness for now, said
      in its tooltip; splitting your own lines into Yours is an adapter follow-up); Configuration filters by `?group=`
      or `?authority=`; `ui/src/model.test.ts`. Real data: Yours 2.5k, Installed 23.9k, Harness 24.1k. Seen in a real
      browser: needs the user.
- [x] The overview ledger: Every session, Findings, Skills and agents, opening in place with the keys; its own
      skeleton; "since <date>" everywhere
      Done: 2026-10-03. `ui/src/Overview.tsx`: three rows of one shape (Every session, Findings, Skills and agents),
      no headline, no Last-session row, no cards; Findings a count line, open by default with errors or warnings, five
      checks then "N more checks" in place, each mix one cluster right-aligned (`SeverityMix`, `ui.tsx`); keys j/k,
      arrows, →/Space, ←, Enter; the ledger skeleton (`ui/src/Loading.tsx`); "since <date>" everywhere (`model.ts`
      `usageSince`); no all-clear past what ran or what is suppressed; `?chosen=1` so each figure opens a list of its
      size (617 of 620 here). Seen in a real browser: needs the user.
- [x] Packages carry a short name from their adapter
      Done: 2026-10-03. `Adapter.package_label()` (Claude Code: the name before "@", split as discovery splits it);
      `report.package_labels`; `format.ts` `packageName`. Core never cuts at "@".
- [x] The Findings list: a count line, human-named group heads with their severity mix, one aligned grid, "N more"
      Done: 2026-10-03. `ui/src/FindingsView.tsx`: the count line (`CountLine`, each figure a filter), group heads
      with the readable check name, the summary muted and the mix (`SeverityMix`), one grid of subgrids (severity,
      title, flags, package by its short name, file:line, +N), "N more" everywhere; groups start folded (default Q2);
      `--json`'s `dismissed_before_inputs_changed` is now `suppression_lapsed`. Real data: 12 groups, every head's mix
      equal to the per-check counts. Seen in a real browser: needs the user.
- [x] The finding detail: the title, → the action, an excerpt, why, a property list; `s`, `c`, `Esc`
      Done: 2026-10-03. `ui/src/FindingDetail.tsx`: a meta line, the title, → the action, the evidence (`Excerpt` in
      `Text.tsx`: five lines, the pointed one marked; or the members side by side), why, a property list (Check,
      Where, Rests on, Confidence when not certain); no Since (default Q1); `s` copies the suppress command, `c` the
      id, Esc closes. Real data: the 27 findings with a line show it, the 47 with members show every one. Seen in a
      real browser: needs the user.
- [x] Members side by side for duplicate names and overlapping triggers
      Done: 2026-10-04. `Finding.members` (`report/finding.py` `Member`) from duplicate-name and trigger-overlap; the
      terminal prints them and a finding's text keeps only why it matters (default Q3); the detail shows them as
      columns (kind, package, how the agent sees it, the description clamped, a link to the source).
- [x] One picker everywhere: a filterable list popover replaces the native selects
      Done: 2026-10-03. `ui/src/FilterMenu.tsx`: a button opening a popover with a filter box over `PickList`, keys
      kept inside; it replaces every native select (Configuration's package and who-put-it-there, Skills and agents'
      kind, Session context's session). A test checks no view renders a select. Real data: 53 packages, a 4-letter
      prefix narrows to 2.
- [x] One "N more" control, and nothing interactive inside a link (a listing opens with a chevron)
      Done: 2026-10-04. One `More` ("N more") in every view, the session reader's pager included; a listing's parts
      open with a chevron beside its link; a test that nothing focusable sits inside a link.
- [x] Detail panes and the reader without chips, tinted boxes, a coloured left border or helper text; help in a
      `<dialog>`
      Done: 2026-10-04. Source, listing, untraced and entity details use the finding detail's property list; rows
      show a packed severity mix; help is a `<dialog>`; the reader sets your prompts apart by weight (15 px/500), a
      hairline and the time, with no tinted box and links as plain text. Seen in a real browser: needs the user.
- [~] Re-measure and close: jsdom timings with this machine's report, the live timings, the shell's size, seen in a
      real browser (needs the user)
      Status: measured 2026-10-04 (`tech_impl/11-ui.md`, the VERIFIED paragraph after "Views"): jsdom 11-39 ms per
      view, live `/api/report` 13-15 s on a loaded machine, the shell 157 KB gzipped. Left: the user's look in a real
      browser — every visual claim of the redesign waits for it. Last touched 2026-10-04.

### Design review (adopted by the user 2026-10-03)

An adversarial review of data types, names, antipatterns and maintainability: 52 confirmed findings in 12 themes.
Its bug parts were fixed with round 8 (pruning that re-imported, settings frozen in an open UI, `usage --forget`
without asking, settings ignored by `rank`/`usage`, exit codes, the scheduler's store, a group pinned open).

- [x] Stale state: one reading's discovery and evidence, so checks never read what an earlier reading left on the
      adapter, and an open UI sees edited configuration (themes 1.3, 1.4)
      Done: 2026-10-03. One `Adapter.read(cwd, sessions)` per reading returns what was declared, the sessions and the
      evidence; `read_config` returns a `Reading` (`config/reading.py`) that `run_checks` hands to every check, and
      nothing is left on the adapter between readings, so an open UI sees edited configuration on `r`;
      `tests/adapters/test_claude_code_adapter.py`.
- [x] The finding recorder: `record()` takes the check run and the snapshot, and closes a finding only where its
      check judged it; one status vocabulary in SQL, Python, JSON and docs; the lifecycle API nothing reads is
      deleted (theme 2; a migration)
      Done: 2026-10-03. `store/findings.py` `record(project=, run=, snapshot=, at=, write=, note=)`, no optional
      guards; `FindingStatus` (schema 8: 'fixed' becomes 'closed', the history table dropped) and `SuppressionState`
      (`store/suppressions.py`); `tests/store/test_findings_store.py`, `test_suppressions.py`. The JSON keys are
      renamed with the redesign.
- [x] One rule per fact: interactive sessions, attributed tokens, the observed total, "before your first words",
      the skills and agents the agent chooses between, hook events (theme 3)
      Done: 2026-10-04. 3.1 `headless()` everywhere; 3.2 one attribution per always-on outcome (`model.Attribution`);
      3.3 overcounted totals; 3.4 `loads`, read by the UI; 3.5 chosen by role, the UI reading `usage.summary` (no namespace
      strings left in `ui/src`); 3.6 one hook-event table (`hooks.EVENTS`); 3.7 listing rows by their parts (`splitOf`).
- [x] Identity: entity ids carry their scope and are checked unique; untraced text has a stable id (theme 4)
      Done: 2026-10-04. 4.1 `points.entity_id(kind, scope, name)` builds every id, unique in `Declared` and
      `ConfigSnapshot`; 4.2 `Untraced.id` (12 hex digits of its point and the adapter's key — for Claude Code the hook's
      event and command, never how the session began — serialised; rounds 10 and 11), Configuration's routes
      typed by kind (`#/config/source/`, `listing/`, `untraced/`), `configHref` and `targetOf` in `model.ts`.
- [x] Settings resolved once, with ranges on the settings themselves and no second defaults (theme 5)
      Done: 2026-10-03. `settings.py` (each default and range on its field, checked in `__post_init__`, a
      `bounded(key)` argparse validator), `cli.py` `Options` passed to every command; `read_config(sessions=)`,
      `plan(budget=)` and `report_data(usage_days=)` take no defaults; `tests/test_settings.py`.
- [x] Contracts: a fixture built from Python's real report output that the UI tests load; named SQL columns; one
      time helper; a typed `from_data`; distinct version constants (theme 8)
      Done: 2026-10-03. `store/_time.py` (every TEXT time, UTC microseconds, naive refused); every store statement
      names its columns, `tests/store/test_schema.py` holds a database upgraded from the real v1 to the fresh schema;
      `serialize.from_data` (stored snapshots read back as the model, `show --diff` on them);
      `SNAPSHOT_FORMAT_VERSION` and `DB_SCHEMA_VERSION`; the golden fixture `ui/src/fixtures/report.ts` written by
      `tests/report/test_golden.py` and type-checked and rendered by `ui/src/golden.test.tsx`.
- [x] Structure and helpers, with the redesign: the live server's catalogue out of `cmd_ui`, tracebacks logged,
      one list-keyboard hook, one copy of each helper, the route and tooltip state bugs (themes 10-12)
      Done: 2026-10-04. 10.1 `ui/catalog.py` `SessionCatalog` and `cli.store()`, 10.2 tracebacks logged, 11.1-11.3
      (a group revealed once, typed routes and an error boundary, tooltip state in React), 12.1 `useListNavigation`
      in every list (the reader included), 12.2 one visibility vocabulary, 12.3 and 12.4 one copy of each helper.
- [x] Data shapes and the renames, after the redesign: the five-in-one locator string split, one transcript-item
      type per kind, one session header, `history`, `Unit` and 33 more renames (themes 6, 7, 9; migrations and
      format versions where stored data or output changes)
      Done: 2026-10-04. 6.1 `Provenance.file` + `anchor` and `Location.source_id` (snapshot format 3, schema 11), 6.2
      opaque subagent references and transcript handles, 6.3 archive columns named for what they hold (schema 10), 7.1
      one transcript-entry type per kind, 7.2 one `SessionHeader`, 7.3 invariants, 7.4 dead fields gone, 7.5 enums by
      member, 9.1 `--sessions`, 9.2 `AblationTarget` (schema 9), 9.3 the Adapter Protocol in three parts; renames
      `CheckResults` and `read(sessions)`. Finding ids unchanged on this machine (171 and 169) across all of it.

### Exit criteria for v1

- [-] Hook adds no measurable latency to a session
      Dropped: 2026-10-02. There is no hook; nothing runs inside a session.
- [-] Hook contributes zero tokens to agent context (verify by diffing a session's token count)
      Dropped: 2026-10-02. There is no hook; true by construction.
- [ ] A week of recording produces a usable entity-frequency ranking
      (needs the user: history already reaches back to 2026-09-15 — run `agentlint usage --unused` and judge)
- [x] UI binds 127.0.0.1 only — verify it is unreachable from another machine
      Done: 2026-10-03. Listening socket is `127.0.0.1:<port>` (`lsof`); a request to this machine's LAN address
      is refused (curl exit 7, HTTP proxy bypassed — through the proxy it was the proxy's 503); loopback answers.

---

## Milestone v2 — Generate and run

Decided 2026-10-04 (user): ad-hoc runs and resuming come first, the overnight budget later; a situation runs in an
empty directory under agentlint's home, so `forget` deletes the results too.

### Ranker

- [x] A line of the plan is what one variant removes — a section where it can be cut out, a whole skill or hook
      otherwise (decided 2026-10-03, user)
      Done: 2026-10-03. `rank/plan.py` `units`, `cuttable`; here 2,630 rules make 710 units.
- [x] Filter — drop `ablation: NONE`, already-settled, unused `ON_INVOKE` bodies (and never-in-context)
      Done: 2026-10-03. `rank/plan.py`; each skip carries its reason. `tests/rank/test_plan.py`.
- [x] Filter — situations that failed the stability gate
      Done: 2026-10-06, in the job rather than the ranker: a test's with/without comparison is gated on its situation's
      stability runs (`runner/gate.py`, D9), so a situation that failed them spends no runs.
- [x] Sort — always-loaded first, then descending token cost
      Done: 2026-10-03. Every session or message first, then tokens (a whole unit counts all its rules).
- [x] Run budget in runs (not dollars), cut the plan at the ceiling
      Done: 2026-10-03. `--runs`, default 600; the ranked prefix that fits, the rest "not this time".
- [x] Staleness tracking (`RuleStatus`, re-queue on fingerprint change)
      Done: 2026-10-03, as `UnitStatus` in the `verdicts` table (schema 4), keyed by project and unit — since 2026-10-04 `TargetStatus`,
      `verdicts.target_id` (schema 9); read by the
      ranker, written by the runner when it exists.
- [x] `agentlint rank` — print the plan including SKIPPED lines with reasons
      Done: 2026-10-03. `report/rank.py`; skips grouped by reason with counts, `--all`, `--explain`, `--phase`.
- [!] `agentlint test --rule <id>` — the escape hatch, more useful than any ranking
      Blocked: needs the runner.

### Probe generator

- [x] Generation prompt + output schema
      Done: 2026-10-04. The prompt is `probes/prompt.py` (version 1), called by `probes/generate.py` through Claude Code's
      `adapters/claude_code/generator.py` (D12, D14-D16); the reply's schema and strict check in `generator.py` and
      `validate.py`, the contamination check in `contamination.py`, a fake in `fake.py`. The call is E5's, run on this
      machine; this code hasn't yet called the real CLI — the first Write situations from a plain terminal is its first run.
- [x] Caching on rule content hash
      Done: 2026-10-04. `probes/store` in schema 13: a target's situations are valid until its text changes (D15); model, prompt version and date recorded.
- [x] Rule→situation linkage
      Done: 2026-10-04. The `probes` table: probe id = hash(target text hash + situation text), with the target id and review status.
- [x] Human review flow — in the UI first (user 2026-10-04, D17)
      Done: 2026-10-05. Tests › Situations: keep, drop or edit each generated situation, with what matters; only kept ones go into a job (D12).
- [x] Record what matters in each situation, from a fixed menu; shown in review, used for no verdict until claim
      checking exists (user 2026-10-04)
      Done: 2026-10-04. The core menu and its column on `probes`; returned for review, read by no verdict.
- [x] What matters names its thing (user 2026-10-07)
      Done: 2026-10-07. `probes/matters.py` (`WhatMatters`, a thing as `skill:<name>`/`skill:*`, `agent:`, `tool:`,
      `program:`, `file:`), prompt version 3, the reply check (a skill or agent the configuration doesn't have rejects
      the situation), frozen in the plan (format 5); `analysis/exercise.py`; review and Results show it. The gate's
      "not exercised" of the same day became "not followed" (below, the expectation's strength).
- [x] Cheap-model selection
      Done: 2026-10-04. Claude Code's generator: `claude -p --bare --model haiku --tools "" --no-session-persistence --output-format json --json-schema …` in an empty directory under `AGENTLINT_HOME` (D14). First run from this code 2026-10-05: 6 situations for 2 package hooks.
- [x] Prompt version 2: situations the agent can only answer by acting — 3 of the first job's 6 got only written
      answers (user 2026-10-05)
      Done: 2026-10-05. `probes/prompt.py` `PROMPT_VERSION = 2`: requests answered only by acting (a tool or command,
      a program, a file created or changed, a skill or agent), never from knowledge alone; recorded with each probe,
      invalidating none (D15). `04-probe-generator.md` "The prompt". Whether it lowers the share of "No action" is
      UNVERIFIED until a job runs its situations.

### Runner

- [~] Variant construction — copy config tree, remove target, clonefile for speed
      Status: `runner/variant.py` clones a home under `AGENTLINT_HOME` from the adapter's copy plan
      (`adapters/claude_code/variants.py`), every write guarded, and cuts a user or project instruction-file section,
      settings key or skill or agent directory (D1) and a package's hook by editing its copies (D3); homes are deleted in
      the background and a killed job's leftovers swept (D2, `runner/execute.py`). Left, each refused today
      (`TargetNotSupported`): a user's or project's own hook (only a package's hook is cut as an entry), and a package's
      skill, agent or other piece. Only ablation (v3) removes anything; a stability job runs the baseline alone. Last
      touched 2026-10-05.
- [x] Variant verification — re-read config from the variant, raise if the rule is still present
      Done: 2026-10-04. `runner/verify.py`: identities mapped (variant home to `~`, run directory to the project), so a
      target still present raises `AblationFailed` — the id comparison could never fire for an instruction file. Reports
      what else changed and the files read from the real home (escapes). The baseline-equals-real check (D4) came with
      the executor.
- [x] Credential handling in the substituted HOME
      Done: 2026-10-04 (D19, E1). Nothing is copied — a variant authenticates through the managed AI Gateway; an auth
      failure the launcher classifies pauses the job (`adapters/claude_code/launch.py`).
- [x] Two-phase execution (stability screen, then ablation on survivors)
      Done: 2026-10-06 (D9, user 2026-10-05). `runner/plan.py` `two_step_plan` (format 4: a comparison's `gate`), the
      engine running a gated comparison only once its gate opens (`runner/gate.py`), a closed one `skipped` in the
      job's progress; Start plans both steps target by target, the plan's size the most it can run and the ceiling;
      one run count for every step (`runs_per_situation`, the user 2026-10-07). Against the fake harness end to end (`tests/test_ui_testing.py`):
      13 runs, the target removed, "seems to do nothing".
- [x] Single-run execution + trace retrieval
      Done: 2026-10-04. `runner/execute.py` `VariantExecutor`, Claude Code's `ClaudeCodeLauncher` (`claude -p … --session-id`), the trace read by session id from the variant home. First real job 2026-10-05: 18 of 18 runs recorded (Verified Facts).
- [x] Run-count ceiling and abort
      Done: 2026-10-04. `runner/engine.py`: counts recorded runs by default since D8 (the user, 2026-10-04); a job that reaches its ceiling closes as `ceiling`. Against fakes: within one run across a kill (`tests/runner/test_kill_and_resume.py`).
- [x] Throttle backoff
      Done: 2026-10-04 (D7). API errors classified from the transcript are retried with backoff, at most 3 times per key and a per-resume cap, then the job pauses; the values are placeholders until real rate limits are seen.
- [x] Concurrency control (within a comparison, to preserve depth-first)
      Done: 2026-10-07. `runs_at_once` (Settings › Tests, default 1, 1-8): the engine keeps that many runs out in
      threads, in plan order, gates waiting for their runs, the ceiling counting runs out, Stop stopping every
      harness out (`05-runner.md` "Concurrency"). Verified against the fake harness; on real runs UNVERIFIED.
- [x] The real Claude Code executor: a fresh variant home per run, deleted in the background (D2); the project's
      configuration beside the situation (D1); a package's hook removed by editing the copy (D3); the baseline
      check, `BaselineMismatch` (D4); the version pinned and the model passed on every run (D5); the org's guardrails with every MCP tool that acts
      outside the machine removed (D6); API errors retried as infrastructure (D7); no credentials copied (D19)
      Done: 2026-10-04. `runner/execute.py`, `runner/variant.py`, `adapters/claude_code/launch.py` and `variants.py`;
      `forget_everything` sweeps run directories too. 1,050 Python tests, all against a fake claude — a Claude session
      can't start the real CLI, so the first real job runs from a plain terminal.
- [x] The ceiling counts recorded runs, with a separate cap on retries (D8, user 2026-10-04)
      Done: 2026-10-04. The engine's default counter is `recorded_runs`; a key is retried at most 3 times and a resume
      launches at most its retry cap of runs that record nothing.
- [x] agentlint's reader follows the harness for a variant's packages: the harness reads a package from its marketplace
      checkout under the substituted HOME (E2, E2b)
      Done: 2026-10-04. `adapters/claude_code/discovery.py` maps a marketplace location recorded under the original home to
      the same path under the copy (symlinked spellings included); a variant without its copy is refused
      (`MarketplaceNotCopied`). A baseline variant here lists 0 escapes (was 648); the real reading is unchanged.
- [x] `agentlint test` onboarding, once: what will run, about how long, the tools removed from every run (D6), that hooks run,
      the harness telemetry line, and the defaults for runs per situation, max runs per test, model and time limit
      per run, then one yes/no — never shown again once accepted (user 2026-10-04, D18; consent decided 2026-10-03)
      Done: 2026-10-05. One screen from GET `/api/onboarding` with a single yes/no; `state.onboarded`; the removed tools, hooks, the telemetry line, the keep-awake line and each test setting with its default.
- [~] Diagnostics: agentlint's findings about itself (the generator wrote nothing, a situation named its own target,
      runs retried) in their own list — `agentlint diagnostics` and a UI page — never with the user's findings (D16)
      Status: the `diagnostics` table (schema 13), the generator's writes and the UI page (key 7) are built. Left: the
      runner writes none yet (a job's launches that recorded no run show in Tests › Jobs); the command is UI-first since
      2026-10-05, later only if wanted. Last touched 2026-10-05.

#### Job lifecycle and resume

- [x] Job state file — frozen plan, per-comparison progress, `completed_runs`, `in_flight`
      Done: 2026-10-04. Store schema 12, `store/jobs.py`: a `jobs` row (frozen plan JSON, status, launches, in-flight key, lock) and `runs`; progress is derived from `runs`, never stored beside them.
- [x] **Plan frozen at creation, replayed verbatim on resume — never recomputed.**
      Recomputing against an overnight package update silently mixes two fingerprints in one
      result set.
      Done: 2026-10-04. `runner/plan.py`, frozen as JSON in the `jobs` row and replayed verbatim; a SIGKILLed runner
      resumes running every key once (`tests/runner/test_kill_and_resume.py`, against fakes).
- [x] Fingerprint / harness-version / model check on resume — raise `ConfigChanged`, don't warn
      Done: 2026-10-04. `runner/engine.py`: `EnvironmentChanged` (version or model) is checked before `ConfigChanged` (fingerprint); nothing launches.
- [x] `--salvage` — analyse completed comparisons and close a job that can't be extended
      Done: 2026-10-04. `runner/engine.py` `salvage` closes the job and keeps its runs; the flag itself comes with `agentlint test`.
- [x] Write ordering: `in_flight` → launch → trace → `completed_runs`, committed at each step (the store's WAL with
      synchronous FULL already syncs every commit; "fsync" was stale wording)
      Done: 2026-10-04. `runner/engine.py`, one transaction per step.
- [x] Interrupted-run detection and reporting on resume
      Done: 2026-10-04. `runner/engine.py`: reported exactly once, in the Report or on the exception a resume ends in (Ctrl-C included).
- [x] Execution order: depth-first across comparisons, interleaved variants within one
      Done: 2026-10-04. `runner/plan.py`: the frozen order.
- [x] `MIN_RUNS_FOR_VERDICT` — a partial comparison reports `incomplete`, never `no-change`
      Done: 2026-10-04. `runner/status.py`: not-attempted, incomplete (below 3 a variant), partial, complete.
- [ ] `agentlint status` — progress, ETA, last-progress age
      UI-first since 2026-10-05: Tests › Jobs does this; a command comes later only if wanted.
- [ ] `agentlint jobs` — list with fingerprint, phase, completion
      UI-first since 2026-10-05: Tests › Jobs does this; a command comes later only if wanted.

### Trace

- [x] Transcript locating by `session_id`
      Done: 2026-10-04. `adapters/claude_code/trace.py` `locate_trace(home, session_id)` through `conversation.find()`; a missing or unreadable transcript raises `TraceMissing`, never an empty trace.
- [x] Trace extraction (tool calls, order, entities fired, files touched)
      Done: 2026-10-04. Core `trace/model.py`, Claude Code's `trace_from`: ordered tool calls kept raw (name, input, message and tool-use ids, error or denied, subagent, usage refs), fired hooks, version, model and cost. No normalisation baked in (Open Question 2 is D10).
- [~] Normalisation per Open Question 2
      Status: decided (D10) and built in `analysis/projections.py`: `used` (a set of tools for 'inconsistent'),
      `effect` and `strict` for the with/without comparison over the adapter's action terms (a shell command as the
      programs it runs, user 2026-10-05; paths relative to each run), `CANON_VERSION` 1. Left: the noise study (E3, 30
      runs) to check the rules against what varies under identical conditions. Last touched 2026-10-06.
- [ ] Canonical form + hash for grouping

### Analysis — stability only

- [x] Outcome grouping across runs
      Done: 2026-10-04. `analysis/stability.py`: a Distribution over outcome classes from a projection passed in; screen states incomplete, disagreed, agreed (never called stable) and mixed environment (refused).
- [x] Modal share computation
      Done: 2026-10-04. `analysis/stability.py` `modal_share`; the first-divergence renderer and the entities two branches differ in (`analysis/render.py`, `branches.py`).
- [x] No action: a screen whose runs all took no action reads "No action", never "Agreed" (user 2026-10-05)
      Done: 2026-10-05. `analysis/stability.py` `ScreenState.NO_ACTION` (at least MIN_RUNS compared runs, every
      projection empty; disagreement, too few runs and mixed environments keep their states), carried by
      GET `/api/jobs/<id>/results` and shown as "No action" in `Results.tsx`; `07-analysis.md`'s table of states.
- [x] The expectation's strength: each situation's runs with the target that did what it expects, then — where at
      least one did — as many without it; works only at every run with and none without (user 2026-10-07)
      Done: 2026-10-07. `analysis/expectation.py` (verdicts works, partial, adds nothing, backfires, not followed;
      strength = share with − share without, exact; a target led by its weakest situation); the gate opens on one run
      that met it, consistent or not, and shuts as not followed (`runner/gate.py`); the second step runs only without
      the target (plan format 6, `Comparison.sides`), so a situation costs 3 + 3 at the default; store schema 17
      deleted situations that expect nothing; Tests › Results' Expects / With it / Without it / Verdict table
      (`Results.tsx`). `07-analysis.md` "The expectation's strength". Pending: these results as Findings (below).
- [x] Test results as findings: not followed, partial, adds nothing, backfires, and runs that weren't consistent, in
      the Findings list beside the static checks' (user 2026-10-07: "any delta short of perfect is a finding")
      Done: 2026-10-07. `runner/tested.py` puts each target's latest decided test into the reading (`Reading.tests`,
      while the target's text is unchanged); `checks/tested.py`'s `tested-expectation` (warn; partial info) and
      `tested-consistency` (info), behaviour / measured, core checks. Verified on real runs from a copy of the
      store (`02-static-checks.md`).
- [ ] Instability finding, naming the competing branches
- [ ] `agentlint test --stability-only`
      UI-first since 2026-10-05: Tests › Jobs does this; a command comes later only if wanted.

### Exit criteria for v2

- [ ] Detects a known instability (construct one deliberately — install two colliding skills)
- [ ] Survives being killed mid-run and resumes correctly
- [ ] Respects the cost ceiling to within one run

---

## Milestone v3 — Ablation

- [x] Variant pairing (baseline / without)
      Done: 2026-10-06. A situation's ablation comparison interleaves `baseline` and `without` runs (the variant with the
      target cut and verified gone, `runner/execute.py`); its results pair them (`ui/testing.py` `effect_entry`).
- [x] Distribution distance + permutation test
      Done: 2026-10-06, the pure core. `analysis/effect.py`: total variation over the `effect` sets, an exact
      permutation p counted class by class (deterministic; a seeded Monte Carlo only past 200,000 partial sums), an
      effect only at tv >= 0.3 and p < 0.05, the evidence per term; `TraceReader.action_terms` and Claude Code's shell
      reader (`adapters/claude_code/terms.py`, `shell.py`). Wiring it into jobs (D9) comes next.
- [x] Instability gate — skip ablation where the baseline is unstable
      Done: 2026-10-06. Twice: the job's gate spends no runs on a situation whose consistency runs disagreed or took
      no action (`runner/gate.py`), and the verdict is "unmeasurable: baseline unstable" when its ten baseline runs fall
      below 0.9 (`analysis/effect.py`).
- [x] `measured: changes | no-change | unmeasurable`
      Done: 2026-10-06, the pure core. `analysis/effect.py` `situation` and `target`: incomplete below 3 runs a side;
      unmeasurable for mixed environments, no action (user 2026-10-05), a baseline unstable, or neither; no-change only
      when every run on both sides did the same actions in the same order on the same targets (D10). Wired into jobs
      next.
      (`matches_intent` is always `unknown` until claim extraction ships)
- [ ] **Blindness guard** — a rule with no trace-checkable claim reports `unmeasurable`,
      never `no-change`. Without this the tool tells users to delete tone rules it can't see.
- [ ] User-facing labels: no effect / changes behaviour / can't measure
- [ ] "No effect" finding with evidence (how many situations, how many runs)
- [ ] "Changes behaviour" finding must NOT read as "works" — intent is not measured in v3
- [ ] `verdicts` table — cross-job rule verdict store, survives trace pruning
- [ ] Coverage derived from verdicts × probes (not stored separately)
- [ ] Staleness tracking per rule
- [ ] Trace retention policy — prune `jobs/<id>/runs/` after N jobs
- [ ] `agentlint test` — full pipeline

### Exit criteria for v3

- [ ] Correctly identifies a deliberately-planted dead rule
- [ ] Correctly identifies a deliberately-planted live rule
- [ ] Does not report an effect for a rule that was not changed (false-positive check)

---

## Milestone v4 — Editing and polish

### UI-2 — coverage (ships with v3)

- [ ] Coverage view — measured / unmeasured / deletion candidates
- [ ] Deletion-candidate total in tokens, prominently
- [ ] Measured verdict shown inline in the config view
- [ ] Trace diff viewer for instability findings — highlight where runs diverged

### UI-3 — editing

- [!] Disable a source or rule
      Blocked: Open Question 6 — unknown whether a single plugin/skill can be disabled through
      the harness's own settings. If not, edits only apply to agentlint's test variants, which
      guts the feature. Resolve before building.
- [ ] Quarantine store with manifest (original path, hash, reason, timestamp)
- [ ] Restore — one click, reverses the manifest
- [ ] Backup-then-edit for user-owned files; never edit package-managed files in place
- [ ] Diff preview before any write
- [ ] Immediate re-check after edit, showing the token delta
- [ ] `agentlint ui --allow-edit` gate — editing off by default

### Polish

- [ ] Finding report — terminal output
- [ ] `--quick` / `--standard` / `--thorough` presets
- [ ] Config file for thresholds
- [ ] Export of derived findings only (never traces)

---

## Deferred

Not dropped, but explicitly out of scope until the above works.

- [ ] `declared-vs-delivered` check — compare what the config says should load against what a
      transcript shows actually loaded
      *Deferred 2026-10-01. High value in one direction: a skill that silently fails to load from
      a YAML typo produces no error anywhere, and the user concludes the agent is flaky. But the
      check's accuracy is a direct proxy for how correct the config reader is — a reader bug
      presents as a false positive, and v0 cannot afford one. Verified during design: a
      first attempt at the declared side mis-resolved plugin ids and reported dozens of existing
      skills as missing. Revisit once enumeration is trustworthy, and scope it to
      declared-not-delivered only; the reverse direction is our bug by construction and does not
      belong in a user's findings list.*
      *Mechanism is cheap when we want it: `skill_listing` attachments carry a flat `names` list
      (502 entries on the reference machine vs 657 SKILL.md files on disk). Needs an observation
      whose config fingerprint matches current, which usually means paying for one headless run
      (~$0.22) — so it does not belong in the free `check` tier either.*

- [ ] Claim extraction + `matches_intent` → the **backfires** finding
      *Deferred past v3. The probe generator already reads a rule's intent to write situations
      for it; emitting a `claim` predicate is the same call, same structured output, same cache
      entry. Unlocks the best finding this tool can produce: "this rule says prefer X, and
      removing it increased X — it does the opposite of what it says."*
      *Constrained to entity-registry IDs, so it parses input and never evaluates output —
      the verdict itself stays arithmetic (a rate comparison).*
      *Note: the blindness guard depends on `claim is None` and should ship in v3 even if
      `matches_intent` doesn't.*
- [~] Rule extraction into structured form + conflict join
      *The semantic conflict detection layer. Valuable, but the static checks and behavioral
      tests deliver findings first with far less machinery. Revisit after v3.*
      Status: started 2026-10-07 (the user: build it next). Decided: contradictions and repeats both; a
      contradiction is a candidate only (suggestive, both quotes, you judge); every section is read, always-on
      and skill/agent bodies alike (~2,400 sections here); read when you trigger it, never automatically, each
      section's text once (cached); the user picks the sources to read (select all, clear), and a section whose
      fingerprint (text hash) was read is never read again. Done: the core (form, prompt, strict check, pairing),
      the store, reading (`directives/extract.py`), the two checks (`12-directives.md`), the endpoints and the
      selector page, Findings › Read instructions (`11-ui.md` "9."). Left: the user's first real reading (how well
      haiku keeps to exact quotes, how many candidates). Last touched 2026-10-07.
- [ ] Adjudication panel for conflict candidates
- [ ] Second harness adapter (Codex or Cursor)
      *Write the adapter interface in v0 even with one implementation; add a second only once
      the core is stable.*
- [ ] Team / plugin-author mode
- [ ] Marketplace admission service

## Dropped

- [-] Mining past sessions for test situations
      Dropped: 2026-10-01. Only 9 days / 227 human prompts available locally — not enough to
      carry a suite, and the complexity is substantial. The recorder survives for ranking
      only. See Decision Log in `docs/02-design.md`.
- [-] Building on `claude plugin eval` as the execution foundation
      Dropped: 2026-10-01. Plugin-scoped rather than config-scoped, and harness-specific.
      May return as an optional fast path on Claude Code.
- [-] In-session skill frontend ("why did you just do that?")
      Dropped: 2026-10-01. Would join the configuration it measures, violating non-negotiable #4.
      The UI's Context view serves the same need from outside the session, and does it better —
      it can show what was loaded without competing for the attention it's measuring.
      Note: this is NOT an argument against the UI, which is core. See `tech_impl/11-ui.md`.
