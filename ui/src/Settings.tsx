// Settings: every setting with its default, saved as you change it; the daily sync; what the archive holds; the
// version; and Privacy › Forget everything (docs/tech_impl/11-ui.md, "Settings"). Live only: everything here is read
// from and written to the server, which checks every value and says why it refuses one — shown beside the setting.
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { Forgotten, SettingEntry, SettingsAnswer } from "./contract";
import { bytes, count, counted, home, when } from "./format";
import { Icon, MARK, Tip, useModal } from "./ui";

/** What Settings asks the live server (Live.tsx's `servedApi`; fakes in Settings.test.tsx). */
export interface ControlApi {
  settings: () => Promise<SettingsAnswer>;
  save: (key: string, value: unknown) => Promise<SettingsAnswer>;
  reset: (key: string) => Promise<SettingsAnswer>;
  schedule: (on: boolean) => Promise<SettingsAnswer>;
  forget: (confirm: string) => Promise<Forgotten>;
  welcomed: () => Promise<unknown>;
}

/** What Forget everything's dialog must be typed: the server requires the same word. */
export const CONFIRM_WORD = "forget";

/** Each setting's name for a person, and the unit its number counts (both forms: no plural is guessed). */
const NAMES: Record<string, { name: string; unit?: [string, string] }> = {
  harnesses: { name: "Harnesses" },
  sessions: { name: "Recent sessions read", unit: ["session", "sessions"] },
  usage_days: { name: "Usage window", unit: ["day", "days"] },
  keep_days: { name: "Keep for", unit: ["day", "days"] },
  situations_per_target: { name: "Situations per target", unit: ["situation", "situations"] },
  runs_per_situation: { name: "Runs per situation", unit: ["run", "runs"] },
  run_timeout_s: { name: "Time limit per run", unit: ["second", "seconds"] },
  model: { name: "Model" },
  keep_tools: { name: "Tools kept" },
  remove_tools: { name: "Tools removed" },
};

/** How an empty value reads, by what the setting holds (settings.py's EMPTY): never a blank. */
const EMPTY_WORDS = { harness: "every harness found", tools: "none", model: "the model your sessions use" };

/** What Forget everything deletes, and what it keeps (store/usage.py's `forget`). */
export const FORGET_GOES = [
  "every archived transcript, your sessions' and their subagents'",
  "all usage history",
  "every stored configuration reading",
  "every finding",
  "every test job and its runs",
  "every generated test situation",
  "every diagnostic agentlint recorded about itself",
];
export const FORGET_STAYS = ["your settings", "your dismissals (suppressed findings)"];

/** A setting's name for a person: the key itself when it has none. */
export const settingName = (key: string): string => NAMES[key]?.name ?? key;

/** A setting's value in words: its unit counted, a list joined, an empty value said ("none"). */
export const valueWords = (entry: SettingEntry, value: SettingEntry["value"]): string => {
  if (typeof value === "string") return value || EMPTY_WORDS.model;
  if (Array.isArray(value)) return value.length ? value.join(", ") : EMPTY_WORDS[entry.kind === "tools" ? "tools" : "harness"];
  const unit = NAMES[entry.key]?.unit;
  return unit ? counted(value, unit[0], unit[1]) : count(value);
};

/** A typed number as the server should judge it: a number when it reads as one, else what was typed (refused there). */
const typed = (draft: string): unknown => (/^-?\d+$/.test(draft.trim()) ? Number(draft.trim()) : draft);

function Row({ entry, error, busy, children, onReset }: {
  entry: SettingEntry; error: string | null; busy: boolean; children: ReactNode; onReset: () => void;
}) {
  const naming = settingName(entry.key);
  const range = entry.range ? ` From ${count(entry.range[0])} to ${count(entry.range[1])}.` : "";
  return (
    <div className="setting" aria-busy={busy}>
      <Tip text={`agentlint config set ${entry.key}`} className="setting-name"><span id={`setting-${entry.key}-name`}>{naming}</span></Tip>
      <div className="setting-control">{children}</div>
      <div className="setting-default">
        {entry.set || entry.problem
          ? <button type="button" className="text-button" onClick={onReset}>Reset to {valueWords(entry, entry.default)}</button>
          : <span className="muted">Default</span>}
      </div>
      <p className="setting-meaning">{entry.meaning}{range}{entry.key === "harnesses" ? " An open agentlint ui takes a change the next time it starts." : ""}</p>
      {entry.problem && <p className="setting-note" role="alert">What is stored can't be used, so the default is in effect: {entry.problem}</p>}
      {entry.note && <p className="setting-note">{entry.note}</p>}
      {error && <p className="setting-note refusal" role="alert">Not saved: {error}</p>}
    </div>
  );
}

function NumberSetting({ entry, save }: { entry: SettingEntry; save: (value: unknown) => void }) {
  const [draft, setDraft] = useState(String(entry.value));
  useEffect(() => setDraft(String(entry.value)), [entry.value]);
  // Sent when changed. Enter also sends the value shown while what is stored can't be used — that value is the default
  // standing in, so Enter keeps it; leaving the field unchanged sends nothing, so tabbing past writes nothing.
  const commit = (pressed: boolean) => { if (draft !== String(entry.value) || (pressed && entry.problem)) save(typed(draft)); };
  return (
    <input className="field" inputMode="numeric" value={draft} aria-labelledby={`setting-${entry.key}-name`}
           onChange={(e) => setDraft(e.target.value)} onBlur={() => commit(false)}
           onKeyDown={(e) => { if (e.key === "Enter") commit(true); else if (e.key === "Escape") setDraft(String(entry.value)); }} />
  );
}

/** A list of tool names as typed — separated by commas or spaces — for the server to judge. */
export const toolsTyped = (draft: string): string[] => draft.split(/[\s,]+/).filter(Boolean);

/** A text setting saved when you leave the field or press Enter, as a number is (never per keystroke); Escape puts
 * back what is saved. `send` gets what was typed, trimmed, and decides what is written. */
function TextSetting({ entry, shown, placeholder, send }: {
  entry: SettingEntry; shown: string; placeholder: string; send: (draft: string) => void;
}) {
  const [draft, setDraft] = useState(shown);
  useEffect(() => setDraft(shown), [shown]);
  const commit = (pressed: boolean) => { if (draft.trim() !== shown || (pressed && entry.problem)) send(draft.trim()); };
  return (
    <input className="field wide" value={draft} placeholder={placeholder} spellCheck={false} autoComplete="off"
           aria-labelledby={`setting-${entry.key}-name`}
           onChange={(e) => setDraft(e.target.value)} onBlur={() => commit(false)}
           onKeyDown={(e) => { if (e.key === "Enter") commit(true); else if (e.key === "Escape") setDraft(shown); }} />
  );
}

/** The model: a name as the harness takes it, or emptied to go back to your sessions' (the setting unset). */
function ModelSetting({ entry, save, reset }: { entry: SettingEntry; save: (value: unknown) => void; reset: () => void }) {
  const shown = typeof entry.value === "string" ? entry.value : "";
  return <TextSetting entry={entry} shown={shown} placeholder={EMPTY_WORDS.model}
                      send={(draft) => (draft ? save(draft) : entry.set || entry.problem ? reset() : undefined)} />;
}

/** A list of tools, typed as names separated by commas or spaces; emptied, none. */
function ToolsSetting({ entry, save }: { entry: SettingEntry; save: (value: unknown) => void }) {
  const shown = Array.isArray(entry.value) ? entry.value.join(", ") : "";
  return <TextSetting entry={entry} shown={shown} placeholder={EMPTY_WORDS.tools} send={(draft) => save(toolsTyped(draft))} />;
}

function HarnessSetting({ entry, save }: { entry: SettingEntry; save: (value: unknown) => void }) {
  const chosen = Array.isArray(entry.value) ? entry.value : [];
  const names = [...(entry.choices ?? []), ...chosen.filter((name) => !(entry.choices ?? []).includes(name))];
  // The server takes only the choices it offers: a name set from the command line that agentlint can't read is shown
  // ticked, and dropped by the next change here.
  const readable = (list: string[]) => list.filter((n) => (entry.choices ?? []).includes(n));
  const flip = (name: string) => save(readable(chosen.includes(name) ? chosen.filter((n) => n !== name) : names.filter((n) => n === name || chosen.includes(n))));
  return (
    <div className="choices" role="group" aria-labelledby={`setting-${entry.key}-name`}>
      {names.map((name) => (
        <label key={name} className="choice">
          <input type="checkbox" checked={chosen.includes(name)} onChange={() => flip(name)} /> {name}
        </label>
      ))}
      <span className="muted">{chosen.length ? "only these" : "none ticked: every harness found"}</span>
    </div>
  );
}

/** The dialog: what goes, what stays, and a field that must read the word before the button does anything. */
export function ForgetDialog({ control, running, onClose, onForgotten }: {
  control: ControlApi; running: SettingsAnswer["jobs_running"]; onClose: () => void; onForgotten: (answer: SettingsAnswer) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [word, setWord] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Forgotten | null>(null);
  useModal(dialog); // closed, focus goes back to Forget everything…
  const ready = word === CONFIRM_WORD && !running.length && !busy;
  const forget = () => {
    if (!ready) return;
    setBusy(true);
    setError(null);
    control.forget(CONFIRM_WORD).then((answer) => { setDone(answer); setBusy(false); onForgotten(answer.settings); },
      (failed: Error) => { setError(failed.message); setBusy(false); });
  };
  return (
    <dialog ref={dialog} className="sheet" aria-labelledby="forget-title"
            onCancel={(e) => { e.preventDefault(); if (!busy) onClose(); }}
            onKeyDown={(e) => e.stopPropagation()}>
      <div className="sheet-body">
        <header className="detail-head">
          <h2 id="forget-title">Forget everything</h2>
          <button type="button" className="icon-button" onClick={onClose} disabled={busy} aria-label="Close"><Icon icon={MARK.close} size={16} /></button>
        </header>
        {done ? (
          <div role="status">
            <p>Forgotten: {counted(done.turns, "turn", "turns")} of usage, {counted(done.transcripts, "archived transcript", "archived transcripts")}, and every reading, finding, test run, situation and diagnostic.</p>
            {done.undeleted.length > 0 && (
              <div role="alert">
                <p>{counted(done.undeleted.length, "test run's directory", "test runs' directories")} couldn't be deleted; the next forget or test run tries again:</p>
                <ul className="sheet-list">{done.undeleted.map((failure) => <li key={failure}><code>{failure}</code></li>)}</ul>
              </div>
            )}
            <p className="muted">From now on agentlint reads no session that started before this moment, and counts only turns taken after it.</p>
            <div className="sheet-actions"><button type="button" className="toggle" onClick={onClose}>Close</button></div>
          </div>
        ) : (
          <form onSubmit={(e) => { e.preventDefault(); forget(); }}>
            <p>This deletes, and can't be undone:</p>
            <ul className="sheet-list">{FORGET_GOES.map((item) => <li key={item}>{item}</li>)}</ul>
            <p>It keeps:</p>
            <ul className="sheet-list">{FORGET_STAYS.map((item) => <li key={item}>{item}</li>)}</ul>
            {running.length > 0 && (
              <p role="alert">Running now: {counted(running.length, "test job", "test jobs")} ({running.map((job) => job.job_id).join(", ")}). Nothing can be forgotten until no test job runs.</p>
            )}
            <label className="sheet-field">
              <span>Type <code>{CONFIRM_WORD}</code> to confirm</span>
              <input className="field wide" value={word} autoComplete="off" spellCheck={false} autoFocus disabled={busy}
                     onChange={(e) => setWord(e.target.value)} />
            </label>
            {error && <p className="refusal" role="alert">Nothing was deleted: {error}</p>}
            <div className="sheet-actions">
              <button type="button" className="toggle" onClick={onClose} disabled={busy}>Cancel</button>
              <button type="submit" className="toggle on" disabled={!ready}>{busy ? "Forgetting…" : "Forget everything"}</button>
            </div>
          </form>
        )}
      </div>
    </dialog>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={`settings-${id}`} className="settings-section" aria-labelledby={`settings-${id}-title`}>
      <h3 id={`settings-${id}-title`}>{title}</h3>
      {children}
    </section>
  );
}

function lastSync(last: SettingsAnswer["sync"]["last"]): string {
  if (!last) return "No sync has run yet.";
  if (!last.ok) return `The last sync, ${when(last.at)}, failed: ${last.error ?? "no reason recorded"}.`;
  return `The last sync, ${when(last.at)}: ${counted(last.changed ?? 0, "transcript", "transcripts")} archived, ${counted(last.turns ?? 0, "turn", "turns")} of usage derived.`;
}

/** The Settings screen. `control` is absent only on a page with no server behind it to change anything on (`servedApi`
 * always gives one). */
export function SettingsView({ control, route, answer: given, onAnswer }: {
  control?: ControlApi; route: { params: URLSearchParams }; // `?at=privacy` scrolls to that section
  answer?: SettingsAnswer | null; // the page's last answer (Live), shown at once while it is asked again
  onAnswer?: (answer: SettingsAnswer) => void; // a newer answer, for the page
}) {
  const [answer, setAnswer] = useState<SettingsAnswer | null>(given ?? null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string | null>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [forgetting, setForgetting] = useState(false);
  const took = useCallback((next: SettingsAnswer) => { setAnswer(next); onAnswer?.(next); }, [onAnswer]);
  useEffect(() => {
    if (!control) return;
    let current = true;
    control.settings().then((next) => { if (current) took(next); }, (failed: Error) => { if (current) setLoadError(failed.message); });
    return () => { current = false; };
  }, [control]); // eslint-disable-line react-hooks/exhaustive-deps -- once per page
  const at = route.params.get("at");
  useEffect(() => {
    if (answer && at) document.getElementById(`settings-${at}`)?.scrollIntoView?.({ block: "start" });
  }, [answer, at]);
  const write = (name: string, call: () => Promise<SettingsAnswer>) => {
    setBusy(name);
    setErrors((was) => ({ ...was, [name]: null }));
    call().then((next) => { took(next); setBusy(null); },
      (failed: Error) => { setErrors((was) => ({ ...was, [name]: failed.message })); setBusy(null); });
  };

  if (!control) {
    return <div className="settings"><div className="settings-body"><h2>Settings</h2><p className="muted">Settings live in agentlint ui: run it to see and change them.</p></div></div>;
  }
  if (!answer) {
    return (
      <div className="settings"><div className="settings-body"><h2>Settings</h2>
        {loadError ? <p role="alert">Settings couldn't be read: {loadError}</p> : <p className="muted">Reading your settings…</p>}
      </div></div>
    );
  }
  const { sync, archive } = answer;
  return (
    <div className="settings">
      <div className="settings-body">
        <h2>Settings</h2>
        {answer.attention.length > 0 && (
          <div className="settings-attention" role="alert">
            {answer.attention.map((why) => <p key={why}>{why}</p>)}
          </div>
        )}
        <Section id="reading" title="Reading and keeping">
          {answer.settings.map((entry) => (
            <Row key={entry.key} entry={entry} error={errors[entry.key] ?? null} busy={busy === entry.key}
                 onReset={() => write(entry.key, () => control.reset(entry.key))}>
              {entry.choices
                ? <HarnessSetting entry={entry} save={(value) => write(entry.key, () => control.save(entry.key, value))} />
                : <NumberSetting entry={entry} save={(value) => write(entry.key, () => control.save(entry.key, value))} />}
            </Row>
          ))}
        </Section>
        {answer.test_settings && answer.test_settings.length > 0 && (
          <Section id="tests" title="Tests">
            <p className="settings-line">
              What a new test runs with. A test's size is the targets you tick on Tests × situations per target × runs per situation, and it runs exactly that many. A test keeps what it started with: a change here applies to the next one. The tools a run removes now, and those it keeps, each with why, are listed on Tests, under Jobs.
            </p>
            {answer.test_settings.map((entry) => {
              const save = (value: unknown) => write(entry.key, () => control.save(entry.key, value));
              const reset = () => write(entry.key, () => control.reset(entry.key));
              return (
                <Row key={entry.key} entry={entry} error={errors[entry.key] ?? null} busy={busy === entry.key} onReset={reset}>
                  {entry.kind === "model" ? <ModelSetting entry={entry} save={save} reset={reset} />
                    : entry.kind === "tools" ? <ToolsSetting entry={entry} save={save} />
                    : <NumberSetting entry={entry} save={save} />}
                </Row>
              );
            })}
          </Section>
        )}
        <Section id="sync" title="Daily sync">
          <div className="setting">
            <span className="setting-name" id="sync-name">Daily sync</span>
            <div className="setting-control">
              <button type="button" role="switch" aria-checked={sync.installed} aria-labelledby="sync-name"
                      className={`toggle${sync.installed ? " on" : ""}`} disabled={busy === "sync"}
                      onClick={() => write("sync", () => control.schedule(!sync.installed))}>
                {sync.installed ? "On" : "Off"}
              </button>
            </div>
            <div className="setting-default" />
            <p className="setting-meaning">
              {sync.installed
                ? `${sync.scheduler} runs agentlint sync each day at ${sync.time}${sync.where ? ` (${home(sync.where, answer.home_dir)})` : ""}, so transcripts are archived before the harness deletes them, even when agentlint isn't run.`
                : `Off. On, ${sync.scheduler} runs agentlint sync each day at ${sync.time} — outside the harness — so transcripts are archived before the harness deletes them.`}
            </p>
            <p className="setting-note">{lastSync(sync.last)}</p>
            {sync.error && <p className="setting-note" role="alert">The scheduler couldn't be asked: {sync.error}</p>}
            {errors.sync && <p className="setting-note refusal" role="alert">Not changed: {errors.sync}</p>}
          </div>
        </Section>
        <Section id="archive" title="Archive">
          <p className="settings-line">
            {counted(archive.transcripts, "transcript", "transcripts")} kept in {home(answer.store, answer.home_dir)} — {bytes(archive.bytes_written)} as written, {bytes(archive.bytes_stored)} stored.
          </p>
        </Section>
        <Section id="privacy" title="Privacy">
          <p className="settings-line">
            agentlint keeps what it reads on this machine only, readable by you only, and deletes what is older than the Keep for setting. Nothing leaves this machine.
          </p>
          <div className="setting">
            <span className="setting-name">Forget everything</span>
            <div className="setting-control">
              <button type="button" className="toggle" onClick={() => setForgetting(true)}>Forget everything…</button>
            </div>
            <div className="setting-default" />
            <p className="setting-meaning">Deletes everything agentlint kept from your transcripts. Your settings and dismissals stay.</p>
          </div>
        </Section>
        <Section id="about" title="About">
          <p className="settings-line">agentlint {answer.version}</p>
        </Section>
      </div>
      {forgetting && <ForgetDialog control={control} running={answer.jobs_running} onClose={() => setForgetting(false)} onForgotten={took} />}
    </div>
  );
}
