// Every skill, agent and tool, by whether your turns use it: what you rely on first, then what never gets
// used — the candidates for removal (docs/tech_impl/11-ui.md, "Entities"; counts from the recorder, 08).
import { useCallback, useDeferredValue, useMemo, useRef, type MouseEvent } from "react";
import type { Entity, Report } from "./contract";
import { count, counted, day, packageName, VISIBILITY_SHORT, when } from "./format";
import { chosenOf, findingsBySource, mixOf, reference, sinceHistoryStart, usageSince } from "./model";
import { FilterMenu } from "./FilterMenu";
import { useFolding } from "./fold";
import { useListNavigation } from "./listNav";
import { configHref, href, type RouteOf } from "./route";
import { GroupSwatch, GroupHead, Icon, MARK, RoleIcon, SearchBox, SeverityIcon, SeverityMix, More, Tip, Toggle, VisibilityIcon } from "./ui";

interface Props {
  report: Report;
  route: RouteOf<"entities">;
  go: (to: string, replace?: boolean) => void;
}


export function EntitiesView({ report, route, go }: Props) {
  const { snapshot, usage, check } = report;
  const { params } = route;
  const { q: query, kind, unused, chosen: onlyChosen } = route.filters; // chosen: what the overview counts, each name once
  const needle = useDeferredValue(query).trim().toLowerCase();
  const searchRef = useRef<HTMLInputElement>(null);
  const set = useCallback((key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    go(href("entities", route.id, next), true);
  }, [params, route.id, go]);

  const sources = useMemo(() => new Map(snapshot.sources.map((s) => [s.id, s])), [snapshot]);
  const seen = useMemo(() => new Map(snapshot.visibility), [snapshot]);
  const findings = useMemo(() => findingsBySource(check.findings.filter((f) => !f.suppressed)), [check]);
  const used = usage?.entities ?? {};
  const chosen = useMemo(() => chosenOf(usage), [usage]);
  const kinds = useMemo(() => [...new Set(snapshot.entities.map((e) => e.kind.label))].sort(), [snapshot]);
  const pkg = (e: Entity) => sources.get(e.provided_by)?.provenance.package ?? null;
  const shown = snapshot.entities.filter((e) => (!kind || e.kind.label === kind) && (!unused || !used[reference(e)]) &&
    (!onlyChosen || chosen.ids.has(e.id)) &&
    (!needle || [e.name, e.description ?? "", pkg(e) ?? "", e.kind.label].join("\n").toLowerCase().includes(needle)));
  // since when the counts reach: the window's start, or the oldest kept turn if that is later — nothing said about before it
  const sinceAt = usageSince(report);
  const since = sinceAt ? day(sinceAt) : "";
  const byUse = (a: Entity, b: Entity) =>
    (used[reference(b)]?.invocations ?? 0) - (used[reference(a)]?.invocations ?? 0) || a.name.localeCompare(b.name);
  const groups = usage
    ? [
        { key: "used", title: `Used since ${since}`, rows: shown.filter((e) => used[reference(e)]).sort(byUse) },
        { key: "unused", title: `Never used since ${since}`, rows: shown.filter((e) => !used[reference(e)]).sort((a, b) =>
            (pkg(a) ?? "").localeCompare(pkg(b) ?? "") || a.name.localeCompare(b.name)) },
      ]
    : [{ key: "all", title: "Everything configured", rows: [...shown].sort((a, b) => a.name.localeCompare(b.name)) }];
  const filtering = !!(needle || kind || unused || onlyChosen);
  const fold = useFolding(filtering, groups.map((g) => ({ key: g.key, at: g.rows.findIndex((e) => e.id === route.id) }))
    .filter((r) => r.at >= 0));
  const max = Math.max(1, ...Object.values(used).map((u) => u.invocations));
  // what turns used in the chosen kinds that nothing here defines, as Python worked it out
  const elsewhere = Object.entries(usage?.summary.unmatched ?? {}).sort((a, b) => b[1] - a[1]);

  const navigable = groups.flatMap((g) => (fold.isOpen(g.key) ? fold.shown(g.key, g.rows) : []));
  const open = route.id ? snapshot.entities.find((e) => e.id === route.id) ?? null : null;
  const nav = useListNavigation({
    items: navigable, keyOf: (e) => e.id, openKey: open?.id ?? null, search: searchRef,
    open: (id, replace) => go(href("entities", id, params), replace),
    close: () => go(href("entities", null, params)),
    clear: () => { if (query) set("q", null); },
  });

  const row = (e: Entity) => {
    const to = href("entities", e.id, params);
    const u = used[reference(e)];
    const here = findings.get(e.provided_by) ?? [];
    const source = sources.get(e.provided_by);
    return (
      <a key={e.id} href={to} className={`row${nav.cursor === e.id ? " cursor" : ""}${open?.id === e.id ? " open" : ""}`}
         {...nav.rowProps(e.id)}>
        {usage && (
          <span className="cost-bar" aria-hidden="true">
            <span className="fill use" style={{ width: `${u ? Math.max(4, (u.invocations / max) * 100) : 0}%` }} />
          </span>
        )}
        {usage && <span className="num">{u ? count(u.invocations) : "—"}</span>}
        <RoleIcon role={e.kind.role} label={e.kind.label} />
        <VisibilityIcon visibility={seen.get(e.id)} />
        <span className="title">{e.name}</span>
        <span className="trail">
          {here.length > 0 && (
            <SeverityMix mix={mixOf(here)} tip={`${counted(here.length, "finding", "findings")} on what defines it`} />
          )}
          {source && <GroupSwatch authority={source.provenance.authority} />}
          {u && <span className="where">{when(u.last_seen)}</span>}
        </span>
      </a>
    );
  };

  return (
    <div className={`split entities-view${open ? " with-detail" : ""}`}>
      <div className="list-pane">
        {usage && (
          <div className="strip">
            <span><strong>{count(chosen.used.length)}</strong> of {counted(chosen.ids.size, "skill or agent", "skills and agents")} used since {since}</span>
            <span className="muted">· across your {counted(usage.turns, "turn", "turns")}</span>
          </div>
        )}
        <div className="toolbar">
          <SearchBox value={query} onChange={(v) => set("q", v || null)} placeholder="Search names, descriptions and packages" inputRef={searchRef} />
          {usage && chosen.ids.size > 0 && (
            <Toggle on={onlyChosen} onClick={() => set("chosen", onlyChosen ? null : "1")}
                    label="Only the skills and agents the agent chooses between, each name once — what the overview counts">
              skills and agents
            </Toggle>
          )}
          {usage && (
            <Toggle on={unused} onClick={() => set("unused", unused ? null : "1")} label="Only what your turns never used">
              never used
            </Toggle>
          )}
          <FilterMenu label="Kind" value={kind ?? ""} onChoose={(key) => set("kind", key || null)}
                      choices={[{ key: "", label: "Every kind" }, ...kinds.map((k) => ({ key: k, label: k }))]} />
          <span className="muted counter">{count(shown.length)} of {count(onlyChosen ? chosen.ids.size : snapshot.entities.length)}</span>
        </div>
        <div className="list">
          {!usage && <p className="empty-state">No usage yet: it is read from your session transcripts each time agentlint runs.</p>}
          {groups.map((g) => (
            <section key={g.key} className="group">
              <GroupHead open={fold.isOpen(g.key)} onToggle={() => fold.flip(g.key)} count={count(g.rows.length)}>
                <span className="group-name">{g.title}</span>
              </GroupHead>
              {fold.isOpen(g.key) && fold.shown(g.key, g.rows).map(row)}
              {fold.isOpen(g.key) && <More hidden={fold.hidden(g.key, g.rows)} onClick={() => fold.showAll(g.key)} />}
            </section>
          ))}
          {elsewhere.length > 0 && !filtering && (
            <section className="not-evaluated">
              <Tip text="Your turns used these, but nothing in this configuration defines them: built-in commands, or packages since removed" className="muted">
                Used, but not in this configuration:
              </Tip>
              {elsewhere.map(([ref, n]) => <span key={ref}><code>{ref.split("/")[1]}</code> ×{n}</span>)}
            </section>
          )}
        </div>
      </div>
      {open && <EntityDetail report={report} entity={open} go={go} onClose={() => go(href("entities", null, params))} />}
    </div>
  );
}

function EntityDetail({ report, entity, go, onClose }: { report: Report; entity: Entity; go: (to: string) => void; onClose: () => void }) {
  const { snapshot, usage, labels, check } = report;
  const source = snapshot.sources.find((s) => s.id === entity.provided_by);
  const visibility = new Map(snapshot.visibility).get(entity.id);
  const u = usage?.entities[reference(entity)];
  const sinceAt = usageSince(report);
  const since = sinceAt ? day(sinceAt) : "";
  const here = check.findings.filter((f) => !f.suppressed && f.sources.includes(entity.provided_by));
  const link = (to: string) => ({ href: to, onClick: (e: MouseEvent) => { e.preventDefault(); go(to); } });
  return (
    <aside className="detail" aria-label="Entity">
      <header className="detail-head">
        <RoleIcon role={entity.kind.role} label={entity.kind.label} />
        <h2>{entity.name}</h2>
        <button type="button" className="icon-button" onClick={onClose} aria-label="Close"><Icon icon={MARK.close} size={16} /></button>
      </header>
      {entity.description && <p className="detail-text">{entity.description}</p>}
      <dl className="props">
        <dt>Kind</dt><dd>{entity.kind.label}</dd>
        <dt>Seen</dt><dd><VisibilityIcon visibility={visibility} /> {VISIBILITY_SHORT[visibility ?? "unknown"]}</dd>
        {usage && (
          <>
            <dt>Used</dt>
            <dd>
              {u
                ? `In ${count(u.invocations)} of your ${counted(usage.turns, "turn", "turns")} since ${since}; last on ${when(u.last_seen)}.`
                : `Never used since ${since}${sinceHistoryStart(report) ? " — as far back as your kept history goes" : ""}.`}
            </dd>
          </>
        )}
        {entity.aliases.length > 0 && (<><dt>Also called</dt><dd>{entity.aliases.join(", ")}</dd></>)}
        {source && (
          <>
            <dt>Defined in</dt>
            <dd>
              <GroupSwatch authority={source.provenance.authority} /> <a {...link(configHref({ kind: "source", id: source.id }))}>{labels[source.id] ?? source.id}</a>
              {source.provenance.package && (
                <> · <Tip text={source.provenance.package}>{packageName(report.package_labels, source.provenance.package)}</Tip></>
              )}
            </dd>
          </>
        )}
      </dl>
      {here.length > 0 && (
        <section>
          <h3>Findings on what defines it <span className="count">{here.length}</span></h3>
          <ul className="links">
            {here.map((f) => <li key={f.id}><SeverityIcon severity={f.severity} /> <a {...link(href("findings", f.id))}>{f.title}</a></li>)}
          </ul>
        </section>
      )}
    </aside>
  );
}
