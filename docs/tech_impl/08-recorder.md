# 08 — Recorder

Usage history: which entities — skills, agents, tools, commands — you actually use, turn by turn.

**One job: ranking.** It tells the ranker (03) which rules' conditions actually occur, so
the testing budget goes somewhere useful. It is explicitly **not** a source of test situations —
see the decision log in `docs/02-design.md`.

## Where the data comes from: the transcripts the harness already writes

Decided 2026-10-02 (user): **no hook.** The spec first had `agentlint record --enable` install a hook into
the user's settings — which would mutate the user's real configuration, and join the pile non-negotiable 4
keeps agentlint out of. But the harness already writes every session to a transcript, and each turn there
carries what it used: tool calls by name, skills invoked through the Skill tool, agents delegated to by
`subagent_type`, MCP tools by name, slash commands in the prompt. So the recorder reads those, through the
adapter, and keeps them — the transcripts themselves, archived, and the usage derived from them — in the agentlint
database whenever agentlint runs.

- **Nothing installed into the harness, nothing added.** No latency and no tokens in any session, by construction.
- **Every transcript is archived** (decided with the user 2026-10-03, replacing "no prompt text, ever"): sessions and
  their subagents, compressed, in the private store — so the transcript reader shows sessions the harness has
  deleted, and usage can be read again when the reading rules change. A record of usage still holds names only.
- **History outlives the transcripts** — as long as agentlint runs at least once within the harness's transcript
  retention (Claude Code: 30 days by default — Verified facts in `docs/02-design.md`), or a daily sync does it for
  you. Transcripts deleted before agentlint ever read them are lost; the usage view says how far back history goes.
- **Incremental and resumable.** A transcript is read again only when its size or modification time changed, and
  then only its new bytes when it merely grew (a hash of its last bytes tells growth from a rewrite). Each
  transcript is its own transaction, so an interrupted import keeps what it finished, and each remembers whether
  usage was derived from what is kept, so the next import derives what an interrupted one archived but didn't
  read. Two agentlints importing at once (the daily sync and a UI reload, say) never keep the same bytes twice:
  each transcript's write first checks nobody else wrote it since it looked. A turn's identity is its session and
  position, so deriving again never double-counts.
- **Kept `keep_days`** (default 183, one setting for everything; `00-overview.md`), measured by a transcript's
  modification time. A transcript past it loses its bytes but keeps its row — how far it had got and a hash of its
  last bytes — while the harness still lists it, so it isn't read in again on every command (setting `keep_days`
  below the harness's own retention would otherwise re-archive and re-prune the older transcripts each time); if
  it carries on, only what it gains is kept. A row with nothing kept goes once the harness no longer lists the
  transcript.
- **Forgetting** — `agentlint forget` (`--yes` skips its question; with nothing to ask on stdin, nothing is
  deleted), or the UI's Settings › Privacy › Forget everything (`11-ui.md`) — one function,
  `forget.forget_everything`, for both (2026-10-04). It refuses, deleting nothing, while a test job's runner is alive
  (a job whose lock — the runner's pid and that process's start time — names a live process; a dead runner's lock
  blocks nothing): stop the job first. Otherwise it deletes every archived byte and every turn, every
  stored configuration reading with its texts (`snapshots`, `blobs`: hook output, session context, the sessions'
  directories), every finding (`findings`, whose text can quote them), every test job with its runs (`jobs`,
  `runs`: a run's trace is read from its transcript) and their directories under `AGENTLINT_HOME/runs.noindex/` (whole variant
  homes, each run's transcript in one; any a deletion fails on is named, and swept again next time —
  `05-runner.md`, Lifetime), every generated situation (`probes`) and every diagnostic
  (`diagnostics`), and records a cutoff to the microsecond.
  It keeps what holds nothing from a transcript: your settings, suppressions and their baselines (your decisions —
  declared hashes and source ids), analyses (check ids), verdicts and agentlint's own bookkeeping (`state`). And it keeps each listed transcript's bookkeeping row
  (`archive_files`): where it is (its path, which names the project directory), its session, how far it had got,
  when it last changed and a hash of its last bytes — no transcript text, but what keeps it from being read in again. A reading of the configuration never uses a session that
  started before the cutoff: `read_config` takes it from the store, the adapter leaves those sessions out of what
  it reads (and core drops any it returned anyway), so until a new session runs, every check that needs one is not
  evaluated — "no session since you forgot" — and a reading or finding that a forget overtook while it was being
  read is refused rather than stored after it. It also lists the harness's transcripts as they are at that moment, so
  what was written since agentlint last read them is forgotten too: a transcript untouched since is never read
  back in, one that carried on is kept only from where it had got when you forgot, and one rewritten after it,
  whose old and new bytes can't be told apart, keeps nothing until it grows again. A transcript the harness
  didn't list when you forgot is read in only if it changed after the cutoff. (A schema-5 store forgotten before
  upgrading had no such list: a transcript that carried on past the cutoff is archived whole, though its usage is
  still cut at the cutoff.)
  A transcript with nothing kept is no longer listed from the archive — not by the UI's sessions or subagents, and
  not by an already-open UI, which drops what it had cached of it.
  What this promises is that of what came before the forget agentlint keeps only that bookkeeping, that no
  reading of the configuration uses a session that started before the cutoff, and that usage keeps only turns taken
  after the cutoff — of any session, including one that started before it and carried on (`store/usage.py` cuts
  by the turn's time, not the session's start; decision 2026-10-04) — not that an earlier session is never read.
  The UI's transcript reader (`11-ui.md`) still lists and opens the sessions the harness itself keeps, from the
  harness's files, when you ask, and stores nothing of them; `forget`'s message and the README say all this
  (2026-10-04; before, both said a session that started before the forget is never read, and then that agentlint
  kept nothing from before it).
  Deleted rows are overwritten (`secure_delete`) and the write-ahead log emptied, so nothing forgotten stays
  readable in the file; the archive keeps no transcript bytes of its own for bookkeeping, only the row above (path,
  session, offsets, time, a hash). The database is readable by you only (0600 in a 0700 directory), like the
  transcripts it copies from.
- **Harness-agnostic core.** Core owns the record and the counting; the adapter turns its harness's transcripts
  into records.

## Record

One row per turn — from one genuine user prompt to the next — in the database's `observations` table:

```
session_id, turn          # identity: the turn's position in its session
at                        # when the turn began
project                   # the session's working directory ("" when the transcript doesn't say)
harness_version           # the version the session started on ("unknown" when it doesn't say)
model                     # the model that answered this turn ("unknown" when none did)
interactive, sidechain    # headless runs and subagent turns are kept but flagged, not counted as your usage
entities                  # JSON: what the turn used, as namespace/name — "skills/data:sql-query",
                          # "agents/codesearch:code-search", "tools/Bash"
```

Entities are stored by namespace and name, not by id, so usage matches the entities of whichever configuration
is current — a skill reinstalled under a new id keeps its history. What a subagent used inside its own
transcript is not counted in v1; the delegation itself is.

In memory a `Turn` carries its session's `SessionHeader` (`09-adapters.md`, "One header per session"), its
position and time, the model that answered it, and what it used. The columns are written from that header and
turn; they are NOT NULL, so an unknown is written as the columns always held it (`''`, `"unknown"`) — nothing
reads them back. Counting reads only `at`, `interactive`, `sidechain` and `entities`: the store does not keep a
session's start, so a stored row is not read back as a `Turn` (decided 2026-10-04).

### Claude Code

The adapter (`adapters/claude_code/transcripts.py`, `usage_turns`) reads each main-session transcript,
`~/.claude/projects/<slug>/<session>.jsonl`:

- A turn starts at a genuine prompt — where the line records its origin, `{"kind": "human"}` — including one typed
  while the agent worked and delivered mid-turn, which the harness writes only as a `queued_command` attachment
  (`commandMode: "prompt"`, VERIFIED — `docs/02-design.md`; `usage_reader_version` `claude-code/3`), or at a slash
  command the user typed: a user line that *begins* with command markup and isn't `isMeta` — or, as 2.1.266 to
  2.1.280 sometimes wrote one, with no user line, a `system`/`local_command` line whose `content` begins with it
  (VERIFIED — `docs/02-design.md`; `usage_reader_version` `claude-code/4`). A command's output line (`commandRun`) is the
  same use as the command line just before it; with no such line it is a command, and a turn, of its own. A
  command run locally (a `system` line) never reaches the model, so its turn holds the command alone: what the
  agent does next stays with the prompt it was answering (VERIFIED 2026-10-04: a `/effort` here was typed while
  the agent was mid-reply). Before this, `/effort` and `/permissions` went uncounted and a `/resume` was stored on
  the turn 68 minutes before it. What the harness writes
  in the user's role starts none: interrupt markers, `!` shell lines and their output, background-task
  notifications, the summary carried over at compaction (VERIFIED — `docs/02-design.md`). Before this, 55 of 320
  "turns" here were such lines.
- Within it: every `tool_use` is `tools/<name>`; a `Skill` call is also `skills/<skill>`; an `Agent` (or older
  `Task`) call is also `agents/<subagent_type>` — `general-purpose` when it names none, and nothing for a fork of
  the caller; a typed command is `skills/<name>`, and so is a built-in command run locally (a `local_command`
  line). Built-in commands such as `/clear`, matching nothing in the configuration, are listed apart as "used, but
  not in this configuration".
- `isMeta` command markup is not counted: a skill the model invokes is already counted from its `Skill` call, and
  one the harness attaches on its own isn't your use — VERIFIED 2026-10-03: all 6 `workflow-authoring` lines here
  were `isMeta`, written right after injected hook context, none chosen by the user or the model.
- **Names only, in their well-formed shape** (letters, digits, `_ : . @ -`): a name is never taken from tool output
  or injected text, and anything else in command markup — a password pasted into it, say — is dropped, never
  stored.
- `interactive` is false for runs a program made: `entrypoint: sdk-cli` (`-p`; VERIFIED), and the Agent SDKs and
  the GitHub action (`sdk-*`, `…action…`; UNVERIFIED names). The adapter's one `headless()` rule decides this
  for a turn, for the session list and for the session assemble reads — one `SessionHeader.interactive`, built by one helper,
  and a test holds the three to agreement for every known entrypoint. Subagents are written to their own files
  beside the session (`<session>/subagents/`), which v1 does not read, so `sidechain` is always false here; it
  exists for harnesses that interleave subagent turns (both VERIFIED — `docs/02-design.md`).
- The harness's own `<synthetic>` messages name no model and are skipped.
- A transcript that vanishes between listing and reading (the harness's cleanup) is skipped and tried next time;
  a vanished transcript never fails a `check`.
- **One odd record never stops a reading.** A line that isn't JSON (a live session's torn last line) is skipped;
  so is a record whose envelope has a shape no harness version writes (`type`, `subtype`, `timestamp`, `cwd`,
  `version`, `entrypoint`, `sessionId` not strings; `message`, `attachment`, `commandRun` not objects; a message's
  `model`, `content` or `usage` of the wrong kind), and one a reader can't read deeper in (a text block whose text
  isn't a string, a tool named by a number) is skipped by that reader — the rest of the transcript is read. This
  holds for the archive's copy too: before, one such line failed every reading command, and kept failing after
  the transcript was deleted, until `forget`. VERIFIED 2026-10-04: of this machine's 539,501 transcript records
  the envelope check drops none.
- A forked session (`--fork-session`) may copy earlier turns into a new transcript, which would count them twice —
  UNVERIFIED: none of the 18 transcripts here share a record.

## Derived signal

### Store counts, never a rate

```python
@dataclass(frozen=True)
class EntityUsage:
    reference: str              # namespace/name, as recorded
    invocations: int            # turns in the window in which this entity was used
    last_seen: datetime         # its latest such turn
```

The denominator is the index's `turns_observed`, once, rather than a copy on every entity.

A stored fraction is lossy in four ways, each of which bites:

- **It discards the denominator.** `1.0` from one observation and `1.0` from five hundred are not
  the same claim, but a float can't tell them apart — and the ranker would hand them the same
  budget.
- **It can't be recomputed over a different window.** "Last 7 days" versus "all time" needs the
  raw counts.
- **It can't be merged.** Averaging fractions without weights is wrong, and any future
  aggregation (across time buckets, across machines) needs the counts.
- **It has no recency.** `last_seen` is what makes "you last used this two months ago" possible, and
  a rate carries none.

The counts are already in the log; a reader that wants a rate divides by `turns_observed`.

```python
@dataclass(frozen=True)
class UsageIndex:
    entities: dict[str, EntityUsage]     # by namespace/name
    turns_observed: int                  # your turns in the window: interactive, not a subagent's
    window_start: datetime | None
    history_start: datetime | None       # the oldest turn kept — how far back "never used" can see

    def for_entity(self, entity: Entity) -> EntityUsage | None: ...
    def is_empty(self) -> bool: ...
```

`UsageIndex` is built from the database (`store/usage.py`, `index`) and looked up by the ranker. It is **not** a
field on `Source` — see `01-config-reader.md` for why.

### Observed, never estimated

**Entity usage is observed.** A skill fired, a tool was called. The trace says so. `EntityUsage`
counts are honest facts, and they are the only usage signal agentlint uses: the ranker skips a
source that provides entities none of your kept turns used (`03-ranker.md`).

A passive instruction — a markdown section — has no firing event: you cannot see whether a
sentence was attended to. agentlint does not guess its relevance. (An `estimated_relevance()`
from entity co-occurrence was built and never called; it was removed 2026-10-04 rather than kept
as a promise the ranker doesn't keep.) Matching a free-text condition against a turn properly
would need an LLM, which would violate "AI never evaluates".

## Archive

`store/archive.py`: `archive_files` holds each transcript's `key` (the adapter's `TranscriptFile.handle`, opaque to
core), `harness`, `session_id` (a subagent's is the
session it ran in), `subagent`, and, named for what they hold: `bytes_read` — how far it was read, the file's length
when last read rather than its size now; `mtime_epoch_s` — the file's modification time then, in Unix seconds;
`forgotten_bytes` — how many leading bytes were forgotten or outlived `keep_days`, so what is kept is `bytes_read -
forgotten_bytes`; `tail_sha256` — a SHA-256 of the last 64 bytes read, never the bytes, so a file that no longer
ends with them was rewritten; and `derived` — whether usage was derived since its kept bytes last changed.
`mtime_epoch_s` is the one time the store keeps as a number rather than ISO text (`store/_time.py`): it is the
file's own stat time, compared with the next stat for equality, which a microsecond text round trip would break.
`archive_chunks` holds the bytes, zlib level 3, in order — a long session's many small appends are joined into one
chunk past 64. A transcript the archive lists back (`archive.sessions`, `archive.subagents`) is an `Archived`:
`TranscriptFile`'s fields in its order, keyword-only, with `bytes_read` where `TranscriptFile` has `size` — not a
`TranscriptFile`, because the two numbers differ once the file has grown. Usage is derived from the archive
(`store/usage.py` `derive`): transcripts not derived since their kept bytes changed are read again — so an
interrupted import is finished by the next — and **every** transcript kept when the adapter's `usage_reader_version`
changes, so a parsing fix reaches the whole history. Each is written in its own transaction, which checks the
transcript and the forget cutoff are still as they were when it was read, so a concurrent import or forget is
never undone. Schema 7 added the derived flag; upgrading derives every kept transcript once. Schema 10 (2026-10-04)
renamed `size`, `mtime`, `start` and `tail` to `bytes_read`, `mtime_epoch_s`, `forgotten_bytes` and `tail_sha256`;
every row and every byte is kept.

## Sync

Every command that reads imports first: it archives what changed, derives usage, and prunes past `keep_days`.
`agentlint ui` is the exception for pruning: it prunes once as it starts, never on a page load or `r`, so a
long-open UI deletes nothing on a request, and never with a `keep_days` you have since changed (it reads the
settings again on every request).
`agentlint sync` does just that and says what the archive holds. `agentlint sync --daily` installs a job in your
system's scheduler — a LaunchAgent (`~/Library/LaunchAgents/dev.agentlint.sync.plist`) on macOS, a marked crontab
line elsewhere — that runs `agentlint sync --quiet` each day at 13:07 with the same Python and store (named in the
job by absolute path, since launchd starts a job in `/` and cron in your home); `--off` removes it. Where there is
no `crontab` at all, `agentlint sync` reports the daily sync as off. Outside the harness, so non-negotiable 4 stands (`AGENTS.md`).
Each `agentlint sync` (the job's too) records when it ran and what it came to in the database (`state.last_sync`:
transcripts archived and turns derived, or the error), which Settings shows beside the daily-sync switch; that switch
installs and removes the same job (2026-10-04). A sync turned away before it starts — a setting that can't be used, a
harnesses setting naming none agentlint reads, a config.toml that can't be read, no harness installed — records its
error too (`main()`'s `refuse`, and the adapter is made inside `cmd_sync`'s `try`), so a daily job that keeps failing
never leaves the last good sync showing. Only a database that can't be opened records nothing: there is nowhere to.

## CLI

```
agentlint usage                  # entities ranked by the turns that used them (last 30 days)
agentlint usage --days N         # another window (1-36500, the usage_days setting's range)
agentlint usage --unused         # entities in your config you never touch
agentlint sync [--daily | --off] # archive and derive now; keep doing it daily, or stop
agentlint forget [--yes]         # delete all kept from transcripts: archive, usage, readings, findings, test runs (asks unless --yes)
```

Usage is read on every `check`, `ui`, `usage`, `rank` and `sync`, and `show` with your sessions setting;
there is nothing to enable. Only what the agent chooses between is listed — entities whose kind has the role
`skill` or `agent` (`usage.chosen`), decided by role, never by a harness's namespace names; tools are recorded,
but every session has them. Usage outside those kinds' namespaces is not called unmatched, and a namespace no
entity in the configuration has is not known to be chosen, so nothing in it is reported. The UI's **Skills and
agents** view shows the same counts for every entity (the report carries counts and last-use dates only), and the
report's `usage.summary` is the summary `agentlint usage` prints, computed once in Python: used (entity id and
reference, most used first), never used, and unmatched references with their counts. The UI reads it and never
restates the rule: `chosenOf` in `ui/src/model.ts` takes the chosen skills and agents from `usage.summary`, and no
namespace rule is written in `ui/src`. An adapter builds every
reference with `usage.reference_to(kind, name)`; the Claude Code adapter's `uses()` is the one rule for what a
tool call used, shared by usage and the transcript reader.

`agentlint usage --unused` is worth calling out — it's immediately useful on its own and gives the
recorder a reason to exist before any testing happens. "You have 412 skills; you have used 23 of
them in the last month."

## Status

Built 2026-10-03, archive included. On the reference machine, a fresh store: `agentlint sync` archives 1,511
transcripts (676 MB as written — main sessions and subagents) into 179 MB in 6.9 s; the next run, with nothing
new, takes 0.2 s. Usage after the sixth round's fixes: 271 of your turns in 30 days; 2 of 618 skills and agents
used. A copied transcript deleted from a scratch home was still listed by `agentlint ui` (marked as kept only by
agentlint) and read back from the archive: 530 entries.
