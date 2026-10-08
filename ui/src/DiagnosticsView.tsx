// Diagnostics (docs/tech_impl/11-ui.md): agentlint's findings about itself — the generator wrote nothing for a target,
// a situation named its own target and was dropped, a run was retried — useful for debugging agentlint and for nothing
// else, so they are their own page: never in Findings, its counts or the overview (decided 2026-10-04, D16). Grouped
// by kind, the most recent kind first, every group folded as every list starts.
import { useEffect, useState } from "react";
import type { DiagnosticInfo, Report } from "./contract";
import type { RecordSource } from "./data";
import { count, diagnosticKind, noun, when } from "./format";
import { useFolding } from "./fold";
import { GroupHead, More, Tip } from "./ui";

interface Props {
  report: Report;
  records?: RecordSource; // what agentlint stored; absent, there is nothing to list
}

export function DiagnosticsView({ report, records }: Props) {
  const [list, setList] = useState<DiagnosticInfo[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const fold = useFolding(false);
  useEffect(() => { // asked for again when a new reading lands; an answer for a page left is dropped
    let current = true;
    setFailed(null);
    records?.diagnostics().then((answer) => { if (current) setList(answer); }, (error: Error) => { if (current) setFailed(error.message); });
    return () => { current = false; };
  }, [records, report.generated_at]);
  if (!records) return <p className="empty-state">No diagnostics to show: they are read from what agentlint stored.</p>;
  if (failed) return <p className="empty-state">Couldn't list the diagnostics: {failed}</p>;
  if (!list) return <p className="empty-state">Listing the diagnostics…</p>;
  if (!list.length) {
    return <p className="empty-state">No diagnostics: agentlint has noted nothing about its own work.</p>;
  }
  const kinds = new Map<string, DiagnosticInfo[]>();
  for (const item of list) kinds.set(item.kind, [...(kinds.get(item.kind) ?? []), item]); // the server's order: newest first
  return (
    <div className="split diagnostics">
      <div className="list-pane">
        <div className="strip">
          <span><strong>{count(list.length)}</strong> {noun(list.length, "diagnostic", "diagnostics")}</span>
          <span className="muted">· agentlint's findings about itself, for debugging agentlint — never findings about your configuration</span>
        </div>
        <div className="list">
          {[...kinds].map(([kind, items]) => (
            <section key={kind} className="group">
              <GroupHead open={fold.isOpen(kind)} onToggle={() => fold.flip(kind)} count={count(items.length)}>
                <Tip text={kind} className="group-name">{diagnosticKind(kind)}</Tip>
                {items[0].created && <span className="group-summary">latest {when(items[0].created)}</span>}
              </GroupHead>
              {fold.isOpen(kind) && fold.shown(kind, items).map((item, i) => (
                <div key={item.id ?? `${kind}/${i}`} className="row tall static">
                  <span className="title">
                    {item.message}
                    <span className="sub muted"><code>{item.subject}</code>{item.created ? ` · ${when(item.created)}` : ""}</span>
                  </span>
                </div>
              ))}
              {fold.isOpen(kind) && <More hidden={fold.hidden(kind, items)} onClick={() => fold.showAll(kind)} />}
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
