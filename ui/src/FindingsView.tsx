// Findings by the check that found them, the most severe first, in one aligned grid; one opens beside the list, and
// the keys move through them (docs/tech_impl/11-ui.md, "Findings"). What you suppressed is its own group.
import { useCallback, useDeferredValue, useMemo, useRef, type MouseEvent } from "react";
import type { Finding, Report, Severity, Source } from "./contract";
import type { Texts } from "./data";
import type { DismissApi } from "./Dismissed";
import { FindingDetail } from "./FindingDetail";
import { checkName, count, counted, home, packageName } from "./format";
import { groupByCheck, mixOf, placeOf } from "./model";
import { useFolding } from "./fold";
import { useListNavigation } from "./listNav";
import { href, type RouteOf } from "./route";
import {
  AllClearIcon, ConfidenceIcon, CountLine, GroupHead, Icon, LapsedIcon, MARK, SearchBox, SeverityIcon, SeverityMix, More,
  SuppressedIcon, Tip,
} from "./ui";

/** Checks whose input is instruction text a model reads when you ask (Findings › Read instructions). */
const READ_FIRST = ["conflicting-directives", "repeated-directive"];

interface Props {
  report: Report;
  texts: Texts | null; // the source texts, for the open finding's excerpt; null while they unpack
  route: RouteOf<"findings">;
  go: (to: string, replace?: boolean) => void;
  dismiss?: DismissApi; // a finding is dismissed, or shown again, from its detail
  onDismissed?: (id: string, dismissed: boolean) => void; // the list moves it, without reading the project again
}

// A member's name, package and description are searched too: they moved out of `detail` into `members`. A location is
// searched as where its source is (its file and the place inside), or its id when the reading has no such source.
const haystack = (f: Finding, byId: Map<string, Source>) => [f.title, f.detail, f.check,
  ...f.locations.map((l) => { const source = byId.get(l.source_id); return (source && placeOf(source)) ?? l.source_id; }),
  ...(f.members ?? []).flatMap((m) => [m.name, m.package ?? "", m.description ?? ""])].join("\n").toLowerCase();

export function FindingsView({ report, texts, route, go, dismiss, onDismissed }: Props) {
  const { check } = report;
  const { params } = route;
  const { q: query, severity, check: only } = route.filters;
  const needle = useDeferredValue(query).trim().toLowerCase();
  const searchRef = useRef<HTMLInputElement>(null);

  const set = useCallback((key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    go(href("findings", route.id, next), true);
  }, [params, route.id, go]);

  const shown = useMemo(() => check.findings.filter((f) => !f.suppressed), [check]);
  const byId = useMemo(() => new Map(report.snapshot.sources.map((s) => [s.id, s])), [report.snapshot]);
  const index = useMemo(() => new Map(check.findings.map((f) => [f.id, haystack(f, byId)])), [check, byId]);
  const filtering = !!(needle || severity || only);
  const matches = useCallback((f: Finding) => (!severity || f.severity === severity) && (!only || f.check === only) &&
    (!needle || index.get(f.id)!.includes(needle)), [severity, only, needle, index]);
  const groups = useMemo(() => groupByCheck(shown.filter(matches), check.summaries), [shown, matches, check.summaries]);
  // What you suppressed is filtered like the rest, so a filter never lists a suppressed finding it doesn't match.
  const suppressed = useMemo(() => check.findings.filter((f) => f.suppressed && matches(f)), [check, matches]);

  const open = route.id ? check.findings.find((f) => f.id === route.id) ?? null : null;
  const key = open?.suppressed ? "suppressed" : open?.check;
  const holding = open?.suppressed ? suppressed : groups.find((g) => g.check === key)?.findings ?? [];
  const at = open ? holding.findIndex((f) => f.id === open.id) : -1;
  const fold = useFolding(filtering, key && at >= 0 ? [{ key, at }] : []);
  const visible = groups.flatMap((g) => (fold.isOpen(g.check) ? fold.shown(g.check, g.findings) : []));
  const navigable = [...visible, ...(fold.isOpen("suppressed") ? fold.shown("suppressed", suppressed) : [])];
  const nav = useListNavigation({
    items: navigable, keyOf: (f) => f.id, openKey: open?.id ?? null, search: searchRef,
    open: (id, replace) => go(href("findings", id, params), replace),
    close: () => go(href("findings", null, params)),
    clear: () => { if (query) set("q", null); },
  });

  const notEvaluated = Object.entries(check.not_evaluated);
  const mix = useMemo(() => mixOf(shown), [shown]); // whatever the other filters: what there is, not what is listed
  /** The package a finding sits in: the source its first location points into, else the first source it rests on. */
  const packageOf = (f: Finding): string | null => {
    const where = f.locations[0];
    const at = where && byId.get(where.source_id);
    return (at ?? byId.get(f.sources[0]))?.provenance.package ?? null;
  };
  const severityLink = (s: Severity) => {
    const next = new URLSearchParams(params);
    if (severity === s) next.delete("severity");
    else next.set("severity", s);
    const to = href("findings", route.id, next);
    return { href: to, onClick: (e: MouseEvent) => { e.preventDefault(); go(to, true); } };
  };

  const row = (f: Finding) => {
    const to = href("findings", f.id, params);
    const where = f.locations[0];
    const at = where && byId.get(where.source_id);
    const line = where?.line ? `:${where.line}` : "";
    const pkg = open ? null : packageOf(f); // the column gives way to the detail
    const others = f.locations.length - 1;
    return (
      <a key={f.id} href={to} className={`row${nav.cursor === f.id ? " cursor" : ""}${open?.id === f.id ? " open" : ""}${f.suppressed ? " dim" : ""}`}
         {...nav.rowProps(f.id)}>
        <SeverityIcon severity={f.severity} />
        <span className="title">{f.title}</span>
        <span className="flags">
          {f.suppression_lapsed && <LapsedIcon />}
          {f.confidence === "suggestive" && <ConfidenceIcon confidence={f.confidence} />}
        </span>
        {!open && <span className="pk">{pkg && <Tip text={pkg}>{packageName(report.package_labels, pkg)}</Tip>}</span>}
        <span className="where">
          {at && (at.provenance.file
            ? <Tip text={`${home(placeOf(at)!, report.home_dir)}${line}`}>{at.provenance.file.split("/").pop()}{line}</Tip>
            : <>{report.labels[at.id] ?? at.id}{line}</>)}
        </span>
        <span className="plus">
          {others > 0 && <Tip text={counted(others, "more location", "more locations")}>+{count(others)}</Tip>}
        </span>
      </a>
    );
  };

  const nothing = groups.length === 0 && suppressed.length === 0;
  return (
    <div className={`split findings${open ? " with-detail" : ""}`}>
      <div className="list-pane">
        <div className="toolbar">
          <h1>Findings</h1>
          <CountLine mix={mix} link={severityLink} current={severity} />
          {only && (
            <button type="button" className="text-button clear-filter" onClick={() => set("check", null)}
                    aria-label={`Show every check, not only ${checkName(only)}`}>
              {checkName(only)} <Icon icon={MARK.close} />
            </button>
          )}
          <SearchBox value={query} onChange={(v) => set("q", v || null)} placeholder="Filter" label="Filter findings" inputRef={searchRef} />
        </div>
        {report.not_recorded && <div className="strip"><span className="muted">{report.not_recorded}</span></div>}
        <div className="list fgrid">
          {nothing && (
            <p className="empty-state">
              {filtering ? "Nothing matches these filters."
                : notEvaluated.length > 0 ? "No findings from the checks that ran."
                : <><AllClearIcon /> No findings. Nothing in this configuration is broken, colliding or failing.</>}
            </p>
          )}
          {groups.map((g) => {
            const opened = fold.isOpen(g.check);
            const rows = fold.shown(g.check, g.findings);
            return (
              <section key={g.check} className="group">
                <GroupHead open={opened} onToggle={() => fold.flip(g.check)} trail={<SeverityMix mix={g.mix} />}>
                  <Tip text={g.check} className="group-name">{checkName(g.check)}</Tip>
                  {g.summary && <span className="group-summary">{g.summary}</span>}
                </GroupHead>
                {opened && rows.map(row)}
                {opened && <More hidden={fold.hidden(g.check, g.findings)} onClick={() => fold.showAll(g.check)} />}
              </section>
            );
          })}
          {suppressed.length > 0 && (
            <section className="group">
              <GroupHead open={fold.isOpen("suppressed")} onToggle={() => fold.flip("suppressed")} count={count(suppressed.length)}>
                <SuppressedIcon /> <span className="group-name">Suppressed</span>
                <span className="group-summary">hidden from check; shown again if what they rest on changes</span>
              </GroupHead>
              {fold.isOpen("suppressed") && fold.shown("suppressed", suppressed).map(row)}
              {fold.isOpen("suppressed") && (
                <More hidden={fold.hidden("suppressed", suppressed)} onClick={() => fold.showAll("suppressed")} />
              )}
            </section>
          )}
          <p className="to-dismissed">
            <a href={href("dismissed")} onClick={(e) => { e.preventDefault(); go(href("dismissed")); }}>Dismissed</a>: every
            finding you dismissed, in every project, to show one again
          </p>
          {notEvaluated.length > 0 && (
            <section className="not-evaluated">
              <span>Not evaluated, lacking their input this time:</span>
              {notEvaluated.map(([id, why]) => (
                <span key={id}>{checkName(id)} <code>{id}</code> {why}
                  {READ_FIRST.includes(id) && <> — <a href={href("instructions")} onClick={(e) => { e.preventDefault(); go(href("instructions")); }}>Read instructions</a></>}
                </span>
              ))}
            </section>
          )}
        </div>
      </div>
      {open && <FindingDetail report={report} finding={open} texts={texts} go={go} onClose={() => go(href("findings", null, params))}
                              dismiss={dismiss} onDismissed={onDismissed} />}
    </div>
  );
}
