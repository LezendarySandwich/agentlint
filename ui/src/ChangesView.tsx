// Configuration › What changed (docs/tech_impl/11-ui.md): the project's stored snapshots, the most recent first, and,
// opened beside the list, what changed from the one before it — or from one you choose — as `agentlint show --diff`
// says it: sources changed, added and removed with their token changes, text a session delivered differently, untraced
// text, and skills and agents defined on disk. Every figure and rule comes from the server (report/diff.py); a source
// that is still configured links to its entry in Configuration.
import { useEffect, useRef, useState, type MouseEvent } from "react";
import type { ChangeKind, Report, SnapshotChange, SnapshotDiff, SnapshotInfo } from "./contract";
import type { RecordSource } from "./data";
import { FilterMenu } from "./FilterMenu";
import { count, counted, noun, signed, when } from "./format";
import { useListNavigation } from "./listNav";
import { configHref, href, type RouteOf } from "./route";
import { Icon, LoadIcon, MARK, NAV_ICON, Tip } from "./ui";

interface Props {
  report: Report;
  route: RouteOf<"changes">;
  go: (to: string, replace?: boolean) => void;
  records?: RecordSource; // what agentlint stored about the project in view; absent, there is nothing to compare
}

/** A fingerprint as the command line shows it: its first 12 hex digits. */
const short = (fingerprint: string) => fingerprint.replace(/^sha256:/, "").slice(0, 12);

const HEADINGS: Record<ChangeKind, [string, string]> = {
  changed: ["Changed", "configuration that differs"],
  added: ["Added", "configured now, not then"],
  removed: ["Removed", "configured then, not now"],
  output: ["Delivered text changed", "the same configuration: a hook printing something new, or seen only then or only now"],
  untraced: ["Untraced text", "delivered every session, explained by no configuration"],
};

/** The snapshot listed after `at` (the list is by when each was last read, the most recent first: a configuration read
 * again moves up, so this is the one last read before it, not necessarily the one first stored before it) that can be
 * compared, if any. */
function before(list: SnapshotInfo[], at: number): SnapshotInfo | null {
  return list.slice(at + 1).find((s) => s.readable) ?? null;
}

export function ChangesView({ report, route, go, records }: Props) {
  const [list, setList] = useState<SnapshotInfo[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const listed = useRef<RecordSource | undefined>(undefined);
  useEffect(() => { // listed again when a new reading lands (it may have stored a snapshot); from scratch for another project
    if (listed.current !== records) setList(null);
    listed.current = records;
    let current = true;
    setFailed(null);
    records?.snapshots().then((answer) => { if (current) setList(answer); }, (error: Error) => { if (current) setFailed(error.message); });
    return () => { current = false; };
  }, [records, report.generated_at]);
  const rows = list ?? [];
  const at = route.id ? rows.findIndex((s) => s.fingerprint === route.id) : -1;
  const open = at >= 0 ? rows[at] : null;
  const nav = useListNavigation({
    items: rows, keyOf: (s) => s.fingerprint, openKey: open?.fingerprint ?? null,
    open: (fingerprint, replace) => go(href("changes", fingerprint), replace),
    close: () => go(href("changes")),
  });
  if (!records) return <p className="empty-state">Nothing stored to compare: what changed is read from what agentlint stored.</p>;
  if (failed) return <p className="empty-state">Couldn't list the stored configurations: {failed}</p>;
  if (!list) return <p className="empty-state">Listing the stored configurations…</p>;
  if (!list.length) {
    return ( // a UI that records nothing says why, never that the next reading will be stored
      <p className="empty-state">
        No configuration of this project is stored yet. {report.not_recorded ?? "agentlint stores one each time it reads " +
          "the project at your sessions setting, so the next reading will be the first."}
      </p>
    );
  }
  // the one chosen to compare with, else (none chosen, or one no longer stored) the one stored before it
  const chosen = route.from ? rows.find((s) => s.fingerprint === route.from && s.readable && s !== open) : undefined;
  const base = open ? chosen ?? before(rows, at) : null;
  return (
    <div className={`split${open ? " with-detail" : ""}`}>
      <div className="list-pane">
        <div className="strip">
          <span><strong>{count(list.length)}</strong> {noun(list.length, "configuration", "configurations")} stored</span>
          {(list.length > 1 || !report.not_recorded) && (
            <span className="muted">
              · {list.length === 1 ? "the next change will have something to compare with" : "open one to see what changed from the one before"}
            </span>
          )}
        </div>
        {report.not_recorded && <div className="strip"><span className="muted">{report.not_recorded}</span></div>}
        <div className="list">
          {rows.map((s, i) => {
            const earlier = before(rows, i);
            const delta = s.tokens !== null && earlier?.tokens != null ? s.tokens - earlier.tokens : null;
            return (
              <a key={s.fingerprint} href={href("changes", s.fingerprint)} {...nav.rowProps(s.fingerprint)}
                 className={`row tall${nav.cursor === s.fingerprint ? " cursor" : ""}${open?.fingerprint === s.fingerprint ? " open" : ""}`}>
                <span className="title">
                  {when(s.taken_at)}
                  {s.fingerprint === report.snapshot.fingerprint && (
                    <Tip text="The configuration as last read" className="muted"> · now</Tip>
                  )}
                  <span className="sub muted">
                    {s.readable
                      ? <>{counted(s.sources ?? 0, "source", "sources")} · ≈{count(s.tokens ?? 0)} always-on tokens{s.harness_version ? ` · harness ${s.harness_version}` : ""}</>
                      : "stored by an agentlint whose format this one can't read back"}
                  </span>
                </span>
                <span className="trail">
                  {earlier && delta !== null && (
                    <Tip text={`Always-on tokens since the configuration stored before it, ${when(earlier.taken_at)}`} className="num">
                      {signed(delta)}
                    </Tip>
                  )}
                  <Tip text={s.fingerprint}><code className="where">{short(s.fingerprint)}</code></Tip>
                </span>
              </a>
            );
          })}
        </div>
      </div>
      {open && (
        <Comparison key={open.fingerprint} report={report} list={rows} open={open} base={base} records={records} go={go}
                    onClose={() => go(href("changes"))} />
      )}
    </div>
  );
}

/** Why an opened configuration has nothing to compare with by default. No claim that it was stored first: a list by
 * when each was last read can't say that (read A, then B, then A again, and B is listed last). */
function nothingBefore(listed: number, others: number): string {
  if (others) return "No other configuration was last read before this one (each is listed by when it was last read): choose one under Compared with.";
  if (listed > 1) return "No other configuration stored can be read back by this agentlint, so there is nothing to compare it with.";
  return "The only configuration of this project agentlint stored: there is nothing to compare it with yet.";
}

function Comparison({ report, list, open, base, records, go, onClose }: {
  report: Report; list: SnapshotInfo[]; open: SnapshotInfo; base: SnapshotInfo | null; records: RecordSource;
  go: (to: string, replace?: boolean) => void; onClose: () => void;
}) {
  const [result, setResult] = useState<{ of: string; diff: SnapshotDiff } | null>(null);
  const [failed, setFailed] = useState<{ of: string; error: string } | null>(null);
  const comparable = !!base?.readable && open.readable;
  const asked = comparable ? `${base.fingerprint}\n${open.fingerprint}` : "";
  useEffect(() => { // an answer for a comparison no longer asked for is dropped
    if (!asked) return;
    const [earlier, later] = asked.split("\n");
    let current = true;
    records.diff(earlier, later).then(
      (diff) => { if (current) setResult({ of: asked, diff }); },
      (error: Error) => { if (current) setFailed({ of: asked, error: error.message }); },
    );
    return () => { current = false; };
  }, [records, asked]);
  const others = list.filter((s) => s.fingerprint !== open.fingerprint && s.readable);
  const shown = result?.of === asked ? result.diff : null;
  const error = failed?.of === asked ? failed.error : null;
  return (
    <aside className="detail changes" aria-label="What changed">
      <div className="detail-meta">
        <Icon icon={NAV_ICON.changes} /> What changed
        <button type="button" className="icon-button" onClick={onClose} aria-label="Close"><Icon icon={MARK.close} size={16} /></button>
      </div>
      <h2>{base ? <>{when(base.taken_at)} → {when(open.taken_at)}</> : when(open.taken_at)}</h2>
      {others.length > 0 && open.readable && (
        <p className="compare-with">
          <span className="muted">Compared with</span>{" "}
          <FilterMenu label="Compared with" value={base?.fingerprint ?? ""}
                      onChoose={(fingerprint) => go(href("changes", open.fingerprint, { from: fingerprint }), true)}
                      choices={[...(base ? [] : [{ key: "", label: "Choose a configuration" }]),
                                ...others.map((s) => ({ key: s.fingerprint, label: when(s.taken_at), hint: short(s.fingerprint) }))]} />
        </p>
      )}
      {!open.readable ? (
        <p className="detail-text">It was stored by an agentlint whose format this one can't read back, so it can't be compared.</p>
      ) : !base ? (
        <p className="detail-text">{nothingBefore(list.length, others.length)}</p>
      ) : error ? (
        <p className="detail-text" role="alert">{error}</p>
      ) : !shown ? (
        <p className="detail-text muted">Comparing…</p>
      ) : (
        <Differences report={report} diff={shown} go={go} />
      )}
    </aside>
  );
}

function Differences({ report, diff, go }: { report: Report; diff: SnapshotDiff; go: (to: string) => void }) {
  const configured = new Set(report.snapshot.sources.map((s) => s.id));
  const link = (to: string) => ({ href: to, onClick: (e: MouseEvent) => { e.preventDefault(); go(to); } });
  const { from, to } = diff;
  const row = (change: SnapshotChange) => {
    const still = change.kind !== "untraced" && configured.has(change.source_id); // an untraced change is named by its text's hash
    return (
      <li key={`${change.kind}/${change.source_id}`}>
        <Tip text={change.delta === null ? "Seen on one side only: whether it changed in size isn't known"
                                          : `${change.before === null ? "—" : count(change.before)} → ${change.after === null ? "—" : count(change.after)} tokens`}
             className="num">{signed(change.delta)}</Tip>
        <LoadIcon load={change.load} />
        {still ? <a {...link(configHref({ kind: "source", id: change.source_id }))}>{change.label}</a> : <span>{change.label}</span>}
        {change.note && <span className="muted">{change.note}</span>}
      </li>
    );
  };
  const kinds = (Object.keys(HEADINGS) as ChangeKind[]).filter((kind) => diff.changes.some((c) => c.kind === kind));
  const nothing = !diff.changes.length && !diff.entities_added.length && !diff.entities_removed.length;
  const entityGroups: [string, SnapshotDiff["entities_added"]][] = [
    ["Defined now, not then", diff.entities_added], ["Defined then, not now", diff.entities_removed],
  ];
  return (
    <>
      <dl className="props">
        <dt>Always-on</dt>
        <dd>≈{count(diff.always_before)} → ≈{count(diff.always_after)} tokens ({signed(diff.always_after - diff.always_before)})</dd>
        {from.harness_version !== to.harness_version && (<><dt>Harness</dt><dd>{from.harness_version ?? "unknown"} → {to.harness_version ?? "unknown"}</dd></>)}
        {from.model !== to.model && (<><dt>Model</dt><dd>{from.model ?? "unknown"} → {to.model ?? "unknown"}</dd></>)}
      </dl>
      {diff.unexplained !== 0 && (
        <p className="detail-text muted">
          Of that, {signed(diff.unexplained)} is text seen only then or only now (marked ?): whether it changed in size isn't known.
        </p>
      )}
      {(from.unseen > 0 || to.unseen > 0) && (
        <p className="detail-text muted">
          Leaves out always-on sources whose text no session showed: {count(from.unseen)} then, {count(to.unseen)} now.
        </p>
      )}
      {diff.untraced_left_out && (
        <p className="detail-text muted">Leaves out untraced text: one of the readings had no session from this project.</p>
      )}
      {nothing && <p className="detail-text">No source changed.</p>}
      {kinds.map((kind) => {
        const changes = diff.changes.filter((c) => c.kind === kind);
        return (
          <section key={kind}>
            <h3><Tip text={HEADINGS[kind][1]}>{HEADINGS[kind][0]}</Tip> <span className="count">{count(changes.length)}</span></h3>
            <ul className="links changes">{changes.map(row)}</ul>
          </section>
        );
      })}
      {entityGroups.filter(([, entities]) => entities.length > 0).map(([title, entities]) => (
        <section key={title}>
          <h3><Tip text="Skills, agents and the like, defined on disk">{title}</Tip> <span className="count">{count(entities.length)}</span></h3>
          <ul className="links">
            {entities.map((e) => <li key={`${e.kind}/${e.name}`}><span className="muted">{e.kind}</span> {e.name}</li>)}
          </ul>
        </section>
      ))}
    </>
  );
}
