// Findings › Read instructions: every source whose text reaches the agent, with how much of it a model has read for
// directives, and a reading of the ones you select (docs/tech_impl/12-directives.md). Each section is read once: a
// section whose text is unchanged since it was read is never read again, so selecting a source read before costs only
// its new or changed sections. Contradictions and repeats among the directives are Findings' (Conflicting directives,
// Repeated directive), found by code from what was read.
import { useCallback, useEffect, useMemo, useState } from "react";
import type { DirectiveSource, DirectivesAnswer, Load } from "./contract";
import { count, counted, LOAD_WORDS } from "./format";
import { href } from "./route";
import { POLL_MS, usePoll, type TestsApi } from "./testsApi";

const STATE_ORDER: Record<DirectiveSource["state"], number> = { stale: 0, "not read": 1, "partly read": 2, read: 3 };

/** The loads shown as filters, in this order: what the agent reads every session first. */
const LOADS: Load[] = ["always", "per_message", "conditional", "on_invoke"];

function toRead(source: DirectiveSource): number {
  return source.sections - source.read;
}

export function InstructionsView({ tests, go, onReread }: { tests?: TestsApi; go: (to: string) => void; onReread?: () => void }) {
  const [answer, setAnswer] = useState<DirectivesAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [query, setQuery] = useState("");
  const [load, setLoad] = useState<Load | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const read = useCallback(() => {
    tests?.directives?.().then((next) => { setAnswer(next); setError(null); }, (failed: Error) => setError(failed.message));
  }, [tests]);
  useEffect(() => { read(); }, [read]);
  const running = answer?.reading?.runner.alive ?? false;
  usePoll(read, POLL_MS, running);
  const [wasRunning, setWasRunning] = useState(false);
  useEffect(() => { // a reading that just ended: the findings come from what it read, so read the configuration again
    if (wasRunning && !running) onReread?.();
    setWasRunning(running);
  }, [running, wasRunning, onReread]);

  const sources = useMemo(() => [...(answer?.sources ?? [])].sort((a, b) =>
    LOADS.indexOf(a.load) - LOADS.indexOf(b.load) || STATE_ORDER[a.state] - STATE_ORDER[b.state] || a.name.localeCompare(b.name)),
  [answer]);
  const shown = useMemo(() => {
    const words = query.trim().toLowerCase();
    return sources.filter((s) => (!load || s.load === load) && (!words || s.name.toLowerCase().includes(words) || (s.package ?? "").toLowerCase().includes(words)));
  }, [sources, query, load]);
  const chosen = sources.filter((s) => picked.has(s.id));
  const sections = chosen.reduce((n, s) => n + toRead(s), 0);
  const tokens = chosen.reduce((n, s) => n + s.tokens, 0);
  const totals = {
    sections: sources.reduce((n, s) => n + s.sections, 0),
    read: sources.reduce((n, s) => n + s.read, 0),
    stale: sources.reduce((n, s) => n + s.stale, 0),
  };

  if (!tests?.directives) {
    return <div className="settings"><div className="settings-body"><h2>Read instructions</h2><p className="muted">Instructions are read from agentlint ui: run it to read them.</p></div></div>;
  }
  const toggle = (id: string, on: boolean) => setPicked((was) => {
    const next = new Set(was);
    if (on) next.add(id); else next.delete(id);
    return next;
  });
  const start = () => {
    setSaid(null);
    if (!sections) {
      setSaid(chosen.length ? "Everything selected is read already: its text hasn't changed since." : "Select the sources to read first.");
      return;
    }
    setStarting(true); // blocked at once: planning and launching the reading takes a moment
    tests.readDirectives?.(chosen.map((s) => s.id)).then((started) => {
      setStarting(false);
      if (!started.read) setSaid("Everything selected is read already: its text hasn't changed since.");
      read();
    }, (failed: Error) => { setStarting(false); setSaid(failed.message); read(); });
  };
  const reading = answer?.reading;
  const blocked = starting || running;
  return (
    <div className="settings tests">
      <div className="settings-body">
        <h2>Read instructions</h2>
        <p className="muted">
          A model reads each section of the sources you select, once, and lists what it tells the agent to do or not do
          with a skill, agent, tool, program or file, quoting it. Code then finds where two sources contradict each other
          and where every session says the same thing twice: <a href={href("findings", null, { check: "conflicting-directives" })}
          onClick={(e) => { e.preventDefault(); go(href("findings", null, { check: "conflicting-directives" })); }}>Conflicting directives</a> and
          Repeated directive in Findings. A section whose text hasn't changed since it was read is never read again.
        </p>
        {!answer ? (error ? <p role="alert">The sources couldn't be read: {error}</p> : <p className="muted">Reading the configuration…</p>) : (
          <>
            <p className="settings-line" aria-label="Summary">
              {counted(sources.length, "source", "sources")} · {count(totals.read)} of {counted(totals.sections, "section", "sections")} read
              {totals.stale ? ` · ${count(totals.stale)} stale: read by an older version of the reader, to read again` : ""} · reader version {answer.version}
            </p>
            {reading && (
              <p className="settings-line" aria-label="Reading">
                {running ? "Reading now" : "A reading is stopped"}: {count(reading.sections.read)} of {counted(reading.sections.planned, "section", "sections")}
                {reading.sections.failed ? ` · ${count(reading.sections.failed)} failed` : ""} ·{" "}
                <a href={href("jobs", reading.read_id)} onClick={(e) => { e.preventDefault(); go(href("jobs", reading.read_id)); }}>open the job</a>
              </p>
            )}
            <div className="instructions-controls">
              <div className="instructions-row">
                <span className="instructions-label">Show</span>
                <input type="search" placeholder="Filter by name or package" aria-label="Filter sources" value={query} onChange={(e) => setQuery(e.target.value)} />
                <span role="radiogroup" aria-label="Loads" className="segmented">
                  <button type="button" role="radio" aria-checked={load === null} onClick={() => setLoad(null)}>All</button>
                  {LOADS.filter((l) => sources.some((s) => s.load === l)).map((l) => (
                    <button key={l} type="button" role="radio" aria-checked={load === l} onClick={() => setLoad(l)}>Loaded {LOAD_WORDS[l]}</button>
                  ))}
                </span>
              </div>
              <div className="instructions-row">
                <span className="instructions-label">Select</span>
                <button type="button" className="toggle" onClick={() => setPicked((was) => new Set([...was, ...shown.map((s) => s.id)]))}>{query || load ? `All ${count(shown.length)} shown` : "All"}</button>
                <button type="button" className="toggle" onClick={() => setPicked(new Set())}>None</button>
                <span className="muted">{chosen.length ? `${counted(chosen.length, "source", "sources")} selected · ${counted(sections, "section", "sections")} to read, about ${count(tokens)} tokens` : "Nothing selected"}</span>
              </div>
            </div>
            <p className="settings-line">
              <button type="button" className="toggle on" onClick={start} disabled={blocked}>{starting ? "Starting…" : running ? "Reading…" : "Read selected"}</button>
              {running && reading && <> <span className="muted">One reading at a time: </span><a href={href("jobs", reading.read_id)} onClick={(e) => { e.preventDefault(); go(href("jobs", reading.read_id)); }}>see it in Jobs</a></>}
            </p>
            {said && <p className="refusal" role="alert">{said}</p>}
            <table className="results-table" aria-label="Sources">
              <colgroup><col className="col-pick" /><col className="col-situation" /><col className="col-result" /><col className="col-runs" /><col className="col-runs" /></colgroup>
              <thead><tr><th scope="col" aria-label="Selected" /><th scope="col">Source</th><th scope="col">Loads</th>
                <th scope="col" className="num">Read</th><th scope="col" className="num">To read</th></tr></thead>
              <tbody>
                {shown.map((source) => (
                  <tr key={source.id}>
                    <td><input type="checkbox" checked={picked.has(source.id)} onChange={(e) => toggle(source.id, e.target.checked)} aria-label={`Select ${source.name}`} /></td>
                    <td><span className="row-text" title={source.name}>{source.name}</span></td>
                    <td>{LOAD_WORDS[source.load]}</td>
                    <td className="num">{source.state === "read" ? "all" : `${count(source.read)} of ${count(source.sections)}`}{source.stale ? ` · ${count(source.stale)} stale` : ""}</td>
                    <td className="num">{toRead(source) ? `${count(source.tokens)} tokens` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!shown.length && <p className="muted">No source matches the filter.</p>}
          </>
        )}
      </div>
    </div>
  );
}
