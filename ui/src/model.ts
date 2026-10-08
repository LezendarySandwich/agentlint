// Derived views of a report, computed once: the budget for the overview, groups for the lists, and
// the cross-references between findings and the sources they rest on.
import type { Authority, Content, Entity, Finding, Load, Report, Severity, Source, Untraced, Usage } from "./contract";

export const SEVERITIES: Severity[] = ["error", "warn", "info"];
export const AUTHORITIES: Authority[] = ["managed", "user", "project", "package", "runtime", "harness"];
export const LOADS: Load[] = ["always", "per_message", "on_invoke", "conditional", "not_in_context"];

/** Who put the text there, by how much of it is yours to change: yours (your home directory, this project), installed
 * (a package), or the harness's (its own text, what it produces while running, and your organisation's policy). */
export type Group = "yours" | "installed" | "harness";
export const GROUPS: Group[] = ["yours", "installed", "harness"];
export const GROUP_OF: Record<Authority, Group> = {
  user: "yours", project: "yours", package: "installed", harness: "harness", runtime: "harness", managed: "harness",
};
export const authoritiesIn = (group: Group): Authority[] => AUTHORITIES.filter((a) => GROUP_OF[a] === group);

/** Part of what the agent receives before your first words: the report's own `loads` block, never a copy of the rule. */
export const inFirstTurn = (report: Report, load: Load): boolean => report.loads[load]?.in_first_turn ?? false;

export const severityRank = (severity: Severity): number => SEVERITIES.indexOf(severity);

/** The tokens a content holds, when its text (or size) is known: the one reader of `content.tokens`. */
export function tokensOf(content: Content): number | null {
  return "tokens" in content ? content.tokens.value : null;
}

/** Whether removing it can be tested (an ablation mechanism exists); `ablation.blocked_reason` says why not. */
export const isTestable = (source: Source): boolean => source.ablation.mechanism !== "none";

/** Tokens in each of the three groups. */
export type Split = Record<Group, number>;

/** The one attribution function — the budget bar and every row use it, so the same tokens are never painted two
 * ways: each part's tokens go to the group of the authority behind it. A part with no authority (null) is untraced
 * text, which counts as harness: nobody configured it, and it arrives with what the harness delivers. */
export function splitOf(parts: [Authority | null, number][]): Split {
  const split: Split = { yours: 0, installed: 0, harness: 0 };
  for (const [authority, n] of parts) split[authority === null ? "harness" : GROUP_OF[authority]] += n;
  return split;
}

export interface Budget {
  byGroup: Split;
  byAuthority: Record<Authority, number>; // for the tooltips: who inside each group
  untraced: number; // counted in harness, named apart in its tooltip
  unattributed: number; // an observed total's remainder: in no group, since nobody knows who put it there
  total: number; // the groups and the remainder: the observed total when reconciled
}

/** What the first turn carries, by who put it there: the always-on outcome's own attribution, grouped — never a second
 * sum over the sources, so the bar's parts and its remainder count the same tokens. */
export function firstTurnBudget(report: Report): Budget {
  const { always_on: outcome, sources } = report.snapshot;
  const authority = new Map(sources.map((s) => [s.id, s.provenance.authority]));
  const byAuthority = Object.fromEntries(AUTHORITIES.map((a) => [a, 0])) as Record<Authority, number>;
  const parts: [Authority | null, number][] = outcome.attribution.map((a) => [
    a.source_id === null ? null : authority.get(a.source_id) ?? null, a.tokens]);
  let untraced = 0;
  for (const [who, n] of parts) {
    if (who === null) untraced += n;
    else byAuthority[who] += n;
  }
  const byGroup = splitOf(parts);
  const unattributed = outcome.type === "reconciled" ? outcome.unattributed.value : 0;
  return { byGroup, byAuthority, untraced, unattributed, total: GROUPS.reduce((n, g) => n + byGroup[g], 0) + unattributed };
}

/** What Configuration opens beside its list, each kind its own id space, so no id is told apart by a prefix:
 * `#/config/source/<source id>`, `#/config/listing/<load>|<point>`, `#/config/untraced/<untraced id>`. */
export type TargetKind = "source" | "listing" | "untraced";
export const TARGET_KINDS: TargetKind[] = ["source", "listing", "untraced"];
export interface Target { kind: TargetKind; id: string }

/** A target as one string, for keying rows and the cursor: "<kind>/<id>". The kind has no "/", so it splits back. */
export const targetKey = (target: Target): string => `${target.kind}/${target.id}`;
export function targetOf(key: string): Target | null {
  const at = key.indexOf("/");
  const kind = key.slice(0, Math.max(at, 0)) as TargetKind;
  return at > 0 && TARGET_KINDS.includes(kind) ? { kind, id: key.slice(at + 1) } : null;
}

/** A row of the configuration list: one source, a listing the harness splits per package, or untraced text — each
 * with its tokens split into the three groups by `splitOf`. `id` is what opens it (a `Target` of its kind: the
 * source's id, the listing's "<load>|<point>", the untraced text's id); `key` is that target as one string. */
export type Row =
  | { kind: "source"; id: string; key: string; tokens: number | null; split: Split; source: Source }
  | { kind: "listing"; id: string; key: string; tokens: number; split: Split; label: string; parts: Source[] }
  | { kind: "untraced"; id: string; key: string; tokens: number | null; split: Split; untraced: Untraced };

export interface LoadGroup {
  load: Load;
  rows: Row[];
  tokens: number | null; // null when no row's text was seen
}

const byCost = (a: { tokens: number | null; key: string }, b: { tokens: number | null; key: string }) =>
  (b.tokens ?? -1) - (a.tokens ?? -1) || a.key.localeCompare(b.key);

/** Sources and untraced text by when they load, most expensive first; listing slices folded into one row. */
export function groupByLoad(sources: Source[], untraced: Untraced[]): LoadGroup[] {
  const rows = new Map<Load, Row[]>(LOADS.map((load) => [load, []]));
  const listings = new Map<string, Source[]>();
  for (const source of sources) {
    if (source.provenance.point.mechanism === "harness_generated") {
      const key = `${source.activation.load}|${source.provenance.point.name}`;
      listings.set(key, [...(listings.get(key) ?? []), source]);
    } else {
      rows.get(source.activation.load)!.push(sourceRow(source));
    }
  }
  for (const [key, parts] of listings) {
    const load = parts[0].activation.load;
    if (parts.length === 1) {
      rows.get(load)!.push(sourceRow(parts[0]));
      continue;
    }
    const sorted = [...parts].sort((a, b) => byCost(sourceRow(a), sourceRow(b)));
    rows.get(load)!.push({
      kind: "listing", id: key, key: targetKey({ kind: "listing", id: key }), label: parts[0].provenance.point.label, parts: sorted,
      split: splitOf(parts.map((p) => [p.provenance.authority, tokensOf(p.content) ?? 0])), // ownership from its parts
      tokens: parts.reduce((sum, p) => sum + (tokensOf(p.content) ?? 0), 0),
    });
  }
  for (const item of untraced) {
    const n = tokensOf(item.content);
    rows.get(item.load)!.push({ kind: "untraced", id: item.id, key: targetKey({ kind: "untraced", id: item.id }), tokens: n,
                                split: splitOf([[null, n ?? 0]]), untraced: item });
  }
  return LOADS.map((load) => {
    const list = rows.get(load)!.sort(byCost);
    const known = list.filter((r) => r.tokens !== null);
    return { load, rows: list, tokens: known.length ? known.reduce((sum, r) => sum + (r.tokens ?? 0), 0) : null };
  }).filter((group) => group.rows.length > 0);
}

function sourceRow(source: Source): Row & { kind: "source" } {
  const n = tokensOf(source.content);
  return { kind: "source", id: source.id, key: targetKey({ kind: "source", id: source.id }), tokens: n, split: splitOf([[source.provenance.authority, n ?? 0]]), source };
}

/** Findings counted by severity. */
export type Mix = Record<Severity, number>;

export function mixOf(findings: Finding[]): Mix {
  const mix: Mix = { error: 0, warn: 0, info: 0 };
  for (const finding of findings) mix[finding.severity] += 1;
  return mix;
}

export interface CheckGroup {
  check: string;
  summary: string | null;
  kind: string;
  severity: Severity; // the worst among its findings
  mix: Mix; // how many of each severity
  findings: Finding[];
}

/** Findings by the check that found them; the most severe checks first, then the largest. */
export function groupByCheck(findings: Finding[], summaries: Record<string, string>): CheckGroup[] {
  const byCheck = new Map<string, Finding[]>();
  for (const finding of findings) byCheck.set(finding.check, [...(byCheck.get(finding.check) ?? []), finding]);
  return [...byCheck]
    .map(([check, list]) => {
      const sorted = [...list].sort((a, b) => severityRank(a.severity) - severityRank(b.severity) || a.title.localeCompare(b.title));
      return { check, summary: summaries[check] ?? null, kind: sorted[0].kind, severity: sorted[0].severity, mix: mixOf(sorted),
               findings: sorted };
    })
    .sort((a, b) => severityRank(a.severity) - severityRank(b.severity) || b.findings.length - a.findings.length ||
      a.check.localeCompare(b.check));
}

/** How usage counts name an entity: its kind's namespace and its name (what the recorder stores, 08-recorder.md). */
export const reference = (entity: Entity): string => `${entity.kind.namespace}/${entity.name}`;

/** Since when the usage counts reach: the window's start, or the oldest turn agentlint kept if that is later —
 * nothing is said about before it. The counts cover only the window, so the window's start is the honest date once
 * kept history is longer. */
export function usageSince(report: Report): string | null {
  const usage = report.usage;
  if (!usage) return null;
  const windowStart = Date.parse(report.generated_at) - usage.days * 86_400_000;
  const kept = usage.history_start ? Date.parse(usage.history_start) : -Infinity;
  return new Date(Math.max(windowStart, kept)).toISOString();
}

/** Whether "since" is the oldest kept turn, not the window's start: then nothing older exists to have been used. */
export function sinceHistoryStart(report: Report): boolean {
  const usage = report.usage;
  return !!usage?.history_start && Date.parse(usage.history_start) >= Date.parse(report.generated_at) - usage.days * 86_400_000;
}

/** The skills and agents the agent chooses between, as Python decided them (`usage.summary`): the entity ids used (most
 * used first) and never used, each name once — never a namespace rule restated here. */
export interface Chosen {
  used: string[];
  unused: string[];
  ids: Set<string>; // used and unused
  byReference: Map<string, string>; // namespace/name -> entity id
}

export function chosenOf(usage: Usage | null): Chosen {
  const used = usage?.summary.used ?? [];
  const unused = usage?.summary.unused ?? [];
  return {
    used: used.map((u) => u.entity), unused: unused.map((u) => u.entity),
    ids: new Set([...used, ...unused].map((u) => u.entity)),
    byReference: new Map([...used, ...unused].map((u) => [u.reference, u.entity])),
  };
}

/** Which findings rest on each source. */
export function findingsBySource(findings: Finding[]): Map<string, Finding[]> {
  const index = new Map<string, Finding[]>();
  for (const finding of findings) {
    for (const id of finding.sources) index.set(id, [...(index.get(id) ?? []), finding]);
  }
  return index;
}

/** Where a source is, as a person reads it: its file, then the adapter's place inside it after a `#` — written to be
 * read, never split back apart. Null when no file holds it: its label says what it is. */
export function placeOf(source: Source): string | null {
  const { file, anchor } = source.provenance;
  return file === null ? null : anchor ? `${file}#${anchor}` : file;
}

/** The report with one finding dismissed, or shown again: what the server just did, shown without reading the project
 * again — its snapshot (and so its texts) the same object. A finding dismissed is hidden, not "dismissed before"; one
 * shown again was hidden only by the dismissal now gone. */
export function withDismissal(report: Report, id: string, dismissed: boolean): Report {
  if (!report.check.findings.some((f) => f.id === id && f.suppressed !== dismissed)) return report;
  return {
    ...report,
    check: { ...report.check, findings: report.check.findings.map((f) => (f.id === id ? { ...f, suppressed: dismissed, suppression_lapsed: false } : f)) },
  };
}
