// What a recent session in this project actually loaded before your first words, beside what is configured to
// (docs/tech_impl/11-ui.md, "Context"): the text it showed, the files read from disk, what it could have shown and
// didn't, and what nothing configured explains. The latest session first; any recent one can be picked.
import { useState } from "react";
import type { Report, Source, Untraced } from "./contract";
import { count, home, short, when } from "./format";
import { splitOf, tokensOf } from "./model";
import { FilterMenu } from "./FilterMenu";
import { useFolding } from "./fold";
import { configHref } from "./route";
import { CostBar, GroupHead, LoadIcon, More, Tip, UntracedIcon } from "./ui";

interface Props {
  report: Report;
  go: (to: string) => void;
}

export function ContextView({ report, go }: Props) {
  const { snapshot, contexts, labels } = report;
  const fold = useFolding(false);
  const [picked, setPicked] = useState(0);
  const context = contexts[picked] ?? contexts[0];
  if (!context) {
    return <p className="empty-state">No session has run in this project yet, so there is nothing to compare. Start one, then look again.</p>;
  }
  const byId = new Map(snapshot.sources.map((s) => [s.id, s]));
  const pick = (ids: string[]) => ids.map((id) => byId.get(id)).filter((s): s is Source => !!s)
    .sort((a, b) => (tokensOf(b.content) ?? -1) - (tokensOf(a.content) ?? -1));
  const seen = pick(context.seen);
  const files = pick(context.files);
  const missing = pick(context.missing);
  // Python already keeps only what is in the first turn (report/document.py context_data): no second copy of the rule here.
  const untracedById = new Map(snapshot.untraced.map((u) => [u.id, u]));
  const untraced = context.untraced.map((id) => untracedById.get(id)).filter((u): u is Untraced => !!u);
  const sum = (list: Source[]) => list.reduce((n, s) => n + (tokensOf(s.content) ?? 0), 0);
  const delivered = sum(seen) + sum(files) + untraced.reduce((n, u) => n + (tokensOf(u.content) ?? 0), 0);
  const largest = Math.max(1, ...[...seen, ...files].map((s) => tokensOf(s.content) ?? 0));
  const s = context.session;

  const row = (source: Source) => {
    const n = tokensOf(source.content);
    const to = configHref({ kind: "source", id: source.id });
    return (
      <a key={source.id} href={to} className="row" onClick={(e) => { e.preventDefault(); go(to); }}>
        <CostBar value={n} max={largest} split={splitOf([[source.provenance.authority, n ?? 0]])} />
        <span className="num">{n === null ? "—" : short(n)}</span>
        <span className="title">{labels[source.id] ?? source.id}</span>
        <span className="trail"><LoadIcon load={source.activation.load} condition={source.activation.condition} /></span>
      </a>
    );
  };
  const group = (key: string, title: string, hint: string, rows: Source[]) =>
    rows.length === 0 ? null : (
      <section className="group">
        <GroupHead open={fold.isOpen(key)} onToggle={() => fold.flip(key)} count={count(rows.length)}>
          <span className="group-name">{title}</span>
          <span className="group-summary">{hint}</span>
        </GroupHead>
        {fold.isOpen(key) && fold.shown(key, rows).map(row)}
        {fold.isOpen(key) && <More hidden={fold.hidden(key, rows)} onClick={() => fold.showAll(key)} />}
      </section>
    );

  return (
    <div className="split context-view">
      <div className="list-pane">
        <div className="strip">
          <FilterMenu label="Session" value={String(picked)} onChoose={(key) => setPicked(Number(key))}
                      choices={contexts.map((c, i) => ({
                        key: String(i), label: when(c.session.started_at),
                        hint: [i === 0 ? "the latest" : "", c.session.interactive ? "" : "headless"].filter(Boolean).join(", ") || undefined,
                      }))} />
          <span className="muted">
            · {s.project ? home(s.project, report.home_dir) : "no project"} · {s.harness_version ?? "version unknown"} · {s.model ?? "model unknown"}
          </span>
          <span className="spacer" />
          <Tip text={context.observed_total !== null ? "This session reported its total; the rest is what agentlint can attribute"
                                                     : "This session reported no total; the figure is what agentlint can attribute"}>
            {context.observed_total !== null ? <><strong>{count(context.observed_total)}</strong> observed · </> : null}
            ≈{count(delivered)} attributed
          </Tip>
        </div>
        <div className="list">
          {group("seen", "Shown in this session", "text the session delivered: listings, hook output, the harness's own", seen)}
          {group("files", "Read from files", "instruction files, loaded every session — configured, not confirmed per session", files)}
          {untraced.length > 0 && (
            <section className="group">
              <GroupHead open={fold.isOpen("untraced")} onToggle={() => fold.flip("untraced")} count={count(untraced.length)}>
                <UntracedIcon />
                <span className="group-name">Explained by nothing configured</span>
              </GroupHead>
              {fold.isOpen("untraced") && fold.shown("untraced", untraced).map((u) => {
                const to = configHref({ kind: "untraced", id: u.id });
                const n = tokensOf(u.content);
                return (
                  <a key={u.id} href={to} className="row" onClick={(e) => { e.preventDefault(); go(to); }}>
                    <CostBar value={n} max={largest} split={splitOf([[null, n ?? 0]])} />
                    <span className="num">{n === null ? "—" : short(n)}</span>
                    <UntracedIcon />
                    <span className="title">{u.label}</span>
                    <span className="trail"><LoadIcon load={u.load} /></span>
                  </a>
                );
              })}
              {fold.isOpen("untraced") && (
                <More hidden={fold.hidden("untraced", untraced)} onClick={() => fold.showAll("untraced")} />
              )}
            </section>
          )}
          {group("missing", "Configured, but not shown in this session", "it could have been — a hook that printed nothing, say", missing)}
        </div>
      </div>
    </div>
  );
}
