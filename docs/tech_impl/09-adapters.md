# 09 — Adapters

The harness-specific boundary. Everything else in the codebase is harness-agnostic.

Write this interface on day one, even with one implementation. It is cheap to maintain from the
start and nearly impossible to retrofit.

## The interface

An adapter is three parts (decided 2026-10-04, design review 9.3), which a second harness implements separately and
core reads separately — `Adapter` is all three, the one type `cli.ADAPTERS` registers, and core takes the narrowest
part it uses (`read_config` a `ConfigReader`):

| Part | What it is | Optional? |
|---|---|---|
| `ConfigReader` | the configuration on disk, with what recent sessions show was delivered; the harness's own checks | no — every adapter is one; with no session history `read` returns no sessions, and `checks()` may be `[]` |
| `TranscriptArchive` | the transcripts the archive keeps and usage is derived from (08) | yes — `transcripts()` returns `[]`, and the rest is never asked |
| `ConversationReader` | the live UI's transcript reader (11): sessions listed, read back, and their subagents | yes — it lists `[]` and finds `None`; a harness without subagents writes no `subagent_ref`, so `is_subagent_ref` is false and the other subagent methods are never called |

```python
class ConfigReader(Protocol):                 # src/agentlint/harness.py
    name: str                                 # lowercase-kebab harness id
    label: str                                # how the user knows it: the UI's scope line (11)

    def installed(self) -> bool:
        """Whether this harness is on this machine, read from disk (never by running it). `agentlint ui` lists only
        installed harnesses (11)."""

    def version(self) -> str | None:
        """The installed harness's version, read from disk; None when it can't be told. Listed by `/api/scope`."""

    def read(self, cwd: Path, sessions: int, since: datetime | None = None) -> AdapterRead:
        """Everything configured in cwd, from disk as it is now, with what the `sessions` most recent sessions
        show the harness delivered (none when 0) — of those that started at or after `since`, the forget cutoff,
        in the sessions and the evidence alike. No AI, no runs, nothing kept for the next read."""

    def checks(self) -> list[Check]:
        """Checks that need this harness's knowledge; they emit the same Finding type and judge a Reading (02),
        finding what they need beyond the snapshot in its `evidence`. Run beside the core checks by `check`,
        `rank` and the live UI."""

    def package_label(self, package_id: str) -> str:
        """How the user knows a package — a `Provenance.package` id — from what the harness's ids mean; the id
        itself when the adapter can't tell (Claude Code: the name before "@" in "<name>@<marketplace>"). The
        report carries one per package present (`package_labels`, 10); core never takes an id apart."""

class TranscriptArchive(Protocol):
    usage_reader_version: str                 # changes when usage is read differently: everything archived is read again

    def transcripts(self) -> list[TranscriptFile]:
        """Every transcript the harness keeps — sessions and their subagents — with size and mtime (08)."""

    def read_transcript(self, handle: str, offset: int = 0, length: int | None = None) -> bytes:
        """A transcript's bytes, from offset: the archive reads only what grew."""

    def turns_from(self, handle: str, data: bytes) -> list[Turn]:
        """The turns a session transcript holds and the entities each used; never what was typed (08). This and
        the two below skip a record they can't read and read the rest: one odd line never fails a reading."""

class ConversationReader(Protocol):
    def list_conversations(self) -> list[SessionListing]:
        """Every session transcript, without its contents — for the live UI's transcript reader (11)."""

    def read_conversation(self, session_id: str) -> Conversation | None:
        """One session, readable; found by id among those listed, never by a path from outside."""

    def conversation_from(self, handle: str, data: bytes, *, parts: bool = True) -> Conversation:
        """A session readable, from the harness's file or the archive's copy alike (11); `parts=False`, the same
        items with each briefing's parts left out, for a listing that only counts them and reads the first prompt."""

    def prepare(self, project: Path) -> None:
        """Start, off the request path, what reading this project's transcripts will need, and return at once —
        called as the UI lists a project's sessions (Claude Code: the discovery briefings are traced against)."""

    def listing_from(self, handle: str, data: bytes) -> SessionListing | None:
        """The same session's listing entry, without its contents (11); None when no line of it is dated."""

    def subagent_id(self, handle: str) -> str:
        """The id the transcript reader shows and asks for a subagent transcript by (Claude Code: `agent-<id>`, its
        file name) (11)."""

    def is_subagent_ref(self, ref: str) -> bool:
        """Whether a request's `ref` is a subagent reference this adapter writes — a `ToolCall.subagent_ref` handed
        back by the UI; the server answers 400 when it isn't (11)."""

    def belongs(self, ref: str, handle: str, read: Callable[[], bytes]) -> bool:
        """Whether a subagent transcript is one an item's `subagent_ref` points to; `read` gives its bytes, read
        only when the handle alone doesn't settle it (Claude Code: a resumed Workflow run, told apart by when each
        agent started) (11)."""

class Adapter(ConfigReader, TranscriptArchive, ConversationReader, Protocol): ...

@dataclass(frozen=True)
class AdapterRead:                            # one pass over the harness's disk and transcripts
    declared: Declared
    sessions: tuple[SessionDeliveries, ...] = ()
    evidence: object = None                   # for the adapter's own checks; core never looks inside

def read_config(adapter, cwd, *, sessions: int, forgotten_before: datetime | None = None) -> Reading:
    read = adapter.read(cwd, sessions, forgotten_before)  # sessions: the setting, no default here
    kept = [s for s in read.sessions if forgotten_before is None or s.header.started_at >= forgotten_before]
    snapshot = assemble(read.declared, kept if sessions else ())
    return Reading(snapshot, file_facts(snapshot), read.evidence, forgotten_before if sessions else None)
```

**An adapter implements a configuration reader with its own checks, and — where its harness keeps them — a
transcript archive source and a conversation reader; core owns the merge.** `assemble()` (`config/assemble.py`)
decides which session each observation may come from, what counts as untraced, how the always-on
total is reconciled, and how the fingerprint is computed — once, for every harness. A harness with
no session history returns no sessions: everything only a session could show stays `NotObserved`,
and the total is `NotReconciled`. That is still a legitimate, useful adapter.

**Each reading is self-contained** (decided 2026-10-03): `read` discovers the disk afresh every
time and keeps nothing a later reading uses (Claude Code hands its discovery to the transcript reader, which no
reading consults: "What a transcript records of what the model was given"), so an adapter held for a long time (the open UI keeps one) sees an
edit at the next reading; and whatever its checks need beyond the snapshot — Claude Code's: the configured
hooks, the installed packages, the sessions read, the hook runs they recorded, and which
`${CLAUDE_PLUGIN_ROOT}` paths are missing — comes back from that same read as `evidence`, which `read_config`
passes on in the `Reading` (`config/reading.py`). Nothing is left on the adapter for a check to find later: a
reading asked for no sessions has none in its evidence, whatever an earlier reading read.

`Declared` is what disk says: the harness, cwd, harness version and model if disk records them,
sources (`FileText` for files; `NotObserved` placeholders for text only sessions show), and
entities. It validates itself: unique source ids, unique entity ids (so an id must carry whatever tells two
same-named entities apart — Claude Code's carries the scope, `01-config-reader.md` "Entity"), every source from
this harness, every entity's
`provided_by` resolving, and no `ObservedText` (that only comes from sessions). A `SessionDeliveries` is a
`SessionHeader` (below), the `Delivery` records it contains
(text, the source id it traces to or None, its injection point and load), its observed
always-on total, and any entities only a session can show (tools, built-ins).

The transcript methods feed the archive and the recorder (`08-recorder.md`): core decides what to read again,
keeps the bytes, derives and counts the `Turn`s. A harness without transcripts returns none.

**One header per session** (decided 2026-10-04): `SessionHeader` (`config/sessions.py`) — session id, project
(`Path | None`), harness version and model (`str | None`: the version it started on, the model that answered
first), an aware `started_at`, and whether it was interactive — is what a reading's `SessionDeliveries`, the
transcript reader's `SessionListing` (header, title, end, prompts, size) and each usage `Turn` (header, position,
time, the model that answered it, sidechain, entities) carry. None where the transcript doesn't say, never `''` or
`"unknown"`. The adapter builds it in one place, which owns the interactive rule (Claude Code: `HeaderReader` in
`transcripts.py`, fed the lines in order, with `headless()`). Before, `SessionRef`, `SessionInfo` and `Turn` each
carried these fields, built by three readers with three fill rules (`''` and `"unknown"` in two, None in the
third; the last version and model seen in one, the first in another). A transcript with no dated line has no
listing and no turns; a reading's first turn of one takes the file's modification time as its start, as before,
and is left out when a forget cutoff applies (`FirstTurn.dated`). The renames: `SessionRef` → `SessionHeader`,
`SessionInfo` → `SessionListing`, `Session` → `SessionDeliveries`, Claude Code's `RawSession` → `FirstTurn`,
`list_sessions` / `read_session` / `info_from` → `list_conversations` / `read_conversation` / `listing_from`. A
`Conversation` holds only its title beside its entries: what the session was is its listing's header.

**Two strings are the adapter's alone** (decided 2026-10-04): a transcript's `TranscriptFile.handle` (Claude Code:
its path) and a tool call's `ToolCall.subagent_ref` (Claude Code: `agent:<id>`, `run:<id>`, or for a resumed run
`run:<id>@<from-ms>-<until-ms>`). Core stores them, compares them and hands them back — `subagent_id(handle)` for
the id the reader shows, `is_subagent_ref(ref)` to check one a request carries, `belongs(ref, handle, read)` to
match the two — and never takes either apart: the UI server once validated references with a copy of the
adapter's grammar, and `cli.py` derived subagent ids with `Path(key).stem`.

**Where each source is, the adapter says in two fields** (decided 2026-10-04, design review 6.1): a source's
`Provenance.file` (a `Path`, or None for text no file holds) and `Provenance.anchor` (where inside that file, or None
when the file is the source's text). Claude Code fills them so: a settings layer, an instruction file, a skill, an
agent or a command is its file, with no anchor; a hook is the settings or manifest file that declares it, anchored at
the JSON pointer to its entry; managed instructions embedded in the managed settings file are that file, anchored at
`claudeMd` (`instructions.EMBEDDED_KEY`); the system prompt, the tool definitions, the session context and the
listings — a package's share of one included, whose `package` says whose — are in no file. The anchor's grammar is
the adapter's: core never reads it, only shows it after the file. An `Instructions` entry's `display_path` (it was
`locator`) is the path as the user sees it — `<managed settings>#claudeMd` for embedded text — and keys the entry's
source id, `claude-code:instructions:<display_path>`, unchanged, so the ids findings are built from did not move.

Behavioural testing adds parts in v2, when the runner needs them. Three are built (2026-10-04):

```python
class TraceReader(Protocol):           # harness.py
    def trace(self, session_id: str) -> Trace: ...           # one run's trace, from the home the adapter was given
    def action_terms(self, action: Action) -> ActionTerms: ...  # what one action did, in core's terms (2026-10-06)
class VariantHomes(Protocol):          # harness.py
    def home_dir(self) -> Path: ...                          # the home this adapter reads, which a variant copies
    def acting_tools(self, reading: Reading) -> tuple[str, ...]: ...  # D6: tools no test run is offered
    def version(self) -> str | None: ...                     # D5: the version a new job pins (ConfigReader's)
    def copy_plan(self) -> CopyPlan: ...                     # what of it a variant copies: configuration, not history
    def at_home(self, home: Path) -> ConfigReader: ...       # a reader of the same machine with that home
    def removal(self, source: Source) -> tuple[Path, ...]: ...  # what to delete for a source removed whole
    def placements(self, project: Path, home: Path, workdir: Path) -> tuple[Placement, ...]: ...  # D1: beside it
    def entry_cut(self, source: Source) -> EntryCut | None: ...  # D3: a package's hook: the loaded copy first, then the rest
    def unplaced(self, project: Path, workdir: Path) -> tuple[str, ...]: ...  # D4: project code the placed config runs
    def telemetry(self) -> str: ...                          # D18: the onboarding's telemetry line, the adapter's words
class Launcher(Protocol):              # runner/execute.py
    def launch(self, launch: Launch) -> Launched: ...       # run one situation in a home and directory, read its trace
```

`trace` is its own part (`harness.TraceReader`): an adapter given the run's home (`Paths(home=<variant>)` for Claude
Code) reads the run's trace by session id, and raises `TraceMissing` — never returns an empty trace — when the harness
kept no transcript or the one it kept can't be read (an `OSError`, chained), or `TraceEmpty` (a `TraceMissing`) when
the one it kept recorded nothing readable (06-trace.md).

`action_terms` says what one action of a trace did, in core's words (`trace/terms.py`): its kind — shell, file, search,
skill, agent or other — its tool's name, and what it acted on. It is what the with/without comparison compares
(07-analysis.md, "Effect of a rule"), so core never names a tool. For Claude Code (`adapters/claude_code/terms.py`):
`Bash` is shell, its targets the programs its command runs; `Read`, `Edit`, `Write`, `MultiEdit` and the notebook tools
are file, the path; `Grep`, `Glob` and `LS` are search, the path searched (`.` when none); `Skill` (and the older
`SlashCommand`) is skill, its name; `Agent` (and the older `Task`) is agent, the subagent it runs (`general-purpose`
when it names none, `fork` for the caller's own fork); everything else, MCP tools among them, is other, the tool alone.
**A shell command is the programs it runs** (decided with the user 2026-10-05; `adapters/claude_code/shell.py`
`programs`): a small reader of the shell's grammar, not a shell — it splits on `;`, `&`, `&&`, `||`, `|`, `|&`,
newlines and a subshell's parentheses, and takes each command's first word, skipping `NAME=value` assignments,
redirections (`2>/dev/null`, `2>&1`, `&>x`, a heredoc and its body) and the shell's keywords (`for`, `do`, `if`, `!`,
`time`, `{` …); a path becomes its last part; the programs inside `$(…)` and backticks count where they appear; a `for
x in …` header, arithmetic `$((…))` and comments run nothing; `case` arms are skipped whole; a command it can't read
(an unclosed quote or parenthesis) is its first word. The first real job's commands read as expected:
`ls; grep … 2>/dev/null | grep … | head -40` is ls, grep, grep, head; `for x in a b; do acme … ; done` is acme.

Core builds the variant from these (`runner/variant.py`: `build_variant`, `build_home`, `cut`) and verifies it
(`runner/verify.py`), so the copying, the write guard and the excision are written once; the adapter says only what
is the harness's to know — which files are configuration, how an entity is laid out on disk, what of a project a run
in an empty directory gets and where (`placements`: a source directory, a `CopyPlan` and a destination each), every
copy of a package's file, the one the harness loads first (for Claude Code the marketplace checkout under the variant
home, E2b, derived as its reader of a copied home finds that checkout — `in_copy` applied to the marketplace location
`known_marketplaces.json` records, not to the reading's resolved file — and refused, `TargetNotSupported`, for a
location `in_copy` doesn't map, or a package directory or hooks file the package's own manifests name by absolute
path, each of which a variant reads in the real home), and how to find one
entry in it (`entry_cut`), what of the project the placed configuration
would run that wasn't placed (`unplaced`: for Claude Code, a file of the project's code named by a hook in the placed
project settings or by a server in the placed `.mcp.json` or project settings' `mcpServers` — any word of the command
or arguments that, resolved from the project's directory, is an existing file of the project the run's directory
doesn't hold, a bare `server.py` included, with a hook's command split as a shell splits it (punctuation apart) and a
`sh`/`bash`/`zsh -c` string split again; a word naming no project file, such as `npx pkg` or a flag, is not — one
sentence each, which fails the baseline check), and how to read a home. Claude Code's are in
`adapters/claude_code/variants.py` (`05-runner.md`, "Building a variant home", "Cutting the target out").

The probe generator is the core `probes.generator.Generator` protocol, not a part of `Adapter`: the composition root
builds the harness's generator and hands it to `probes.generate.generate`. Claude Code's is
`adapters/claude_code/generator.py` (`ClaudeCodeGenerator`, `MODEL = "haiku"`, 2026-10-04): the headless call E5
settled (D14), in an empty directory under `AGENTLINT_HOME` (`04-probe-generator.md`, "The real generator").

**Running** (built 2026-10-04) is a `Launcher`, not an adapter method: core's `VariantExecutor` (`runner/execute.py`)
builds each run's home and directory, verifies and deletes, and hands the launcher one `Launch` — the situation, the
run's home and working directory, the model, the time limit, where to record the running harness, and the harness
version the job pins (D5: recorded from the adapter's `version()` when the job is created; the launcher must run
exactly that version on every launch, resumes included).
The launcher starts the harness in a process group of its own and records it at once (`record_running(launch.record,
pid)`, so a killed runner's successor can stop it), runs it, reads the
trace (through its own `read_trace`), and says how the run ended: a `Launched` outcome, or `engine.Retry` for an
infrastructure failure, or an `engine.Paused` for one only you can fix. It is separate from the trace reader because
trace retrieval varies (Claude Code needs a transcript round-trip; another harness might return the trace inline), and
from variants because isolation mechanics are the harness's even where HOME substitution is the common default.

Claude Code's launcher (`adapters/claude_code/launch.py`, `ClaudeCodeLauncher`) runs
`claude -p <situation> --session-id <uuid> --output-format json --model <model> [--disallowedTools <tool>…]` with
`HOME` the variant home and the run's directory as its working directory (and `PWD`; a `CLAUDE_CONFIG_DIR` in the
environment is dropped, since it would point the harness elsewhere), and `CLAUDE_CODE_VERSION_OVERRIDE` set to the
job's pinned version (`VERSION_ENV`; one already in agentlint's own environment is replaced, never inherited). The
launcher runs, and downloads when it lacks it, the version that variable asks for — VERIFIED 2026-10-04 (E7: asked
for 2.1.284 where the default was 2.1.285; the launcher printed "Downloading native claude_code 2.1.284 from
Manifold…" and the transcript recorded `version` 2.1.284).

**No permission mode is overridden, and the tools that act outside the machine are removed (D6 final, 2026-10-04).**
A run follows the org's guardrails as your own sessions do: the guardrails' PreToolUse hook answers "allow" for
in-project writes and shell commands, and a hook's allow beats any permission mode (E8b), so a strict mode couldn't
be enforced. What can be enforced is removing whole tools (E8c). The job's frozen list of removed tools
(`Launch.removed_tools`) is passed as `--disallowedTools` with whole tool names, the flag last so its variadic list
takes nothing else; with no tools, no flag. The adapter chooses the list (`ClaudeCodeAdapter.acting_tools(reading)`,
`adapters/claude_code/acting.py`) from the MCP tools the configuration is known to offer — the `mcp__…` names recent
sessions' deferred-tool listings and tool definitions recorded, on any harness version (for example
`mcp__plugin_calendar_calendar-tools__calendar_book_beta`; read from the sessions themselves, `Evidence.sessions`, not
the snapshot's entities, which keep a session's only on the installed version, so a launcher update would leave none) — by each tool's own name (after `mcp__<server>__`), split
into words (on `_`, `-`, camelCase):

- a verb that writes or acts removes it: `ACTING` — book, send, post, publish, upsert, delete, create, update, cancel,
  decline, accept, add, write, edit, submit, approve, merge, push, kill, revert, toggle, enable, disable, start, stop,
  abort, schedule, renew, resolve, and more of the kind (remove, set, save, upload, execute, deploy, comment, invite,
  share, move, archive, trigger, restart, …), each with its simple inflections (`booked`, `scheduling`, `pushes`);
  verbs as often nouns — run, log, review, change, store, record, drain, flush — count as acting too: a read word is as
  often a noun (`change_status`, `store_info`, `run_query`, `drain_load`), and must not keep a tool whose verb acts,
  so a read tool named for one (`get_review_queue`, `workflow_run_load`) is removed, which costs little; file, page
  and link are left out (rarely a tool's verb, and such a name is removed anyway unless a read verb is in it). A verb
  in neither list followed by a read noun (`bump_status`) is still kept — the residual gap;
- otherwise a read verb keeps it: `READING` — get, list, search, read, load, info, status, query, fetch, show, find,
  describe, lookup, view (whole words only; an acting word wins over them: `get_and_delete` is removed);
- otherwise it can't be told, and is removed (leaning toward removing: a read tool wrongly removed only makes both
  variants a little less like your sessions; an acting tool wrongly kept could act for real).

`tool_verdicts(reading)` gives every known MCP tool's verdict and why (`ToolVerdict(name, removed, why)`) for the
onboarding screen and a Settings override (the UI comes later); `runner.execute.acting_tools(db, adapter, project)`
is the list a new job would take, and `run_test(…, removed_tools=[…])` overrides it. Built-in tools are not classified.
An MCP server no recent session showed is not known, so its tools are not removed; a session of any harness version
counts (05-runner.md, "The executor").

`telemetry()` is the onboarding's telemetry line (D18): a test run is a real Claude Code session, so Claude Code's own
telemetry may see the situations, as it sees your sessions, and agentlint sends nothing. Where the launcher's
preset is on disk (`~/.claude/acme/preset`, which the launcher rewrites on every launch) the line names the launcher's
session streaming and its OpenTelemetry logging, as D18 says it should; elsewhere it names neither, since the adapter
can't tell that they run. Core shows the sentence and never names a harness.
Its constants:

| Constant | Value | Why |
|---|---|---|
| `PAUSED` | `authentication_failed`, `billing_error`, `verification_required`, `account_on_hold`, `oauth_org_not_allowed` | D7, D19: pause at once with a clear message (`AuthFailed`). Codes the 2.1.285 binary's error classifier writes (read 2026-10-04); none yet seen in a transcript |
| `PAUSED_REASONS` | `gateway_signin_required`, `gateway_session_expired`, `provider_credentials`, `invalid_credential_header`, `org_disabled_credential`, `api_key_auth_disabled` | the record's `apiError`: pause whatever the code. A gateway sign-in required or expired is written as `unknown`, a credential header rejected as `invalid_request` — so the code alone can't say it is auth |
| `GATEWAY_AUTH` | "The gateway could not authenticate with its upstream provider" | when the provider is the gateway, the binary writes the upstream authentication failure as `invalid_request` with this text and no reason. This machine uses "AI Gateway (Vertex upstream)" (E5), so this is its likeliest auth failure |
| `UNSERVED` | `model_not_found` | the job's model isn't served: pause (`HarnessFailed`), retrying can't fix it |
| `OUTCOME` | `invalid_request` | a refusal of the request itself (2 records here, a safeguard refusal): an outcome, unless a reason or the gateway text marks it as auth |
| `GRACE_S` | 10 | after the time limit, SIGTERM to the run's process group, SIGKILL this long after |

An API error is the transcript's line marked `isApiErrorMessage` (`trace.api_errors`: its `error` code, `apiError`
reason, `apiErrorIsTransient` mark and text). Every API error not paused and not `OUTCOME` is retried — `server_error`,
`rate_limit`, `cloud_credential_error` (the binary marks it transient), and any code not recognised, `unknown` and an
unreadable one included (D7: API errors are never outcomes; precision over recall) — at most 3 times, then the job
pauses (`RetriesExhausted`). Local transcripts hold only `server_error` (46) and `invalid_request` (2), neither with a
reason: whether reasons reach a transcript is UNVERIFIED, which is why the gateway's text is matched as well. A call the JSON result's `permission_denials` names by
`tool_use_id` is marked denied in the stored trace (the entries' shape is UNVERIFIED: none seen yet). A run that wrote
no transcript and wasn't timed out raises `HarnessFailed` (a `Paused`). `launch.run_test(db, project=…, model=…, …)`
is the Python entry point with this machine's adapter and launcher; no command calls it yet. The prompt is passed as
the argument after `-p`, as E5 did: a situation beginning with `-` would be read as a flag (UNVERIFIED how the CLI
treats it), so the generator's situations must not start with one.

## Vocabulary discipline

**Core never imports an adapter and never names a harness.** Harness-specific things — a particular
instruction file, a listing, a transcript format — live in that harness's adapter as its own enums
of `InjectionPoint` and `EntityKind` value objects. Core reasons only about the generic categories
those values carry (`Mechanism`, `EntityRole`, `Load`, `Authority`). Shared concepts — hook, plugin,
skill, MCP — are fine in core.

`tests/test_boundaries.py` enforces both rules structurally: it parses every core module's imports
(resolving relative ones), and scans core files for harness names — and the UI's own code (`ui/src`, `.ts` and
`.tsx`, tests aside), whose comments the build strips before the shell is scanned. `cli.py`, the composition root
that wires an adapter in, is the one exception. This replaced a word ban (2026-10-02), which forbade
shared concepts and so forced worse names while missing the real hazard.

## Claude Code adapter

Status: **primary, VERIFIED 2026-10-01.** See `01-config-reader.md` for enumeration details and
`05-runner.md` for isolation.

Summary of what's confirmed:

| Capability | Mechanism | Status |
|---|---|---|
| Headless run | `claude -p "..." --output-format json` | VERIFIED |
| Pre-assigned session id | `--session-id <uuid>` | VERIFIED 2026-10-04 (E1) |
| A fresh variant home authenticates | managed AI Gateway, nothing copied but configuration | VERIFIED 2026-10-04 (E1) |
| A package's hook removed in the copy | edit both copies of its `plugin.json` | VERIFIED 2026-10-04 (E2) |
| The copy of a package that loads under a substituted HOME | its marketplace checkout under that home, not the plugin cache | VERIFIED 2026-10-04 (E2b) |
| A harness version pinned per run | `CLAUDE_CODE_VERSION_OVERRIDE=<version>` (the launcher; downloads it if missing) | VERIFIED 2026-10-04 (E7) |
| A permission mode headless | `--permission-mode default` / `dontAsk` don't deny: the org's guardrails hook answers "allow", which beats any mode | VERIFIED 2026-10-04 (E8, E8b); no mode is overridden (D6 final) |
| A whole tool removed from what the model is offered | `--disallowedTools <Tool>…` (or `permissions.deny`), whatever a hook answers | VERIFIED 2026-10-04 for built-in tools (E8c); 2026-10-05 for an `mcp__…` name (E8d), subagents the run starts included (E8e) |
| Isolation | `HOME=<variant> claude ...` | VERIFIED |
| Trace | transcript `<session_id>.jsonl`, found by id under `<variant-home>/.claude/projects/` (`conversation.find`, no slug computed) | VERIFIED |
| Model override | `--model` | VERIFIED (flag exists) |
| Settings override | `--settings <file-or-json>` | VERIFIED (flag exists) |
| Tool restriction | `--allowedTools` / `--disallowedTools` | VERIFIED (flag exists) |
| Managed settings ablation | **not possible** | VERIFIED |
| Nested invocation | **fails** under macOS sandbox | VERIFIED |
| Generator call | `claude -p <prompt> --bare --model haiku --tools "" --no-session-persistence --output-format json --json-schema <schema>`: `structured_output`, `modelUsage`, no transcript | VERIFIED 2026-10-04 (E5, by hand); the code's call tested only against a fake `claude` |

### What a transcript records of what the model was given

Read once, in `transcripts.py`, for both the configuration reading (`parse_session` → `FirstTurn`) and the transcript
reader's briefings (`conversation.py` `briefings`, 11-ui.md "Sessions"); never parsed two ways. Version boundaries
and counts are in the Verified Facts table (`docs/02-design.md`, 2026-10-04 rows).

| Record | What it holds | Text shown |
|---|---|---|
| `prompt_snapshot` (2.1.280+) | `systemPrompt` (blocks), and in the second one of a (re)build `tools` and `cliPrefix` | the blocks joined; the tools as JSON (`snapshot_texts`); `cliPrefix` not kept |
| `instructions` | `files[]`: `path`, `type` (`Managed`, `User`, `Project`, `Local`, `AutoMem`), `content` | each file's `content` (`rendered` frames them all as one) |
| `skill_listing`, `agent_listing_delta`, `deferred_tools_delta` | names, and the listing; a turn may hold a later one of a kind (after a `/compact`, a `deferred_tools_delta` naming only the MCP servers that failed to connect) | `rendered` (2.1.280+), else `content` / `addedLines`; the configuration reading reads the first of each kind, a briefing joins on each later one that says something new (`LaterListing`; an exact re-send is not kept) |
| `environment`, `date`, `model`, `session_context`, `auto_mode`, `ultra_effort_enter` | the session's context | `rendered`, else from the attachment's fields |
| `file`, `compact_file_reference`, `invoked_skills` | what a compaction re-attaches (`CarriedOver`); `invoked_skills` has each skill's `name`, `path`, `content` | `rendered`, else the file's or skills' content; each skill's own `content` kept too (`pieces`), to split the part by whose each skill is |
| `hook_success` / `hook_additional_context` / `async_hook_response` with `hookEvent: SessionStart` | hook output the model got: `content` (a list, one piece per hook, for additional context); `rendered` (2.1.280+) frames it ("SessionStart:startup hook success: …", "SessionStart hook additional context: …") | in a briefing, `rendered`, cut into one share per piece so the shares join back into it whole (`rendered_shares`: each piece with the framing before it, the last with the framing after it), else `content`; the configuration reading and the hook tracing read `content` (`hooks.py`) |
| `system` / `compact_boundary` | where a compaction happened (`compactMetadata.trigger`) | — |

The rule the reader builds on: a briefing at the start (everything before the first reply, plus the first snapshot
with tools after it); one from every `compact_boundary` to the first reply after it, read the same way; one where a
later snapshot records a system prompt or tools unlike the last, outside those. An interrupted start keeps only what
the configuration reading counts delivered (`FirstTurn.interrupted_start`), and says so; a subagent's start is never
an interrupted one (it is asked in two user lines). Each part is traced as the configuration reading traces it
(`_listings`, `_instruction_target`, `trace_outputs`; the skills a compaction carried over are split by owner as the
skill listing is, `_carried_skill_shares`), against the project's configuration as discovered for the reader —
kept per project and refreshed off the request path (`ClaudeCodeAdapter._discover`): under `SCAN_SECONDS` (120 s) old
it serves as it is; older, under `SCAN_KEPT_SECONDS` (30 min), it serves while a fresh one is discovered in the
background; with none, a read waits for the discovery under way or runs it. A configuration reading (`read`) keeps
the discovery it just ran there for its project (`_keep`; stamped with when it began, and never over one that began
later), so in the UI, which reads a project's report before you open any of its sessions, an open traces against
that and waits for no discovery. `prepare(project)`, which the UI's catalog calls as it lists a project's sessions,
starts one before any is opened when none is kept. A reading never uses what is kept, its own or the reader's. A listing of a
workflow's subagents parses each with `parts=False`: the same items, briefings without their parts, so no discovery
and no counting. A briefing with no
recorded prompt blames the harness version when the model answered, or when the version is one known never to record
it (`LAST_UNRECORDING_VERSION`, 2.1.267, and older); otherwise, unanswered, it says the model never answered.

Optional fast path, not built: `claude plugin eval` provides ablation variants, repetition, cost
ceilings and a `tool_used` grader natively. Plugin-scoped only. Revisit once the core exists —
see `docs/02-design.md`.

## Adding a harness

What a new adapter must answer:

1. **Where does config live?** Files on disk, in what precedence order, with what authority tiers.
2. **What entities exist?** Tools, skills/commands, sub-agents — enumerable, with stable IDs.
3. **How do you run one turn headlessly?** CLI invocation, exit codes, output format.
4. **How do you isolate config?** HOME substitution is the default assumption; verify it, or find
   the equivalent.
5. **Where is the trace?** Inline in output, a log file, or a transcript.
6. **What can't be ablated?** Record the limits rather than failing silently.

If a harness can't answer 3, 4, and 5, only the static half of the tool works on it — which is
still worth shipping, and `read_config()` alone is a legitimate partial adapter.

To wire it in, add one entry to `cli.ADAPTERS` (name → a function making the adapter), in the composition root.
`agentlint ui` then lists it wherever `installed()` is true, in the `harnesses` setting's order when that names it
(else in registry order), and reads through it when it is chosen; `config set harnesses` accepts its name. The
other commands still read through the one `cli._adapter()` until they learn to take a harness.

## Candidate harnesses

| Harness | Config | Headless | Isolation | Status |
|---|---|---|---|---|
| **Claude Code** | `~/.claude/*`, `CLAUDE.md`, plugins | `-p` | HOME | VERIFIED |
| **Codex** | `AGENTS.md` hierarchy; CLI config location unknown | likely | likely HOME | UNVERIFIED |
| **Cursor** | `.cursor/rules/`, `.cursorrules` | unknown — may be GUI-only | unknown | UNVERIFIED |
| **Copilot** | repo + org instruction files | unknown | unknown | UNVERIFIED |

Nothing beyond Claude Code should be attempted until the core is stable. Adding a second adapter
early is the classic way to build the wrong abstraction — one real implementation plus a written
interface is the right amount of generality for now.

## Testing an adapter

A conformance suite every adapter must pass:

- it has every member each of the three parts declares (a `Protocol` isn't checked at runtime; built 2026-10-04 for
  Claude Code, `test_the_adapter_answers_every_part_of_the_interface`)
- `read()` returns a declaration with at least one source whose load is `ALWAYS`
- `read()` constructs its `Declared` without error (which already proves every `Entity.provided_by`
  resolves and source and entity ids are unique — `Declared` validates itself; `ConfigSnapshot` checks ids
  again once session entities merge in)
- the fingerprint is stable across two reads with no changes
- the fingerprint changes when one declared source's content changes, and does **not** change when
  only observed session text changes
- `read()` never executes the harness
- two `read()` calls on one adapter see an edit made between them, and a read asked for no sessions carries no
  sessions in its evidence
- `build_variant()` produces a config that reads differently from the baseline variant's (built for Claude Code's
  user-level targets, `tests/runner/test_variant_verify.py`; compared by path-mapped identity, since ids embed paths)
- `build_variant()` never writes outside `AGENTLINT_HOME` (`tests/runner/test_variant_home.py`)
- for every source the adapter claims is ablatable: `build_variant()` then `read_variant()` no longer contains
  it — the claim must be empirically true, per mechanism (user-level sections, settings keys, skills and agents so
  far, project targets (D1) and a package's hook (D3) too, `tests/runner/test_variant_project.py`)
- reading a variant home never reads the real home, except through paths the copied configuration names there;
  the sources read from there are reported as escapes (the manifests opened on the way to them are not listed).
  A path the harness itself maps into the substituted HOME is read in the variant, as the harness reads it. For
  Claude Code that is a marketplace recorded under the real `~/.claude/` (01-config-reader.md)
- for every source marked `Ablation.NONE`: `AblationPolicy.blocked_reason` is a non-empty, human-readable reason
- `run()` on a trivial situation returns a result with a locatable trace
- `trace()` on that result's session id returns at least one action
- two runs of the same trivial situation produce comparable traces
