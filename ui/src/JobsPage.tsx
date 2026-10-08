// Jobs: what runs, or ran, on your machine for agentlint — tests and readings of instructions — the running ones
// first; each opens to what is inside it: a test to its results (Tests › Results), a reading to its sections
// (docs/tech_impl/11-ui.md, "10. Jobs"). Asked again every few seconds while one runs.
import { useCallback, useEffect, useState } from "react";
import type { JobEntry, ReadDetail, ReadEntry } from "./contract";
import { count, counted, when } from "./format";
import { href } from "./route";
import { POLL_MS, usePoll, type TestsApi } from "./testsApi";

/** One row of the list: a test or a reading, in the same columns. */
interface Row { id: string; kind: "Test" | "Reading instructions"; what: string; progress: string; standing: string; created: string | null; running: boolean; to: string }

function testRow(job: JobEntry): Row {
  return { id: job.job_id, kind: "Test", what: job.targets.map((t) => t.name).join(" · ") || counted(job.situations, "situation", "situations"),
           progress: `${count(job.runs.recorded)} of ${counted(job.runs.planned, "run", "runs")}`, standing: job.standing,
           created: job.created, running: job.runner.alive, to: href("results", job.job_id) };
}

function readRow(read: ReadEntry): Row {
  const s = read.sections;
  return { id: read.read_id, kind: "Reading instructions", what: counted(read.sources, "source", "sources"),
           progress: `${count(s.read)} of ${counted(s.planned, "section", "sections")}${s.failed ? ` · ${count(s.failed)} failed` : ""}`,
           standing: read.standing, created: read.created, running: read.runner.alive, to: href("jobs", read.read_id) };
}

function JobList({ tests, go }: { tests: TestsApi; go: (to: string) => void }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const read = useCallback(() => {
    Promise.all([tests.jobs(), tests.reads ? tests.reads() : Promise.resolve({ reads: [] })]).then(([jobs, reads]) => {
      const all = [...jobs.jobs.map(testRow), ...reads.reads.map(readRow)];
      all.sort((a, b) => Number(b.running) - Number(a.running) || (b.created ?? "").localeCompare(a.created ?? ""));
      setRows(all);
      setError(null);
    }, (failed: Error) => setError(failed.message));
  }, [tests]);
  useEffect(() => { read(); }, [read]);
  usePoll(read, POLL_MS, (rows ?? []).some((r) => r.running));
  return (
    <div className="settings tests">
      <div className="settings-body">
        <h2>Jobs</h2>
        <p className="muted">What agentlint runs on this machine: tests, and readings of your instructions. Each opens to what is inside it.</p>
        {!rows ? (error ? <p role="alert">The jobs couldn't be read: {error}</p> : <p className="muted">Reading the jobs…</p>) : rows.length ? (
          <table className="results-table" aria-label="Jobs">
            <colgroup><col className="col-when" /><col className="col-result" /><col /><col className="col-without" /><col className="col-standing" /></colgroup>
            <thead><tr><th scope="col">Started</th><th scope="col">Job</th><th scope="col">What</th><th scope="col">Progress</th><th scope="col">Where it stands</th></tr></thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="row-link" onClick={() => go(row.to)}>
                  <td><a href={row.to} onClick={(e) => { e.preventDefault(); e.stopPropagation(); go(row.to); }}>{row.created ? when(row.created) : row.id}</a></td>
                  <td>{row.kind}</td>
                  <td><span className="row-text" title={row.what}>{row.what}</span></td>
                  <td>{row.progress}</td>
                  <td className={row.running ? undefined : "muted"}>{row.standing}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="muted">No job has run yet: tests start in Tests, readings in Findings › Read instructions.</p>}
        {rows && error && <p className="refusal" role="alert">The jobs couldn't be read again: {error}</p>}
      </div>
    </div>
  );
}

const SECTION_WORDS: Record<ReadDetail["sections_list"][number]["status"], string> = {
  pending: "Waiting", reading: "Reading now", read: "Read", failed: "Failed",
};

/** One reading: what it read and what is left, section by section, with Stop or Resume. */
function ReadingView({ tests, id, go }: { tests: TestsApi; id: string; go: (to: string) => void }) {
  const [detail, setDetail] = useState<ReadDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const read = useCallback(() => {
    tests.readDetail?.(id).then((next) => { setDetail(next); setError(null); }, (failed: Error) => setError(failed.message));
  }, [tests, id]);
  useEffect(() => { read(); }, [read]);
  usePoll(read, POLL_MS, detail?.runner.alive ?? false);
  const act = (call: ((read: string) => Promise<unknown>) | undefined) => {
    if (!call) return;
    setBusy(true);
    call(id).then(() => { setBusy(false); read(); }, (failed: Error) => { setBusy(false); setError(failed.message); });
  };
  const s = detail?.sections;
  const left = s ? s.pending + s.failed + s.reading : 0;
  return (
    <div className="settings tests">
      <div className="settings-body">
        <p><button type="button" className="text-button" onClick={() => go(href("jobs"))}>Jobs</button> ›</p>
        <h2>{detail?.created ? `Reading instructions, ${when(detail.created)}` : "Reading instructions"}</h2>
        {!detail ? (error ? <p role="alert">The reading couldn't be read: {error}</p> : <p className="muted">Reading…</p>) : (
          <>
            <p className="settings-line" aria-label="Summary">
              {counted(detail.sources, "source", "sources")} · {count(s!.read)} of {counted(s!.planned, "section", "sections")} read
              {s!.reading ? ` · ${count(s!.reading)} reading now` : ""}{s!.failed ? ` · ${count(s!.failed)} failed` : ""} · {detail.standing}
              {" "}
              {detail.runner.alive ? <button type="button" className="toggle" disabled={busy} onClick={() => act(tests.stopRead)}>Stop</button>
                : left > 0 && <button type="button" className="toggle" disabled={busy} onClick={() => act(tests.resumeRead)}>Resume</button>}
            </p>
            {error && <p className="refusal" role="alert">{error}</p>}
            <table className="results-table" aria-label="Sections">
              <colgroup><col className="col-situation" /><col className="col-runs" /><col className="col-result" /><col /></colgroup>
              <thead><tr><th scope="col">Source</th><th scope="col" className="num">Line</th><th scope="col">Where it stands</th><th scope="col">Found</th></tr></thead>
              <tbody>
                {detail.sections_list.map((section, i) => (
                  <tr key={i}>
                    <td><span className="row-text" title={section.source}>{section.source}</span></td>
                    <td className="num">{section.line}</td>
                    <td>{SECTION_WORDS[section.status]}</td>
                    <td className={section.status === "failed" ? "refusal" : "muted"}>
                      {section.status === "read" ? counted(section.directives ?? 0, "directive", "directives") : section.status === "failed" ? section.message : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}

/** Jobs, or one reading (`#/jobs/<read id>`); a test opens in Tests › Results. */
export function JobsView({ tests, route, go }: { tests?: TestsApi; route: { id: string | null }; go: (to: string) => void }) {
  if (!tests) return <div className="settings"><div className="settings-body"><h2>Jobs</h2><p className="muted">Jobs run from agentlint ui: run it to see them.</p></div></div>;
  return route.id ? <ReadingView key={route.id} tests={tests} id={route.id} go={go} /> : <JobList tests={tests} go={go} />;
}
