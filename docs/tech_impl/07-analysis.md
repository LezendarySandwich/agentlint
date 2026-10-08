# 07 — Analysis

Turns traces into findings. Pure statistics — no AI, no judgment, no LLM evaluating anything.

Two engines. Everything reduces to one of them.

- **Engine A** — a predicate over one trace. Used by must-never checks.
- **Engine B** — distance between two distributions of outcomes. Used by everything else.

## Engine A — predicate over a trace

For must-never checks only.

```python
def evaluate(trace: Trace, assertion: Assertion) -> bool:
    ...
```

Assertion vocabulary, deliberately tiny:

```
called(entity)              not_called(entity)
before(a, b)                exactly_one_of([entities])
touched(glob)               arg_matches(tool, field, regex)
outcome_is(ok|timeout|error)
```

Composed with `and` / `or` / `not`.

**If an assertion needs an LLM to evaluate, the wrong assertion was chosen.** There is no `llm`
assertion type and there should never be one.

Verdict: `P(violation) must be 0 across all runs`. A single violation fails. No statistics — this
is a bound, not an estimate.

**Caveat for the docs:** zero violations in 20 runs bounds the true rate only loosely. Adversarial
probe design buys far more assurance than more runs.

## Engine B — distribution distance

Every run reduces to an outcome class (`06-trace.md`): a hash of its trace's projected steps. The projection and the
hash are parameters — which parts of an action count is Open Question 2, decided as D10 (2026-10-04: a set of tools,
skills and agents for "inconsistent", `projections.used`; no difference at all for "does nothing"), its exact
normalisation fixed after the noise study. A set of runs
becomes a categorical distribution over those classes.

```python
Distribution = Counter[str]   # outcome_class -> count
```

### Instability (within one variant) — the stability screen

Built 2026-10-04 in `src/agentlint/analysis/stability.py`, pure and harness-agnostic, tested on synthetic runs
(`tests/analysis/`). Constants are the decided ones: `RUNS_STABILITY = 3`, `STABILITY_FLOOR = 0.9`, `MIN_RUNS = 3`.

```python
def modal_share(d: Distribution) -> float:
    return max(d.values()) / sum(d.values())

def screen(runs, projection, *, hasher=sha256_steps, floor=STABILITY_FLOOR, min_runs=MIN_RUNS) -> Screen
```

`group` puts runs with one outcome class into a **branch** (its steps and its run indices, largest first; the same
runs in any order give the same branches). A screen is one of five states, decided in this order:

| State | When | Branches compared |
|---|---|---|
| `mixed environment` | two runs recorded a different harness version, set of models or configuration fingerprint — refused whatever their number, since more runs can't make them comparable (readiness D5). The screen names the fields (`differing`) | no |
| `incomplete` | fewer than `MIN_RUNS` completed runs | no |
| `no action` | every compared run's projection is empty — under D10's rule, no run used a tool, skill or agent (decided with the user 2026-10-05). **Never agreed**, whatever the share: runs that all did nothing say nothing about the target, and two sides that both do nothing are no evidence that a rule does nothing, so the with/without step skips the situation and it never counts towards "seems to do nothing" | yes: one branch, nothing in it |
| `disagreed` | `modal_share < stability_floor` — runs that disagree stay disagreed though some took no action | yes |
| `agreed` | `modal_share >= stability_floor` | yes |

`no action` is decided from the projections themselves, never from the branches' hashes, so a hasher that puts an
empty run in one class with a run that acted can't make it. It needs every compared run empty: at ten runs or more,
nine that took no action and one that acted pass the floor as `agreed`, the empty branch the modal one.

Only what each run recorded is compared, and models as a set (sorted), not in the order they first answered. A run
that recorded nothing for a field — no model answered it because its one model call failed, say, or no version line —
differs from nobody in that field: an infrastructure failure is not environment drift. Such runs are compared like
the rest and listed in the screen's `unrecorded`, for the runner to classify; which failed runs count as outcomes is
readiness D7, open. **Being listed doesn't take a run out of the share:** until the runner classifies it, an unrecorded
run — even one that recorded none of the three fields — is grouped and counts toward `modal_share` like any other, so
the runner must drop or retry a run it doesn't count before screening. A transcript that recorded nothing at all never
gets this far: reading it raises `TraceEmpty` (06-trace.md), so it is never a "did nothing" run.

**`agreed` is not evidence of stability, and is never shown as "stable".** At n = 3 the 0.9 floor means "all three
agree", and a true 50/50 split looks unanimous a quarter of the time (a 70/30 split 37% of the time). In the other
direction, three runs that disagree prove the agent is non-deterministic on that situation, not that its modal share
is below 0.9: a true 90/10 split is flagged 27% of the time. The floor bites as a share only at n ≥ 10 (the v3 gate,
or a screen run at 10 by choice before it starts).

No expected value anywhere. Nobody says which branch is correct. That's why this scales to four
hundred skills.

The finding names the competing branches, with counts and the diff position (`analysis/render.py`). Which
disagreements become a finding and its exact wording are readiness D11, not decided; D11's recommendation is a
finding only where the branches differ in which **configured** entities they invoked, counts only, no percentage.
The configured entities two branches invoked differently are already computed (`analysis/branches.py`), matched by
usage reference and given as the existing `Member` type; the harness's own tools and agents are never members. The
default is the symmetric difference of what each branch invoked (some run of one, no run of the other); a stricter
rule (every run of one, no run of the other) is a parameter. Which a finding uses is part of D11. Sketch of the shape, with three runs:

```
UNSTABLE   <situation>  (3 runs)
  2 of 3  →  skills/data:sql-query      runs 0, 2
  1 of 3  →  skills/analytics:sql-query    run 1
  Narrow one, or remove one.
```

No similarity figure: trigger-overlap is lexical TF-IDF (decision 2026-10-02) and most skills are listed name-only,
so a number would claim a cause that was never measured. The finding can point at an open trigger-overlap or
duplicate-name finding for the same pair instead.

### The stability screen in the UI (built 2026-10-05)

A job's results (`GET /api/jobs/<id>/results`, `ui/testing.stability_results`) are this screen per situation, with
the projection D10 decided for "your setup makes the agent inconsistent": **the set of tools, skills and agents a run
used** (`analysis/projections.used`: the trace's usage references, sorted, each once — the order, repeats and the files
or arguments they were used on are ignored). Two runs that read then edited and one that edited then read twice are
one branch; a run that invoked a skill instead is another. The exact normalisation is fixed after the noise study; the
stricter rule for "this rule seems to do nothing" (no difference at all) isn't built.

Per situation it gives the screen's state — `incomplete` (fewer than 3 runs screened), `mixed environment` (refused,
with the fields that differ, before anything is compared), `no action` (every screened run used no tool, skill or
agent: shown as "No action", never as agreement — the first real job, 2026-10-05, had 3 of 6 situations answered in
words only), `disagreed` with its branches, `agreed` — and for each
branch its runs, "2 of 3" (`count` of `of`) and what it used, grouped by the references' namespaces (for Claude Code
`tools`, `skills`, `agents`), the modal share, each run's outcome and each environment. **`agreed` is never called
stable** (above), and an agreed screen keeps every branch: at ten runs or more 9 of 10 passes the floor, and the run
that differed is listed under it, never folded into "all the runs used the same". A run whose trace couldn't be read — a timeout before the harness wrote anything — is listed apart
(`unscreened`, with its outcome) and screened with nothing, never as a run that did nothing, so it can leave a screen
`incomplete`. A run's fingerprint is its job's (one job, one fingerprint). An ablation comparison isn't screened here.
Each screened run is listed with its actions in order (since 2026-10-05, for Tests › Results), each action's input on
one line — its string values, no key read by name — so a person can see what the runs did; what is compared is still
only the projection above (`11-ui.md` "The API for Tests › Results"). A job's screens are also counted by state for
the history of tests, with the same code (`ui/testing.screen_counts`).
**No finding is made of a disagreement yet**: how an inconsistency finding reads is D11, decided at v3, so the results
are shown as the screen and nothing enters Findings, its counts or an exit code.

### Effect of a rule (between two variants) — the pure core built 2026-10-06

`analysis/effect.py`, over the projections in `analysis/projections.py` (06-trace.md, "Normalisation"); wiring it into
jobs (D9: one test, target by target, the with/without runs gated on the consistency runs that agreed) is the next
step. One situation's runs **with** the target (the baseline) against its runs **without** it. Two claims, each on the
evidence that makes it safe (D10, the user 2026-10-04: "a big difference before saying something is wrong, none at all
before saying a rule can go"):

**Changes behaviour** — what the runs used differs with and without. Each run is its `effect` set (the programs its
shell commands ran, the skills and agents it used, every other tool by name, files and searches by kind), the two
sides' distributions over those sets are compared by their total variation distance

```python
def total_variation(a: Distribution, b: Distribution) -> float:
    keys = set(a) | set(b)
    na, nb = sum(a.values()), sum(b.values())
    return 0.5 * sum(abs(a[k]/na - b[k]/nb) for k in keys)
```

— 0 identical, 1 disjoint — and the gap's significance by an **exact permutation test**: the chance, were the two
sides one population, that splitting these runs into sides of these sizes gaps at least as much. It is computed, not
sampled, so the same runs always give the same p: each outcome class's runs can go to the sides in every way, weighted
by C(n_i, k_i) (the multivariate hypergeometric), and a split's gap is Σ|k_i·N − n_i·n_a| / (2·n_a·n_b), a sum over
classes, so the splits' sums are counted class by class in integers without listing a single split (`p_value`). At 10
against 10 that is a handful of milliseconds and checked against brute-force shuffling in the tests. Past 200,000
partial sums at once (never reached at 10 against 10) it falls back to a Monte Carlo test of 20,000 draws seeded from
the comparison's id, so it stays deterministic.

**An effect needs both: `tv >= 0.3` (effect_floor) and `p < 0.05`.** The floor is compared exactly, in fractions
(`effect.gap`): a gap of exactly 0.3 is at the floor whatever floating point makes of it — one real comparison's came
to 0.30000000000000004 (2026-10-06). The evidence names what changed — for each term,
how many runs used it with and without, the biggest gap in share first, terms used equally on both sides left out:
"shell:acme — 10 of 10 with, 0 of 10 without".

**This is why Phase 2 runs at n=10, not n=3.** With three runs a side the permutation test has 20 arrangements: a total
gap (every run with, none without) has p = 2/20 = 0.1, never below 0.05 — correct behaviour ("three runs can't
establish this"), and why three runs a side can only ever be "can't measure" here. Phase 1 uses 3 because three
*disagreeing* runs already prove the agent is non-deterministic there; Phase 2 needs 10. The run count is fixed per
phase and chosen before the job starts. **There is no escalation**, and a verdict never silently depends on how many
runs happened to be spent.

**Seems to do nothing** — no difference at all: every run, on both sides, has the same `strict` sequence — the same
actions, in the same order, on the same targets (a shell command's targets being its programs, decided 2026-10-05;
paths relative to each run's own directories) — and it isn't empty. A rule like "run tests before committing" that
changes the order is never called dead, and wording that varies run to run (`ls tests | head` against `ls tests | wc
-l`) is only ever "can't measure", never "does nothing".

**One situation's verdict** (`situation`), checked in this order:

| Condition | `measured` | reason |
|---|---|---|
| fewer than 3 readable runs on either side | `incomplete` | too few runs on a side to compare |
| harness version, models or fingerprint differ among the runs | `unmeasurable` | mixed environments |
| every baseline run's `effect` set is empty | `unmeasurable` | no action (decided 2026-10-05: two sides doing nothing are no evidence) |
| the baseline's modal share under `used` is below 0.9 | `unmeasurable` | baseline unstable (the gate, below) |
| `tv >= 0.3` and `p < 0.05` | `changes` | — with the evidence |
| every run on both sides has one, non-empty `strict` sequence | `no-change` | — |
| anything else | `unmeasurable` | neither a clear change nor the same actions |

Every verdict carries its runs per side, `tv` and `p` when the test ran, the baseline's modal share, every term's
counts, and `CANON_VERSION` (`to_dict`, for the API built next).

## The stability gate

**Check stability before attempting ablation.** If the `baseline` variant is unstable on a
situation, that situation cannot measure a rule's effect — the noise swamps the comparison.

```python
if modal_share(baseline_dist) < stability_floor:
    return Unmeasurable(reason="baseline unstable", modal_share=...)
```

Report `unmeasurable`, move on, and let the separate instability finding stand on its own. Don't
spend more runs trying to see through it: more samples sharpen a number that was never going to be
interpretable.

This decision is in the design doc's decision log. Don't quietly reverse it.

**Built twice over (2026-10-06).** Before a with/without comparison runs at all, the job's gate (`runner/gate.py`, D9)
asks the situation's stability comparison — its 3 runs, recorded earlier in the same job — and runs it only when they
screened `agreed`: a situation whose runs disagreed, or took no action, spends no runs there. Then the verdict checks
again on all of its baseline runs (10 by default): below 0.9 under `used` is "unmeasurable: baseline unstable" — at
ten runs a split that three couldn't show is caught.

## The expectation's strength (built 2026-10-07)

Decided with the user 2026-10-07: **the situations and their expectations are trusted** — each says what its target
should make the agent do (its what matters names a thing, `04-probe-generator.md` "What matters"); a person may edit
one, but checking them is not this tool's job. What is measured is whether the target makes the agent do it.

`analysis/expectation.py`, per situation. A run **met the expectation** when it did the thing (`analysis/exercise.py`
`ran`, matching the thing against the run's actions through the adapter's action terms: a skill or agent by name,
`skill:*` any, a tool by name, a program among a shell command's programs, a file by its path; `tool_present` and
`targets` need their thing, `which_tools` either choice, `order` both). The situation's consistency runs are its runs
**with** the target; count those that met it.

- **None met it → not followed.** The target didn't make the agent do what it is for — a finding, with what the runs
  did instead (each effect term, by runs; "no action" for a run that did nothing). It is **never run without the
  target** (`runner/gate.py` `NOT_FOLLOWED` keeps the gate shut): there is nothing to take away.
- **At least one met it → run without.** As many runs without the target, counted the same. **Strength** = share with
  − share without, an exact fraction. The user: "for the skill to have a place in my context, the skill has to
  demonstrate 3/3 (with) and 0/3 without (that's perfect)". So `works` only at strength 1; anything less is a finding:
  `partial` (0 < strength < 1: not always followed, or the agent does it anyway without it), `adds nothing` (0: as
  often without it), `backfires` (< 0).
- **Runs that weren't consistent** (D10's screen, `disagreed`) are a finding of their own and **still run without**
  when at least one met the expectation (the user: "if the runs weren't consistent → finding here + without").
- Fewer than 3 readable runs on a side is `incomplete`; runs in different environments, `mixed environments`. A
  situation written before expectations has `no expectation` (store schema 17 deleted those: Write again).

A target's result (`target`): `works` only when every situation worked; otherwise its **weakest** situation leads
(backfires, not followed, adds nothing, partial), with the mean strength — a situation not followed counts 0 — and
how many situations ended each way. Any situation not yet measured leaves the target undecided.

Counts are said as they are. At 3 runs a side one run's difference can be chance; the result claims no more than the
counts, and no p-value is attached: the bar is the user's (perfect or a finding), not a significance test. No model
reads a run (non-negotiables 1 and 2): the generator named the thing as an input, and this is plain matching.

**Findings.** Every result but `works` is a finding in the Findings list (`tested-expectation`), and runs with the
target that weren't consistent another (`tested-consistency`): each target's latest decided test, while its text is
unchanged (`02-static-checks.md`).

The user's case it was built for: the power-engineer hook tells the agent to invoke a skill whenever one might apply.
Its three situations got written answers or ran `ls`, and one spent 20 with/without runs to end "can't measure".
Expecting `skill:*`, each is "not followed" after its 3 runs — the finding — and spends nothing more.

## Verdicts per rule

Two independent fields, not one enum. They answer different questions and are computed from
different statistics over the same traces.

```python
@dataclass(frozen=True)
class RuleVerdict:
    measured:       Literal["changes", "no-change", "unmeasurable", "incomplete"]
    matches_intent: Literal["yes", "no", "unknown"]
```

**`measured`** — does removing the rule change behaviour? From the total-variation engine.
Rule-agnostic; needs no knowledge of what the rule says.

| Condition (`effect.target`, built 2026-10-06) | `measured` |
|---|---|
| Any situation changes | `changes` |
| Else any situation is incomplete | `incomplete` |
| Else every situation is no-change (at least one): the same actions with and without, every run | `no-change` |
| Anything else — a situation that couldn't say | `unmeasurable` |

A target is never "does nothing" because some situations showed nothing and the rest couldn't measure: D10 asks for no
difference at all, so every situation has to show the same actions.

`incomplete` exists because a job gets killed mid-comparison. A comparison with fewer than
`MIN_RUNS_FOR_VERDICT` (3) runs per variant is not a measurement:

```python
if comparison.runs_done_per_variant < MIN_RUNS_FOR_VERDICT:
    return RuleVerdict(measured="incomplete", matches_intent="unknown")
```

Same class of error as the blindness guard — reporting "no measurable effect, deletion candidate"
off two runs would be confidently wrong. Coverage output distinguishes measured, incomplete, and
never attempted.

**`matches_intent`** — does it change behaviour *the way it says*? Requires a `claim` (see
`04-probe-generator.md`). `unknown` whenever no claim could be extracted. **Deferred past v3** —
until then it is always `unknown`.

```python
def matches_intent(baseline, without, claim) -> str:
    if claim is None:
        return "unknown"
    b = mean(claim.holds(t) for t in baseline)      # rate, not hard predicate
    w = mean(claim.holds(t) for t in without)
    if b == w:
        return "unknown"
    return "yes" if b > w else "no"
```

Same predicate language as must-never checks, evaluated as a rate instead of a bound:

```
must-never       the predicate must hold in 100% of runs
matches_intent   the predicate's rate should be higher with the rule than without
```

### User-facing labels

The report never prints the field names.

| State | Label |
|---|---|
| `no-change` | **no effect** |
| `changes` + `yes` | **works** |
| `changes` + `no` | **backfires** |
| `changes` + `unknown` | **changes behaviour** |
| `unmeasurable` | **can't measure** |
| `incomplete` | **not finished** — job was interrupted; resume or re-run |

**`backfires` is the finding this whole layer exists for.** It is also the one most likely to be
disbelieved, so it must always show the claim, both rates, and the run counts.

### Reporting honestly

**Never say "this rule does nothing."** Say what was actually done:

```
no effect   ~/.claude/CLAUDE.md §"Memory"   1,840 tokens, every request

  Removed it; behaviour was identical across 4 situations × 10 runs.
  (1 further situation was unmeasurable — unstable baseline.)

  This is evidence, not proof. Deleting it is a reasonable bet.
```

**Never let `changes` read as "working."** Until `matches_intent` ships, a rule that changes
behaviour has only been shown to do *something*:

```
changes behaviour   hook:SessionStart §search-files

  Removing it changed behaviour on 2 of 3 situations (10 runs each).
  Whether that is the change the rule intends is not measured.
```

**And `backfires`, when it exists:**

```
backfires   hook:SessionStart §search-files

  Claim: prefer search_files over Grep.
  With the rule:    search_files in 3/10 runs
  Without the rule: search_files in 10/10 runs
  This rule is doing the opposite of what it says.
```

The distinction between evidence and proof must survive into the UI. It's the difference between a
tool people trust and a tool that burns someone once.

## The blindness guard

Our observable is the action trace. A large share of real instructions — tone, length,
formatting, reasoning style — produce no action signal at all. For those, the ablation comparison
returns no difference, and reporting `no-change` would be **confidently harmful advice**: it would
tell someone to delete `be concise` because we couldn't see it working.

```python
if claim is None and measured == "no-change":
    return RuleVerdict(measured="unmeasurable", matches_intent="unknown")
    # reason: "this rule does not concern tool use; action traces cannot observe it"
```

`unmeasurable` and `no-change` must never be collapsed. See the scope boundary in
`docs/02-design.md`.

A narrower sibling case: a claim *was* extracted but none of its entities appear in either
variant's traces. That means the situation never exercised the rule — a probe-quality problem, not
a rule verdict. Report it against the probe, regenerate, and don't count it toward the rule.

## Regression (across time)

Same engine as ablation, with the axis being fingerprint rather than variant.

Hard rule: if anything other than the intended change moved between the baseline fingerprint and
the current one — harness version, model version — **re-baseline rather than compare**. Comparing
across two uncontrolled deltas is how a tool like this silently starts lying.

```python
if baseline.harness_version != current.harness_version or baseline.model != current.model:
    raise StaleBaseline(...)
```

Raise, don't warn.

## Coverage matrix

The central artifact. Rows = rules, columns = situations, cells = measured effect.

Read off directly:

- **Empty row** → rule with no measurable effect anywhere → deletion candidate
- **Identical rows** → two rules that are behaviorally indistinguishable → one is redundant
- **Empty column** → a situation that discriminates nothing → prune the probe
- **Fill rate** → how much of the config has actually been looked at

Store it; it's what makes the report say "here's what we've learned so far" rather than pretending
to be a pass/fail suite.
