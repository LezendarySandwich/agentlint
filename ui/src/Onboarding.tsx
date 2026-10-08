// The one-time test onboarding (D18): what Start would run of the ticked targets and for about how long, the tools
// removed from every run and why, that hooks run, the harness's telemetry, keeping the machine awake, and each test
// setting — then one question, "Run with these?". Yes is recorded by the server and the screen never shows again; No
// records nothing (docs/tech_impl/11-ui.md, "Tests").
import { useRef, useState } from "react";
import type { OnboardingAnswer } from "./contract";
import { count, counted } from "./format";
import { href } from "./route";
import { settingName, valueWords } from "./Settings";
import { consistencyOnlyWords, shortWords, sizeWords, stepsWords } from "./testsApi";
import { Icon, MARK, More, useModal } from "./ui";

const FIRST_TOOLS = 8; // removed tools listed before More

export function Onboarding({ answer, error, busy, onYes, onNo, go }: {
  answer: OnboardingAnswer | null; // null while it is read: it reads the project as a test would
  error: string | null; // reading it, accepting or starting failed, in the server's words
  busy: boolean; // accepting and starting
  onYes: () => void;
  onNo: () => void;
  go: (to: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [allTools, setAllTools] = useState(false);
  useModal(dialog); // closed, focus goes back to Start
  const removed = answer ? answer.removed_tools.filter((t) => t.removed) : [];
  const kept = answer ? answer.removed_tools.length - removed.length : 0;
  const listed = allTools ? removed : removed.slice(0, FIRST_TOOLS);
  return (
    <dialog ref={dialog} className="sheet onboarding" aria-labelledby="onboarding-title"
            onCancel={(e) => { e.preventDefault(); if (!busy) onNo(); }}
            onKeyDown={(e) => e.stopPropagation()}>
      <div className="sheet-body">
        <header className="detail-head">
          <h2 id="onboarding-title">Before your first test</h2>
          <button type="button" className="icon-button" onClick={onNo} disabled={busy} aria-label="Close"><Icon icon={MARK.close} size={16} /></button>
        </header>
        {!answer ? (
          error ? <p role="alert">What a test would run couldn't be read: {error}</p>
            : <p className="muted" role="status">Reading what a test would run…</p>
        ) : (
          <>
            <p>
              Start runs the agent on the targets you ticked: {sizeWords(answer.job)} ({count(answer.seconds_per_run)} s a run, from {answer.seconds_from}).
              It runs in the background, survives closing this page, and stops when you press Stop, or when it has run that many.
            </p>
            {stepsWords(answer.job) && <p>{stepsWords(answer.job)}</p>}
            {shortWords(answer.job) && <p>{shortWords(answer.job)}</p>}
            {consistencyOnlyWords(answer.job) && <p>{consistencyOnlyWords(answer.job)}</p>}
            <p>
              Every run asks for {answer.model.value ? <code>{answer.model.value}</code> : "no model yet"}
              {answer.model.from === "setting" ? " (your model setting)" : answer.model.from === "sessions" ? " (the model your sessions use)" : ""}.
            </p>
            <p>{answer.policy}</p>
            <p>
              {counted(removed.length, "tool is", "tools are")} removed from every run{kept ? `; ${counted(kept, "other tool stays", "other tools stay")}` : ""}:
            </p>
            {listed.length > 0 && (
              <ul className="sheet-list onboarding-tools" aria-label="Removed from every run">
                {listed.map((tool) => <li key={tool.name}><code>{tool.name}</code> <span className="muted">— {tool.why}</span></li>)}
              </ul>
            )}
            {!allTools && removed.length > FIRST_TOOLS && <More hidden={removed.length - FIRST_TOOLS} onClick={() => setAllTools(true)} />}
            <p>{answer.hooks}</p>
            <p>{answer.telemetry}</p>
            <p>{answer.keep_awake}</p>
            <p>Each test runs with these settings:</p>
            <ul className="sheet-list" aria-label="Test settings">
              {answer.settings.map((entry) => (
                <li key={entry.key}>
                  {settingName(entry.key)}: {valueWords(entry, entry.value)}{entry.set ? " (you set it)" : " (the default)"}
                </li>
              ))}
            </ul>
            <p>
              <button type="button" className="text-button" disabled={busy} onClick={() => { onNo(); go(href("settings", null, { at: "tests" })); }}>
                Change them in Settings › Tests
              </button>
            </p>
            {answer.blocked.length > 0 && (
              <div role="alert">
                <p>A test can't start now:</p>
                <ul className="sheet-list">{answer.blocked.map((why) => <li key={why}>{why}</li>)}</ul>
              </div>
            )}
            {error && <p className="refusal" role="alert">Not started: {error}</p>}
            <div className="sheet-actions">
              <span className="onboarding-question" id="onboarding-question">Run with these?</span>
              <button type="button" className="toggle" onClick={onNo} disabled={busy}>No</button>
              <button type="button" className="toggle on" onClick={onYes} disabled={busy || answer.blocked.length > 0}
                      aria-describedby="onboarding-question">{busy ? "Starting…" : "Yes"}</button>
            </div>
          </>
        )}
      </div>
    </dialog>
  );
}
