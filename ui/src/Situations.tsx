// Tests › Situations: written by the generator for the targets you tick (never by hand, D12), each with what
// matters in it, written kept and reviewed only where you choose to — drop or edit one, or keep it again — in the order
// written. Each target is one line, folded: its name, its situations in review's words and Write again; opening it
// shows its situations. Only kept and edited situations run (docs/tech_impl/11-ui.md, "Tests"; 04-probe-generator.md,
// "Review").
import { useEffect, useId, useRef, useState } from "react";
import type { PlanAnswer, PlanTarget, Situation, SituationsAnswer } from "./contract";
import { counted, diagnosticKind } from "./format";
import { POLL_MS, reviewWords, usePoll, type TestsApi } from "./testsApi";
import { Icon, MARK, Tip } from "./ui";

/** The review words a person reads beside a situation. */
const REVIEW_SHOWN: Record<Situation["review"], string> = { kept: "kept", edited: "kept, edited", dropped: "dropped" };

function SituationRow({ situation, tests, onReviewed }: {
  situation: Situation; tests: TestsApi; onReviewed: (next: Situation) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(situation.text);
  const [names, setNames] = useState<string[]>([]); // what an edit names of its target, as the guard found it
  const decide = (decision: "keep" | "drop" | "edit") => {
    setBusy(true);
    setError(null);
    tests.review(situation.id, decision, decision === "edit" ? draft : undefined).then((next) => {
      setBusy(false);
      setEditing(false);
      setNames(next.names_its_target ?? []);
      onReviewed(next);
    }, (failed: Error) => { setBusy(false); setError(failed.message); });
  };
  const labelled = `situation-${situation.id}`;
  return (
    <li className="situation" aria-labelledby={labelled} aria-busy={busy}>
      {editing ? (
        <form onSubmit={(e) => { e.preventDefault(); decide("edit"); }}>
          <label className="sheet-field">
            <span id={labelled}>Edit the situation</span>
            <textarea className="field wide area" value={draft} rows={4} disabled={busy} autoFocus
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Escape") { setEditing(false); setDraft(situation.text); } }} />
          </label>
          <div className="situation-actions">
            <button type="submit" className="toggle on" disabled={busy || !draft.trim()}>Save and keep</button>
            <button type="button" className="toggle" disabled={busy} onClick={() => { setEditing(false); setDraft(situation.text); }}>Cancel</button>
          </div>
        </form>
      ) : (
        <>
          <p id={labelled} className={situation.review === "dropped" ? "situation-text muted" : "situation-text"}>{situation.text}</p>
          <p className="situation-meta">
            <Tip text={`What matters · ${situation.matters}`}><span>What matters: {situation.matters_meaning ?? situation.matters}{situation.matters_thing ? `: ${situation.matters_thing}` : ""}</span></Tip>
            {" · "}<span className="situation-review muted">{REVIEW_SHOWN[situation.review]}</span>
          </p>
          <div className="situation-actions" role="group" aria-label="Review">
            <button type="button" className={`toggle${situation.review === "kept" ? " on" : ""}`} aria-pressed={situation.review === "kept"}
                    disabled={busy} onClick={() => decide("keep")}>Keep</button>
            <button type="button" className={`toggle${situation.review === "dropped" ? " on" : ""}`} aria-pressed={situation.review === "dropped"}
                    disabled={busy} onClick={() => decide("drop")}>Drop</button>
            <button type="button" className={`toggle${situation.review === "edited" ? " on" : ""}`} aria-pressed={situation.review === "edited"}
                    disabled={busy} onClick={() => { setDraft(situation.text); setEditing(true); }}>Edit…</button>
          </div>
        </>
      )}
      {names.length > 0 && (
        <p className="setting-note" role="alert">
          This text names what it tests ({names.join(", ")}): runs would be told what the target says. Edit it again so it describes only the situation.
        </p>
      )}
      {error && <p className="refusal" role="alert">Not saved: {error}</p>}
    </li>
  );
}

function TargetSituations({ target, answer, writing, tests, took, again, onReviewed }: {
  target: PlanTarget; answer: SituationsAnswer | undefined; writing: boolean; tests: TestsApi;
  took: (answer: SituationsAnswer) => void; again: () => void; onReviewed: () => void;
}) {
  const situations = answer ? answer.situations : []; // in the order written, which is the order a test takes them
  const reviewed = (next: Situation) => {
    if (answer) took({ ...answer, situations: answer.situations.map((s) => (s.id === next.id ? next : s)) });
    onReviewed();
  };
  const headed = useId(); // a target's id holds spaces, which an aria-labelledby list can't
  const [open, setOpen] = useState(false); // folded until you open it: review is the exception
  const tally = answer ? { kept: 0, dropped: 0, edited: 0 } : target.situations;
  if (answer && tally) for (const s of answer.situations) tally[s.review] += 1;
  const listed = `situations-of-${headed}`;
  return (
    <li className="situations-target" aria-labelledby={headed}>
      <div className="dismissal-head">
        <button type="button" className="fold" aria-expanded={open} aria-controls={open ? listed : undefined}
                disabled={!situations.length} onClick={() => setOpen(!open)}>
          <Icon icon={MARK.disclose} className={open ? "fold-chev open" : "fold-chev"} />
          <Tip text={target.id} className="dismissal-title"><span id={headed}>{target.name}</span></Tip>
        </button>
        <span className="muted">{writing ? "writing…" : reviewWords(tally)}</span>
        <Tip text="Write this target's situations again: they replace the ones there, reviewed ones too">
          <button type="button" className="toggle" disabled={writing} onClick={again}>Write again</button>
        </Tip>
      </div>
      {!answer && !writing && reviewWords(target.situations) === "none written" && <p className="muted">Not written yet.</p>}
      {answer && !writing && !answer.situations.length && <p className="muted">No situation was kept from the generator for this target.</p>}
      {answer && answer.diagnostics.length > 0 && (
        <ul className="situations-notes" aria-label="What the generator said">
          {answer.diagnostics.slice(0, 3).map((d, i) => (
            <li key={i}><Tip text={d.kind}><span>{diagnosticKind(d.kind)}</span></Tip>: {d.message}</li>
          ))}
        </ul>
      )}
      {open && situations.length > 0 && (
        <ul id={listed} className="situation-list">
          {situations.map((s) => <SituationRow key={s.id} situation={s} tests={tests} onReviewed={reviewed} />)}
        </ul>
      )}
    </li>
  );
}

/** The situations of the ticked targets and of every target that has some, in the ranked order; Write starts the
 * generator for the ticked ones that have none, in the background, and the page asks every few seconds until it is
 * done. */
export function SituationsSection({ tests, plan, ticked, answers, took, onWritten, onReviewed }: {
  tests: TestsApi; plan: PlanAnswer; ticked: string[]; answers: Record<string, SituationsAnswer>;
  took: (answer: SituationsAnswer) => void; onWritten: () => void; onReviewed: () => void;
}) {
  const [writing, setWriting] = useState<Set<string>>(new Set());
  const [refusal, setRefusal] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const targets = plan.targets;
  const chosen = new Set(ticked);
  const total = (t: PlanTarget) => t.situations.kept + t.situations.dropped + t.situations.edited;
  const written = (t: PlanTarget) => (answers[t.id] ? answers[t.id].situations.length > 0 : total(t) > 0);
  const shown = targets.filter((t) => chosen.has(t.id) || total(t) > 0 || writing.has(t.id) || answers[t.id]?.situations.length);

  // Each shown target's situations, asked once; a target being written is asked again until it is done.
  const asked = useRef(new Set<string>());
  useEffect(() => {
    for (const t of shown) {
      if (asked.current.has(t.id) || total(t) === 0) continue;
      asked.current.add(t.id);
      tests.situations(t.id).then(took, (e: Error) => setFailed(e.message));
    }
  });
  const writingNow = useRef(writing);
  writingNow.current = writing;
  const poll = () => {
    for (const id of writing) {
      tests.situations(id).then((answer) => {
        took(answer);
        if (answer.generating) return;
        const next = new Set(writingNow.current);
        if (!next.delete(id)) return;
        writingNow.current = next;
        setWriting(next);
        if (!next.size) onWritten(); // all written: the plan's counts and Start's job change
      }, (e: Error) => setFailed(e.message));
    }
  };
  usePoll(poll, POLL_MS, writing.size > 0);

  const write = (ids: string[], again: boolean) => {
    setRefusal(null);
    tests.generate(ids, again).then((answer) => {
      for (const id of answer.generating) asked.current.add(id);
      setWriting((was) => new Set([...was, ...answer.generating]));
    }, (e: Error) => setRefusal(e.message));
  };
  // The ticked targets with none written: a target written before keeps its situations (Write again replaces them).
  const wanted = targets.filter((t) => chosen.has(t.id) && !writing.has(t.id) && !written(t)).map((t) => t.id);
  return (
    <section id="tests-situations" className="settings-section" aria-labelledby="tests-situations-title">
      <h3 id="tests-situations-title">
        <Tip text="A model writes a few situations for each target's text, each saying what in the agent's actions the target should change. Each is kept as written and runs: open a target to drop or edit any you don't want run. A target written before keeps its situations until you write it again.">
          <span>Situations</span>
        </Tip>
      </h3>
      <div className="situation-actions">
        <button type="button" className="toggle on" disabled={!wanted.length} onClick={() => write(wanted, false)}>
          Write situations for {counted(wanted.length, "ticked target", "ticked targets")}
        </button>
        {writing.size > 0 && <span className="muted" role="status">Writing for {counted(writing.size, "target", "targets")}…</span>}
      </div>
      {refusal && <p className="refusal" role="alert">Not started: {refusal}</p>}
      {failed && <p className="refusal" role="alert">Situations couldn't be read: {failed}</p>}
      {shown.length > 0 ? (
        <ul className="situations">
          {shown.map((t) => (
            <TargetSituations key={t.id} target={t} answer={answers[t.id]} writing={writing.has(t.id)} tests={tests} took={took}
                              again={() => write([t.id], true)} onReviewed={onReviewed} />
          ))}
        </ul>
      ) : <p className="muted">Tick targets in the plan, then write their situations.</p>}
    </section>
  );
}
