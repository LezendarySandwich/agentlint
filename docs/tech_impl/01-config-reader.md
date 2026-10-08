# 01 — Config reader

Assembles everything the agent is given into one addressed list. Nothing else can work without this,
and it's independently valuable: nobody can currently see their own configuration.

No AI. File parsing plus one transcript read.

---

## Source vs Rule

**A Source is a container.** One addressable artifact that reaches the agent — a file, a hook's
output, a package's skill definition. It has an origin, a format, and a position in the authority
order.

**A Rule is an addressable region inside a Source.** It is what we ablate where a variant can cut it out
alone; where none can, the whole source is removed instead. Either is an *ablation target* (`03-ranker.md`).

They're separate because a 2,000-token instruction file is not one rule, and because the useful
granularity differs by format: a markdown file is addressed by section, a settings file by key
path, a hook's output not at all.

```
Source: ~/.claude/CLAUDE.md                      (markdown, 4,980 tokens)
  ├─ Rule: § "Memory"                            (1,840 tokens)  ← ablatable
  ├─ Rule: § "Environment"                       (  620 tokens)  ← ablatable
  └─ Rule: § "Delivering work"                   (2,520 tokens)  ← ablatable

Source: hook:UserPromptSubmit                    (plain, 3,100 tokens)
  └─ Rule: whole                                 (3,100 tokens)  ← atomic; disable the hook
```

---

## Source

Composed of four concerns rather than one flat bag. Each sub-type carries only what it needs, and
**every class validates its own invariants on construction**, so an object that exists is
consistent and nothing downstream interprets sentinels: counts are never negative, reasons and labels
are never empty, and a value that follows from others is computed on construction (a rule's `id`,
`tokens`, `content_hash`) or checked against them (a text's `content_hash` and `tokens`). Two values
are trusted to their maker instead, as `model.py` says: a snapshot's `config_hash` and `fingerprint`,
which `assemble()` computes, and a non-file source's `declared_hash`, which only the adapter can know.
Implemented in `src/agentlint/config/` (`model.py`, `content.py`, `locators.py`, `taxonomy.py`,
`tokens.py`, `sessions.py`).

```python
@dataclass(frozen=True)
class Source:
    id: str
    provenance: Provenance       # where it came from, how authoritative
    activation: Activation       # when it's in context
    declared_hash: str           # what the user controls; the fingerprint's only input
    content: Content             # what we know about its text — one of four states
    ablation: AblationPolicy     # whether and how we can remove it
```

`declared_hash` is a file's bytes for a file-backed source (and must equal its `FileText` hash), or
a hook's configuration for hook output. Text observed in sessions never enters it — see "Config
fingerprint".

### Provenance — where it came from

```python
@dataclass(frozen=True)
class Provenance:
    point: InjectionPoint        # how it reaches the agent; carries harness + mechanism
    file: Path | None            # the file it is read from or configured in; None for text no file holds
    authority: Authority         # managed > user > project > package > runtime
    package: str | None = None   # the package it shipped in, if any
    anchor: str | None = None    # where inside `file`, when the source is one part of it; opaque to core
    # .harness is derived from point, so the two can never disagree
```

**Where a source is: a file, and a place inside it** (decided 2026-10-04, design review 6.1). A source read from a
file of its own has `file` and no `anchor`. One that is one part of a file has both: a hook's `anchor` is the JSON
pointer to its entry in the settings or manifest file, embedded managed instructions' is the settings key that holds
them. Text no file holds — the harness's prompt and tools, the listings it composes, a package's share of a listing —
has neither: its point's label says what it is, and a listing share's `package` says whose. An anchor needs a file
and is never blank, and `file` is a `Path`, never a string. Core compares `file` and branches on
`point.mechanism`; it never looks inside `anchor`, whose grammar only the adapter that wrote it knows. Renderers
show a source as its file with the anchor written after a `#` (`report/paths.place`, the UI's `placeOf`), never
split back apart, and a source with no file by its label. Before, one string, `locator`, held all of these — a
path, `path#claudeMd`, `settings.json#<pointer>`, `skill_listing#<pkg>`, `builtin:`/`generated:` labels — and core
took it apart by prefix and by `#`; "locator" now names only the `Locator` union below. Source ids, which several
were built from, are unchanged, so no finding's id moved (`agentlint check --json` on this machine listed the
same 171 ids before and after, VERIFIED 2026-10-04).

**Two levels: core categories, adapter-specific points.** Core logic branches only on `Mechanism`,
a small generic enum. Each adapter defines its own `Enum` whose values are `InjectionPoint` value
objects — that is where harness-specific names live.

```python
class Mechanism(Enum):           # core
    HARNESS_BUILTIN    # the harness's own prompt and tool definitions
    CONFIG_FILE        # settings files
    INSTRUCTION_FILE   # free-text instruction files
    PACKAGE_ENTITY     # an entity definition shipped in a package
    HOOK_OUTPUT        # text a user-configured command injects at runtime
    HARNESS_GENERATED  # listings the harness composes from what's installed

@dataclass(frozen=True)
class InjectionPoint:            # core value object; validates itself
    harness: str                 # lowercase-kebab
    name: str                    # stable id, lowercase_snake
    label: str                   # what the user sees
    mechanism: Mechanism
    description: str = ""        # what it is, in a sentence, for text the user didn't write; never part of equality

class Point(Enum):               # adapter-side: adapters/claude_code/points.py, e.g.
    CLAUDE_MD = InjectionPoint("claude-code", "claude_md", "CLAUDE.md", Mechanism.INSTRUCTION_FILE)
```

Mechanisms are orthogonal to authority and to format. A `CONFIG_FILE` can be `managed` or `user`. An
`INSTRUCTION_FILE` is markdown; a `CONFIG_FILE` is JSON. Each mechanism has a different discovery
method and a different ablation path, which is exactly why it deserves its own field.

#### The harness built-in

The harness ships its own system prompt and tool definitions. They're large, part of the pile, not
in any config file, and not ablatable. **They are partly inspectable**: on Claude Code, sessions
record the delivered system prompt and full tool list (Verified Facts). The adapter declares them
as `HARNESS_BUILTIN` sources with `NotObserved` content, and recent sessions fill in the text.

What still can't be seen is sized by subtraction against the observed total, and the result is one
of three explicit states rather than a number that might be negative or missing:

```python
AlwaysOn = Reconciled | Overcounted | NotReconciled
Attribution(source_id | None, tokens, label | None)   # one source's tokens, or untraced text under its label
Reconciled(observed_total, attribution, session)      # .attributed, .unattributed: TokenCount[DERIVED]
Overcounted(observed_total, attribution, session)     # .attributed, .excess — our estimate exceeds what was seen
NotReconciled(attribution, reason)                    # no suitable session; .attributed is a lower bound
```

`Reconciled` and `Overcounted` share one check on `observed_total`: it is what a session reported
(`OBSERVED` or `DERIVED`), so both refuse an `ESTIMATED` one. `NotReconciled.reason` is never empty.

Each outcome carries its **attribution** — which source delivered how many tokens — computed once in `assemble`.
`attributed`, `unattributed` and `excess` are derived from it (and written to JSON beside it, `serialize.to_data`'s
`DERIVED`), so there is one sum per outcome and no second one to pair it with. What the attribution holds depends on
the outcome: for `Reconciled` and `Overcounted` it is what the anchor session itself delivered (plus files on disk);
for `NotReconciled` it is each source's latest observation (`assemble.latest_attribution`), which may come from
different sessions. Readers — token-budget's breakdown, the UI's budget bar — group that tuple by mechanism or by
authority and never sum the sources again: summing each source's latest tokens and showing the result beside the
anchor session's remainder was the mixing below, re-introduced by two readers before 2026-10-03.

Illustrative output (the attributed/remainder split is not yet measured):

```
ALWAYS-ON  43,132 tokens   (observed, session 867de26a, 2026-10-02)
  40,900   attributed across 240 sources (≈, estimated from text)
   2,232   harness built-in we could not see
```

The date is the session's start in local time, the day the UI shows that session on (UTC before review round 12).

A reader who sees "we can account for most of this" trusts the tool more than one who sees a
suspiciously tidy total. The total is reconciled against what the **same session** delivered (plus files on disk),
not against each source's latest observation — an interactive session's tool set differs from a
headless one's, and mixing them overcounted by 6.9% before this rule. VERIFIED 2026-10-02 on a clean
2.1.285 session: 44,280 observed, 44,120 attributed, 160 (0.4%) unattributed. `Overcounted` is surfaced, never hidden: a negative remainder would be a
confident lie.

The harness's own prompt and tools carry authority `harness`, never `managed`: `managed` is the
organisation's policy (77 tokens here), and labelling the harness's ~19k tokens as `managed` would
make org policy look like it costs 40% of the budget. Observations prefer interactive sessions —
headless runs get a smaller tool set; "interactive" is the same rule usage counts by (08-recorder.md) — and entities only a session can show are the union across
sessions on the harness version, so a headless run cannot make an interactive tool look missing. The observed total is `input + cache_creation + cache_read` of the first model call,
minus the user's own first message — `cache_creation` alone undercounts on a warm cache.

### Activation — when it's in context

```python
@dataclass(frozen=True)
class Activation:
    load: Load
    condition: str | None = None   # required for CONDITIONAL

class Load(Enum):
    ALWAYS          # from the first turn of every session
    PER_MESSAGE     # re-injected with every user message; cost grows with the conversation
    ON_INVOKE       # when the entity it defines is invoked
    CONDITIONAL     # when a condition holds, e.g. before a particular tool call
    NOT_IN_CONTEXT  # configures the harness; the text itself never reaches the agent
```

`NOT_IN_CONTEXT` exists because settings files are sources — their entries change behaviour and can
be ablated — but their JSON is never sent to the model, so it must not count toward the budget. Text
embedded in a settings file that *is* delivered (a managed instruction string) is its own source.

A skill's description reaches the agent through the harness-generated listing, which is one
`HARNESS_GENERATED` source; its body is a separate `ON_INVOKE` source. Counting each description as
its own always-on source as well would double-count it — and on this machine most descriptions are
never delivered at all (Verified Facts: 438 of 506 listing entries are bare names).

**No usage data here.** `Activation` describes what the configuration *declares*. What actually
happened is observed data, lives in a separate index keyed by id, and is looked up by the
ranker. Three reasons to keep them apart:

- Config reading must work with no recorder data at all. Embedding usage couples two components
  that should be independent.
- The fingerprint hashes declared configuration. If usage lived there, usage changes would perturb
  the fingerprint and invalidate cached verdicts for no reason.
- Declared and observed are different kinds of claim. Mixing them is how the old flat `Source`
  became a god-object.

See `08-recorder.md` for `EntityUsage` and why it stores counts rather than a rate.

### Content — what we know about the text

Exactly one of four states. Each is its own class, so there is no empty string meaning "unknown"
and no zero meaning "unmeasured".

```python
Content = FileText | ObservedText | SizeOnly | NotObserved

class FileText:        # read from disk: deterministic, addressable, excisable
    format, text, content_hash, tokens: TokenCount[ESTIMATED], required_keys
    regions(), region(), excise(), validate()

class ObservedText:    # not on disk; what recent sessions show the harness delivered
    format, text (most recent), content_hash, tokens: TokenCount[ESTIMATED],
    seen_in: tuple[SessionHeader, ...]   # non-empty, most recent first
    sessions_examined: int            # the denominator
    regions(), region()               # no excise(): you can't edit what a command prints

class SizeOnly:        # the size is known, the text is not
    tokens: TokenCount[OBSERVED | DERIVED], reason

class NotObserved:     # declared on disk, absent from every session examined
    sessions_examined, reason

class ContentFormat(Enum):
    MARKDOWN | JSON | YAML | FRONTMATTER_DOC | PLAIN
```

Every count says how it was obtained, because the same number means different things:

```python
class TokenMethod(Enum):
    ESTIMATED   # counted from text with the approximate tokenizer
    OBSERVED    # the harness's own usage numbers, from a session
    DERIVED     # an observed total minus everything we could attribute

@dataclass(frozen=True)
class TokenCount:
    value: int          # >= 0, enforced
    method: TokenMethod
```

Addressing and excision are **methods on the text states**, dispatched by format. There is no
generic "delete a character span" operation, because for three of the five formats that produces a
corrupt artifact.

### AblationPolicy

```python
class Ablation(Enum):
    FILE_REMOVAL      # drop the file from the variant's config tree
    SPAN_EXCISION     # rewrite the file in the variant with a region removed
    SETTINGS_DISABLE  # flip or remove an entry in the variant's settings
    NONE

@dataclass(frozen=True)
class AblationPolicy:
    mechanism: Ablation
    blocked_reason: str | None = None       # required exactly when mechanism is NONE
    linked_sources: tuple[str, ...] = ()    # removing this necessarily removes these
```

`linked_sources` exists because removing a skill removes both its description and its body. The
runner must not claim to have ablated one independently of the other. No adapter fills it yet, so
it is always empty and nothing reads it: it is kept because the report serialises it
(`ablation.linked_sources` in `ui/src/contract.ts`). Claude Code's listing is one source per
package, so a skill's listing entry is part of a source, not a whole one, and linking the two would
overstate what removing the skill removes. Fill it when the runner needs it, with a test.

---

## Rule

```python
@dataclass(frozen=True)
class Rule:
    id: str                # computed: "<source id>#<locator key>"
    source_id: str
    locator: Locator       # format-specific address inside the source
    text: str              # the excerpt, for display and for probe generation
    tokens: TokenCount     # computed: ESTIMATED from text
    content_hash: str      # computed from text
    start_line: int        # 1-indexed, inclusive, within the source's text
    end_line: int          # >= start_line
```

`id`, `tokens` and `content_hash` are `field(init=False)`, computed in `__post_init__` from the
other fields, so a rule cannot carry another text's hash. They are still written to JSON, and
computed again when read back.

`Source.rules()` yields rules only for text states (`FileText`, `ObservedText`). A source whose text
isn't known has no rules to divide.

### Locator

Not a char span. A format-appropriate address.

```python
@dataclass(frozen=True)
class Whole: ...                                    # the entire source, atomic

@dataclass(frozen=True)
class Body: ...                                     # a frontmatter document's body

@dataclass(frozen=True)
class MarkdownSection:
    heading_path: tuple[str, ...]                   # ("Memory", "Routing"); () = preamble
    occurrence: int = 0                             # disambiguates repeated heading paths; >= 0
    until_level: int | None = None                  # a heading level, 1-6; see below

@dataclass(frozen=True)
class LineRange:
    start: int                                      # 1-indexed, inclusive; >= 1
    end: int                                        # >= start

@dataclass(frozen=True)
class KeyPath:
    path: tuple[str | int, ...]                     # ("hooks", "UserPromptSubmit", 0)

Locator = Whole | Body | MarkdownSection | LineRange | KeyPath
```

Locators are **stable against unrelated edits** where possible — a heading path survives text
changes elsewhere in the file, a char span does not. That matters because rule verdicts are cached
across runs.

Each locator has a stable string form (`locator_key`, used inside rule ids: `section:/Memory/Routing`,
`key:/hooks/UserPromptSubmit/0`, `lines:3-7`, `body`, `whole`) with percent-encoded segments so no
heading or key text can collide with the separators. Implemented in `src/agentlint/config/locators.py`.

**Markdown sections, precisely.** A section runs from its heading to the next heading whose level is
at most `max(own level, until_level)`; `until_level=None` gives the default "next heading of same or
higher level". Coarse rules cut the document at the **shallowest heading level that occurs at least
twice**, so a lone `# Title` doesn't swallow the file into one rule — the title's own text becomes the
first rule (`until_level` set to the cut level) and each section at the cut level is a rule with its
subsections. Fine rules cut at every heading. Either way the rules **partition** the document: their
texts concatenate back to the original. `until_level` is pinned at extraction rather than inferred
from neighbouring headings, so a locator doesn't change meaning when an unrelated heading is added.
Headings inside fenced code blocks and a leading `---` frontmatter block are ignored; only ATX
headings are recognised (an unrecognised heading makes rules coarser, never wrong).

---

## How excision works, per format

This replaces the old global "granularity" question. Granularity isn't one choice; it's per format.

| Format | Default rule unit | Excision | Validity check |
|---|---|---|---|
| `MARKDOWN` | `MarkdownSection` — heading to next heading of same or higher level | Delete those lines | Always valid. Markdown tolerates missing sections. |
| `PLAIN` | `LineRange` — blank-line-separated paragraph | Delete those lines | Always valid |
| `JSON` / `YAML` | `KeyPath` | **Delete the key**. JSON is re-serialised (indent preserved); YAML prefers a minimal textual edit and falls back to re-serialising | Re-parse; result must equal the original with exactly that key removed; required keys survive; assert the harness still starts |
| `FRONTMATTER_DOC` | `KeyPath` into frontmatter, or `Body` | Remove the key's lines from the frontmatter (every other byte unchanged), or blank the body | Re-parse frontmatter; required fields (`name`) survive |
| `HOOK_OUTPUT` (any format) | `Whole` only | **Not text excision.** Remove the hook's config entry from the variant's settings | The hook must not fire in the variant |
| `HARNESS_BUILTIN` | none | Not addressable | — |

Three consequences worth stating explicitly:

**Excision must produce a valid artifact.** Deleting a text span from JSON yields invalid JSON and
the harness won't start — you'd measure a crash, not a rule's absence. `Content.validate()` runs
after every excision and raises.

**"Required keys survive" means present-before implies present-after**, not "must exist". VERIFIED
2026-10-02 on this machine's 1,309 frontmatter files: 66 entity files have no `name` key at all (the
harness takes the name from the directory), and a "must exist" check refused every excision on them.

**YAML is minimally edited, not re-dumped, where possible.** Re-serialising can change how a
different YAML parser reads untouched values (`enabled: yes` becomes `true`). The textual edit is
verified semantically; re-dumping is the fallback for flow-style collections.

**Strict YAML is stricter than the harness.** 2 of 1,309 frontmatter files on this machine fail to
parse as YAML (an unquoted `argument-hint: [path | N<number>]`) yet ship in installed packages. The
core reports a `FormatError`; how to read such a file is an adapter decision, because it must mirror
what the harness actually does with it.

**Coarse by default.** Removing one sentence from a paragraph often produces incoherent prose, and
then you're measuring incoherence rather than the rule's absence. Section-level for markdown is
the default for exactly this reason. Finer granularity is available but opt-in.

**Hook output is atomic.** You can't remove part of what a hook injects; you remove the hook. This
means a hook that injects five unrelated instructions is one indivisible rule — which is itself
worth reporting as a finding, since it makes the hook's contents untestable individually.

### Interesting case: ablating a skill's description

For `FRONTMATTER_DOC`, `KeyPath(("description",))` removes the trigger text while leaving the skill
installed. That answers a question nothing else can: **does selection follow the description or the
body?** Cheap, and directly relevant to the trigger-collision thesis.

---

## ConfigSnapshot

Built only by `assemble(declared, sessions)` in core; adapters never construct it directly.

```python
@dataclass(frozen=True)
class ConfigSnapshot:
    harness: str
    harness_version: str | None          # None when neither disk nor sessions say
    model: str | None
    cwd: Path
    sources: tuple[Source, ...]
    entities: tuple[Entity, ...]
    untraced: tuple[Untraced, ...]       # delivered text that traces to nothing on disk
    sessions: tuple[SessionHeader, ...]  # every session examined, most recent first
    always_on: AlwaysOn                  # Reconciled | Overcounted | NotReconciled
    config_hash: str                     # declared sources only: "did the configuration change?"
    fingerprint: str                     # + harness_version + model: "can results be compared?"
```

`Untraced` is text a session delivered that the adapter could not trace to anything declared — on
this machine, e.g., hooks from a package a launcher enables per invocation. It was in context and
cost tokens, so it is counted; it is not declared configuration, so it never enters the fingerprint.
Its `id` (computed on construction, like a rule's; 12 hex digits of a SHA-256 over the point's harness and name and
its `key`) follows from where it arrived and that key — what `assemble` groups deliveries by — never from its place in
the list, which changes between readings; the snapshot checks the ids are unique. The key is the delivery's
`Delivery.key`, which the adapter sets so that it does not vary from reading to reading; empty, the label stands in
(and does, for snapshots written before the key existed). The label is for reading — the Claude Code adapter's names
the text's opening line and how many hooks it might be, both of which change — so it is not the key: untraced hook
output is keyed by its event and recorded command, so its id holds while what it prints changes. The recorded
command is the output's own for plain stdout and, for JSON `additionalContext`, that of the run whose stdout carried
it: Claude Code records such a hook's run with its command and the JSON on stdout (VERIFIED 2026-10-04 read-only on
this machine's transcripts: all 172 SessionStart `additionalContext` texts in the latest 400 matched a recorded run's
command), the same match that traces it (below). Until round 12 the key took only the output's own command, which
JSON `additionalContext` never has, so a JSON hook's id changed with its text while a plain-text hook's held. Output
with no recorded command (async responses, every UserPromptSubmit — 68 of 68 had no recorded run), and output whose
command several hooks the reading configures for its event share — two packages' hooks are both recorded as
`${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh`, in all 19 sessions with hook runs on this machine, and a command they
share names neither — has nothing but its text to tell it from other untraced text, so its key is its event, channel
and a digest of the text, whitespace aside, and its id holds while the text does. Whether a command is shared is
counted from the configuration, which is the same in every session of a reading, never from which hooks printed in
one session: so a hook keeps one key whether or not the other printed beside it, and two hooks that share a command
are told apart in every session alike. A command one configured hook has (untraced because its matcher did not
accept how the session began, say), or none has (a hook from a package not on disk), is known by itself — the
configuration cannot say it is shared. Round 12 decided it per session, from the outputs that session's untraced
hooks ran: a hook that printed alone took the command's key, so with A and B sharing a command, A printing in
sessions 1 and 4 and B in 1–3, B alone in sessions 2–3 and A alone in session 4 shared one id, "seen in 3 of 4"
(round 13). Neither key holds the transcript's `hookName`: past the event it names what fired the
hook — for SessionStart, how the session began (`startup`, `clear`, `resume`, `compact`) — which changes from session to
session while the configured hook does not; with it in the key the same text got a new id after a `/clear`, and
`seen_in`, which counts sessions by key, missed the sessions that began another way (round 11). The untraced text shows the label of the latest session's delivery. `id` is written to JSON with the
other fields (`key` too) and computed again on reading, so the report's session contexts name untraced text by id
and the UI links to it as `#/config/untraced/<id>`: a link survives reading again (built 2026-10-04, design review
4.2; until then the UI addressed it by its index in the list. Until round 10 the key was the label, so installing
one more hook for the event, or output opening with a date, gave the same text a new id).

---

## Entity

A named thing that rules refer to. This registry is what stops rule analysis from inventing
vocabulary — rules point at resolved IDs, never free text.

```python
@dataclass(frozen=True)
class Entity:
    id: str                      # unique (checked by Declared and ConfigSnapshot), adapter-defined
    name: str                    # what the agent invokes it by
    kind: EntityKind             # adapter value object, see below
    provided_by: str             # source id; must resolve
    description: str | None = None
    aliases: tuple[str, ...] = ()  # other names it goes by; the report serialises it
    # short_name: the name without what provides it ("pkg:name" -> "name"), by the kind's separator

@dataclass(frozen=True)
class EntityKind:                # same pattern as InjectionPoint
    harness: str
    name: str                    # "skill", "agent", ...
    label: str
    role: EntityRole             # core: TOOL | SKILL | AGENT | COMMAND | PATH | DOMAIN
    namespace: str               # kinds chosen from one list share it; names collide only within one
    separator: str | None = None # how the harness prefixes a name with its provider (":" in Claude Code)
```

**An id names one entity, never two.** `Declared` and `ConfigSnapshot` (after session entities merge in) both
refuse a duplicate id, as they refuse a duplicate source id. An adapter builds its ids in one place, and the id
carries the entity's scope, because two scopes can define one name: in Claude Code a user skill `deploy` and a
project skill `deploy` are `skill:user:deploy` and `skill:project:deploy` (`points.entity_id(kind, scope,
name)`: kind, then `package`, `user`, `project` or `session` — known only from a session — then the name, so
`skill:package:pk:deploy`, `tool:session:Bash`, `mcp_tool:session:mcp__srv__x`). Before 2026-10-03 both were
`skill:deploy`: one entity's file stood in for the other's, and duplicate-name could call a copy "different bodies"
with `certain` confidence. A listing shows names, not scopes, so what a session showed of a name applies to every
entity on disk of that kind and name. Nothing persistent holds an entity id: finding identities use names, and
`show --diff` matches entities by the source, kind and name that define them, so the new form is no change.

The separator is how core tells an entity's own name without knowing any harness's naming convention:
duplicate-name and trigger-overlap compare `short_name`s, never split names themselves.

Aliases are **never model output**. An adapter sets one where the harness's own naming gives an entity a second
name a person would use: in Claude Code a server's tool `mcp__<server>__search_files` has the alias `search_files`
(2026-10-04), so the contamination guard (`04-probe-generator.md`) catches a situation that says "use search_files".
It is an alias and not a separator because two servers' `search_files` are two tools the agent tells apart by their
full names, not namesakes for duplicate-name to report. Beyond that, aliases are hand-maintained registry data that
grow by deliberate addition when an unresolved reference recurs.

**Some entities are only knowable from sessions**: the harness's tools, tools a connected server
exposes, and entities built into the harness binary (13 skills and 5 agents on the reference
machine). Adapters report them in `SessionDeliveries.entities`; `assemble()` merges them from the most recent
session on the same harness version, declared entities winning on id, and each must name a declared
source (the tool definitions, or the listing that showed it).

---

## Ablatability: who sets it

Three layers. Each can only make a source *less* ablatable, never more.

**1. The adapter, structurally.** Derived from mechanism and authority against the fixed table
below. Never inferred from text, never guessed by a model.

**2. The user, optionally.** A denylist among your settings (the database's `settings` table, `00-overview.md`;
not built), e.g. `never_touch = ["~/.claude/CLAUDE.md", "managed:*"]`.

Someone may not want a file modified even inside a disposable copy. Forces `NONE` with reason
`"excluded by user config"`.

**3. The runner, empirically.** The adapter's answer is a claim, not a fact — see below.

### Odd bytes never drop a file

Configuration is decoded leniently: a byte that isn't UTF-8 becomes U+FFFD, and a JSON escape for a lone surrogate
(which no filesystem can name) becomes U+FFFD too. Dropping such a file instead turned its hooks' output into
"untraced" findings and its skills into "missing" ones, and a lone surrogate in a path made every command crash
(sixth review round, 2026-10-03). A path that still can't be looked up (a NUL byte) makes no claim either way.

### Claude Code table

| Mechanism | Ablation | Notes |
|---|---|---|
| `HARNESS_BUILTIN` | `NONE` | Not on disk; text partly observable in sessions; not removable |
| Managed `CONFIG_FILE` | `NONE` | Applies regardless of `HOME`. VERIFIED: `claude --help` — "managed settings and --settings still apply" |
| User/project `CONFIG_FILE` | `SPAN_EXCISION` (key path) | Inside the copied tree |
| `INSTRUCTION_FILE` | `SPAN_EXCISION` (section) | Rewrite the copy |
| `PACKAGE_ENTITY` | `FILE_REMOVAL`, or `SETTINGS_DISABLE` if supported | Settings-level disable **UNVERIFIED** — Open Question 6 |
| `HOOK_OUTPUT` | `SPAN_EXCISION` of the hook's entry in its settings or hooks file | Removes the whole hook: its output can't be cut, so the ranker plans a hook as one ablation target (`03-ranker.md`) |
| `HARNESS_GENERATED` | `NONE` | Composed by the harness from what's installed; ablate the underlying package instead |

### Verification is mandatory

```python
baseline = read_variant(adapter, base.home.root, base_workdir, project)
without = read_variant(adapter, variant.home.root, workdir, project)
verify(target, baseline, without)     # raises AblationFailed
```

Raise, don't warn, **before** the first run of that variant (`src/agentlint/runner/verify.py`; details in
`05-runner.md`, "Verify the variant before running against it").

A variant that silently failed to ablate is identical to the baseline. Every run measures nothing,
the distributions match, and the tool confidently reports *"this rule has no effect."* That false
negative is indistinguishable from a real finding and would be acted on. One config read prevents a comparison's
worth of runs producing a confident lie.

Sources are compared by what they stand for, not by id: an instruction file's or settings layer's id embeds its
absolute path, so a check that the target's id is absent from the variant — this section's first version — passes
whether or not anything was removed. Each source is matched by its injection point, its file with the variant home
mapped back to the real home and the run's directory to the project, and its anchor, with its `declared_hash`; a
rule by that plus its locator and text.

---

## Which sessions are read

The `sessions` setting (`--sessions`, 0-1000; anything else is a usage error, exit 2) says how many recent sessions are read
for what disk can't show, by every command — `rank` and `usage`, which have no `--sessions`, read the setting
(2026-10-03; before, they always read 10). They are the
project's own most recent sessions first, then the most recent elsewhere: a project picked in the UI gets evidence
from its own sessions even when others ran since (2026-10-03; before, the most recent overall, so an older project
got none). Project-scoped sources only learn from that project's sessions either way.

## Claude Code enumeration

**VERIFIED 2026-10-02 with a listing oracle**: the names disk enumeration produces must equal the
names a clean session's harness delivered. Against two independent clean sessions (2.1.285 in
`/private/tmp`, 2.1.284 in another project), skills + commands + workflows matched 493 of 506
delivered names and agents 45 of 50. The residual is fully explained: 13 skills and 5 agents are
built into the harness binary (no file on disk), and 2 skills were installed after the oracle
session. Implemented in `adapters/claude_code/discovery.py` and `entities.py`; rules pinned by
`tests/adapters/test_claude_code_discovery.py`. The oracle is the reader self-check, re-runnable with `uv run python scripts/selfcheck.py`
(`adapters/claude_code/selfcheck.py`): it explains every difference mechanically — defined in the harness bundle, defined
nowhere on disk, or installed after the oracle session — and exits non-zero on anything unexplained. Re-run after
every harness update.

**Settings layers**, in the order the harness applies `enabledPlugins` (`true` enables, `false`
disables):

| Layer | Where | Authority |
|---|---|---|
| add-dir | `<dir>/.claude/settings.json`, `settings.local.json` for each additional directory — settings' `permissions.additionalDirectories` (a leading `~` is the home being read, never this process's `HOME`, so a variant's reading stays in the variant), and the launcher's preset (`~/.claude/acme/preset`, rewritten on every launch) | user |
| managed | `/Library/Application Support/ClaudeCode/managed-settings.json` | managed |
| user | `~/.claude/settings.json` (there is no user-level `settings.local.json`; `~/.claude/settings.local.json` is only read as project-local when cwd is home) | user |
| project | `<cwd>/.claude/settings.json` | project |
| local | `<cwd>/.claude/settings.local.json` | project |

Settings files are sources with load `not_in_context`. The launcher's `--settings` flag layer is not
on disk and is not read.

**Packages** resolve through marketplaces, never `installed_plugins.json` (whose `installPath`s are
absolute and can point at directories that don't exist): marketplace directories come from
`~/.claude/plugins/known_marketplaces.json` plus `extraKnownMarketplaces` in any layer (`~` there, too, is the home
being read), and a
package's root is the marketplace directory joined with its `source` in
`.claude-plugin/marketplace.json`.

**A copied home reads its own copy of a marketplace** (`discovery.in_copy`, since 2026-10-04). A variant home is a
copy of `~/.claude`, so its `known_marketplaces.json` still records each `installLocation` by absolute path in the
home it was copied from (here `/Users/<you>/.claude/acme-market`). Under a substituted HOME the harness reads the
package from the copy's marketplace checkout, not from that path and not from the plugin cache (Verified Facts, E2
and E2b, 2026-10-04). So a reader of a copied home (`Paths.copied_from`, set by `at_home`) reads a location recorded
under the original home's `.claude/` at the same relative path under its own `.claude/`. Either side may be
spelled through a symlink, so `/tmp` and `/private/tmp` count as the same place whichever of the two the original
home is given in and the location was recorded in. The location is first matched as written against the original
`.claude/` as written and resolved. Failing that, its shallowest ancestor that resolves to the same place as the
original `.claude/` stands for it. Only that ancestor is resolved: a link inside the marketplace (say `market` ->
`real-market`) doesn't change the relative path the copy is read at (`market`). Both directions are tested. The
matching is `discovery.under_config`. A variant's cut of a package's hook applies it to the same recorded location,
never to the resolved file read, so in every layout mapped here the cut edits the very file read here, and a hook
whose location isn't mapped is refused (05-runner.md, "Cutting the target out"). The mapping moves only the
marketplace's directory: a path the package's own manifests name absolutely — its `source` in `marketplace.json`, or
a `hooks` item in its `plugin.json` — is joined to the copy's directory as the absolute path it is, so a copied home
reads that file where it is written, in the original home, and a cut of a hook read there is refused too.

- **Only that case is mapped.** It is the one E2b verified. A location anywhere else (under the original home but
  outside `.claude/`, or outside the home altogether) and an absolute `extraKnownMarketplaces` path are still read
  where they are written. What the harness does with those under a substituted HOME is UNVERIFIED. A variant lists
  what it reads there as escapes (05-runner.md).
- **No copy, no reading.** Suppose the mapped directory doesn't exist in the copy and an enabled package resolves
  through it. Then the reading raises `MarketplaceNotCopied` (a `VariantError`), naming the path it looked for and
  the recorded one. It never falls back to the original home: the harness reads the copy's, and what it does when
  there is none is UNVERIFIED. A refused variant is not run, so nothing gets measured against a configuration the
  reader can't vouch for. A missing marketplace that only disabled packages name refuses nothing.
- **Reading a home as itself is unchanged.** `copied_from` is None, so every location is read where it is
  written. Checked 2026-10-04: `agentlint check --json` and `show --json` on this machine gave byte-identical
  output before and after the change, each run with its own throwaway `AGENTLINT_HOME`.

**Entities** inside each enabled package root (and in `~/.claude` and `<cwd>/.claude`, with bare
names):

| Entity | Where | Delivered name |
|---|---|---|
| Skill | immediate children of `skills/` holding `SKILL.md`, plus manifest `skills` paths (supplementing the default; a listed path holding `SKILL.md` is itself a skill, even `./`). Nothing deeper loads. | `<package>:<directory>` — not the frontmatter `name`, except a `SKILL.md` directly in a listed `skills/` root |
| Command | `commands/**/*.md` plus manifest `commands` paths | `<package>:<path segments joined by ':'>` |
| Workflow | `workflows/*.workflow.js` — content format `code`: addressed whole, never divided into rules; load `not_in_context` (the harness runs it; only its listing entry reaches the agent) | `<package>:<meta name>` |
| Agent | manifest `agents` paths, which *replace* the default `agents/` | `<package>:<sub-directories>:<frontmatter name or stem>` |
| Built-in skills and agents | inside the harness binary | known only from sessions |

`disable-model-invocation: true` keeps a skill or command out of the model's listing; a command
also needs a description. Frontmatter is read strictly, then leniently (top-level `key: value`
lines) where strict YAML fails, because the harness accepts files strict YAML rejects.

**Instruction files** (`adapters/claude_code/instructions.py`) — VERIFIED 2026-10-02: the delivered
paths of all 9 local sessions with an `instructions` attachment match. Run-verified cases: managed
`claudeMd`, a project `CLAUDE.md`, the `AGENTS.md` fallback, the auto-memory index. From the 2.1.285
loader but not run-verified (no such files here): ancestors, `.claude/CLAUDE.md`, `CLAUDE.local.md`,
`rules/`, the managed directory.

| Label | Where |
|---|---|
| Managed | `claudeMd` in managed settings; `CLAUDE.md` and `.claude/rules/**` in the managed directory |
| User | `~/.claude/CLAUDE.md`, `~/.claude/rules/**` |
| Project / Local | each directory from cwd up to (not including) `/`: `CLAUDE.md`, `.claude/CLAUDE.md`, `.claude/rules/**`; `CLAUDE.local.md` (Local) |
| Project | `AGENTS.md` or `.claude/AGENTS.md` at cwd, only when cwd has no `CLAUDE.md` of its own |
| AutoMem | `~/.claude/projects/<cwd slug>/memory/MEMORY.md`, fixed at session start |

Not yet handled: `@path` imports.

**Hooks** (`adapters/claude_code/hooks.py`). A source is one configured hook entry — 206 here, 45 of
them SessionStart from 34 packages — declared in managed settings, any settings layer, a manifest's
inline `hooks`, a file the manifest names, or `hooks/hooks.json`; a `{"modules": [...]}` file is one
opaque entry. `disableAllHooks` / `allowManagedHooksOnly` in managed settings apply. Load by event:
SessionStart `always`, UserPromptSubmit (and UserPromptExpansion) `per_message`, tool events `conditional`,
everything else `not_in_context` (its output never reaches the model — VERIFIED in the 2.1.285 bundle). That
is one table, `hooks.EVENTS` — each event's load, injection point, and whether its plain stdout is added to
context — read through `event_load()` / `event_point()` by a configured entry, by the text a session shows
whether it traces to an entry or not, and by the transcript reader. (Before 2026-10-03 untraced text from any
other event was `conditional` while its entry was `not_in_context`; now tracing never changes a text's load.
VERIFIED 2026-10-03 on the reference machine: of 3,466 `hook_additional_context` and `async_hook_response`
attachments, every one carrying text came from a SessionStart, UserPromptSubmit or tool event.)

Delivered hook text is traced to its entry, never guessed: (1) JSON `additionalContext` matched
against recorded executions' stdout, whose command names the entry; (2) a recorded (event, command)
pair, filtered by the run's match target (`SessionStart:startup`); (3) line openings of the text
found in exactly one candidate's command, scripts, or package files. Without a recorded execution
(UserPromptSubmit records none), attribution requires a text match even when one candidate is left.
VERIFIED 2026-10-02 on all 17 sessions with hook output: 92.8% of delivered hook text traced. What
remains is genuinely ambiguous — the same text shipped by two packages, or text fetched at runtime
that exists nowhere on disk — and is counted as `Untraced` with its candidates.

### Dynamic sources

Hook output and the harness's built-in text cannot be read from disk — only observed. The adapter
declares each from disk with `NotObserved` content (a hook from its configuration; the built-in from
the harness), and returns the last N sessions as `SessionDeliveries` records. `assemble()` then fills in text
from the most recent session that delivered it, recording every session it was seen in:

- **Project-scoped** sources learn only from sessions whose working directory is this project.
- **Harness built-in** text learns only from sessions on the same harness version.
- Everything else learns from any session examined.
- Delivered text that traces to no declared source becomes `Untraced`, from this project's most
  recent session only.

N is the `sessions` setting (default 10), or `--sessions` over it (its old name, `--history`, went 2026-10-04); `read_config(sessions=)` has no default of its own, so every caller passes it. Reading sessions never executes the harness.

Only sessions that started at or after the store's forget cutoff count (`read_config(forgotten_before=)`, which
every command passes from the store; `08-recorder.md`, "Forgetting"): the adapter skips the others — a transcript
last written before the cutoff without opening it, one written since by its start time, and one whose start can't
be told — and still returns up to N, and core drops any earlier session an adapter returned anyway.

VERIFIED: transcripts carry these as attachment types `hook_additional_context`, `hook_success`,
`hook_system_message`, `async_hook_response`, `skill_listing`, `agent_listing_delta`,
`deferred_tools_delta`, and `prompt_snapshot` (system prompt and tool list).

**Sessions are also ground truth for the reader.** Reading files tells us what *should* load; a
session shows what *did*. Comparing the two runs as a **reader self-check** — a mismatch is our bug
until proven otherwise — and stays deferred as a user-facing check (see `docs/03-checklist.md`).

---

## Token counting

Every count is a `TokenCount` that says how it was obtained: `ESTIMATED` from text, `OBSERVED` from
the harness's usage numbers, or `DERIVED` by subtraction. The harness tokenizer isn't available
offline, so text is estimated and labelled approximate (`≈`): it is split into tokenizer-like pieces
(letter runs with long words split, digits, punctuation, whitespace runs, CJK characters) and scaled
by `TOKENS_PER_PIECE = 1.22` (`src/agentlint/config/tokens.py`).

**Calibration, VERIFIED 2026-10-02** against two independent sets of exact usage numbers from local
sessions (`scripts/calibrate_tokens.py` re-measures):

| Data | n | tokens per piece |
|---|---|---|
| First turns of clean sessions (listings, tool schemas, hook text) | 6 | median 1.222, IQR 1.217–1.226 |
| Tool-result deltas between consecutive calls (code, files, command output) | 463 | median 1.249, IQR 1.16–1.34 |

Characters-per-token was rejected: 2.82 on first turns versus 2.25 on tool results, a 25% swing by
content type, where pieces hold within 2%. Per-source estimates are typically within about ±7%, the
always-on total within about 2%. The deltas also showed that thinking stays in context within a
tool loop (assuming it is stripped scatters the ratio across 1.3–2.7). `tiktoken` is not used: it
needs a network download and measures a different tokenizer.

Because the estimate is only good to a few percent, a reconciliation remainder (or an `Overcounted`
excess) within ~3% of the observed total is estimation noise, and the report must say so rather than
present it as unseen content.

The budget is **one total, harness built-in included**: everything attributed, plus the remainder
derived from the observed total (see "The harness built-in" and `AlwaysOn`). The observed total is
`input + cache_creation + cache_read` of a session's first model call, minus the user's own first
message — never `cache_creation` alone, which undercounts on a warm cache. Only sources whose load is
`ALWAYS` or `PER_MESSAGE` count toward it; `NOT_IN_CONTEXT` sources never do.

## Config fingerprint

Hashes **declared configuration only** — never text observed in sessions, which changes every
session (hook output, git status) with no config change.

```python
def config_hash(sources) -> str:
    return sha256(json.dumps(sorted(
        [s.id, s.declared_hash, s.provenance.authority.value, s.activation.load.value]
        for s in sources
    )))

def fingerprint(config_hash, harness_version, model) -> str:
    return sha256(json.dumps([config_hash, harness_version, model]))
```

Implemented in `src/agentlint/config/assemble.py`. Keeping `config_hash` separate means "the
configuration changed" and "the harness or model changed" stay distinguishable — resume reports
which one. Per-source `declared_hash` values are retained, so two snapshots can be diffed to find
exactly which sources changed — that's what drives selective re-testing.

Comparing artifacts across different fingerprints without explicitly intending to should raise,
not warn.

## CLI

```
agentlint show                  # assembled config, ranked by token cost
agentlint show --json           # the full snapshot (schema_version 3, `SNAPSHOT_FORMAT_VERSION`) — the shared data contract
agentlint show --always-only    # just what's loaded on every session
agentlint show --rules <source> # rules inside one source, with locators and token costs
agentlint show --all            # every source, not just the 25 largest
agentlint show --cwd <dir> --sessions <n>
agentlint show --diff [<fp>]    # what changed since this project's previous configuration (or a given one)
agentlint show --diff --text    # … with the changed lines of each text
agentlint show --snapshots      # the stored configurations of this project
```

Every `show` and `check` at the default `--sessions`, and every page load of `agentlint ui` (2026-10-04), stores the
configuration it read (`store/snapshots.py`: each distinct text once, in the agentlint database; an unchanged
configuration replaces its own row, so reading it again adds no snapshot); a reading with another window sees other evidence, so it
isn't stored, and `--diff` refuses to run with one. The UI's Configuration › What changed lists the same snapshots
and shows the same comparison (`ui/records.py`, `11-ui.md`). `--diff` compares with the most recent stored configuration
of the same project whose fingerprint differs (`report/diff.py`). The stored configuration is read back into the
model (`serialize.from_data`, texts taken back in from the store), so the diff applies the model's own rules — the
labels `show` gives, `Load.in_first_turn`, a source's tokens, `assemble.attributed_latest` — not copies of them. One stored in
a snapshot format this agentlint can't read back (`SNAPSHOT_FORMAT_VERSION`, the JSON's `schema_version`) is said
to be unreadable and isn't compared; `--snapshots` lists it as such:

- sources **changed** (declared hash differs), **added** and **removed**, each with its token change and load;
- apart, sources in context every session whose configuration is unchanged but whose delivered text differs —
  a hook printing something new, or one **newly observed** / **no longer observed** because a session showed
  its output on one side only (its change in size is then `?`) — and untraced always-on text, grouped by
  identity and summed over the labels it arrived under, compared only when both readings had a session from
  this project (otherwise it is left out of both figures, and the diff says so);
- entities added and removed, **only those defined on disk**: tools and built-ins known from sessions come and go
  with the sessions read and the harness version (after an update, before any session on it, they would all
  read as removed);
- the always-on figure, attributed the same way on both sides (what is in context before the first words by
  `Load.in_first_turn`, the rule every reader uses), with the part of its change that comes from text
  seen only then or only now, and a note of how many always-on sources had no observed text; a token count that
  isn't known shows as `?`, never as 0.

VERIFIED 2026-10-02 on a throwaway project with the real adapter: editing its `CLAUDE.md` and adding a skill showed
the edit (with the added line under `--text`), the new skill and the new entity; a simulated harness update in the
tests no longer reports observed tools as removed. Each new snapshot costs ~160 KB here plus changed text; saving
an unchanged one takes ~45 ms.

Implemented in `src/agentlint/cli.py` (composition root), `report/show.py` (rendering) and
`config/serialize.py` (JSON, with `"type"` tags on union members; `from_data` reads it back into a
`ConfigSnapshot`, driven by the same field types, and `as_model` any other model dataclass — a stored finding; the
live transcript reader's conversations carry `"type"` tags on their entries too, though nothing reads them back).
The JSON format version is `SNAPSHOT_FORMAT_VERSION`; the database's own is `DB_SCHEMA_VERSION`, an unrelated
number. Version 2 (2026-10-03) replaced the always-on outcome's stored `attributed`/`unattributed` with its
`attribution`, which a version-1 snapshot can't supply, so snapshots stored before then read as unreadable.
Version 3 (2026-10-04) replaced a source's `provenance.locator` with `file` and `anchor`, and a finding location's
`path` with `source_id`: version-2 snapshots read as unreadable the same way, and database schema 11 drops the
locations of stored findings (nothing reads them back; the next check rewrites each open finding whole). The always-on total is stated as
far as the data allows: observed and reconciled, observed but overcounted, or not observed with the
reason. The on-invoke section lists the largest definitions rather than a meaningless sum. On the
reference machine one slash command's definition is ~90k tokens; how much of it the harness
injects when invoked is UNVERIFIED — no invocation is recorded locally. VERIFIED 2026-10-02 on this
machine: ~2s end to end.
