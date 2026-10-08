# 06 — Trace

What the agent did, in a comparable form. Everything downstream compares traces, so the
normalisation rules here decide what the tool can and cannot see.

## Getting the trace

**Not from the JSON result.** VERIFIED 2026-10-01: `--output-format json` returns timing, cost,
`result` text, `num_turns`, `permission_denials`, `subagent_stats` — and no tool calls.

Two sources, in preference order:

### 1. Session transcript (primary)

The JSON result returns `session_id`. The transcript is found **by that id** among the ones the harness keeps under
the run's home — `<variant-home>/.claude/projects/*/<session_id>.jsonl`, the adapter's `locate_trace(home,
session_id)` through `conversation.find()` — never by computing the slug from the working directory: the harness
records `/tmp/...` as `/private/tmp/...` and slugs that (VERIFIED, the two headless transcripts here are under
`-private-tmp`). Decided 2026-10-04 in 09-adapters.

Under HOME substitution it lands in the **variant's** home, not the user's — which is convenient
(disposable) and a trap (delete the variant after extracting, not before).

**No transcript is an error, never an empty trace** (`TraceMissing`). An empty action list is an outcome of its own
("did nothing") and would read as a behaviour. Nor is **a transcript that recorded nothing**: one with no readable
record, or none of the run's own lines naming the harness version or entry point (a zero-byte file, one whose only
line is torn — what a run killed as it began may leave, UNVERIFIED), raises `TraceEmpty`, a kind of `TraceMissing`.
A transcript the run's home holds but that **can't be read** — it can't be opened, or the read fails partway — raises
`TraceMissing` with the `OSError` chained: never the lines read before the failure, which would pass for a run that
stopped early. A transcript with a header and no call is a run that did nothing, and is read as one.

VERIFIED structure: assistant lines carry `message.content[]` with `tool_use` blocks containing
`name`, `input` and `id`; the line's `message.id` is shared by the calls the model made together (each block is
written on its own line). Results come back as `tool_result` blocks in user lines, naming the call
(`tool_use_id`) and carrying `is_error`. Also available on every line: `type`, `timestamp`, `uuid`, `parentUuid`,
`version`, `cwd`, `gitBranch`, `entrypoint` (`sdk-cli` for `-p`).

**Subagent work is not in the session's transcript.** A subagent writes its own file,
`<session>/subagents/agent-<id>.jsonl` (a workflow's under `workflows/<run>/`), and the session's transcript has no
sidechain lines (Verified Facts, 2026-10-03). The call that started it carries where it went (`subagent_ref`,
from the result's `agentId` / `runId`). The trace reads the main transcript only; delegating is the action.

### 2. `--output-format stream-json` (alternative)

Referenced in `claude --help` for per-event streaming. UNVERIFIED whether it emits tool_use events
in a more convenient form. Not used in v2: the transcript path is verified.

## Trace schema

Built 2026-10-04: `src/agentlint/trace/model.py` (core) and `adapters/claude_code/trace.py` (`trace_from` over
`records()`, `locate_trace`, `read_trace`; the adapter's `trace(session_id)`, core's `TraceReader`).

**Everything is kept raw.** Which parts of an action count as "the same outcome" is Open Question 2, not decided
(readiness D10), so the trace keeps a call's whole input and ids, and the projection is chosen later — a different
choice needs no re-run.

```python
@dataclass(frozen=True)
class Action:
    seq: int                   # position in the run, from 0
    tool: str                  # "Bash", "Skill", "Agent", "mcp__srv__tool" — the harness's name
    input: dict                # as given, whole
    message_id: str | None     # calls sharing one were made together
    call_id: str | None        # the harness's id for the call
    uses: tuple[str, ...]      # usage references: ("tools/Skill", "skills/data:sql-query"),
                               # ("tools/Agent", "agents/Explore") — the one naming rule (config/usage.py)
    answered: bool             # a result was recorded
    error: bool                # its result was an error
    denied: bool               # the harness refused to run it (an error too)
    subagent_ref: str | None   # where the subagent work it started is recorded

@dataclass(frozen=True)
class FiredHook:               # beside the actions, not among them: the config ran it, not the agent;
                               # only hooks recorded with an outcome — not every hook that ran (table below)
    event: str
    name: str                  # "<Event>:<target>"
    command: str               # as configured, unexpanded
    outcome: str               # ok | error | timeout | cancelled
    call_id: str | None        # the call it ran for

@dataclass(frozen=True)
class Trace:
    session_id: str
    harness_version: str | None
    models: tuple[str, ...]    # every model that answered, first first
    headless: bool
    actions: tuple[Action, ...]
    fired: tuple[FiredHook, ...]
    api_errors: tuple[ApiError, ...]  # failed model calls the harness wrote in their place:
                                      # code, reason, transient mark, text (cut to 500 chars)
    cost: RunCost | None       # the closing cost record; None when there is none
```

Where each field comes from in a Claude Code transcript:

| Field | Source | Status |
|---|---|---|
| actions | `tool_use` blocks of main-thread assistant lines, in order; `uses()` names what each used | VERIFIED |
| answered / error | the `tool_result` naming the call; `is_error` | VERIFIED |
| denied | an error result beginning with a refusal the harness writes (`trace.DENIALS`): a call the user rejected, one auto mode couldn't judge | VERIFIED 2026-10-04 (seen in interactive transcripts); a headless denial's wording is UNVERIFIED, and an unrecognised one stays an error. A test run's launcher also marks denied every call the `-p` result's `permission_denials` names by `tool_use_id` (`launch._denied`; that shape UNVERIFIED) |
| subagent_ref | the result's `toolUseResult.agentId` / `runId` (`subagent_ref_of`) | VERIFIED |
| fired | `hook_success`, `hook_non_blocking_error`, `hook_error_during_execution`, `hook_cancelled` (`hook_record`, shared with hook-health); `toolUseID` kept only when it names one of the run's calls — SessionStart and Stop records carry one that names none . **Not every hook that ran**: a UserPromptSubmit hook has no outcome record unless cancelled, and an async hook only an `async_hook_response`, so both are absent. A SessionStart hook whose only record is its `hook_additional_context` is absent too. Output records (`hook_additional_context`, `hook_system_message`) aren't read as firings because one can sit beside its own hook's `hook_success` (same event, `toolUseID` and hook name): one execution would count twice | VERIFIED 2026-10-04: 60 hook records in the 3-call headless run here, 39 naming a call; across the 60 most recent transcripts, UserPromptSubmit has 397 `hook_additional_context`, 56 `hook_cancelled` and 0 `hook_success` records, and there are 3,286 `async_hook_response` records (SubagentStart 1,925, PostToolUseFailure 738, Stop 524); an output record shares event, `toolUseID` and hook name with a `hook_success` 26 times (SessionStart, PreToolUse), and stands alone for 19 SessionStart firings. Whether a prompt-submit hook that printed nothing leaves any record is UNVERIFIED |
| harness_version, headless | the first line naming them (`HeaderReader`) | VERIFIED |
| models | assistant lines' `message.model`, `<synthetic>` left out | VERIFIED |
| api_errors | assistant lines with `isApiErrorMessage`: `error` (e.g. `server_error`), `apiError` (a finer reason, e.g. `gateway_session_expired`), `apiErrorIsTransient`, and the message's text | `error` VERIFIED by the readiness readers (46 here, `server_error` and `invalid_request`, none with a reason); the other three are what the 2.1.285 binary writes (read 2026-10-04), not yet seen in a transcript. A trace stored with codes only reads back as codes with no reason. Which of them are infrastructure is D7 (09 "Running") |
| cost | the last `cost-state` record: `totalCostUSD`, `totalDuration`, `totalAPIDuration` | VERIFIED 2026-10-04 as the last record of both headless transcripts; what a killed run leaves is UNVERIFIED |

Read 2026-10-04, read-only, from the two headless transcripts on this machine (2.1.285): one gives three `tools/Bash`
actions, all answered, none errors, with cost and duration; the other no actions and 21 session hooks. A trace is
JSON-shaped (`to_dict` / `from_dict`) for the runs table.

Not in the trace yet: **files touched.** Tool arguments name some paths, but writes through the shell name none; a
before/after diff of the run's working directory needs the runner and D1 (what is copied into it).

## Normalisation

**Open Question 2 — decided as two rules (D10, the user 2026-10-04), the fine print after the noise study.** Nothing is
normalised at extraction, and analysis takes the projection as a parameter, so no choice changes a stored trace. Built
(`analysis/projections.py`):

- `used` — "your setup makes the agent inconsistent": the SET of tools, skills and agents a run used (its usage
  references), order, repeats and files ignored. The stability screen's.
- `effect` — "removing it changes behaviour" (2026-10-06): the SET of what a run used over the adapter's action terms
  (`TraceReader.action_terms`), each program a shell command ran (`shell:ls`; decided 2026-10-05: a shell command is the
  programs it runs, arguments and wording ignored), `skill:<name>`, `agent:<name>`, `tool:<name>` for any other tool,
  and `file` / `search` by kind alone.
- `strict` — "this rule seems to do nothing": every action in order with what it acted on (a shell command's programs,
  a file's or search's path, a skill's or agent's name), paths shown relative to the run (`RunPlaces`: its working
  directory as `./`, its home as `~/`, either spelling of macOS's /tmp) so two runs' own directories compare equal.

`effect` and `strict` carry `CANON_VERSION` 1 (below).

Two traces are "the same" if their projections match. Too strict and everything looks unstable
(noise). Too loose and real differences vanish.

### What a projection has to cope with

Facts that rule out parts of the first sketch (VERIFIED 2026-10-04 by the readiness readers, read-only):

- **Headless runs have no Grep or Glob.** The headless tool list is Agent, Bash, Edit, ListAgents, Read,
  ReportFindings, ScheduleWakeup, Skill, ToolSearch, Workflow, Write (Verified Facts, "Headless runs see a different
  tool set"). Searching goes through Bash, so a rule for a search tool's pattern has nothing to apply to.
- **Bash is most of the calls, and "argv[0] and subcommand" doesn't survive real commands.** In this machine's main
  transcripts Bash is 1,962 of 2,592 tool calls; 77% are compound (`&&`, `;`), 75% have pipes, 49% heredocs, and 25%
  start with `cd` — so argv[0] would mostly be `cd`, and its target is a per-run directory. In the one headless run
  with tool calls, 2 of 3 commands start with `cd <dir> &&`.
- **Calls made together come in one message** (7% of messages issue 2–12 in parallel), in an order that is likely
  noise: `message_id` is kept so a projection can treat a message's calls as a set.
- **Paths differ between variants by construction** (each run has its own home and working directory), so any path a
  projection keeps needs placeholders for both.
- **Hooks fire because of configuration.** Hashing them would give every hook target an "effect" by construction; the
  trace keeps them apart (`fired`).

### How to settle it empirically

Cheapest path, do this before choosing (the readiness review has the runnable experiment, E3):

1. Pick 3 situations of different kinds
2. Run each 10× against an unchanged config
3. Diff the raw traces pairwise
4. **Whatever varies under identical conditions is noise by definition.** Build the normalisation
   rule from the observed variation rather than from intuition.

Record the result in `docs/02-design.md` Open Questions and write the chosen projection here.

### Normalisation must be versioned

When a projection is chosen it gets a `CANON_VERSION`, stored with every result computed under it. Changing the rules
invalidates stored comparisons — the code should detect a version mismatch and refuse to compare, not silently
compare apples to oranges. Raw traces don't change, so a new version recomputes without re-running.

## Outcome class

For grouping, each run's projected steps reduce to a hash. Both are parameters of the analysis
(`analysis/stability.py`): `Projection = Callable[[Trace], Sequence[str]]` and `Hasher`, the default a sha256 of the
steps exactly, in order.

```python
def group(runs, projection, hasher=sha256_steps) -> tuple[Branch, ...]:
    # runs with one hasher(projection(trace)) form a branch, largest first
```

This hash is what the analysis stage counts. Two runs with the same hash did the same thing — by that projection.

## Human-readable form

Findings must name what differed, not print a hash. `analysis/render.py` (built 2026-10-04) prints each branch's runs
and steps, and marks the first position where the branches part — with steps named by usage reference under a
names-only projection, a 2/1 split in a three-run screen reads:

```
runs 0, 2  →  tools/Read → skills/data:sql-query → tools/Bash
run 1      →  tools/Read → skills/analytics:sql-query → tools/Bash
                           ^^^^^^^^^^^^^^^^^^^^^^^^^^ differs
```

The diff position is the actionable part. A branch that stopped where another went on shows `(ends)` there.

## Storage

One row per run in the `runs` table, keyed `<job_id>`, `<comparison>:<variant>:<n>`, the trace as JSON
(`Trace.to_dict`). Keep raw traces — they're small, and re-running to recover one is expensive.
