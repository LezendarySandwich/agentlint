// Where the flow starts: a ledger of three rows of one shape — what every session costs and who put it there, what
// is wrong, and which skills and agents your turns use — each opening in place to its top five, each figure a link to
// the list behind it (docs/tech_impl/11-ui.md, "Overview").
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import type { Report } from "./contract";
import { BudgetLegend, BudgetSegments } from "./Budget";
import { checkName, count, counted, day, short } from "./format";
import {
  chosenOf, firstTurnBudget, groupByCheck, groupByLoad, inFirstTurn, mixOf, SEVERITIES, usageSince, type Mix, type Row,
} from "./model";
import { configHref, href, useKeys } from "./route";
import { AllClearIcon, CostBar, CountLine, Icon, Kbd, MARK, SeverityMix, splitWords, Tip } from "./ui";

interface Props {
  report: Report;
  go: (to: string) => void;
}

export function rowLabel(row: Row, labels: Record<string, string>): string {
  if (row.kind === "source") return labels[row.source.id] ?? row.source.id;
  if (row.kind === "listing") return `${row.label} · ${counted(row.parts.length, "part", "parts")}`;
  return row.untraced.label;
}

const TOP = 5; // an opened row shows this many lines; the rest are a view away (or, for checks, "N more checks")
const MORE = "more"; // the key of the line that shows the rest of the checks

type RowKey = "session" | "findings" | "used";

/** A line of an opened row: a link to where it leads, or the "N more checks" control. */
interface Line {
  key: string;
  to: string | null; // null: the control that shows the rest
  label: ReactNode;
  right: ReactNode; // its figure or mix, right-aligned on the figure column
}

/** A ledger row: a label, a bar or the counts, a right-aligned figure, a chevron; opened, its lines. */
interface LedgerRow {
  key: RowKey;
  label: string;
  to: string; // Enter on the row: its view
  body: ReactNode;
  figure: ReactNode;
  sub: ReactNode;
  lines: Line[]; // empty: the row doesn't open
}

const sumMix = (mixes: Mix[]): Mix => {
  const sum: Mix = { error: 0, warn: 0, info: 0 };
  for (const mix of mixes) for (const s of SEVERITIES) sum[s] += mix[s];
  return sum;
};

const stopOf = (row: RowKey, line?: string) => (line === undefined ? `row:${row}` : `${row}:${line}`);

export function Overview({ report, go }: Props) {
  const { snapshot, check, labels, usage } = report;
  const link = useCallback((to: string) => ({ href: to, onClick: (e: MouseEvent) => { e.preventDefault(); go(to); } }), [go]);
  const budget = useMemo(() => firstTurnBudget(report), [report]);
  const costs = useMemo(() => {
    const untraced = snapshot.untraced.filter((u) => inFirstTurn(report, u.load));
    return groupByLoad(snapshot.sources.filter((s) => inFirstTurn(report, s.activation.load)), untraced)
      .flatMap((g) => g.rows)
      .sort((a, b) => (b.tokens ?? -1) - (a.tokens ?? -1))
      .slice(0, TOP);
  }, [report, snapshot]);
  const shown = useMemo(() => check.findings.filter((f) => !f.suppressed), [check]);
  const groups = useMemo(() => groupByCheck(shown, check.summaries), [shown, check.summaries]);
  const chosen = useMemo(() => chosenOf(usage), [usage]);
  const entities = useMemo(() => new Map(snapshot.entities.map((e) => [e.id, e])), [snapshot]);
  const since = usageSince(report);
  const mix = mixOf(shown);
  const suppressed = check.findings.length - shown.length;
  const notEvaluated = Object.entries(check.not_evaluated);

  // what is open lives here, not in the URL: Findings starts open when something is wrong enough to look at
  const [open, setOpen] = useState<Record<RowKey, boolean>>(() => ({ session: false, findings: mix.error + mix.warn > 0, used: false }));
  const [allChecks, setAllChecks] = useState(false);

  const state = snapshot.always_on;
  const observed = state.type !== "not_reconciled";
  const total = observed ? state.observed_total.value : state.attributed;
  const rows: LedgerRow[] = [{
    key: "session", label: "Every session", to: href("config"),
    body: <BudgetSegments budget={budget} onPick={(group) => go(href("config", null, { group }))} />,
    figure: (
      <Tip text={observed ? `${counted(total, "token", "tokens")} every session, observed in session ${state.session.session_id.slice(0, 8)}`
                          : `${counted(total, "token", "tokens")} every session, estimated: ${state.reason}`}>
        {observed ? short(total) : `≈${short(total)}`}
      </Tip>
    ),
    sub: <BudgetLegend budget={budget} onPick={(group) => go(href("config", null, { group }))} />,
    lines: costs.map((row) => ({
      key: row.key, to: configHref(row),
      label: (
        <>
          <CostBar value={row.tokens} max={costs[0]?.tokens ?? 1} split={row.split} text={splitWords(row.split) || undefined} />
          <span className="name">{rowLabel(row, labels)}</span>
        </>
      ),
      right: <Tip text={row.tokens === null ? "not observed" : counted(row.tokens, "token", "tokens")}>{row.tokens === null ? "—" : short(row.tokens)}</Tip>,
    })),
  }];

  const checkLine = (g: (typeof groups)[number]): Line => ({
    key: g.check, to: href("findings", null, { check: g.check }),
    label: <Tip text={g.summary ? `${g.summary} (${g.check})` : g.check} className="name">{checkName(g.check)}</Tip>,
    right: <SeverityMix mix={g.mix} />,
  });
  const rest = groups.slice(TOP);
  const findingLines: Line[] = groups.slice(0, TOP).map(checkLine);
  if (rest.length) {
    findingLines.push({
      key: MORE, to: null, label: `${counted(rest.length, "more check", "more checks")}`,
      right: <SeverityMix mix={sumMix(rest.map((g) => g.mix))} muted />,
    });
    if (allChecks) findingLines.push(...rest.map(checkLine));
  }
  rows.push({
    key: "findings", label: "Findings", to: href("findings"),
    body: shown.length > 0 ? <CountLine mix={mix} link={(severity) => link(href("findings", null, { severity }))} />
      // never an all-clear while findings are suppressed or checks never ran: each says what it rests on
      : suppressed > 0 ? <a className="quiet" {...link(href("findings"))}>Nothing open · {count(suppressed)} suppressed</a>
      : notEvaluated.length > 0 ? <span className="quiet">No findings from the checks that ran</span>
      : <span className="quiet"><AllClearIcon /> Nothing found</span>,
    figure: null,
    sub: notEvaluated.length > 0 && (
      <Tip text={notEvaluated.map(([id, why]) => `${checkName(id)} (${id}): ${why}`).join(" · ")}>
        <a {...link(href("findings"))}>{counted(notEvaluated.length, "check", "checks")} not evaluated</a>
      </Tip>
    ),
    lines: shown.length > 0 ? findingLines : [],
  });

  const usedCount = chosen.used.length;
  const choices = chosen.ids.size;
  if (usage && choices > 0) {
    const neverUsed = chosen.unused.length;
    const sinceWords = since ? ` since ${day(since)}` : "";
    rows.push({
      key: "used", label: "Skills and agents", to: href("entities", null, { chosen: "1" }),
      body: (
        <span className="use-bar" aria-hidden="true">
          {usedCount > 0 && <span className="fill" style={{ width: `${(usedCount / choices) * 100}%` }} />}
        </span>
      ),
      figure: (
        <Tip text={`Skills and agents your turns used${sinceWords}; the other ${counted(neverUsed, "was", "were")} never used`}>
          {count(usedCount)} of {count(choices)} used
        </Tip>
      ),
      sub: since && `since ${day(since)}`,
      lines: [
        ...usage.summary.used.slice(0, TOP).flatMap(({ entity: id, reference }) => {
          const entity = entities.get(id);
          const u = usage.entities[reference];
          if (!entity || !u) return [];
          return [{
            key: id, to: href("entities", id), label: <span className="name">{entity.name}</span>,
            right: <Tip text={`Used in ${count(u.invocations)} of your ${counted(usage.turns, "turn", "turns")}${sinceWords}`}>{count(u.invocations)}</Tip>,
          }];
        }),
        ...(neverUsed > 0 ? [{
          key: "unused", to: href("entities", null, { chosen: "1", unused: "1" }),
          label: <span className="name muted">{count(neverUsed)} never used</span>, right: null,
        }] : []),
      ],
    });
  }

  return <Ledger rows={rows} open={open} setOpen={setOpen} allChecks={allChecks} setAllChecks={setAllChecks} go={go} />;
}

interface LedgerProps {
  rows: LedgerRow[];
  open: Record<RowKey, boolean>;
  setOpen: (update: (open: Record<RowKey, boolean>) => Record<RowKey, boolean>) => void;
  allChecks: boolean;
  setAllChecks: (update: (all: boolean) => boolean) => void;
  go: (to: string) => void;
}

/** The rows and the keys that walk them: j/k (and the arrows) move through the rows and the lines of open rows, → or
 * Space opens, ← closes (or returns from a line to its row), Enter goes. One tab stop, which follows the cursor. */
function Ledger({ rows, open, setOpen, allChecks, setAllChecks, go }: LedgerProps) {
  const isOpen = (row: LedgerRow) => row.lines.length > 0 && open[row.key];
  const stops = rows.flatMap((row) => [
    { key: stopOf(row.key), row, line: null as Line | null },
    ...(isOpen(row) ? row.lines.map((line) => ({ key: stopOf(row.key, line.key), row, line })) : []),
  ]);
  const [cursorKey, setCursorKey] = useState(() => stops[0]?.key ?? "");
  const found = stops.findIndex((s) => s.key === cursorKey);
  // a line that went away (its row closed) leaves the cursor on its row
  const at = found >= 0 ? found : Math.max(0, stops.findIndex((s) => s.line === null && cursorKey.startsWith(`${s.row.key}:`)));
  const cursor = stops[at];
  const elements = useRef(new Map<string, HTMLElement>());
  const moved = useRef(false); // the keys moved the cursor: focus follows it (a click already has focus)
  useEffect(() => {
    if (!moved.current || !cursor) return;
    moved.current = false;
    elements.current.get(cursor.key)?.focus();
  });

  const toggle = useCallback((row: LedgerRow, to?: boolean) => {
    if (!row.lines.length) return;
    setOpen((o) => ({ ...o, [row.key]: to ?? !o[row.key] }));
  }, [setOpen]);
  const activate = useCallback((stop: (typeof stops)[number]) => {
    if (!stop.line) go(stop.row.to);
    else if (stop.line.to) go(stop.line.to);
    else setAllChecks((all) => !all);
  }, [go, setAllChecks]);

  const keys = useCallback((key: string, event: KeyboardEvent) => {
    if (!cursor) return false;
    const target = event.target as HTMLElement | null;
    // Enter or Space on another control (a severity link, a legend label, the sidebar) is that control's own
    const elsewhere = !!target?.closest?.("a, button, input, select") && !target.closest("[data-stop]");
    const move = (to: number) => {
      moved.current = true;
      setCursorKey(stops[Math.min(Math.max(to, 0), stops.length - 1)].key);
    };
    if (key === "j" || key === "ArrowDown") move(at + 1);
    else if (key === "k" || key === "ArrowUp") move(at - 1);
    else if (key === "ArrowRight") {
      if (!cursor.line) toggle(cursor.row, true);
      else if (cursor.line.to === null && !allChecks) setAllChecks(() => true);
    } else if (key === "ArrowLeft") {
      if (cursor.line) move(stops.findIndex((s) => s.key === stopOf(cursor.row.key)));
      else toggle(cursor.row, false);
    } else if (key === " ") {
      if (elsewhere) return false;
      if (!cursor.line) toggle(cursor.row);
      else if (cursor.line.to === null) setAllChecks((all) => !all);
    } else if (key === "Enter") {
      if (elsewhere) return false;
      activate(cursor);
    } else return false;
    return true;
  }, [cursor, at, stops, toggle, allChecks, setAllChecks, activate]);
  useKeys(keys);

  const stopProps = (key: string) => ({
    "data-stop": "",
    tabIndex: key === cursor?.key ? 0 : -1,
    ref: (el: HTMLElement | null) => { if (el) elements.current.set(key, el); else elements.current.delete(key); },
    onKeyUp: (e: { key: string; preventDefault: () => void }) => { if (e.key === " ") e.preventDefault(); }, // Space was handled on keydown
  });

  const renderLine = (row: LedgerRow, line: Line) => {
    const key = stopOf(row.key, line.key);
    const className = `line${cursor?.key === key ? " cur" : ""}`;
    const click = () => setCursorKey(key);
    if (line.to === null) {
      return (
        <button key={key} type="button" className={`${className} more`} aria-expanded={allChecks} aria-controls="overview-rest"
                {...stopProps(key)} onClick={() => { click(); setAllChecks((all) => !all); }}>
          <span className="name"><Icon icon={MARK.disclose} className={`chev${allChecks ? " open" : ""}`} />{line.label}</span>
          {" "}<span className="right">{line.right}</span>
        </button>
      );
    }
    const to = line.to;
    return (
      <a key={key} className={className} href={to} {...stopProps(key)} onClick={(e) => { e.preventDefault(); click(); go(to); }}>
        {line.label}
        {" "}<span className="right">{line.right}</span>
      </a>
    );
  };

  return (
    <div className="overview">
      <div className="ledger" role="list" aria-label="Overview">
        {rows.map((row) => {
          const key = stopOf(row.key);
          const opens = row.lines.length > 0;
          const opened = isOpen(row);
          const head = () => {
            setCursorKey(key);
            if (opens) toggle(row);
            else go(row.to);
          };
          const more = row.lines.findIndex((l) => l.to === null);
          return (
            <div key={row.key} role="listitem" className={`lrow${cursor?.key === key ? " cur" : ""}`}
                 onClick={(e) => { if (!(e.target as HTMLElement).closest("a, button, .items")) head(); }}>
              <button type="button" className="lab" {...stopProps(key)} onClick={head}
                      {...(opens ? { "aria-expanded": opened, "aria-controls": `overview-${row.key}` } : {})}>
                {row.label}
              </button>
              <div className="body">{row.body}</div>
              <span className="fig">{row.figure}</span>
              <span className="chev-cell" aria-hidden="true">
                {opens && <Icon icon={MARK.disclose} className={`chev${opened ? " open" : ""}`} />}
              </span>
              {row.sub && <div className="sub">{row.sub}</div>}
              {opens && (
                <div className="items" id={`overview-${row.key}`} hidden={!opened}>
                  {opened && (more < 0 ? row.lines.map((l) => renderLine(row, l)) : (
                    <>
                      {row.lines.slice(0, more + 1).map((l) => renderLine(row, l))}
                      <div className="rest" id="overview-rest" hidden={!allChecks}>
                        {row.lines.slice(more + 1).map((l) => renderLine(row, l))}
                      </div>
                    </>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="keys-hint" aria-hidden="true">
        <span><Kbd>j</Kbd> <Kbd>k</Kbd> rows</span>
        <span><Kbd>→</Kbd> open</span>
        <span><Kbd>↵</Kbd> go</span>
      </div>
    </div>
  );
}
