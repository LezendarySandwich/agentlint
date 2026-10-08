// Everything configured, by when it loads and then by what it costs — searchable, filterable, and each
// source open-able beside the list. What loads every session comes first, because that is what you pay for.
import { useCallback, useDeferredValue, useMemo, useRef } from "react";
import type { Report, Source } from "./contract";
import type { Texts } from "./data";
import { BudgetBar } from "./Budget";
import { count, counted, GROUP_WORDS, home, LISTING_OWN_PART, LOAD_WORDS, packageName, short } from "./format";
import { FilterMenu } from "./FilterMenu";
import {
  authoritiesIn, findingsBySource, isTestable, targetKey, targetOf, firstTurnBudget, GROUP_OF, groupByLoad, GROUPS, LOADS, mixOf, placeOf, splitOf, tokensOf,
  type Row,
} from "./model";
import { rowLabel } from "./Overview";
import { useFolding, type Reveal } from "./fold";
import { useListNavigation } from "./listNav";
import { configHref, type RouteOf } from "./route";
import { ListingDetail, SourceDetail, UntracedDetail } from "./SourceDetail";
import {
  CostBar, GroupHead, GroupSwatch, Icon, LOAD_ICON, LoadIcon, LockIcon, MARK, SearchBox, SeenIcon, SeverityMix, More, splitWords, Tip, Toggle,
  UnseenIcon, UntracedIcon, WhatIcon,
} from "./ui";

interface Props {
  report: Report;
  texts: Texts | null; // null while still unpacking
  textsMissing?: string | null; // why there are no texts, when asking for them failed
  route: RouteOf<"config">;
  go: (to: string, replace?: boolean) => void;
}

export function ConfigView({ report, texts, textsMissing = null, route, go }: Props) {
  const { snapshot, labels, check } = report;
  const { params } = route;
  const { q: query, authority, group, load, package: pkg, line } = route.filters; // group or authority, one at a time
  const needle = useDeferredValue(query).trim().toLowerCase(); // typing stays responsive while results catch up
  const searchRef = useRef<HTMLInputElement>(null);

  const set = useCallback((key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("line");
    go(configHref(route.target, next), true);
  }, [params, route.target, go]);

  // Who put it there: one group or one authority at a time, the other cleared.
  const pick = useCallback((by: { group: string } | { authority: string } | null) => {
    const next = new URLSearchParams(params);
    next.delete("group");
    next.delete("authority");
    next.delete("line");
    if (by) for (const [key, value] of Object.entries(by)) next.set(key, value);
    go(configHref(route.target, next), true);
  }, [params, route.target, go]);

  // A finding's ?line= marks the source it pointed into, and nothing after: moving on drops it.
  const kept = useMemo(() => {
    const next = new URLSearchParams(params);
    next.delete("line");
    return next;
  }, [params]);

  const packages = useMemo(
    () => [...new Set(snapshot.sources.map((s) => s.provenance.package).filter((p): p is string => !!p))].sort(),
    [snapshot],
  );
  // Lowercased once, when the texts arrive — searching then scans, never re-lowercases megabytes per keystroke.
  const haystack = useMemo(() => {
    const text = (content: Source["content"]) => ("text_ref" in content && texts ? texts[content.text_ref] ?? "" : "");
    const index = new Map<string, string>();
    for (const s of snapshot.sources) {
      const where = placeOf(s) ?? ""; // both forms: the UI shows paths with ~
      index.set(s.id, [labels[s.id] ?? "", s.id, where, home(where, report.home_dir), s.provenance.package ?? "", text(s.content)]
        .join("\n").toLowerCase());
    }
    for (const u of snapshot.untraced) index.set(u.id, [u.label, text(u.content)].join("\n").toLowerCase());
    return index;
  }, [snapshot, labels, texts, report.home_dir]);

  const filtering = !!(needle || authority || group || load || pkg);
  const groups = useMemo(() => {
    const sources = snapshot.sources.filter((s) => (!authority || s.provenance.authority === authority) &&
      (!group || GROUP_OF[s.provenance.authority] === group) &&
      (!load || s.activation.load === load) && (!pkg || s.provenance.package === pkg) && (!needle || haystack.get(s.id)!.includes(needle)));
    // Untraced text has no authority: it shows under no authority filter, and of the groups only under harness.
    const untraced = snapshot.untraced.filter((u) =>
      !authority && (!group || group === "harness") && (!load || u.load === load) && !pkg &&
      (!needle || haystack.get(u.id)!.includes(needle)));
    return groupByLoad(sources, untraced);
  }, [snapshot, haystack, needle, authority, group, load, pkg]);
  const budget = useMemo(() => firstTurnBudget(report), [report]);
  const largest = useMemo(
    () => Math.max(1, ...snapshot.sources.map((s) => tokensOf(s.content) ?? 0)),
    [snapshot],
  );
  const findings = useMemo(() => findingsBySource(check.findings.filter((f) => !f.suppressed)), [check]);
  // Sources, counted as the total counts them: a listing by its parts, untraced text not at all (it has no source).
  const sourcesIn = (rows: Row[]) => rows.reduce((m, r) => m + (r.kind === "listing" ? r.parts.length : r.kind === "source" ? 1 : 0), 0);
  const shownCount = groups.reduce((n, g) => n + sourcesIn(g.rows), 0);

  // What you opened — from a finding's location, the overview — opens the group holding it, and the listing it is
  // a part of, far enough to show it; they stay yours to fold.
  const target = route.target;
  const holds = (r: Row) => r.key === route.id ||
    (r.kind === "listing" && target?.kind === "source" && r.parts.some((p) => p.id === target.id));
  const reveal: Reveal[] = [];
  for (const g of groups) {
    const at = g.rows.findIndex(holds);
    if (at < 0) continue;
    reveal.push({ key: g.load, at });
    if (g.rows[at].kind === "listing") reveal.push({ key: g.rows[at].key, at: 0 });
  }
  const fold = useFolding(filtering, reveal);
  const partsOpen = (r: Row & { kind: "listing" }) => fold.isOpen(r.key);

  // What the keys move through: the rows shown in open groups, and the parts of opened listings.
  const navigable: string[] = [];
  const listingOf = new Map<string, string>(); // a listing's key under its own key and each of its shown parts'
  for (const g of groups) {
    if (!fold.isOpen(g.load)) continue;
    for (const r of fold.shown(g.load, g.rows)) {
      navigable.push(r.key);
      if (r.kind !== "listing") continue;
      listingOf.set(r.key, r.key);
      if (!partsOpen(r)) continue;
      for (const p of r.parts) {
        const key = targetKey({ kind: "source", id: p.id });
        navigable.push(key);
        listingOf.set(key, r.key);
      }
    }
  }
  const nav = useListNavigation({
    items: navigable, keyOf: (key) => key, openKey: route.id, search: searchRef,
    open: (key, replace) => go(configHref(targetOf(key), kept), replace), // the open source follows the cursor
    close: () => go(configHref(null, kept)),
    clear: () => { if (query) set("q", null); },
    // On a listing, → opens its parts and ← closes them; ← on a part closes its listing and puts the cursor on it.
    more: (key, cursor, setCursor) => {
      const listing = cursor === null ? undefined : listingOf.get(cursor);
      if (!listing || (key !== "ArrowRight" && key !== "ArrowLeft")) return false;
      fold.setOpen(listing, key === "ArrowRight");
      if (key === "ArrowLeft") setCursor(listing);
      return true;
    },
  });

  const sourceSplit = (s: Source) => splitOf([[s.provenance.authority, tokensOf(s.content) ?? 0]]);
  // A listing's own part — no package behind it — counts as harness until the adapter can split it; it says so.
  const ownPart = (s: Source) => s.provenance.point.mechanism === "harness_generated" && !s.provenance.package;
  const listingWords = (r: Row & { kind: "listing" }) => {
    const own = r.parts.find(ownPart);
    const holds = own ? `${GROUP_WORDS[GROUP_OF[own.provenance.authority]]} holds ${LISTING_OWN_PART}` : "";
    return [splitWords(r.split), holds].filter(Boolean).join(" — ") || undefined;
  };

  const rowClass = (key: string, extra = "") =>
    `row${extra}${nav.cursor === key ? " cursor" : ""}${route.id === key ? " open" : ""}`;
  const sourceRow = (s: Source, nested = false) => {
    const key = targetKey({ kind: "source", id: s.id });
    const to = configHref({ kind: "source", id: s.id }, kept);
    const content = s.content;
    const tokens = tokensOf(content);
    const here = findings.get(s.id) ?? [];
    return (
      <a key={s.id} href={to} className={rowClass(key, nested ? " nested" : "")} {...nav.rowProps(key)}>
        <CostBar value={tokens} max={largest} split={sourceSplit(s)}
                 text={ownPart(s) ? `${GROUP_WORDS[GROUP_OF[s.provenance.authority]]} · ${LISTING_OWN_PART}` : undefined} />
        <span className="num">{tokens === null ? "—" : short(tokens)}</span>
        <span className="title">{labels[s.id] ?? s.id}</span>
        {!nested && s.provenance.point.description && <WhatIcon text={s.provenance.point.description} />}
        <span className="trail">
          {here.length > 0 && (
            <SeverityMix mix={mixOf(here)} tip={`${counted(here.length, "finding rests", "findings rest")} on this source`} />
          )}
          {content.type === "observed_text" && <SeenIcon seen={content.seen_in.length} of={content.sessions_examined} />}
          {content.type === "not_observed" && <UnseenIcon reason={content.reason} />}
          {!isTestable(s) && <LockIcon reason={s.ablation.blocked_reason} />}
        </span>
      </a>
    );
  };
  const row = (r: Row) => {
    if (r.kind === "source") return sourceRow(r.source);
    const to = configHref(r, kept);
    if (r.kind === "untraced") {
      return (
        <a key={r.key} href={to} className={rowClass(r.key)} {...nav.rowProps(r.key)}>
          <CostBar value={r.tokens} max={largest} split={r.split} />
          <span className="num">{r.tokens === null ? "—" : short(r.tokens)}</span>
          <UntracedIcon />
          <span className="title">{r.untraced.label}</span>
          {r.untraced.point.description && <WhatIcon text={r.untraced.point.description} />}
          <span className="trail" />
        </a>
      );
    }
    const opened = partsOpen(r);
    const what = r.parts[0]?.provenance.point.description;
    // The chevron opens the parts in place; the row beside it is a link to the listing, with nothing inside it.
    return (
      <div key={r.key} className="listing">
        <button type="button" className="parts-toggle" aria-expanded={opened} aria-label="Parts" onClick={() => fold.flip(r.key)}>
          <Icon icon={MARK.disclose} className={`chevron${opened ? " open" : ""}`} />
        </button>
        <a href={to} className={rowClass(r.key)} {...nav.rowProps(r.key)}>
          <CostBar value={r.tokens} max={largest} split={r.split} text={listingWords(r)} />
          <span className="num">{short(r.tokens)}</span>
          <span className="title">{rowLabel(r, labels)}</span>
          {what && <WhatIcon text={what} />}
          <span className="trail" />
        </a>
        {opened && r.parts.map((p) => sourceRow(p, true))}
      </div>
    );
  };

  // What is open, by its kind: each kind its own id space (a listing is found among every listing, filtered or not).
  const openSource = target?.kind === "source" ? snapshot.sources.find((s) => s.id === target.id) : undefined;
  const openListing = target?.kind === "listing"
    ? groupByLoad(snapshot.sources, []).flatMap((g) => g.rows).find((r) => r.kind === "listing" && r.id === target.id)
    : undefined;
  const openUntraced = target?.kind === "untraced" ? snapshot.untraced.find((u) => u.id === target.id) : undefined;
  const close = () => go(configHref(null, kept));
  const detail = openSource ? (
    <SourceDetail report={report} source={openSource} texts={texts} textsMissing={textsMissing} line={line} needle={needle}
                  findings={findings.get(openSource.id) ?? []} go={go} onClose={close} />
  ) : openListing?.kind === "listing" ? (
    <ListingDetail report={report} row={openListing} go={go} onClose={close} />
  ) : openUntraced ? (
    <UntracedDetail untraced={openUntraced} texts={texts} textsMissing={textsMissing} onClose={close} />
  ) : null;

  return (
    <div className={`split config${detail ? " with-detail" : ""}`}>
      <div className="list-pane">
        <div className="budget-strip">
          <BudgetBar budget={budget} onPick={(g) => pick(group === g ? null : { group: g })} />
        </div>
        <div className="toolbar">
          <SearchBox value={query} onChange={(v) => set("q", v || null)} inputRef={searchRef}
                     placeholder={texts ? "Search names, paths and text" : "Search names and paths"} />
          {LOADS.map((l) => {
            const n = snapshot.sources.filter((s) => s.activation.load === l).length;
            return n === 0 ? null : (
              <Toggle key={l} on={load === l} onClick={() => set("load", load === l ? null : l)} label={`Only what loads ${LOAD_WORDS[l]}: ${n}`}>
                <Icon icon={LOAD_ICON[l]} /> {count(n)}
              </Toggle>
            );
          })}
          <FilterMenu label="Package" value={pkg ?? ""} onChoose={(key) => set("package", key || null)}
                      choices={[{ key: "", label: "Any package" }, ...packages.map((p) => {
                        const name = packageName(report.package_labels, p);
                        return { key: p, label: name, hint: name === p ? undefined : p };
                      })]} />
          <FilterMenu label="Who put it there" value={group ? `group:${group}` : authority ? `authority:${authority}` : ""}
                      onChoose={(key) => {
                        const [by, value] = key.split(":");
                        pick(by === "group" ? { group: value } : by === "authority" ? { authority: value } : null);
                      }}
                      choices={[{ key: "", label: "Anyone" }, ...GROUPS.flatMap((g) => [
                        { key: `group:${g}`, label: GROUP_WORDS[g], mark: <GroupSwatch group={g} /> },
                        ...authoritiesIn(g).map((a) => ({ key: `authority:${a}`, label: a, mark: <GroupSwatch authority={a} />, hint: GROUP_WORDS[g],
                                                          nested: true })),
                      ])]} />
          <span className="muted counter">{count(shownCount)} of {count(snapshot.sources.length)}</span>
        </div>
        <div className="list">
          {groups.length === 0 && <p className="empty-state">Nothing matches these filters.</p>}
          {groups.map((g) => {
            const opened = fold.isOpen(g.load);
            const rows = fold.shown(g.load, g.rows);
            const n = sourcesIn(g.rows);
            const untraced = g.rows.filter((r) => r.kind === "untraced").length;
            return (
              <section key={g.load} className="group">
                <GroupHead open={opened} onToggle={() => fold.flip(g.load)}
                           count={g.tokens === null ? <Tip text="No recent session showed their text">—</Tip>
                                                    : <Tip text={counted(g.tokens, "token", "tokens")}>{short(g.tokens)}</Tip>}>
                  <LoadIcon load={g.load} />
                  <span className="group-name">{LOAD_WORDS[g.load][0].toUpperCase() + LOAD_WORDS[g.load].slice(1)}</span>
                  <span className="group-summary">
                    {[n ? counted(n, "source", "sources") : "", untraced ? `${count(untraced)} untraced` : ""].filter(Boolean).join(" · ")}
                  </span>
                </GroupHead>
                {opened && rows.map(row)}
                {opened && <More hidden={fold.hidden(g.load, g.rows)} onClick={() => fold.showAll(g.load)} />}
              </section>
            );
          })}
        </div>
      </div>
      {detail}
    </div>
  );
}
