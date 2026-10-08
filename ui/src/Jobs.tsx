// Tests › Jobs: Start (disabled, with the server's reason, when a test can't start — inside an agent session, while
// one runs, nothing ticked, a ticked target with nothing kept), the tools removed from every run, the one-time
// onboarding the first time, and the open job — running, stopping or paused — asked about every few seconds while it
// runs, with Stop / Resume / Salvage and the keep-awake line while it runs (docs/tech_impl/11-ui.md, "Tests"). Finished
// and closed tests are in Tests › Results. A job runs in a worker of its own, which outlives this page and the server.
import { useCallback, useEffect, useState } from "react";
import type { JobEntry, JobsAnswer, OnboardingAnswer, PlanAnswer, PlanJob, ToolVerdictInfo } from "./contract";
import { counted, duration, when } from "./format";
import { Onboarding } from "./Onboarding";
import type { Unsized } from "./Tests";
import { href } from "./route";
import { POLL_MS, usePoll, type TestsApi } from "./testsApi";
import { Tip } from "./ui";

/** How far each situation of a job got (runner/status.py's states), in words. */
const PROGRESS_WORDS: Record<string, [string, string]> = {
  complete: ["situation complete", "situations complete"],
  partial: ["situation stopped short, with enough runs to compare", "situations stopped short, with enough runs to compare"],
  incomplete: ["situation with too few runs to compare", "situations with too few runs to compare"],
  "not-attempted": ["situation not started", "situations not started"],
};

function JobRow({ job, tests, go, busyElsewhere, onChanged, onNote }: {
  job: JobEntry; tests: TestsApi; go: (to: string) => void; busyElsewhere: boolean; onChanged: () => void;
  onNote: (note: string) => void; // said under the section: a salvaged job leaves the list, its note stays
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [salvaging, setSalvaging] = useState(false);
  const act = (name: string, call: () => Promise<unknown>, done?: (answer: unknown) => void) => {
    setBusy(true);
    setError(null);
    call().then((answer) => { setBusy(false); setSalvaging(false); done?.(answer); onChanged(); },
      (failed: Error) => { setBusy(false); setError(`${name}: ${failed.message}`); });
  };
  const labelled = `job-${job.job_id}`;
  const states = Object.entries(job.comparisons).filter(([, n]) => n > 0);
  const open = job.status === "open";
  return (
    <li className="job" aria-labelledby={labelled} aria-busy={busy}>
      <div className="dismissal-head">
        <span id={labelled} className="dismissal-title">
          <Tip text={job.job_id}><span>{job.created ? `Test of ${when(job.created)}` : "Test"}</span></Tip> — {job.standing}
        </span>
        {job.runner.alive && (
          <button type="button" className="toggle" disabled={busy || job.runner.stopping}
                  onClick={() => act("Not stopped", () => tests.stop(job.job_id))}>Stop</button>
        )}
        {!job.runner.alive && open && (
          <>
            <button type="button" className="toggle" disabled={busy || busyElsewhere}
                    onClick={() => act("Not resumed", () => tests.resume(job.job_id))}>Resume</button>
            <button type="button" className="toggle" disabled={busy} onClick={() => setSalvaging(true)}>Salvage…</button>
          </>
        )}
        {job.runs.recorded > 0 && (
          <a className="toggle" href={href("results", job.job_id)} onClick={(e) => { e.preventDefault(); go(href("results", job.job_id)); }}>Results</a>
        )}
      </div>
      <p className="dismissal-meta">
        {job.runs.recorded} of {counted(job.runs.planned, "run", "runs")} recorded
        {job.last_progress ? ` · last run ${when(job.last_progress)}` : ""}
        {job.model ? <> · <code>{job.model}</code></> : null}
        {states.length > 0 && ` · ${states.map(([state, n]) => {
          const words = PROGRESS_WORDS[state];
          return words ? counted(n, words[0], words[1]) : `${n} ${state}`;
        }).join(" · ")}`}
      </p>
      {job.launched_without_a_run > 0 && (
        <p className="dismissal-meta">{counted(job.launched_without_a_run, "launch", "launches")} recorded no run: Resume tries again.</p>
      )}
      {salvaging && (
        <div className="situation-actions" role="group" aria-label="Salvage">
          <span>Close this job for good, keeping what completed? Nothing more runs in it.</span>
          <button type="button" className="toggle on" disabled={busy}
                  onClick={() => act("Not salvaged", () => tests.salvage(job.job_id), (answer) => {
                    const undeleted = (answer as { undeleted?: string[] }).undeleted ?? [];
                    if (undeleted.length) onNote(`${counted(undeleted.length, "run's directory", "runs' directories")} couldn't be deleted; the next test run tries again.`);
                  })}>Close it</button>
          <button type="button" className="toggle" disabled={busy} onClick={() => setSalvaging(false)}>Cancel</button>
        </div>
      )}
      {error && <p className="refusal" role="alert">{error}</p>}
      {job.log_tail.length > 0 && (
        <details className="job-log">
          <summary>Log</summary>
          <pre>{job.log_tail.join("\n")}</pre>
          <p className="muted"><code>{job.log}</code></p>
        </details>
      )}
    </li>
  );
}

/** The tools every new run is offered none of, each with why, and those the adapter judged and keeps — folded, read from
 * the plan (your Settings override applied), so the names to put in Tools kept are here after the onboarding too. */
function RemovedTools({ tools }: { tools: ToolVerdictInfo[] }) {
  const removed = tools.filter((t) => t.removed);
  const kept = tools.filter((t) => !t.removed);
  return (
    <details className="removed-tools">
      <summary>
        Tools removed from every run: {removed.length ? counted(removed.length, "tool", "tools") : "none"}
        {kept.length ? `; ${counted(kept.length, "other tool stays", "other tools stay")}` : ""}
      </summary>
      {tools.length ? (
        <>
          {removed.length > 0 && (
            <ul className="sheet-list" aria-label="Removed from every run">
              {removed.map((tool) => <li key={tool.name}><code>{tool.name}</code> <span className="muted">— {tool.why}</span></li>)}
            </ul>
          )}
          {kept.length > 0 && (
            <ul className="sheet-list" aria-label="Kept in every run">
              {kept.map((tool) => <li key={tool.name}><code>{tool.name}</code> <span className="muted">— {tool.why}</span></li>)}
            </ul>
          )}
        </>
      ) : <p className="muted">No tool was judged: your sessions showed none that a test removes or keeps.</p>}
      <p className="muted">Change them with Tools kept and Tools removed in Settings › Tests; a test keeps what it started with.</p>
    </details>
  );
}

/** Why Start is disabled while ticks are on their way to the server: `job` is still the previous ticks'. */
export const TICKS_SAVING = "the ticks are being saved: Start waits until the server has every one";
/** Why Start is disabled after the newest tick was refused: the server may hold other ticks than the page. */
export const TICKS_REFUSED = "the ticks couldn't be saved: tick again";
/** Why Start is disabled after a tick answered with no job, when the plan read again for its size couldn't be read. */
export const SIZE_UNREAD = "the test's size couldn't be read: tick again";
const UNSIZED_REASONS: Record<Unsized, string> = { saving: TICKS_SAVING, refused: TICKS_REFUSED, unread: SIZE_UNREAD };

/** Start, what it would run of the ticked targets, and every job. `saving` is the last ticks sent: Start waits for them,
 * so the onboarding reads the ticks the page shows; Start sends the page's ticks as the job's targets. `unsized`: why
 * `job` isn't the ticks' (ticks being saved, the newest refused, or its size unread) — Start is disabled, saying so,
 * and what it would run isn't shown. */
export function JobsSection({ tests, plan, job, ticked, saving, unsized, go }: {
  tests: TestsApi; plan: PlanAnswer | null; job: PlanJob | null; ticked: string[];
  saving: { current: Promise<unknown> }; unsized: Unsized | null; go: (to: string) => void;
}) {
  const [answer, setAnswer] = useState<JobsAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [onboarding, setOnboarding] = useState<{ answer: OnboardingAnswer | null; error: string | null } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const refresh = useCallback(() => {
    tests.jobs().then((next) => { setAnswer(next); setError(null); }, (failed: Error) => setError(failed.message));
  }, [tests]);
  useEffect(() => { refresh(); }, [refresh]);
  const running = !!answer?.jobs.some((job) => job.runner.alive);
  // The open jobs of the project in view (running, stopping or paused); finished and closed ones are Results'.
  const mine = answer ? answer.jobs.filter((entry) => answer.project === null || entry.project === answer.project) : [];
  const openJobs = mine.filter((entry) => entry.status === "open" || entry.runner.alive);
  usePoll(refresh, POLL_MS, running || starting);

  const start = () => {
    setStarting(true);
    setRefusal(null);
    tests.start(ticked).then(() => { setStarting(false); setOnboarding(null); refresh(); }, (failed: Error) => {
      setStarting(false);
      if (onboarding) setOnboarding({ ...onboarding, error: failed.message });
      else setRefusal(failed.message);
      refresh();
    });
  };
  const pressStart = () => {
    if (answer?.accepted) return start();
    setOnboarding({ answer: null, error: null }); // the first time: the onboarding, read from the server
    saving.current.then(() => tests.onboarding()).then((read) => setOnboarding({ answer: read, error: null }),
      (failed: Error) => setOnboarding({ answer: null, error: failed.message }));
  };
  const yes = () => {
    setStarting(true);
    tests.accept().then(() => {
      setAnswer((was) => was && { ...was, accepted: true });
      start();
    }, (failed: Error) => { setStarting(false); setOnboarding((was) => was && { ...was, error: failed.message }); });
  };

  // Why Start is disabled: the machine's reasons (GET /api/jobs) and the ticked targets' (their job, as the server
  // sized it), each in the server's words, the same POST /api/jobs would refuse with; while `job` isn't the ticks', why
  // instead of the previous ticks' reasons.
  const reasons = [...new Set([...(answer?.blocked ?? []), ...(unsized ? [UNSIZED_REASONS[unsized]] : job?.blocked ?? [])])];
  return (
    <section id="tests-jobs" className="settings-section" aria-labelledby="tests-jobs-title">
      <h3 id="tests-jobs-title">Jobs</h3>
      <div className="situation-actions">
        <Tip text={`${job && job.runs > 0 && !unsized ? `Start runs up to ${counted(job.runs, "run", "runs")}, about ${duration(job.seconds)}: that total is the test's ceiling. ` : ""}${answer && !answer.accepted ? "The first time, Start says what a test runs and asks once." : ""}`.trim() || "Start a test of the ticked targets"}>
          <button type="button" className="toggle on" disabled={!answer || !plan || !job || reasons.length > 0 || starting} onClick={pressStart}>
            {starting ? "Starting…" : "Start"}
          </button>
        </Tip>
        {mine.length > openJobs.length && (
          <a className="text-button" href={href("results")} onClick={(e) => { e.preventDefault(); go(href("results")); }}>Past tests: Results</a>
        )}
      </div>
      {reasons.length > 0 && (
        <ul className="start-blocked" aria-label="Why Start is disabled">
          {reasons.map((why) => <li key={why}>{why}</li>)}
        </ul>
      )}
      {refusal && <p className="refusal" role="alert">Not started: {refusal}</p>}
      {running && answer && <p className="setting-note" role="status">{answer.keep_awake}</p>}
      {error && <p className="refusal" role="alert">The jobs couldn't be read: {error}</p>}
      {openJobs.length > 0 && (
        <ul className="dismissals" aria-label="Jobs">
          {openJobs.map((entry) => (
            <JobRow key={entry.job_id} job={entry} tests={tests} go={go} busyElsewhere={running} onChanged={refresh} onNote={setNote} />
          ))}
        </ul>
      )}
      {note && <p className="setting-note">{note}</p>}
      {plan && <RemovedTools tools={plan.tools} />}
      {onboarding && (
        <Onboarding answer={onboarding.answer} error={onboarding.error} busy={starting} onYes={yes}
                    onNo={() => setOnboarding(null)} go={go} />
      )}
    </section>
  );
}
