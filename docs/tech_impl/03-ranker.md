# 03 — Ranker

Decides what gets tested tonight, and in what order.

No AI. A filter and a sort over the config reader's output.

## Why this exists

A full sweep is roughly 200 testable lines × 3 situations × 2 variants × 10 runs ≈ **12,000 runs**
(`RunCounts`: 60 a line). At ~30s per run, sequential, a night is 500–800 runs, so that's three weeks of nights.
You can't do it in one go, so something has to choose the order.

That's the entire job. It is not a scoring model.

## Budget is measured in runs, not dollars

The `total_cost_usd` the harness returns ($0.22 on the reference machine) is **list API pricing**.
Every AI call in this tool goes through the harness CLI on the user's own credential, so for a
subscription user the marginal dollar cost is zero. What's actually scarce:

- **Wall-clock** — ~30s per run, sequential. A night is ~500–800 runs. This is the real cap.
- **Rate limits** — one credential, shared across concurrent runs.

So the budget unit is `--runs`. Show a dollar estimate only when the user is genuinely billed
(API key, or a metered gateway), and label it as an estimate.

**Ad-hoc tests don't use the budget** (decided 2026-10-05). A test started from the UI runs the targets you tick ×
situations per target × runs per situation — what to test and how hard are the inputs, and the total is their
product (and the job's ceiling, `05-runner.md` "Budget"). For it the ranker only orders: `plan(budget=None)` cuts
nothing, so Tests › Plan lists every testable target in this order, with the top-ranked one ticked until you tick
others. The budget cut below is `agentlint rank --runs` (a developer's view, its own default of 60 runs, no
setting) and returns as the overnight layer, where a night's runs are the scarce thing.

```
agentlint test --runs 600
```

---

## Two phases

Testing isn't one pass over a ranked list. Stability screening is a prerequisite, it's per
*situation* rather than per rule, and it's cheap — so it runs first and its results decide what
Phase 2 can even measure.

```
Phase 1  STABILITY
         every generated situation, baseline variant only, N=3
         → instability findings (standalone, actionable)
         → marks which situations are measurable at all

Phase 2  ABLATION
         only rules whose situations survived Phase 1
         ordered by the sort below, cut at the run budget
         → effect / no-effect verdicts
```

Phase 1 over 200 situations is 600 runs — one night — and it's the phase most likely to produce
something you act on immediately. It also saves Phase 2 from spending runs on situations where the
baseline flips and nothing could have been measured.

---

## A line of the plan is what one variant removes

Decided 2026-10-03 (user). A variant can cut a section out of a file it rewrites — an instruction file's
section, a settings key — so each such rule is its own line. A skill file can only be dropped whole, and a
hook's output is the hook's to print (cutting its entry out of the settings removes all of it), so a skill or a
hook is one line covering all its rules: "does this matter at all?". Planning a line per rule there would run
the same variant once per section and credit one effect to each. In code a line is an `AblationTarget` and
`rank/plan.py` `targets()` lists them — a rule is
cut out alone only when its source's ablation is span excision *and* its text is a file (`cuttable`).

## The filter

Applied before any sorting. Cheap, and it's most of the value.

| Excluded | Why |
|---|---|
| `ablation: NONE` | Can't build a variant. Managed settings, the harness's own text and listings. |
| `load: NOT_IN_CONTEXT` | Its text never reaches the model: nothing to measure. |
| No text to divide | A source whose text is unknown — a hook no examined session showed (`NotObserved`), or one whose size alone is known (`SizeOnly`) — or an empty file. Nothing to write situations about. It is still a line of the plan, with no rules, so it is skipped with the content's own reason rather than left out. |
| Measured under this configuration | Its verdict stands (see Staleness). |
| Already settled by a static check | A broken reference is proven (an error finding located in the section); don't spend runs confirming it. A whole skill or hook isn't settled by a break inside it — whether it matters is a different question. |
| `load: ON_INVOKE` with no usage evidence | Skill bodies nobody calls — none of the entities it provides appears in your kept usage history (all of it, not a window). Nothing to measure. |
| All situations failed the Phase 1 stability gate | Unmeasurable. Report that, don't test it. Needs phase 1 results: not built until the runner. |

Excluded rules must still appear in the plan output with their reason — users need to know what
*can't* be checked, not just what wasn't.

## The sort

Two keys, applied to whatever survives the filter:

1. **Every session or every message first** (`load.in_first_turn`). These are in context on every single
   request. Everything else is conditional and costs you less when it's wrong.
2. **Then descending token cost** — a whole skill or hook counts all its rules.

That's it. No weights, nothing to calibrate.

### Why not a scoring formula

An earlier draft had exposure × suspicion with about ten weights and five tiers. Cut, because:

- **Nothing calibrates the weights.** It produces an opaque number no user can check, which
  contradicts the precision-first posture everywhere else in this project.
- **The tiers were a second ordering** layered on the score, and the two could disagree.
- **It had no inputs.** The formula consumed recorder usage data, and the recorder is v1. On day
  one every conditional source got a hardcoded fallback and the whole thing collapsed to
  `authority × log(tokens)` — which is "sort by size," i.e. the simple version, with extra steps.

"Test the expensive always-on things first" is defensible with no data at all. Add weights later
only if observed ordering turns out to matter.

### The escape hatch matters more than the sort

```
agentlint test --rule <id>
```

The user usually knows what's bothering them, and that beats any ranking we could compute.

---

## Run counts

Fixed per phase. **No escalation** — a run count is chosen up front, not adjusted mid-flight.

| Setting | Default | Notes |
|---|---|---|
| `situations` | 3 | Per target: the `situations_per_target` setting (1–10), which the generator writes and a test uses at most of, kept. |
| `runs_stability` | 3 | Three *disagreeing* runs conclusively proves instability. The `runs_per_situation` setting (3–20). |
| `runs_ablation` | 10 | Three is underpowered for detecting an effect — too few permutations for significance. |
| `runs_safety` | 20 | Must-never checks. |

Dynamic escalation was cut: it made cost unpredictable, the plan non-deterministic, and
resumption harder, in exchange for saving runs on a job that runs unattended overnight anyway.
If you want more confidence, set the number higher before you start.

**The caveat that must reach the user:** three *matching* runs is weak evidence of stability. At a
true 70/30 split, three identical results occur about a third of the time. `runs_stability = 3`
finds instability; it never establishes stability, and the tool must not print "stable" on that
basis.

---

## Staleness

A rule measured under a different config fingerprint has not been measured.

```python
@dataclass(frozen=True)
class TargetStatus:                   # an ablation target: a rule, or a whole skill or hook
    target_id: str
    last_measured: datetime | None
    last_fingerprint: str | None
    last_job_id: str | None
    verdict: Mapping | None           # RuleVerdict — measured + matches_intent, see 07-analysis.md
    situations_tried: int

    def current(self, fingerprint) -> bool: ...   # a verdict, measured under this fingerprint
```

Persisted in the `verdicts` table of the agentlint database, keyed by project and target id (schema 4;
schema 9 renamed the column from `unit_id`; the runner writes it, the ranker reads it). This is **cross-job state** — it
accumulates over weeks and is the main long-lived artifact the tool produces. It cannot live in a
job directory, and it must survive traces being pruned.

Anything measured under another fingerprint goes back in the queue; a verdict under the current one keeps it
out (skipped as "measured under this configuration"; the skip's detail says "measured on <date>", the day in local
time, as every other time shown — UTC before review round 12).

Coverage is a join over this store and the probe store — derived on demand, never persisted
separately.

---

## Plan output

Printed before anything runs. Spends nothing.

```
agentlint rank — phase 2, ablation · budget 600 runs · 10 planned, 600 runs (60 each: 3 situations × 2 variants × 10 runs)

    #  ≈tokens  loaded         runs  what a variant removes
    1    1,368  every session    60  hook: SessionStart · power-engineer@acme-market (whole, 27 parts)
    2      906  every session    60  hook: SessionStart · catalog_lookup@acme-market (whole, 18 parts)
    3      750  every session    60  AGENTS.md · ~/projects/agentlint/AGENTS.md § "agentlint" › "Keeping these docs current"
  ...

NOT THIS TIME  9 more need 540 more runs (--runs 1,140 plans them all)

SKIPPED  901
      604  on invoke, with no record of use — never used in 287 of your turns
      161  no text to test — not delivered in any of the 10 sessions examined
       82  can't be tested by removal
           e.g. composed by the harness; disable the package instead; managed hooks always apply
       54  never in the agent's context — its text never reaches the model
```

## CLI

```
agentlint rank                     # print the plan, spend nothing
agentlint rank --runs 600          # default: 60 (rank's own; no setting); 1-100000, anything else is a usage error (exit 2)
agentlint rank --phase stability   # the stability screen at your situations_per_target and runs_per_situation: the
                                   # order Tests › Plan lists, cut at --runs
agentlint rank --explain <target> # why this rule, skill or hook is where it is, or why it was skipped
agentlint rank --all               # every target, planned, deferred and skipped
```

Skipped lines are grouped by reason with a count (and `--all` lists each), so a plan over 700 targets stays
readable.

## Status

Built 2026-10-03: `rank/plan.py`, `store/verdicts.py`, `report/rank.py`, `agentlint rank`. On the reference
machine (~6 s): 2,631 rules in 909 sources make 920 targets (every source is in one; 210 have no rules); 19
testable, 10 planned within 600 runs (two whole SessionStart hooks lead, 1,368 and 906 tokens, then this
repository's AGENTS.md sections); 604 skipped as never used on invoke, 161 as no text to test, 82 untestable, 54
never in context. Round 8 (2026-10-03): sources with no rules were left out of the plan entirely (197 of them,
mostly hooks no examined session showed); they are now skipped with their reason. And a section proven broken
is matched by its source's full locator, so a project path containing `#` no longer defeats the "settled by a
static check" skip; since 2026-10-04 a finding's location names its source by id, and the match is by that id. Not built: the stability-gate exclusion and `agentlint test --rule` — both need
the runner.
