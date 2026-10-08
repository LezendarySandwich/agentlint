// Tests: the plan (every testable target in the ranked order, each ticked to test or not, and those not testable, with
// why, folded) with the size of the test the ticks make — N targets × situations per target × runs per situation, about
// how long — the situations written for the ticked targets, folded under each, and Start with the open job: the one-time
// onboarding, progress, Stop / Resume / Salvage (docs/tech_impl/11-ui.md, "Tests"). Finished tests are Tests › Results'
// (Results.tsx). Explanations live in tooltips; every refusal and reason stays on the page. Everything shown comes from
// the server (ui/testing.py), which checks every request and says why it refuses one; the ticks are kept there, so a
// reload keeps them.
import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { PlanAnswer, PlanJob, PlanTarget, ReviewCounts, SituationsAnswer } from "./contract";
import { count, LOAD_WORDS } from "./format";
import { JobsSection } from "./Jobs";
import { href } from "./route";
import { SituationsSection } from "./Situations";
import { consistencyOnlyWords, reviewWords, shortWords, sizeWords, stepsWords, type TestsApi } from "./testsApi";
import { More, Tip } from "./ui";

/** Why the size the page holds isn't the ticks': "saving", ticks sent and not all answered, or a tick answered with no
 * job and the plan being read again for its size; "refused", the newest tick refused, so the server's ticks may differ
 * from the page's; "unread", a tick answered with no job and the plan read again for its size couldn't be read. */
export type Unsized = "saving" | "refused" | "unread";

/** The test's size while ticks are on their way to the server: the size held is the previous ticks'. */
export const SAVING_WORDS = "Saving the ticks… the size follows once the server has them.";
/** The test's size after the newest tick was refused: the server may hold other ticks than the page. */
export const REFUSED_WORDS = "The ticks couldn't be saved, so the test isn't sized: tick again.";
/** The test's size after a tick answered with no job, when the plan read again for its size couldn't be read. */
export const UNREAD_WORDS = "The test's size couldn't be read: tick again.";
const UNSIZED_WORDS: Record<Unsized, string> = { saving: SAVING_WORDS, refused: REFUSED_WORDS, unread: UNREAD_WORDS };

/** One ranked target: its place, name (its id on hover), its situations so far, size and when it loads, and whether it
 * is ticked to test. */
function TargetRow({ target, place, counts, ticked, onTick }: {
  target: PlanTarget; place: number; counts: ReviewCounts | null; ticked: boolean; onTick: (on: boolean) => void;
}) {
  const label = useId(); // a target's id holds spaces, which an aria-labelledby list can't
  return (
    <li className="plan-row">
      <input type="checkbox" checked={ticked} onChange={(e) => onTick(e.target.checked)} aria-labelledby={label} />
      <span className="plan-place num">{place}</span>
      <Tip text={target.id} className="plan-name"><span id={label}>{target.name}</span></Tip>
      <span className="plan-situations">{reviewWords(counts)}</span>
      <span className="plan-meta">
        {target.tokens !== null ? `≈${count(target.tokens)} tokens · ` : ""}{LOAD_WORDS[target.load]}
      </span>
    </li>
  );
}

/** The plan: every testable target in the ranked order, each tickable, the size of the test the ticks make, and the
 * targets that can't be tested, each with why. `unsized`: why the size the page holds isn't the ticks', so it isn't
 * shown (null: it is theirs). */
function PlanSection({ plan, job, counts, ticked, onTick, unsized }: {
  plan: PlanAnswer; job: PlanJob; counts: (target: PlanTarget) => ReviewCounts | null;
  ticked: string[]; onTick: (next: string[]) => void; unsized: Unsized | null;
}) {
  const [allSkipped, setAllSkipped] = useState(false);
  const [skippedOpen, setSkippedOpen] = useState(false);
  const tick = (id: string, on: boolean) => {
    const next = new Set(ticked);
    if (on) next.add(id); else next.delete(id);
    onTick(plan.targets.filter((t) => next.has(t.id)).map((t) => t.id)); // in the ranked order
  };
  const skipped = allSkipped ? plan.skipped : plan.skipped.slice(0, 5);
  const short = unsized ? "" : shortWords(job);
  const steps = unsized ? "" : stepsWords(job);
  const only = unsized ? "" : consistencyOnlyWords(job);
  return (
    <section id="tests-plan" className="settings-section" aria-labelledby="tests-plan-title">
      <h3 id="tests-plan-title">
        <Tip text={`Tick the targets to test. They are in the ranker's order: what loads every session and costs the most comes first. A test runs up to ${count(job.situations_per_target)} kept situations of each ticked target, ${count(job.runs_per_situation)} times each on your configuration; where those runs agree, as many times with the target and as many without, to see whether it changes what the agent does (Runs per situation, Settings › Tests).`}>
          <span>Plan</span>
        </Tip>
      </h3>
      <p className="plan-total" role="status" aria-label="Test size">
        {unsized ? UNSIZED_WORDS[unsized]
          : <>{sizeWords(job)}{job.runs ? ` ${count(job.seconds_per_run)} s a run, from ${job.seconds_from}.` : ""}</>}
      </p>
      {steps && <p className="setting-note">{steps}</p>}
      {short && <p className="setting-note">{short}</p>}
      {only && <p className="setting-note">{only}</p>}
      {plan.targets.length > 0 ? (
        <ul className="plan-list" aria-label="Targets">
          {plan.targets.map((target, i) => (
            <TargetRow key={target.id} target={target} place={i + 1} counts={counts(target)} ticked={ticked.includes(target.id)}
                       onTick={(on) => tick(target.id, on)} />
          ))}
        </ul>
      ) : <p className="muted">Nothing in this project can be tested yet.</p>}
      {plan.skipped.length > 0 && (
        <>
          <p className="plan-sub">
            <button type="button" className="text-button" aria-expanded={skippedOpen} onClick={() => setSkippedOpen(!skippedOpen)}>
              Not tested — {plan.skipped.length === 1 ? "1 target" : `${count(plan.skipped.length)} targets`}
            </button>
          </p>
          {skippedOpen && (
            <>
              <ul className="plan-skipped" aria-label="Not tested">
                {skipped.map((item) => (
                  <li key={item.id}>
                    <Tip text={item.id}><span>{item.name}</span></Tip>
                    <span className="muted"> — {item.why}{item.detail ? `: ${item.detail}` : ""}</span>
                  </li>
                ))}
              </ul>
              {!allSkipped && plan.skipped.length > 5 && <More hidden={plan.skipped.length - 5} onClick={() => setAllSkipped(true)} />}
            </>
          )}
        </>
      )}
    </section>
  );
}

/** The Tests screen for the project in view; `#/tests/<job>`, as links were before 2026-10-06, opens that test in Tests ›
 * Results. `tests` is absent only on a page with no server behind it to run anything (`servedApi` always gives one). */
export function TestsView({ tests, route, go }: {
  tests?: TestsApi; route: { id: string | null }; go: (to: string, replace?: boolean) => void;
}) {
  useEffect(() => { if (route.id) go(href("results", route.id), true); }, [route.id, go]);
  const [plan, setPlan] = useState<PlanAnswer | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [replanning, setReplanning] = useState(false);
  const [answers, setAnswers] = useState<Record<string, SituationsAnswer>>({}); // situations by target, as last asked
  const [ticked, setTicked] = useState<string[]>([]); // the targets to test: the server's, then as you tick them
  const [job, setJob] = useState<PlanJob | null>(null); // what the ticks make, as the server last sized it
  const [tickError, setTickError] = useState<string | null>(null);
  const [unsized, setUnsized] = useState<"refused" | "unread" | null>(null); // the newest tick's size not known, and why
  const asked = useRef(0);
  const sent = useRef(0); // ticks sent so far, numbering each
  const saving = useRef<Promise<unknown>>(Promise.resolve()); // the last ticks sent: Start waits for them
  const pending = useRef(0); // ticks sent and not answered: a plan read meanwhile keeps the page's ticks
  const [inFlight, setInFlight] = useState(0); // the same count, so the page re-renders as it changes
  const sending = useCallback((delta: number) => { pending.current += delta; setInFlight(pending.current); }, []);
  const owed = useRef(false); // a tick answered with no job: the plan being read is its size
  const [resizing, setResizing] = useState(false); // the same, so the page re-renders as it changes
  // The plan read again; `forTicks`: for the size of the newest ticks, answered with no job. Its ticks and job replace
  // the page's only when no tick was on its way as it was asked and none was sent since, so it can't be older ticks'.
  const replan = useCallback((forTicks = false) => {
    if (!tests) return;
    const id = ++asked.current;
    const quiet = !pending.current, ticksAsked = sent.current;
    if (forTicks) { owed.current = true; setResizing(true); }
    setReplanning(true);
    const landed = () => { const was = owed.current; owed.current = false; setResizing(false); setReplanning(false); return was; };
    tests.plan().then((next) => {
      if (id !== asked.current) return;
      landed();
      setPlan(next);
      setPlanError(null);
      if (quiet && !pending.current && ticksAsked === sent.current) {
        setTicked(next.ticked);
        setJob(next.job);
        setUnsized(null);
      }
    }, (failed: Error) => {
      if (id !== asked.current) return;
      if (landed()) setUnsized("unread");
      setPlanError(failed.message);
    });
  }, [tests]);
  useEffect(() => { replan(); }, [replan]);

  // Ticking sends the ticks to the server, which keeps them and sizes their job from the project's last reading, at
  // once; with no reading of it (the server restarted) the plan is read again. Ticks go one request at a time, each
  // after the one before is answered, so the server keeps them in click order; and only the newest request's answer
  // is shown, so the total and Start's state are always those of the ticks on the page: until every tick sent is
  // answered, and the plan read again for one answered with no job has landed, the total says the ticks are being
  // saved and Start is disabled, saying so; after the newest tick is refused, the page's ticks may not be the server's,
  // so the total isn't shown and Start is disabled, saying to tick again, until a later tick is answered or the plan is
  // read again.
  const send = useCallback((next: string[]) => {
    if (!tests) return;
    const id = ++sent.current;
    sending(1);
    saving.current = saving.current.then(() => tests.tick(next)).then((answer) => {
      sending(-1);
      if (id !== sent.current) return;
      setTickError(null);
      setUnsized(null);
      if (answer.job) {
        setJob(answer.job);
        owed.current = false; // an earlier job-less tick's plan read no longer sizes the page: this answer does
        setResizing(false);
      } else replan(true);
    }, (failed: Error) => {
      sending(-1);
      if (id !== sent.current) return;
      setTickError(failed.message);
      setUnsized("refused");
    });
  }, [tests, replan, sending]);
  const sizing: Unsized | null = inFlight > 0 || resizing ? "saving" : unsized;
  const onTick = useCallback((next: string[]) => { setTicked(next); send(next); }, [send]);
  const tickedNow = useRef(ticked);
  tickedNow.current = ticked;
  const resize = useCallback(() => send(tickedNow.current), [send]); // after a review: what is kept changed

  const took = useCallback((answer: SituationsAnswer) => setAnswers((was) => ({ ...was, [answer.target]: answer })), []);
  const counts = useCallback((target: PlanTarget): ReviewCounts | null => {
    const answer = answers[target.id];
    if (!answer) return target.situations;
    const tally: ReviewCounts = { kept: 0, dropped: 0, edited: 0 };
    for (const s of answer.situations) tally[s.review] += 1;
    return tally;
  }, [answers]);

  if (!tests) {
    return <div className="settings"><div className="settings-body"><h2>Tests</h2><p className="muted">Tests run from agentlint ui: run it to plan and start one.</p></div></div>;
  }
  if (route.id) return null; // on its way to Results
  return (
    <div className="settings tests">
      <div className="settings-body">
        <h2>
          <Tip text="A test runs the agent on situations written for your configuration's text, a few times each, and compares which tools, skills and agents each run used. Nothing here becomes a finding yet.">
            <span>Tests</span>
          </Tip>
        </h2>
        {!plan || !job ? (
          planError ? <p role="alert">The plan couldn't be read: {planError}</p>
            : <p className="muted">Reading the plan — this reads the project as a test would, which takes a few seconds…</p>
        ) : (
          <>
            {planError && <p className="refusal" role="alert">The plan couldn't be read again: {planError}</p>}
            {tickError && <p className="refusal" role="alert">The ticks couldn't be kept: {tickError}</p>}
            {replanning && <p className="muted" role="status">Updating the plan…</p>}
            <PlanSection plan={plan} job={job} counts={counts} ticked={ticked} onTick={onTick} unsized={sizing} />
            <SituationsSection tests={tests} plan={plan} ticked={ticked} answers={answers} took={took}
                               onWritten={() => replan()} onReviewed={resize} />
          </>
        )}
        <JobsSection tests={tests} plan={plan} job={job} ticked={ticked} saving={saving} unsized={sizing} go={go} />
      </div>
    </div>
  );
}
