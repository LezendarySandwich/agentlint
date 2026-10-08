// The data agentlint hands the UI — mirrors src/agentlint/config/serialize.py and the report built
// by src/agentlint/report/document.py. Held in step by src/fixtures/report.ts, which tests/report/test_golden.py
// writes from what Python really produces and the build type-checks against these types, extra fields included.

export type Load = "always" | "per_message" | "on_invoke" | "conditional" | "not_in_context";
export type Authority = "managed" | "user" | "project" | "package" | "runtime" | "harness";
export type Severity = "error" | "warn" | "info";
export type Confidence = "certain" | "measured" | "suggestive";
/** What the agent was shown of an entity in the latest session (config/model.py Visibility); its words are format.ts's. */
export type Visibility = "described" | "name_only" | "not_shown";

export interface TokenCount {
  value: number;
  method: "estimated" | "observed" | "derived";
}

/** What one session was, as its transcript says (config/sessions.py): the same header in a reading's sessions and
 * the transcript reader's list. null where the transcript doesn't say. */
export interface SessionHeader {
  session_id: string;
  project: string | null; // its working directory
  harness_version: string | null; // the harness version it started on
  model: string | null; // the model that answered first
  started_at: string;
  interactive: boolean;
}

/** Text is never inline in a report: `text_ref` names it in the separately compressed texts. */
export type Content =
  | {
      type: "file_text";
      format: string;
      text_ref: string;
      content_hash: string;
      tokens: TokenCount;
      required_keys: { type: "key_path"; path: (string | number)[] }[]; // keys an excision must never remove
    }
  | {
      type: "observed_text";
      format: string;
      text_ref: string;
      content_hash: string;
      tokens: TokenCount;
      seen_in: SessionHeader[];
      sessions_examined: number;
    }
  | { type: "size_only"; tokens: TokenCount; reason: string }
  | { type: "not_observed"; sessions_examined: number; reason: string };

export interface Source {
  id: string;
  provenance: {
    point: { harness: string; name: string; label: string; mechanism: string; description?: string };
    file: string | null; // the file it is read from or configured in; null for text no file holds (its label says what)
    authority: Authority;
    package: string | null;
    anchor: string | null; // where inside `file`, when the source is one part of it; the adapter's, never parsed here
  };
  activation: { load: Load; condition: string | null };
  declared_hash: string;
  content: Content;
  ablation: { mechanism: string; blocked_reason: string | null; linked_sources: string[] };
}

export interface Entity {
  id: string;
  name: string;
  kind: { harness: string; name: string; label: string; role: string; namespace: string; separator: string | null };
  provided_by: string;
  description: string | null;
  aliases: string[];
}

/** Tokens one thing delivered before your first words: a source, or untraced text (no source, under its label). */
export interface Attribution {
  source_id: string | null;
  tokens: number;
  label: string | null;
}

/** The always-on outcome carries its attribution, computed once in Python; the sums beside it are derived from it:
 * what the anchor session delivered when it is observed, each source's latest observation when not. */
export type AlwaysOn =
  | { type: "reconciled"; observed_total: TokenCount; attribution: Attribution[]; session: SessionHeader; attributed: number;
      unattributed: TokenCount }
  | { type: "overcounted"; observed_total: TokenCount; attribution: Attribution[]; session: SessionHeader; attributed: number;
      excess: number }
  | { type: "not_reconciled"; attribution: Attribution[]; reason: string; attributed: number };

/** Text a session showed that no configured source explains. `id` follows from its point and `key` (empty: the
 * label), the same across readings (config/model.py Untraced): rows and links name it by that, never by its place in
 * the list. The label is for reading and may change between readings. */
export interface Untraced {
  id: string;
  point: Source["provenance"]["point"];
  label: string;
  load: Load;
  content: Content;
  key: string;
}

export interface Snapshot {
  schema_version: number;
  harness: string;
  harness_version: string | null;
  model: string | null;
  cwd: string;
  sources: Source[];
  entities: Entity[];
  untraced: Untraced[];
  sessions: SessionHeader[];
  always_on: AlwaysOn;
  visibility: [string, Visibility][];
  config_hash: string;
  fingerprint: string;
}

/** One of the entities a finding sets side by side (duplicate-name, trigger-overlap): the facts the terminal prints
 * and the UI lays out, from one list (report/finding.py Member). */
export interface Member {
  entity: string; // entity id, in snapshot.entities
  name: string;
  kind: string; // its kind's label
  source: string; // the id of the source that provides it
  scope: Authority | null; // where that source sits; null when the snapshot has no such source
  package: string | null; // the package it shipped in: show it with packageName (format.ts)
  seen: Visibility | null; // what the agent saw of it in the latest session; null: unknown
  description: string | null;
  identical_to: string | null; // the entity id of another member whose file is the same content
  symlink: string | null; // where its file links to, when it is a symlink
}

export interface Finding {
  id: string;
  check: string;
  kind: string;
  severity: Severity;
  confidence: Confidence;
  title: string;
  detail: string;
  discriminator: string;
  sources: string[];
  locations: { source_id: string; line: number | null }[]; // a source, by id, and a line within its text
  figures: { label: string; value: number; unit: string; unit_one: string }[]; // unit_one: the unit for a value of 1
  suggested_action: string | null;
  members?: Member[]; // what it sets side by side; empty for checks that compare nothing, absent in older reports
  suppressed: boolean;
  suppression_lapsed: boolean;
}

/** What one recent session in this project loaded before your first words, against what is configured to. */
export interface Context {
  session: SessionHeader;
  seen: string[]; // source ids whose text the session showed
  files: string[]; // read from disk: configured, not confirmed per session
  missing: string[]; // could have shown, didn't (a hook that printed nothing)
  untraced: string[]; // ids of snapshot.untraced
  observed_total: number | null; // when this session reported its total
}

/** Counts of your turns that used each entity — never what was typed (docs/tech_impl/08-recorder.md). */
export interface Usage {
  days: number;
  turns: number;
  history_start: string | null;
  entities: Record<string, { invocations: number; last_seen: string }>; // by namespace/name
  /** What `agentlint usage` prints: the skills and agents the agent chooses between, used and not. */
  summary: {
    used: { entity: string; reference: string }[]; // entity id, namespace/name; most used first
    unused: { entity: string; reference: string }[];
    unmatched: Record<string, number>; // used in those kinds, but not defined in this configuration
  };
}

export interface Report {
  schema_version: number;
  generated_at: string;
  agentlint_version: string;
  harness_label?: string; // how the user knows the harness (its adapter's label); absent in reports written before it
  home_dir: string; // the user's home directory: a path under it is shown in the `~` form (format.ts home), no other
  snapshot: Snapshot;
  labels: Record<string, string>; // source id -> the words `agentlint show` uses for it
  /** package id -> how the user knows it, from the harness's adapter; absent in reports written before it. Read
   * through format.ts's packageName, never by taking an id apart. */
  package_labels?: Record<string, string>;
  loads: Record<Load, { in_first_turn: boolean }>; // each load's facts, so no reader re-states the rule
  contexts: Context[]; // what each recent session in this project loaded, most recent first; empty when none ran
  usage: Usage | null;
  /** Why this reading was neither stored nor had its findings recorded, in a sentence — `agentlint ui` started with a
   * --sessions other than your sessions setting; null when it was recorded, as every reading at the setting is. Shown
   * on Findings and What changed, never worded here. */
  not_recorded: string | null;
  check: {
    summaries: Record<string, string>; // check id -> what it looks for, in a sentence
    not_evaluated: Record<string, string>;
    findings: Finding[];
  };
}

/** What there is to choose from, served live: every installed harness agentlint reads, and the chosen one's projects. */
export interface Scopes {
  harnesses: { name: string; label: string; version: string | null }[]; // the harnesses setting's order, else agentlint's
  harness: string; // the harness these projects are of
  projects: { path: string; sessions: number; last: string | null }[]; // this directory first, then the most recent
  default: string; // where agentlint ui was started
  home_dir: string; // the user's home directory, as the report's
}

/** A session transcript, without its contents — the live server's list (config/conversation.py). */
export interface SessionListing {
  header: SessionHeader;
  title: string | null; // the harness's own name for it
  ended_at: string;
  prompts: number;
  size: number;
  archived_only?: boolean; // the harness has deleted it; agentlint's archive kept it
}

/** Text kept for reading, and how many characters were left out of it. */
export interface Clipped {
  text: string;
  omitted: number;
}

/** What a note is (config/conversation.py's NoteKind): neither a prompt nor the agent's words. */
export type NoteKind = "command" | "interrupted" | "shell" | "notification" | "hook_error" | "recap" | "compacted";

/** One entry of a conversation: each kind holds only its own fields, told apart by `type`. */
export type Item =
  | { type: "prompt"; at: string | null; text: string }
  | { type: "reply"; at: string | null; text: string }
  | { type: "thinking"; at: string | null; text: string }
  | {
      type: "tool_call";
      at: string | null;
      name: string; // the tool's name
      summary: string; // the call in one line
      input: string; // its input, in full
      result: Clipped | null; // what it returned, cut; null when no result was recorded
      error: boolean;
      entities: string[]; // namespace/name
      subagent_ref: string | null; // the subagent transcripts this call started, in the adapter's terms: only handed back
    }
  | { type: "injected"; at: string | null; by: string; text: Clipped } // by: what injected it, in words
  | { type: "note"; at: string | null; kind: NoteKind; text: Clipped; entities: string[] }
  | Briefing;

/** Why the harness gave the model its prompt and context at this point (config/conversation.py's BriefingReason):
 * before the first prompt was answered, after a compaction rebuilt the context, or where its own prompt or tool
 * definitions changed with no rebuild. */
export type BriefingReason = "start" | "compacted" | "changed";

/** One source's share of a part that spans several ownership groups — a listing whose entries come from the harness
 * and from packages. Shares are estimated piece by piece, so they may not add up exactly to the part's tokens. */
export interface Share {
  label: string; // what holds it, in words: a package's name, "the listing's own part"
  authority: Authority;
  tokens: TokenCount; // estimated
  source_id: string | null; // the configuration source it is, when the adapter traced it
}

/** One part of what the harness gave the model. Its exact recorded text is the conversation's `texts[text_ref]`. */
export interface BriefingPart {
  // where it reaches the agent, as Source.provenance.point: read its label, mechanism and description, never its name
  point: { harness: string; name: string; label: string; mechanism: string; description: string };
  label: string; // the adapter's words, shown as they are: "System prompt", an instruction file's name
  detail: string; // what tells it apart: an instruction file's absolute path, a hook's package or name; "" when nothing
  count: number | null; // the entries it holds — tools, skills, agents, files — when it is a list of them
  authority: Authority | null; // null with shares: it spans groups ("mixed"); null without: untraced (counts as harness)
  shares: Share[]; // its split across groups, only when it spans more than one
  source_id: string | null; // the configuration source it is: linked only when the report has a source of that id
  text_ref: string; // a key of Conversation.texts
  tokens: TokenCount; // estimated from its text
}

/** What the harness gave the model at one point, as the transcript recorded it — never reconstructed. `recorded`:
 * whether the harness recorded its own prompt here; `note`: the adapter's words for what wasn't recorded or may not
 * have reached the model. */
export interface Briefing {
  type: "briefing";
  at: string | null;
  reason: BriefingReason;
  recorded: boolean;
  note: string | null;
  parts: BriefingPart[]; // may be empty: a transcript the model never answered
  tokens: TokenCount; // derived: the parts' estimated tokens, together
}

/** A subagent a call started: enough to choose one to read. */
export interface SubagentInfo {
  id: string;
  title: string | null; // the first thing it was asked
  entries: number;
}

/** One session, readable: `agentlint ui` serves it by id, read from the transcript as you open it. */
export interface Conversation {
  title: string | null; // the harness's own name for it; what the session was is its listing's header
  items: Item[];
  texts: Record<string, string>; // each briefing part's exact text by its content hash, once however often it was re-sent
}

/** One setting, as the live server's Settings answer gives it (settings.py's `describe`, ui/control.py). */
export interface SettingEntry {
  key: string;
  value: number | string[] | string; // in effect: the default when it isn't set, or when what is stored can't be used
  default: number | string[] | string; // "" for the model: the model your sessions use
  group?: "reading" | "tests"; // Settings › Reading and keeping, or Settings › Tests (settings.py GROUPS)
  kind?: "model" | "tools"; // a model's name, or a list of tool names: neither a number nor a harness
  set: boolean; // you changed it: Reset puts back the default
  meaning: string; // one line
  problem: string | null; // why the stored value can't be used
  note: string | null; // something true of the value you chose, e.g. a harness agentlint can't read yet
  range?: [number, number]; // a whole number from, to
  choices?: string[]; // the harnesses agentlint reads
}

/** GET /api/settings, and what each Settings write answers. */
export interface SettingsAnswer {
  settings: SettingEntry[]; // Reading and keeping
  test_settings?: SettingEntry[]; // a test's settings, the removed-tools override among them (Settings › Tests)
  attention: string[]; // what is wrong that Settings can fix: the page opens on Settings while there is any
  sync: {
    installed: boolean;
    scheduler: string; // launchd or cron: the one in use, or the one that would be
    where: string | null; // the job's file, or "your crontab"
    time: string; // HH:MM, each day
    error: string | null; // asking the scheduler failed
    last: { at: string; ok: boolean; changed?: number; turns?: number; error?: string } | null; // the last sync
  };
  archive: { transcripts: number; bytes_written: number; bytes_stored: number };
  store: string; // the directory agentlint keeps everything in
  home_dir: string; // the user's home directory, so the store and the job's file are shown in the ~ form (format.ts home)
  version: string;
  first_run: boolean; // the first-run panel hasn't been closed, and the CLI hasn't shown its notice
  jobs_running: { job_id: string; pid: number | null }[]; // Forget everything waits for these
}

/** POST /api/forget's answer. */
export interface Forgotten {
  turns: number;
  transcripts: number;
  /** Test runs' directories a deletion failed on, each with why: the next forget or test run sweeps them again. */
  undeleted: string[];
  settings: SettingsAnswer;
}

/** One stored snapshot of a project's configuration, served live from agentlint's database (ui/records.py): the most
 * recent first. `taken_at` is when it was last read; a configuration read again replaces its own row. */
export interface SnapshotInfo {
  fingerprint: string;
  taken_at: string;
  harness_version: string | null;
  readable: boolean; // false: stored by an agentlint whose format this one can't read back; nothing below is known
  sources: number | null;
  tokens: number | null; // always-on, attributed, as `show --diff` counts it
}

/** One side of a comparison. `unseen`: always-on sources whose text no session showed, left out of its figure. */
export interface SnapshotSide {
  fingerprint: string;
  taken_at: string;
  harness_version: string | null;
  model: string | null;
  unseen: number;
}

/** What one change is (report/diff.py's Change.kind): configuration that differs, a source on one side only, the same
 * configuration whose delivered text changed, or untraced text. */
export type ChangeKind = "changed" | "added" | "removed" | "output" | "untraced";

/** One source (or one untraced text) that differs. Tokens are null where that side has none or didn't see it; `delta`
 * is null where a side that should have a count doesn't ("?", never 0). An untraced change's `source_id` is its text's
 * content hash, never a source's id. */
export interface SnapshotChange {
  kind: ChangeKind;
  source_id: string;
  label: string;
  load: Load;
  before: number | null;
  after: number | null;
  delta: number | null;
  note: string; // "newly observed", "no longer observed", …; empty when there's nothing to add
}

/** What changed from one stored snapshot to another — `agentlint show --diff`, served live. */
export interface SnapshotDiff {
  from: SnapshotSide;
  to: SnapshotSide;
  always_before: number;
  always_after: number;
  unexplained: number; // the part of the always-on change no listed change accounts for: text seen on one side only
  untraced_left_out: boolean; // one side had no session from this project and a side has untraced text: it is left out of both (show --diff's rule, report/diff.py)
  changes: SnapshotChange[]; // by kind in ChangeKind's order, the largest change first within each
  entities_added: { kind: string; name: string }[]; // defined on disk only
  entities_removed: { kind: string; name: string }[];
}

/** One of agentlint's findings about itself (a Diagnostic, store/diagnostics.py), served live: never a finding, never
 * counted with them. `kind` is its producer's name for what happened, free text; `subject` what it is about (a
 * target, a job). `created` is null only for one the store hasn't stamped, which the server never serves. */
export interface DiagnosticInfo {
  id: number | null;
  kind: string;
  subject: string;
  message: string;
  created: string | null;
}

/** A dismissal (suppression) as GET /api/suppressions lists it, and POST /api/suppress answers with it: the finding as
 * last recorded (check and title null when no project records it any more), when and why, and per project with a
 * baseline what it does there — active (hiding it), lapsed (shown again: what it rests on there changed) or moot (no
 * longer found there). */
export interface Suppression {
  id: string;
  check: string | null;
  title: string | null;
  at: string; // ISO 8601
  reason: string;
  projects: { project: string; state: "active" | "lapsed" | "moot" }[];
}

export interface Suppressions {
  suppressions: Suppression[]; // the most recent first
  home_dir: string; // for the ~ form
}

export interface Dismissed {
  suppression: Suppression;
  note: string; // where it stays hidden, and until when
}

export interface ShownAgain {
  finding_id: string;
  was_dismissed: boolean;
}

// Tests (ui/testing.py; docs/tech_impl/11-ui.md "Tests"): the plan, situations and their review, the one-time
// onboarding, jobs and each job's results.

/** How many of a target's situations stand at each review. A situation is written kept, so only a drop takes one out
 * of a test. */
export interface ReviewCounts { kept: number; dropped: number; edited: number }

/** A testable target, in the ranker's order. */
export interface PlanTarget {
  id: string;
  name: string; // for a person: its kind, where it is, and how much of it
  tokens: number | null;
  load: Load;
  situations: ReviewCounts; // its situations by review
  kept: number; // kept or edited: what a test may run of it, at most situations per target
}

/** What Start would run of the ticked targets: N targets × situations per target × runs per situation (`most`), the
 * situations kept for them (at most situations per target each) and the runs those make (`runs`: what Start runs, and
 * the job's ceiling), about how long, and why it would run nothing — the words Start would be refused with (nothing
 * ticked; a ticked target with nothing kept; no model) — [] when it would run. */
export interface PlanJob {
  targets: string[]; // the ticked target ids, in the ranked order
  situations: number;
  runs: number; // the most Start can run, every gate open (D9), and the job's ceiling
  most: number; // what the ticked targets make with every situation kept: more than `runs` only when some have fewer
  situations_per_target: number;
  runs_per_situation: number; // each situation's runs on your configuration, and each side's with and without
  short: { id: string; name: string; kept: number }[]; // ticked targets with fewer kept than situations per target
  consistency_only: { id: string; name: string; why: string }[]; // ticked targets no variant can remove yet: no
  // with/without step, and why
  seconds: number; // about how long `runs` take, at most: in rounds of `runs_at_once`
  seconds_per_run: number;
  runs_at_once?: number; // your setting: runs a test keeps out at once
  seconds_from: string; // where that figure comes from
  blocked: string[];
}

/** GET /api/plan. */
export interface PlanAnswer {
  harness: string;
  project: string;
  phase: string;
  counts: { situations_per_target: number; runs_per_situation: number };
  targets: PlanTarget[]; // every testable target, in the ranker's order
  skipped: { id: string; name: string; why: string; detail: string | null }[];
  ticked: string[]; // the targets to test, as you last ticked them here (the top-ranked one when you never did)
  job: PlanJob;
  tools: ToolVerdictInfo[]; // every tool the adapter judged, removed from every run or kept, with why (your override applied)
}

/** POST /api/plan/ticked: the ticked targets kept, and the job they make now — null when the server has no reading of
 * the project (read the plan again). */
export interface TickedAnswer { ticked: string[]; job: PlanJob | null }

/** Where a situation stands: written "kept"; "dropped" or "edited" only by a person. */
export type ReviewWord = "kept" | "dropped" | "edited";

/** One situation as review shows it. */
export interface Situation {
  id: string;
  target: string;
  text: string; // the edit, when edited
  generated: string; // as the generator wrote it
  edited: boolean;
  matters: string; // what matters in it: the generator's pick from a fixed menu
  matters_meaning: string | null;
  matters_thing: string | null; // the thing it names ("skill:*", "program:acme, then tool:Write"); null: written before
  // things were named (2026-10-07)
  review: ReviewWord;
  model: string | null;
  created: string | null;
  reviewed: string | null; // when a person last reviewed it; null: kept as written, which nobody reviewed
  names_its_target?: string[]; // an edit that names what it tests: the guard's hits
}

/** GET /api/situations?target=. */
export interface SituationsAnswer {
  target: string;
  generating: boolean;
  situations: Situation[];
  diagnostics: { kind: string; message: string; created: string | null }[];
}

/** Every tool the adapter judged, with your override applied: removed from every run or kept, and why. */
export interface ToolVerdictInfo { name: string; removed: boolean; why: string }

/** GET /api/onboarding: what the one-time screen shows, and whether it was accepted. */
export interface OnboardingAnswer {
  accepted: boolean;
  blocked: string[]; // why Start is disabled now
  runs: number;
  situations: number;
  seconds: number; // about how long the runs take
  seconds_per_run: number;
  seconds_from: string; // where that figure comes from
  job: PlanJob; // what Start runs of the ticked targets
  model: { value: string | null; from: "setting" | "sessions" | null };
  removed_tools: ToolVerdictInfo[];
  policy: string;
  hooks: string;
  telemetry: string;
  keep_awake: string;
  settings: SettingEntry[];
}

export type JobStatusWord = "open" | "complete" | "salvaged" | "ceiling";

/** A job's situations counted by screen state, every state named, in this order (Tests › Results). */
export interface ScreenCounts {
  agreed: number;
  disagreed: number;
  "no action": number;
  incomplete: number;
  "mixed environment": number; // refused: the runs ran in different environments
}

/** One job as GET /api/jobs lists it (and POST /api/jobs, stop, resume and salvage answer with it). */
export interface JobEntry {
  job_id: string;
  project: string; // its real path
  harness: string | null;
  status: JobStatusWord;
  /** Where it stands, in words: "running", "stopping", "paused: Resume goes on where it stopped", "complete",
   * "closed: what completed is kept", "stopped at its run ceiling". */
  standing: string;
  targets: { id: string; name: string }[]; // what its situations were written for, in the order they run; the name is
  // the server's last reading of the project's (GET /api/plan's), else the id
  situations: number;
  screens: ScreenCounts; // its situations by screen state, as its results screen them
  created: string | null;
  last_progress: string | null;
  model: string | null;
  harness_version: string | null;
  run_ceiling: number;
  timeout_s: number;
  runs: { recorded: number; planned: number };
  launched_without_a_run: number;
  comparisons: Record<string, number>; // per progress state: complete, partial, incomplete, not-attempted
  in_flight: string | null; // the first run under way, if one is
  running?: string[]; // every run under way, by key: several with runs_at_once
  runner: { alive: boolean; pid: number | null; stopping: boolean };
  keep_awake: boolean;
  removed_tools: string[];
  log: string;
  log_tail: string[];
}

/** GET /api/jobs. */
export interface JobsAnswer {
  jobs: JobEntry[]; // every project's, the most recent first
  project: string | null; // the project the request names, its real path as a job's `project` is: its jobs are the
  // tests of the project in view (Tests › Results); null when the server can't tell
  accepted: boolean; // the onboarding was accepted
  blocked: string[]; // why Start is disabled now
  keep_awake: string;
}

/** POST /api/jobs/<id>/salvage. */
export interface Salvaged {
  job: JobEntry;
  undeleted: string[];
  comparisons: { comparison: string; state: string }[];
}

/** A stability screen's state (07-analysis.md): "no action" when every compared run used no tool, skill or agent —
 * never agreed; "mixed environment" is refused. */
export type ScreenWord = "incomplete" | "mixed environment" | "no action" | "disagreed" | "agreed";

/** One action of a run, in the order the agent took them. */
export interface RunAction {
  tool: string; // as the harness names it
  what: string; // its input on one line: its string values joined by " · ", whitespace collapsed, at most 200
  // characters (cut with "…"); the run's working directory shown as ".", its home as "~"
  uses: Record<string, string[]>; // what else it used — the skill it invoked, the agent it delegated to — by the
  // adapter's namespaces, as a branch's `used`; {} for most actions
}

/** One run whose trace was read, as a job's results list it. */
export interface RunResult {
  run: number; // from 0, as the server's run keys count: a person counts from 1
  outcome: string;
  seconds: number; // how long it took, to a tenth of a second
  actions: RunAction[]; // the first 60
  more_actions: number; // how many more it took
  met?: boolean; // whether it did what its situation expects; absent when the situation expects nothing
}

/** How many runs on each side of a with/without comparison used one term ("shell:acme", "skill:tdd", "tool:WebFetch",
 * "file", "search"). */
export interface TermCount { term: string; baseline: number; without: number }

/** A with/without verdict (analysis/effect.py): changes, no-change ("seems to do nothing"), unmeasurable, incomplete. */
export type EffectWord = "changes" | "no-change" | "unmeasurable" | "incomplete";

/** The expectation's strength (analysis/expectation.py, 2026-10-07): "works" only when every run with the target met
 * the expectation and none without; "not followed" when no run with it did (never run without); "partial", "adds
 * nothing" and "backfires" by the strength; the rest say why it can't be told yet. */
export type StrengthWord = "works" | "partial" | "adds nothing" | "backfires" | "not followed" | "incomplete"
  | "mixed environments" | "no expectation";

/** Runs that met the expectation, of the readable runs on one side. */
export interface MetCount { met: number; runs: number }

/** A situation's second step (D9), as its result carries it: how far it got, and — "measured" — the verdict from the
 * runs recorded so far. "waiting": its gate hasn't decided (the situation's runs aren't all in); "skipped": its gate
 * closed (`why`); "unreadable": this server has no adapter to read the job's actions. A situation that expects a thing
 * (`kind` "expectation", 2026-10-07) runs only without the target — its own runs are the runs with it — and is measured
 * as its expectation's strength (`verdict`, `with`, `without`, `strength`, `instead`, `sides.without`); one from before
 * is a with/without comparison (`measured`, `evidence`, both sides). */
export interface EffectResult {
  kind?: "expectation";
  expected?: string; // the thing it expects, as the generator named it: "skill:tdd", "program:acme"
  verdict?: StrengthWord;
  with?: MetCount; // its runs with the target (the situation's runs) that met the expectation
  without?: MetCount | null; // the same without it; null when never run (not followed) or not in yet
  strength?: number | null; // share with minus share without: 1 is perfect, 0 adds nothing, below 0 backfires
  instead?: { term: string; runs: number }[]; // not followed: what the runs with it did, by term, most runs first
  comparison: string;
  planned: number; // runs a side
  recorded: Record<string, number>; // runs recorded, per side
  progress: string;
  state: "waiting" | "skipped" | "unreadable" | "measured";
  why?: string | null; // skipped: the situation's screen state when its runs couldn't be compared or (from before
  // expectations) didn't agree, or "not followed" when none did what it expects
  measured?: EffectWord;
  reason?: string | null; // why it is unmeasurable or incomplete
  runs?: { baseline: number; without: number }; // readable runs compared, per side
  tv?: number | null;
  p?: number | null;
  evidence?: TermCount[]; // the terms that changed, the biggest gap first (changes only)
  modal_share?: number | null;
  differing?: string[];
  terms?: TermCount[]; // every term's counts
  canon?: number;
  sides?: { baseline?: RunResult[]; without: RunResult[] };
  unscreened?: { side: string; run: number; outcome: string; seconds: number }[];
}

/** A target's verdict across its situations. */
export interface TargetEffect { measured: EffectWord; reason: string | null; situations: Record<string, number> }

/** A target's expectation strength across its situations: "works" only when every one worked, else its weakest
 * result leads; the mean strength, a situation not followed counting 0; how many situations ended each way. */
export interface TargetStrength { verdict: StrengthWord; strength: number | null; situations: Record<string, number> }

/** One situation's stability screen, as GET /api/jobs/<id>/results gives it. */
export interface SituationResult {
  comparison: string;
  situation: string;
  about: string | null; // the target its situation was written for
  name: string | null; // that target's name: the server's last reading of the project's, else its id; null without `about`
  text: string;
  matters: string | null; // what matters in it, the generator's tag; null once the situation is regenerated away
  matters_meaning: string | null; // that tag's menu words
  matters_thing: string | null; // the thing it names; null: written before things were named
  planned: number;
  recorded: Record<string, number>;
  progress: string;
  state: ScreenWord | null; // null: an ablation comparison, not screened here
  note?: string;
  runs?: RunResult[]; // the runs screened, in run order: those whose trace was read
  unscreened?: { run: number; outcome: string; seconds: number }[]; // runs with no readable trace, never screened
  outcomes?: { run: number; outcome: string }[]; // every run's
  modal_share?: number | null; // the largest branch's share; null with no runs
  branches?: { runs: number[]; count: number; of: number; used: Record<string, string[]> }[];
  differing?: string[];
  environments?: { harness_version: string | null; models: string[] }[];
  effect?: EffectResult | null; // its with/without comparison (D9); null: the test gives it none
}

/** A source that can be read for directives (12-directives.md), with how much of it is read: sections whose
 * fingerprint is stored, of all its sections. "partly read": some sections are new or changed since. */
export interface DirectiveSource {
  id: string;
  name: string;
  load: Load;
  authority: Authority;
  package: string | null;
  sections: number;
  read: number;
  stale: number; // sections read only under an older version of the reader: to read again
  tokens: number; // what the sections not read hold: what reading it would cost
  state: "read" | "partly read" | "not read" | "stale";
}

/** A reading of instructions as a job (GET /api/reads): its sections by where they stand, and whether its worker runs.
 * standing: "running", "complete" (with " · 3 failed"), or "stopped: Resume reads the rest". */
export interface ReadEntry {
  read_id: string;
  kind: "reading";
  project: string;
  harness: string;
  status: "open" | "complete" | "stopped";
  standing: string;
  created: string | null;
  finished: string | null;
  sources: number; // selected
  sections: { planned: number; pending: number; reading: number; read: number; failed: number };
  runner: { alive: boolean };
  log_tail: string[];
}

/** GET /api/reads/<id>: the reading and each section, in the order they are read. */
export interface ReadDetail extends ReadEntry {
  sections_list: { source_id: string; source: string; line: number; loads: string; status: "pending" | "reading" | "read" | "failed";
                   directives: number | null; message: string | null }[];
}

/** GET /api/directives: the sources, and the project's reading still open, if one is. */
export interface DirectivesAnswer {
  project: string;
  version: number; // the reader's version (set by hand): what was read under an older one is stale
  sources: DirectiveSource[];
  reading: ReadEntry | null;
}

/** GET /api/jobs/<id>/results. */
export interface ResultsAnswer {
  job_id: string;
  project: string;
  created: string | null;
  status: JobStatusWord;
  fingerprint: string;
  situations: SituationResult[];
  targets?: ({ about: string; name: string; effect: TargetEffect | null; kind?: undefined }
    | { about: string; name: string; kind: "expectation"; effect: TargetStrength })[]; // each target's verdict, in the order it ran
  runs?: { recorded: number; planned: number }; // the job's runs, both steps; planned leaves out gated-off comparisons
  in_flight?: string | null; // the run under way, by its key ("cmp-0005:baseline:8"), as GET /api/jobs gives it
  running?: string[]; // every run under way: several with runs_at_once
}
