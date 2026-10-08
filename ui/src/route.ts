// Where you are lives in the URL's hash — `#/findings/<id>`, `#/config?load=always` — so the browser's
// back button works and a view can link to another.
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Authority, Load, Severity } from "./contract";
import { AUTHORITIES, GROUPS, LOADS, SEVERITIES, TARGET_KINDS, targetKey, type Group, type Target } from "./model";

export type View = "overview" | "findings" | "config" | "entities" | "context" | "sessions" | "diagnostics" | "tests" | "settings" | "changes" | "dismissed" | "results" | "instructions" | "jobs";
export const VIEWS: View[] = ["overview", "findings", "config", "entities", "context", "sessions", "diagnostics", "tests", "settings"]; // keys 1-9, in this order
/** Every view the hash can name: the keyed ones, What changed, which is Configuration's, Dismissed, reached from
 * Findings, and Results, Tests' — none with a key of its own. */
export const PAGES: View[] = [...VIEWS, "changes", "dismissed", "results", "instructions", "jobs"];

export const VIEW_NAMES: Record<View, string> = {
  overview: "Overview",
  findings: "Findings",
  config: "Configuration",
  entities: "Skills and agents",
  context: "Session context",
  sessions: "Sessions",
  diagnostics: "Diagnostics",
  tests: "Tests",
  settings: "Settings",
  changes: "What changed",
  dismissed: "Dismissed",
  results: "Results",
  instructions: "Read instructions",
  jobs: "Jobs",
};

/** Views about agentlint itself, not the project in view: they open with or without one, and never wait for its report. */
export const PROJECTLESS: readonly View[] = ["settings", "dismissed", "jobs"];

/** The key that opens a view: its place in VIEWS; none for a view reached from another (What changed, Dismissed,
 * Results). */
export const viewKey = (view: View): string => (VIEWS.includes(view) ? String(VIEWS.indexOf(view) + 1) : "");

/** Findings' filters (`?q=&severity=&check=`). */
export interface FindingsFilters { q: string; severity: Severity | null; check: string | null }
/** Configuration's filters (`?q=&load=&group=&authority=&package=&line=`): one of group and authority at a time. */
export interface ConfigFilters {
  q: string; load: Load | null; group: Group | null; authority: Authority | null; package: string | null; line: number | null;
}
/** Skills and agents' filters (`?q=&kind=&unused=1&chosen=1`). */
export interface EntitiesFilters { q: string; kind: string | null; unused: boolean; chosen: boolean }

interface At<V extends View> {
  view: V;
  id: string | null; // what is open beside the list: a finding, a source, an entity, a session
  params: URLSearchParams; // the query as written, to carry over when moving
}

/** Where you are: the view, what is open, and that view's filters, parsed once and checked — a value no filter has
 * (`?severity=loud`) is no filter, never a cast that renders "undefined" or an empty list with nothing marked. */
export type Route =
  | (At<"findings"> & { filters: FindingsFilters })
  | (At<"config"> & { filters: ConfigFilters; target: Target | null })
  | (At<"entities"> & { filters: EntitiesFilters })
  | (At<"changes"> & { from: string | null }) // id: the snapshot open; from: the one it is compared with (none: the one before)
  | At<"overview" | "context" | "sessions" | "diagnostics" | "tests" | "settings" | "dismissed" | "results" | "instructions" | "jobs">; // results: id, a
  // test's (`#/tests/<job>`, as links were before 2026-10-06, opens it there)
export type RouteOf<V extends View> = Extract<Route, { view: V }>;

/** The value if it is one of `values`, else null. */
function oneOf<T extends string>(values: readonly T[], value: string | null): T | null {
  return value !== null && (values as readonly string[]).includes(value) ? (value as T) : null;
}

/** A part of the hash decoded, or null when it doesn't decode (`%E0%A4%A`): a malformed link opens nothing. */
function decoded(part: string): string | null {
  try {
    return decodeURIComponent(part);
  } catch {
    return null;
  }
}

export function parse(hash: string): Route {
  const [path, query = ""] = hash.replace(/^#\/?/, "").split("?");
  const [first, ...rest] = path.split("/");
  const view = PAGES.includes(first as View) ? (first as View) : "overview";
  const id = rest.length && rest.join("/") ? decoded(rest.join("/")) : null;
  // Configuration splits before decoding: a source id may hold a "/", written encoded. A bare id (`#/config/<id>`, as
  // links were before 2026-10-04) is a source's.
  const kind = view === "config" && rest.length === 2 ? oneOf(TARGET_KINDS, rest[0]) : null;
  const typed = kind ? decoded(rest[1]) : null;
  const target: Target | null = kind ? (typed ? { kind, id: typed } : null) : id ? { kind: "source", id } : null;
  const params = new URLSearchParams(query);
  const q = params.get("q") ?? "";
  switch (view) {
    case "findings":
      return { view, id, params, filters: { q, severity: oneOf(SEVERITIES, params.get("severity")), check: params.get("check") || null } };
    case "config": {
      const line = Number(params.get("line"));
      const group = oneOf(GROUPS, params.get("group"));
      return { view, id: target && targetKey(target), params, target, filters: {
        q, load: oneOf(LOADS, params.get("load")), group, authority: group ? null : oneOf(AUTHORITIES, params.get("authority")),
        package: params.get("package") || null, line: Number.isInteger(line) && line > 0 ? line : null,
      } };
    }
    case "entities":
      return { view, id, params, filters: { q, kind: params.get("kind") || null, unused: params.get("unused") === "1",
                                            chosen: params.get("chosen") === "1" } };
    case "changes":
      return { view, id, params, from: params.get("from") || null };
    default:
      return { view, id, params };
  }
}

export function href(view: View, id: string | null = null, params: Record<string, string> | URLSearchParams = {}): string {
  const query = new URLSearchParams(params).toString();
  return `#/${view}${id ? `/${encodeURIComponent(id)}` : ""}${query ? `?${query}` : ""}`;
}

/** Configuration with `target` open beside the list, or nothing open. */
export function configHref(target: Target | null, params: Record<string, string> | URLSearchParams = {}): string {
  const query = new URLSearchParams(params).toString();
  return `#/config${target ? `/${target.kind}/${encodeURIComponent(target.id)}` : ""}${query ? `?${query}` : ""}`;
}

/** The current route, and a way to move: `replace` for filter changes, so typing doesn't fill the history. */
export function useRoute(): [Route, (to: string, replace?: boolean) => void] {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const changed = () => setHash(window.location.hash);
    window.addEventListener("hashchange", changed);
    window.addEventListener("popstate", changed); // Back across a pushed entry (a project chosen) fires only this
    return () => {
      window.removeEventListener("hashchange", changed);
      window.removeEventListener("popstate", changed);
    };
  }, []);
  const go = useCallback((to: string, replace = false) => {
    if (replace) window.history.replaceState(null, "", to);
    else if (window.location.hash !== to) window.location.hash = to;
    setHash(to);
  }, []);
  const route = useMemo(() => parse(hash), [hash]);
  return [route, go];
}

/** Keys anywhere on the page, except while typing — where only Escape gets through. */
export function useKeys(handle: (key: string, event: KeyboardEvent) => boolean) {
  useEffect(() => {
    const pressed = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      const typing = !!target && (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable);
      if (typing && event.key !== "Escape") return;
      if (handle(event.key, event)) event.preventDefault();
    };
    window.addEventListener("keydown", pressed);
    return () => window.removeEventListener("keydown", pressed);
  }, [handle]);
}
