// Tests › Results: every test of the project in view, newest first, and one test's results as one table — a row per
// situation, grouped by the target its situation was written for, each row opening to its runs and what each did
// (docs/tech_impl/11-ui.md, "Results"). A situation's result is its stability screen by D10's rule: the set of tools,
// skills and agents each run used, order and files ignored (07-analysis.md). Runs that agree are only that, and the
// page says so once; runs that all used nothing took no action, never agreement; runs in different environments are
// refused, not compared. A test of what each situation expects (2026-10-07) shows instead each situation's runs with
// and without the target that did it, and its verdict: every one but Works is a finding.
import { Fragment, useCallback, useEffect, useState } from "react";
import type { EffectResult, EffectWord, JobEntry, JobsAnswer, ResultsAnswer, RunResult, ScreenCounts, SituationResult, StrengthWord, TargetEffect, TargetStrength } from "./contract";
import { count, counted, duration, noun, when } from "./format";
import { href } from "./route";
import { POLL_MS, usePoll, type TestsApi } from "./testsApi";
import { Icon, MARK, Tip } from "./ui";

/** What one branch used, for a person: "tools Bash, Read · skills notes"; the namespaces are the adapter's. */
export function usedWords(used: Record<string, string[]>): string {
  const parts = Object.entries(used).filter(([, names]) => names.length).map(([space, names]) => (space ? `${space} ${names.join(", ")}` : names.join(", ")));
  return parts.length ? parts.join(" · ") : "nothing";
}

/** A screen's state in words: never "stable" — runs that agree show no inconsistency, and nothing more. */
export const SCREEN_WORDS: Record<NonNullable<SituationResult["state"]>, string> = {
  agreed: "Agreed",
  disagreed: "Disagreed",
  "no action": "No action",
  incomplete: "Incomplete",
  "mixed environment": "Mixed environments",
};

/** What each result means, on hover over it. Agreed and No action both mean the runs matched: in No action what
 * matched is that no run did anything, which says nothing about the target. */
export const SCREEN_MEANING: Record<NonNullable<SituationResult["state"]>, string> = {
  agreed: "Every run used the same tools, skills and agents. The agent is consistent here; that doesn't show the target does anything.",
  disagreed: "The runs used different tools, skills or agents: the agent can't make up its mind here.",
  "no action": "No run used any tool, skill or agent: each answered in words alone, so the runs say nothing about the target.",
  incomplete: "Too few runs can be compared to say whether they agree.",
  "mixed environment": "The runs ran under different harness versions or models, so they aren't compared.",
};

/** A with/without verdict in words (D9): "changes" never reads as "works" — only that removing the target changed what
 * the agent did; "no-change" is only ever "seems to". */
export const EFFECT_WORDS: Record<EffectWord, string> = {
  changes: "Changes", "no-change": "Seems to do nothing", unmeasurable: "Can't measure", incomplete: "Incomplete",
};

/** The same, for a target across its situations, on its group row. */
const TARGET_WORDS: Record<EffectWord, string> = {
  changes: "Removing it changes what the agent does", "no-change": "Removing it seems to change nothing",
  unmeasurable: "Can't measure what removing it does", incomplete: "Not enough runs yet to say",
};

/** Why a with/without comparison couldn't say, in words (analysis/effect.py's reasons). */
const REASON_WORDS: Record<string, string> = {
  "too few runs on a side to compare": "too few runs on a side to compare",
  "mixed environments": "the runs ran in different environments",
  "no action": "the runs with the target took no action",
  "baseline unstable": "the runs with the target didn't agree",
  "neither a clear change nor the same actions": "no clear change, but not the same actions every run either",
};

/** What the "Without it" column means, on hover over its heading. */
export const WITHOUT_MEANING = "Where a situation's runs agreed, it ran again with the target and as often without it. Changes: removing the target changed what the agent did. Seems to do nothing: every run, with and without, took the same actions. Can't measure: neither.";

/** A situation's with/without comparison in a word or two, and what that means on hover. While the test runs, a
 * comparison not yet finished says how far it got, never a verdict: one read from half its runs would read as final. */
/** Why a situation's with/without step was skipped, in words: "Not followed" when none of its runs did what it
 * expects — the target didn't make the agent do it, so there is nothing to take away. */
function skippedCell(why: string | null, thing: string | null): { words: string; meaning: string } {
  switch (why) {
    case "not followed":
      return { words: "Not followed", meaning: `None of this situation's runs did ${thing ?? "what it expects"}: the target didn't make the agent do it, so it was never run without.` };
    case "no action": return { words: "Skipped", meaning: "This situation's runs took no action: nothing to compare with and without. It spent no runs." };
    case "incomplete": return { words: "Skipped", meaning: "Too few of this situation's runs could be read (a run that timed out leaves nothing): it spent no with/without runs." };
    case "mixed environment": return { words: "Skipped", meaning: "This situation's runs ran in different environments: it spent no with/without runs." };
    default: return { words: "Skipped", meaning: "This situation's runs didn't agree, so with and without can't show a change: it spent no runs." };
  }
}

function effectCell(effect: EffectResult | null | undefined, running: boolean, now: ReadonlySet<string>, thing: string | null = null): { words: string; meaning: string } {
  if (!effect) return { words: "—", meaning: "This test ran no with/without runs for this situation: tests made before 6 Oct ran consistency only, and a target no variant can remove yet gets none." };
  const withRuns = effect.recorded.baseline ?? 0, withoutRuns = effect.recorded.without ?? 0;
  switch (effect.state) {
    case "waiting":
      return running ? { words: "Queued", meaning: "It runs after this situation's consistency runs, if they agree." }
        : { words: "—", meaning: "This situation's consistency runs never all finished, so it didn't run." };
    case "skipped": return skippedCell(effect.why ?? null, thing);
    case "unreadable": return { words: "—", meaning: "This server can't read the test's actions." };
    case "measured": break;
  }
  if (running && effect.progress !== "complete") {
    if (withRuns + withoutRuns === 0 && !now.has(effect.comparison)) {
      return { words: "Queued", meaning: "This situation's runs agreed: it runs when the test reaches it." };
    }
    return { words: `Running · ${count(withRuns)} with, ${count(withoutRuns)} without`,
             meaning: `${count(withRuns + withoutRuns)} of ${count(effect.planned * 2)} runs so far: it says what it found once they are all in.` };
  }
  const word = effect.measured ?? "incomplete";
  const side = `${count(effect.runs?.baseline ?? 0)} with, ${count(effect.runs?.without ?? 0)} without`;
  if (word === "changes") {
    const top = (effect.evidence ?? [])[0];
    return { words: EFFECT_WORDS.changes, meaning: `Removing the target changed what the agent did (${side})${top ? `: ${termWords(top, effect)}` : ""}.` };
  }
  if (word === "no-change") return { words: EFFECT_WORDS["no-change"], meaning: `Every run, with and without the target, took the same actions (${side}).` };
  return { words: EFFECT_WORDS[word], meaning: `${REASON_WORDS[effect.reason ?? ""] ?? effect.reason ?? ""} (${side}).` };
}

/** A situation's consistency runs, while the test runs and they aren't all in: "Not run yet" or "Running", never a
 * screen read from some of them. None once they are all in, or the test closed. */
function runningResult(result: SituationResult, running: boolean, now: ReadonlySet<string>): { words: string; meaning: string } | null {
  const recorded = Object.values(result.recorded).reduce((a, b) => a + b, 0);
  if (!running || recorded >= result.planned) return null;
  if (recorded === 0 && !now.has(result.comparison)) return { words: "Not run yet", meaning: "It runs when the test reaches it: one target at a time, in the plan's order." };
  return { words: "Running", meaning: `${count(recorded)} of ${count(result.planned)} runs so far.` };
}

/** Whether a situation still has runs to come in a test that can take them: its target's verdict waits for it. */
function unsettled(result: SituationResult): boolean {
  const recorded = Object.values(result.recorded).reduce((a, b) => a + b, 0);
  const effect = result.effect;
  return recorded < result.planned || effect?.state === "waiting" || (effect?.state === "measured" && effect.progress !== "complete");
}

/** One term's evidence: "shell:acme — 5 of 5 with, 0 of 5 without". */
function termWords(term: { term: string; baseline: number; without: number }, effect: EffectResult): string {
  return `${term.term} — ${count(term.baseline)} of ${count(effect.runs?.baseline ?? 0)} with, ${count(term.without)} of ${count(effect.runs?.without ?? 0)} without`;
}

/** The expectation's strength in words (analysis/expectation.py): every result but "Works" is a finding. */
export const STRENGTH_WORDS: Record<StrengthWord, string> = {
  works: "Works", partial: "Partial", "adds nothing": "Adds nothing", backfires: "Backfires", "not followed": "Not followed",
  incomplete: "Incomplete", "mixed environments": "Mixed environments", "no expectation": "No expectation",
};

/** What the columns of a test of expectations mean, on hover over their headings. */
export const EXPECTS_MEANING = "What the situation expects the target to make the agent do: a skill, an agent, a tool, a program it runs or a file it touches, as the generator named it.";
export const WITH_MEANING = "Of the situation's runs with the target, how many did what it expects. Runs that didn't all use the same tools, skills and agents are inconsistent: a finding of its own.";
export const WITH_OUT_MEANING = "The same count with the target removed. It runs only where at least one run with the target did what the situation expects: otherwise there is nothing to take away.";
export const VERDICT_MEANING = "Works: every run with the target did it and none without — the only perfect result. Partial: it helps, not fully. Adds nothing: as often without it. Backfires: less often with it. Not followed: no run with it did it. Each of these but Works is a finding.";

/** "2 of 3": runs that met the expectation, of those read. */
function met(counted: { met: number; runs: number } | null | undefined): string {
  return counted ? `${count(counted.met)} of ${count(counted.runs)}` : "—";
}

/** A strength as a signed number to two places: "+0.33", "0", "−0.50". */
export function strengthWords(strength: number | null | undefined): string {
  if (strength === null || strength === undefined) return "—";
  if (strength === 0) return "0";
  return `${strength > 0 ? "+" : "−"}${Math.abs(strength).toFixed(2)}`;
}

/** A situation's runs with the target: how many did what it expects, "inconsistent" when they didn't agree. */
function withCell(result: SituationResult): { words: string; meaning: string } {
  const runs = result.runs ?? [];
  const did = runs.filter((run) => run.met).length;
  const words = `${count(did)} of ${count(runs.length)}`;
  if (result.state === "disagreed") return { words: `${words} · inconsistent`, meaning: `${SCREEN_MEANING.disagreed} ${count(did)} of ${count(runs.length)} did what it expects.` };
  if (result.state === "mixed environment") return { words: "Mixed environments", meaning: SCREEN_MEANING["mixed environment"] };
  return { words, meaning: `${count(did)} of the ${counted(runs.length, "run", "runs")} with the target did what the situation expects.` };
}

/** A situation's runs without the target: how far they got while the test runs, never a count read from some of them;
 * "Not run" when its gate closed. */
function withoutCell(effect: EffectResult | null | undefined, running: boolean, now: ReadonlySet<string>): { words: string; meaning: string } {
  if (!effect) return { words: "—", meaning: "A target no variant can remove yet is tested for consistency only." };
  const recorded = effect.recorded.without ?? 0;
  switch (effect.state) {
    case "waiting":
      return running ? { words: "Queued", meaning: "It runs after this situation's runs with the target, if at least one does what it expects." }
        : { words: "—", meaning: "This situation's runs with the target never all finished, so it didn't run." };
    case "skipped": return { words: "Not run", meaning: effect.why === "incomplete" ? "Too few of the runs with the target could be read." : "The runs with the target ran in different environments." };
    case "unreadable": return { words: "—", meaning: "This server can't read the test's actions." };
    case "measured": break;
  }
  if (effect.verdict === "not followed") return { words: "Not run", meaning: "No run with the target did what the situation expects: there is nothing to take away, so it spent no runs." };
  if (running && effect.progress !== "complete") {
    if (recorded === 0 && !now.has(effect.comparison)) return { words: "Queued", meaning: "It runs when the test reaches it." };
    return { words: `Running · ${count(recorded)} of ${count(effect.planned)}`, meaning: "It says what it found once they are all in." };
  }
  return { words: met(effect.without), meaning: `${met(effect.without)} runs without the target did what the situation expects.` };
}

/** The situation's verdict: its expectation's strength in a word, the strength where it is partial or backfires. */
function verdictCell(effect: EffectResult | null | undefined, running: boolean): { words: string; meaning: string } {
  if (!effect || effect.state !== "measured" || !effect.verdict) return { words: "—", meaning: running ? "It says once its runs are in." : "No verdict: its runs didn't all finish, or couldn't be read." };
  if (running && effect.progress !== "complete" && effect.verdict !== "not followed") return { words: "—", meaning: "It says once its runs without the target are in." };
  const expected = effect.expected ?? "what it expects";
  const sides = `${met(effect.with)} with the target, ${met(effect.without)} without`;
  const strength = strengthWords(effect.strength);
  switch (effect.verdict) {
    case "works": return { words: STRENGTH_WORDS.works, meaning: `Every run with the target did ${expected} and none without (${sides}): strength 1, the only perfect result.` };
    case "partial": return { words: `${STRENGTH_WORDS.partial} · ${strength}`, meaning: `It did ${expected} in ${sides}: strength ${strength}, where 1 is every run with it and none without. A finding: it helps, not fully.` };
    case "adds nothing": return { words: STRENGTH_WORDS["adds nothing"], meaning: `As often without the target as with it (${sides}): strength 0. A finding: it adds nothing here.` };
    case "backfires": return { words: `${STRENGTH_WORDS.backfires} · ${strength}`, meaning: `Less often with the target than without it (${sides}): strength ${strength}. A finding.` };
    case "not followed": {
      const instead = (effect.instead ?? []).map((term) => `${term.term} in ${counted(term.runs, "run", "runs")}`).join(", ");
      return { words: STRENGTH_WORDS["not followed"], meaning: `No run with the target did ${expected}${instead ? `; they did ${instead}` : ""}. A finding: the target didn't make the agent do what it is for.` };
    }
    case "mixed environments": return { words: STRENGTH_WORDS["mixed environments"], meaning: "The runs ran in different environments, so they aren't compared." };
    default: return { words: STRENGTH_WORDS[effect.verdict], meaning: "Too few readable runs to say." };
  }
}

/** The states counted, in the order the server lists them: "4 agreed · 1 disagreed · 1 no action". */
const TALLY_WORDS: Record<keyof ScreenCounts, string> = {
  agreed: "agreed", disagreed: "disagreed", "no action": "no action", incomplete: "incomplete", "mixed environment": "refused",
};

export function screensWords(screens: ScreenCounts): string {
  const parts = (Object.keys(TALLY_WORDS) as (keyof ScreenCounts)[]).filter((state) => screens[state] > 0)
    .map((state) => `${count(screens[state])} ${TALLY_WORDS[state]}`);
  return parts.length ? parts.join(" · ") : "nothing to compare yet";
}

/** Every test of the project in view, the most recent first; a row opens that test. */
function TestList({ tests, go }: { tests: TestsApi; go: (to: string) => void }) {
  const [answer, setAnswer] = useState<JobsAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const read = useCallback(() => {
    tests.jobs().then((next) => { setAnswer(next); setError(null); }, (failed: Error) => setError(failed.message));
  }, [tests]);
  useEffect(() => { read(); }, [read]);
  const shown: JobEntry[] = answer ? answer.jobs.filter((job) => answer.project === null || job.project === answer.project) : [];
  usePoll(read, POLL_MS, shown.some((job) => job.runner.alive));
  const open = (job: JobEntry) => go(href("results", job.job_id));
  return (
    <div className="settings tests">
      <div className="settings-body">
        <h2>Results</h2>
        {!answer ? (
          error ? <p role="alert">The tests couldn't be read: {error}</p> : <p className="muted">Reading the tests…</p>
        ) : shown.length ? (
          <table className="results-table" aria-label="Tests">
            <colgroup><col className="col-when" /><col className="col-targets" /><col className="col-count" /><col className="col-runs" /><col /><col className="col-standing" /></colgroup>
            <thead>
              <tr><th scope="col">Started</th><th scope="col">Targets</th><th scope="col" className="num">Situations</th>
                <th scope="col" className="num">Runs</th><th scope="col">Results</th><th scope="col">Where it stands</th></tr>
            </thead>
            <tbody>
              {shown.map((job) => (
                <tr key={job.job_id} className="row-link" onClick={() => open(job)}>
                  <td>
                    <a href={href("results", job.job_id)} onClick={(e) => { e.preventDefault(); e.stopPropagation(); open(job); }}>
                      {job.created ? when(job.created) : job.job_id}
                    </a>
                  </td>
                  <td><Tip text={job.targets.map((t) => t.name).join(" · ") || "none"}><span>{counted(job.targets.length, "target", "targets")}</span></Tip></td>
                  <td className="num">{count(job.situations)}</td>
                  <td className="num">{count(job.runs.recorded)} of {count(job.runs.planned)}</td>
                  <td>{screensWords(job.screens)}</td>
                  <td className="muted">{job.standing}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="muted">No test has run in this project yet: start one in Tests.</p>}
        {answer && error && <p className="refusal" role="alert">The tests couldn't be read again: {error}</p>}
      </div>
    </div>
  );
}

/** Branches with how many runs took each, which runs on hover: "2× tools Grep, Edit; 1× nothing". */
function Branches({ branches }: { branches: NonNullable<SituationResult["branches"]> }) {
  return (
    <>
      {branches.map((branch, i) => (
        <Fragment key={i}>
          {i > 0 && "; "}
          <Tip text={`${noun(branch.runs.length, "Run", "Runs")} ${branch.runs.map((n) => n + 1).join(", ")}`}><span>{count(branch.count)}×</span></Tip>
          {" "}{usedWords(branch.used)}
        </Fragment>
      ))}
    </>
  );
}

/** A situation's result in one word or two; an agreed screen that isn't unanimous (9 of 10) says so. */
function resultWords(result: SituationResult): string {
  if (result.state === null) return "Not screened";
  const branches = result.branches ?? [];
  if (result.state === "agreed" && branches.length > 1) return `Agreed, ${count(branches[0].count)} of ${count((result.runs ?? []).length)}`;
  return SCREEN_WORDS[result.state];
}

/** What the runs used: the one branch's tools, skills and agents, or each branch with its runs. */
function Used({ result }: { result: SituationResult }) {
  const branches = result.branches ?? [];
  switch (result.state) {
    case "agreed": return branches.length > 1 ? <Branches branches={branches} /> : <>{usedWords(branches[0]?.used ?? {})}</>;
    case "disagreed": return <Branches branches={branches} />;
    case "no action": return <>nothing</>;
    case "mixed environment": return <>differing: {(result.differing ?? []).join(", ") || "the environment"}</>;
    default: return <span className="muted">—</span>;
  }
}

/** One run: its number (from 1, as a person counts; the server counts from 0), how it ended, how long it took, and its
 * actions in order. */
function RunItem({ run }: { run: RunResult }) {
  return (
    <li>
      <span className="run-head">Run {run.run + 1} · {run.outcome} · {duration(run.seconds)}{run.actions.length ? "" : " · no action"}{run.met === undefined ? "" : run.met ? " · did what it expects" : " · didn't do what it expects"}</span>
      {run.actions.length > 0 && (
        <ol className="run-actions" aria-label={`Run ${run.run + 1}'s actions`}>
          {run.actions.map((action, i) => (
            <li key={i}><Tip text={action.what || action.tool}><span><span className="run-tool">{action.tool}</span> {action.what}</span></Tip></li>
          ))}
          {run.more_actions > 0 && <li className="muted">{counted(run.more_actions, "more action", "more actions")}</li>}
        </ol>
      )}
    </li>
  );
}

/** A situation's row, and beneath it, once opened, the whole text, what matters and each run. */
function SituationRows({ result, open, toggle, running, now, expecting }: {
  result: SituationResult; open: boolean; toggle: () => void; running: boolean; now: ReadonlySet<string>; expecting: boolean;
}) {
  const recorded = Object.values(result.recorded).reduce((a, b) => a + b, 0);
  const detail = `result-detail-${result.comparison}`;
  const effect = result.effect;
  const cell = effectCell(effect, running, now, result.matters_thing ?? null);
  const pending = runningResult(result, running, now);
  return (
    <>
      <tr className={open ? "open" : undefined}>
        <td>
          <button type="button" className="row-open" aria-expanded={open} aria-controls={open ? detail : undefined} onClick={toggle}>
            <Icon icon={MARK.disclose} size={14} className="row-chev" />
            <span className="row-text">{result.text}</span>
          </button>
        </td>
        {expecting ? <ExpectationCells result={result} running={running} now={now} pending={pending} /> : (
          <>
            <td className="num">{count(recorded)} of {count(result.planned)}</td>
            <td>
              {pending ? <Tip text={pending.meaning}><span>{pending.words}</span></Tip>
                : result.state ? <Tip text={SCREEN_MEANING[result.state]}><span>{resultWords(result)}</span></Tip> : resultWords(result)}
            </td>
            <td className="used">{pending ? <span className="muted">—</span> : <Used result={result} />}</td>
            <td><Tip text={cell.meaning}><span>{cell.words}</span></Tip></td>
          </>
        )}
      </tr>
      {open && (
        <tr id={detail} className="detail-row">
          <td colSpan={5}>
            <div className="result-detail">
            <p className="situation-text">{result.text}</p>
            {result.matters && (
              <p className="situation-meta"><Tip text={`What matters · ${result.matters}`}><span>What matters: {result.matters_meaning ?? result.matters}{result.matters_thing ? `: ${result.matters_thing}` : ""}</span></Tip></p>
            )}
            {result.state === null && <p className="muted">{result.note ?? "Not screened here."}</p>}
            {result.state === "mixed environment" && (
              <ul className="result-branches" aria-label="Environments">
                {(result.environments ?? []).map((env, i) => (
                  <li key={i}>harness {env.harness_version ?? "version not recorded"} · {env.models.length ? env.models.join(", ") : "model not recorded"}</li>
                ))}
              </ul>
            )}
            {(result.runs ?? []).length > 0 && (
              <ol className="runs" aria-label="Runs">
                {(result.runs ?? []).map((run) => <RunItem key={run.run} run={run} />)}
              </ol>
            )}
            {(result.unscreened ?? []).map((run) => (
              <p key={run.run} className="dismissal-meta">Run {run.run + 1} left no readable trace ({run.outcome}): it isn't compared, and never counts as a run that did nothing.</p>
            ))}
            {effect?.state === "measured" && (effect.kind === "expectation" ? <ExpectationDetail effect={effect} /> : <EffectDetail effect={effect} />)}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

/** A situation's with/without comparison, opened: its verdict, what changed (each term: how many runs used it with and
 * without), and each side's runs. */
function EffectDetail({ effect }: { effect: EffectResult }) {
  const word = effect.measured ?? "incomplete";
  const said = word === "changes" || word === "no-change" ? "" : `: ${REASON_WORDS[effect.reason ?? ""] ?? effect.reason ?? ""}`;
  return (
    <div className="effect-detail">
      <p className="effect-head"><strong>Without the target: {EFFECT_WORDS[word].toLowerCase()}</strong>{said}</p>
      {(effect.evidence ?? []).length > 0 && (
        <ul className="result-branches" aria-label="What changed">
          {(effect.evidence ?? []).map((term) => <li key={term.term}>{termWords(term, effect)}</li>)}
        </ul>
      )}
      {(["baseline", "without"] as const).map((side) => (effect.sides?.[side] ?? []).length > 0 && (
        <div key={side}>
          <p className="run-head">{side === "baseline" ? "With the target" : "Without the target"}</p>
          <ol className="runs" aria-label={side === "baseline" ? "Runs with the target" : "Runs without the target"}>
            {(effect.sides?.[side] ?? []).map((run) => <RunItem key={run.run} run={run} />)}
          </ol>
        </div>
      ))}
    </div>
  );
}

/** A situation's cells in a test of expectations: what it expects, its runs with the target and without that did it,
 * and its verdict. */
function ExpectationCells({ result, running, now, pending }: {
  result: SituationResult; running: boolean; now: ReadonlySet<string>; pending: { words: string; meaning: string } | null;
}) {
  const effect = result.effect;
  const expected = effect?.expected ?? result.matters_thing;
  const withIt = pending ?? withCell(result);
  const without = withoutCell(effect, running, now);
  const verdict = verdictCell(effect, running);
  return (
    <>
      <td className="used">{expected ? <Tip text={result.matters_meaning ?? expected}><span>{expected}</span></Tip> : <span className="muted">—</span>}</td>
      <td><Tip text={withIt.meaning}><span>{withIt.words}</span></Tip></td>
      <td><Tip text={without.meaning}><span>{without.words}</span></Tip></td>
      <td><Tip text={verdict.meaning}><span>{verdict.words}</span></Tip></td>
    </>
  );
}

/** A situation's second step, opened, in a test of expectations: what it expects, each side's count, the verdict, what
 * the runs did instead when not followed, and the runs without the target. */
function ExpectationDetail({ effect }: { effect: EffectResult }) {
  const verdict = effect.verdict ?? "incomplete";
  const without = effect.sides?.without ?? [];
  return (
    <div className="effect-detail">
      <p className="effect-head"><strong>{STRENGTH_WORDS[verdict]}</strong>: expects {effect.expected ?? "—"} · with the target {met(effect.with)} · without {met(effect.without)}{effect.strength === null || effect.strength === undefined ? "" : ` · strength ${strengthWords(effect.strength)}`}</p>
      {(effect.instead ?? []).length > 0 && (
        <ul className="result-branches" aria-label="What the runs did instead">
          {(effect.instead ?? []).map((term) => <li key={term.term}>{term.term} — {counted(term.runs, "run", "runs")}</li>)}
        </ul>
      )}
      {without.length > 0 && (
        <div>
          <p className="run-head">Without the target</p>
          <ol className="runs" aria-label="Runs without the target">
            {without.map((run) => <RunItem key={run.run} run={run} />)}
          </ol>
        </div>
      )}
    </div>
  );
}

/** A target's expectation strength across its situations, on its group row: "Works" only when every situation worked,
 * else its weakest result and the mean strength; how its situations ended on hover. */
function TargetStrengthVerdict({ effect, settled }: { effect: TargetStrength; settled: boolean }) {
  if (!settled) return <span className="target-verdict">In progress</span>;
  const tally = Object.entries(effect.situations).filter(([, n]) => n > 0)
    .map(([word, n]) => `${count(n)} ${STRENGTH_WORDS[word as StrengthWord]?.toLowerCase() ?? word}`).join(" · ");
  const words = effect.verdict === "works" ? "Works in every situation"
    : `${STRENGTH_WORDS[effect.verdict]}${effect.strength === null ? "" : ` · mean strength ${strengthWords(effect.strength)}`}`;
  return <Tip text={`Its situations: ${tally || "none measured"}. The weakest leads; the mean counts a situation not followed as 0.`} className="target-verdict"><span>{words}</span></Tip>;
}

/** A target's verdict across its situations, on its group row, with how its situations ended on hover; "In progress"
 * while some of them still have runs to come. */
function TargetVerdict({ effect, settled }: { effect: TargetEffect | null | undefined; settled: boolean }) {
  if (!effect) return null;
  if (!settled) return <span className="target-verdict">In progress</span>;
  const tally = Object.entries(effect.situations).filter(([, n]) => n > 0)
    .map(([word, n]) => `${count(n)} ${EFFECT_WORDS[word as EffectWord]?.toLowerCase() ?? word}`).join(" · ");
  return <Tip text={`Its situations: ${tally || "none measured"}`} className="target-verdict"><span>{TARGET_WORDS[effect.measured]}</span></Tip>;
}

/** A target's verdict on its group row, whichever kind of test it was. */
function GroupVerdict({ target, settled }: { target: NonNullable<ResultsAnswer["targets"]>[number] | undefined; settled: boolean }) {
  if (!target) return null;
  return target.kind === "expectation" ? <TargetStrengthVerdict effect={target.effect} settled={settled} />
    : <TargetVerdict effect={target.effect} settled={settled} />;
}

/** One test: a line saying what it ran and found, the caveat once, and the table. Asked again every few seconds while
 * the test can still take runs. */
function TestResults({ tests, job, go }: { tests: TestsApi; job: string; go: (to: string) => void }) {
  const [answer, setAnswer] = useState<ResultsAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const read = useCallback(() => {
    tests.results(job).then((next) => { setAnswer(next); setError(null); }, (failed: Error) => setError(failed.message));
  }, [tests, job]);
  useEffect(() => { read(); }, [read]);
  usePoll(read, POLL_MS, answer?.status === "open");
  const shown = answer?.situations ?? [];
  // Grouped by the target each situation was written for, in the order the test ran them.
  const groups = new Map<string, { about: string | null; name: string | null; rows: SituationResult[] }>();
  for (const result of shown) {
    const key = result.about ?? "";
    if (!groups.has(key)) groups.set(key, { about: result.about, name: result.name, rows: [] });
    groups.get(key)!.rows.push(result);
  }
  const targets = new Map((answer?.targets ?? []).map((t) => [t.about, t]));
  const expecting = shown.some((result) => result.effect?.kind === "expectation");
  const running = answer?.status === "open";
  // The comparisons whose runs are under way: several with runs at once.
  const now: ReadonlySet<string> = new Set((answer?.running ?? (answer?.in_flight ? [answer.in_flight] : [])).map((key) => key.split(":")[0]));
  const tally: ScreenCounts = { agreed: 0, disagreed: 0, "no action": 0, incomplete: 0, "mixed environment": 0 };
  let coming = 0; // situations whose consistency runs aren't all in while the test runs: counted apart, never incomplete
  for (const result of shown) {
    if (running && Object.values(result.recorded).reduce((a, b) => a + b, 0) < result.planned) coming += 1;
    else if (result.state) tally[result.state] += 1;
  }
  const found = Object.values(tally).some((n) => n > 0) || !coming ? screensWords(tally) : "";
  // Both steps' runs, a gated-off comparison's left out, as the server counts them; a test from before it said so
  // counts its situations' runs.
  const recorded = answer?.runs?.recorded ?? shown.reduce((n, r) => n + Object.values(r.recorded).reduce((a, b) => a + b, 0), 0);
  const planned = answer?.runs?.planned ?? shown.reduce((n, r) => n + r.planned, 0);
  const toggle = (id: string) => setOpen((was) => {
    const next = new Set(was);
    if (!next.delete(id)) next.add(id);
    return next;
  });
  return (
    <div className="settings tests">
      <div className="settings-body">
        <p><button type="button" className="text-button" onClick={() => go(href("results"))}>Results</button> ›</p>
        <h2><Tip text={job}><span>{answer?.created ? `Test of ${when(answer.created)}` : "Test"}</span></Tip></h2>
        {!answer ? (
          error ? <p role="alert">The results couldn't be read: {error}</p> : <p className="muted">Reading the results…</p>
        ) : (
          <>
            <p className="settings-line" aria-label="Summary">
              {counted(groups.size, "target", "targets")} · {counted(shown.length, "situation", "situations")} · {count(recorded)} of {running ? "up to " : ""}{counted(planned, "run", "runs")}{found ? ` · ${found}` : ""}{coming ? ` · ${count(coming)} to come` : ""}
              {answer.status === "open" ? " · the test can still take runs: this updates as it goes" : ""}
            </p>
            {expecting
              ? <p className="muted">Each situation expects its target to make the agent do one thing. Works: every run with the target did it, none without. Anything less is a finding.</p>
              : <p className="muted">Agreed: every run used the same tools, skills and agents. No action: no run used any — each answered in words alone. Neither shows the target does anything.</p>}
            {error && <p className="refusal" role="alert">The results couldn't be read again: {error}</p>}
            <table className="results-table" aria-label="Situations">
              {expecting ? (
                <>
                  <colgroup><col className="col-situation" /><col /><col className="col-result" /><col className="col-result" /><col className="col-without" /></colgroup>
                  <thead>
                    <tr><th scope="col">Situation</th><th scope="col"><Tip text={EXPECTS_MEANING}><span>Expects</span></Tip></th>
                      <th scope="col"><Tip text={WITH_MEANING}><span>With it</span></Tip></th>
                      <th scope="col"><Tip text={WITH_OUT_MEANING}><span>Without it</span></Tip></th>
                      <th scope="col"><Tip text={VERDICT_MEANING}><span>Verdict</span></Tip></th></tr>
                  </thead>
                </>
              ) : (
                <>
                  <colgroup><col className="col-situation" /><col className="col-runs" /><col className="col-result" /><col /><col className="col-without" /></colgroup>
                  <thead>
                    <tr><th scope="col">Situation</th><th scope="col" className="num">Runs</th><th scope="col">Result</th><th scope="col">Used</th>
                      <th scope="col"><Tip text={WITHOUT_MEANING}><span>Without it</span></Tip></th></tr>
                  </thead>
                </>
              )}
              {[...groups.values()].map((group) => (
                <tbody key={group.about ?? ""}>
                  <tr className="group-row">
                    <th scope="colgroup" colSpan={5}>
                      {group.about ? <Tip text={group.about}><span>{group.name ?? group.about}</span></Tip> : "No target recorded"}
                      <GroupVerdict target={targets.get(group.about ?? "")} settled={!running || !group.rows.some(unsettled)} />
                    </th>
                  </tr>
                  {group.rows.map((result) => (
                    <SituationRows key={result.comparison} result={result} open={open.has(result.comparison)} toggle={() => toggle(result.comparison)}
                                   running={running} now={now} expecting={expecting} />
                  ))}
                </tbody>
              ))}
            </table>
          </>
        )}
      </div>
    </div>
  );
}

/** Tests › Results for the project in view: every test, or one test's results (`#/results/<job>`). `tests` is absent
 * only on a page with no server behind it. */
export function ResultsView({ tests, route, go }: { tests?: TestsApi; route: { id: string | null }; go: (to: string) => void }) {
  if (!tests) {
    return <div className="settings"><div className="settings-body"><h2>Results</h2><p className="muted">Tests run from agentlint ui: run it to see their results.</p></div></div>;
  }
  return route.id ? <TestResults key={route.id} tests={tests} job={route.id} go={go} /> : <TestList tests={tests} go={go} />;
}
