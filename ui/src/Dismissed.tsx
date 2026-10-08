// Dismissing a finding and showing it again, and the Dismissed screen: every dismissal across projects, with its date,
// its reason and what it does in each project (docs/tech_impl/11-ui.md, "Views › 3. Findings" and "Views › 8.
// Dismissed"). Dismissals are written by the server, which checks the project first and refuses what it won't do —
// shown in place.
import { useEffect, useState } from "react";
import type { Dismissed, ShownAgain, Suppression, Suppressions } from "./contract";
import { checkName, counted, day, home } from "./format";
import { Tip } from "./ui";

/** What dismissing asks the server (Live.tsx's `servedApi`; fakes in Dismissed.test.tsx). */
export interface DismissApi {
  /** Dismiss a finding seen in a project (and harness: the scope's), with why. */
  suppress: (finding: string, project: string, harness: string | null, reason: string) => Promise<Dismissed>;
  /** Show a dismissed finding again, everywhere. */
  unsuppress: (finding: string) => Promise<ShownAgain>;
  suppressions: () => Promise<Suppressions>;
}

/** What a dismissal does in one project, in words. */
export const STATE_WORDS: Record<Suppression["projects"][number]["state"], string> = {
  active: "hidden",
  lapsed: "shown again: what it rests on changed",
  moot: "no longer found",
};

function Entry({ entry, homeDir, dismiss, onShownAgain }: {
  entry: Suppression; homeDir: string; dismiss: DismissApi; onShownAgain: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const showAgain = () => {
    setBusy(true);
    setError(null);
    dismiss.unsuppress(entry.id).then(() => onShownAgain(entry.id), (failed: Error) => { setError(failed.message); setBusy(false); });
  };
  const labelled = `dismissed-${entry.id}`;
  return (
    <li className="dismissal" aria-labelledby={labelled} aria-busy={busy}>
      <div className="dismissal-head">
        <span id={labelled} className={entry.title ? "dismissal-title" : "dismissal-title muted"}>
          {entry.title ?? "No longer recorded in any project"}
        </span>
        <button type="button" className="toggle" onClick={showAgain} disabled={busy}>Show again</button>
      </div>
      <p className="dismissal-meta">
        {entry.check && <>{checkName(entry.check)} · </>}
        <Tip text={entry.id}><code>{entry.id.slice(0, 12)}</code></Tip> · dismissed {day(entry.at)}
      </p>
      <p className="dismissal-reason">“{entry.reason}”</p>
      {entry.projects.length ? (
        <ul className="dismissal-projects" aria-label="Where">
          {entry.projects.map(({ project, state }) => (
            <li key={project}>
              <Tip text={project}><span className="dismissal-project">{home(project, homeDir)}</span></Tip>
              <span className={state === "lapsed" ? "dismissal-state" : "dismissal-state muted"}>{STATE_WORDS[state]}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="dismissal-meta">Found in no project yet: it is hidden the first time a check sees it.</p>
      )}
      {error && <p className="refusal" role="alert">Not shown again: {error}</p>}
    </li>
  );
}

/** The Dismissed screen. `dismiss` is absent only on a page with no server behind it to keep dismissals on (`servedApi`
 * always gives one). */
export function DismissedView({ dismiss, onShownAgain }: {
  dismiss?: DismissApi;
  onShownAgain?: (id: string) => void; // the report in view, if it holds that finding, shows it again too
}) {
  const [answer, setAnswer] = useState<Suppressions | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!dismiss) return;
    let current = true;
    dismiss.suppressions().then((next) => { if (current) setAnswer(next); }, (failed: Error) => { if (current) setError(failed.message); });
    return () => { current = false; };
  }, [dismiss]);

  const body = (() => {
    if (!dismiss) return <p className="muted">Dismissals live in agentlint ui: run it to see them and show a finding again.</p>;
    if (!answer) return error ? <p role="alert">Dismissals couldn't be read: {error}</p> : <p className="muted">Reading your dismissals…</p>;
    if (!answer.suppressions.length) {
      return <p className="muted">Nothing dismissed. Dismiss a finding from its detail in Findings — press s — and it stays hidden until what it rests on changes.</p>;
    }
    const shownAgain = (id: string) => {
      setAnswer((was) => was && { ...was, suppressions: was.suppressions.filter((s) => s.id !== id) });
      onShownAgain?.(id);
    };
    return (
      <>
        <p className="settings-line">
          {counted(answer.suppressions.length, "finding", "findings")} dismissed, across every project. Each stays hidden in a project until what it rests on there changes.
        </p>
        <ul className="dismissals">
          {answer.suppressions.map((entry) => (
            <Entry key={entry.id} entry={entry} homeDir={answer.home_dir} dismiss={dismiss} onShownAgain={shownAgain} />
          ))}
        </ul>
      </>
    );
  })();
  return (
    <div className="settings">
      <div className="settings-body">
        <h2>Dismissed</h2>
        {body}
      </div>
    </div>
  );
}
