// The three groups and the one attribution function, pure: no DOM (docs/tech_impl/11-ui.md, "Who put it there").
import { describe, expect, it } from "vitest";
import type { Authority, Report, Source, Untraced } from "./contract";
import { report as written } from "./fixtures/report";
import { checkName } from "./format";
import {
  AUTHORITIES, chosenOf, isTestable, tokensOf, firstTurnBudget, GROUP_OF, GROUPS, groupByCheck, groupByLoad, inFirstTurn, sinceHistoryStart, splitOf, usageSince,
} from "./model";

function source(id: string, authority: Authority, tokens: number, over: Partial<Source["provenance"]> = {}): Source {
  return {
    id,
    provenance: { point: { harness: "test", name: "instructions", label: "instructions file", mechanism: "instruction_file" },
                  file: `/u/${id}`, authority, package: null, anchor: null, ...over },
    activation: { load: "always", condition: null },
    declared_hash: `sha256:${id}`,
    content: { type: "size_only", tokens: { value: tokens, method: "estimated" }, reason: "test" },
    ablation: { mechanism: "none", blocked_reason: null, linked_sources: [] },
  };
}

const listing = { point: { harness: "test", name: "listing", label: "skill listing", mechanism: "harness_generated" }, file: null };
const sources = [
  source("mine", "user", 1000),
  source("here", "project", 200),
  source("policy", "managed", 77),
  source("prompt", "harness", 19_000),
  source("listing:a", "package", 300, { ...listing, package: "a" }),
  source("listing:b", "package", 200, { ...listing, package: "b" }),
  source("listing", "runtime", 50, listing), // the listing's own part: header, the harness's entries, your own lines
];
const untraced: Untraced = { id: "u1", point: listing.point, label: "hook output", load: "per_message",
                             content: { type: "size_only", tokens: { value: 85, method: "estimated" }, reason: "test" }, key: "" };
const latest = [...sources.map((s) => ({ source_id: s.id, tokens: (s.content as { tokens: { value: number } }).tokens.value, label: null })),
                { source_id: null, tokens: 85, label: "hook output" }];
const attributed = latest.reduce((n, a) => n + a.tokens, 0);
const report = {
  ...written,
  snapshot: { ...written.snapshot, sources, untraced: [untraced],
              always_on: { type: "not_reconciled", attribution: latest, attributed, reason: "no session" } },
} as Report;

describe("the three groups", () => {
  it("user and project are yours; package is installed; harness, runtime and managed are the harness's", () => {
    expect(GROUP_OF).toEqual({ user: "yours", project: "yours", package: "installed", harness: "harness", runtime: "harness",
                               managed: "harness" });
    expect(GROUPS).toEqual(["yours", "installed", "harness"]);
  });

  it("splits by the authority behind each part, untraced text as harness", () => {
    expect(splitOf([["user", 1], ["package", 2], ["managed", 3], [null, 4]])).toEqual({ yours: 1, installed: 2, harness: 7 });
  });

  it("groups add up to the authorities and the untraced text, and to the outcome's attributed sum", () => {
    const budget = firstTurnBudget(report);
    const byAuthority = AUTHORITIES.reduce((n, a) => n + budget.byAuthority[a], 0);
    expect(GROUPS.reduce((n, g) => n + budget.byGroup[g], 0)).toBe(byAuthority + budget.untraced);
    expect(budget.total).toBe(attributed);
    expect(budget.byGroup).toEqual({ yours: 1200, installed: 500, harness: 77 + 19_000 + 50 + 85 });
  });

  it("draws a listing by its parts: the packages' installed, its own part harness", () => {
    const rows = groupByLoad(sources, [untraced]).flatMap((g) => g.rows);
    const row = rows.find((r) => r.kind === "listing")!;
    expect(row.split).toEqual({ yours: 0, installed: 500, harness: 50 });
    expect(rows.find((r) => r.kind === "untraced")!.split).toEqual({ yours: 0, installed: 0, harness: 85 });
  });

  it("paints no token twice: the bar's groups are the sum of every first-turn row's split", () => {
    const rows = groupByLoad(sources, [untraced]).flatMap((g) => g.rows);
    const summed = splitOf([]);
    for (const row of rows) for (const g of GROUPS) summed[g] += row.split[g];
    expect(summed).toEqual(firstTurnBudget(report).byGroup);
  });

  it("keeps an observed total's remainder in no group", () => {
    const outcome = written.snapshot.always_on;
    const budget = firstTurnBudget(written as Report);
    expect(outcome.type).toBe("reconciled");
    expect(GROUPS.reduce((n, g) => n + budget.byGroup[g], 0)).toBe(outcome.attributed);
    expect(budget.total).toBe(outcome.observed_total.value);
  });

  it("reads what is in the first turn from the report, not from a copy of the rule", () => {
    const flipped = { ...report, loads: { ...report.loads, on_invoke: { in_first_turn: true } } } as Report;
    expect(inFirstTurn(report, "on_invoke")).toBe(false);
    expect(inFirstTurn(flipped, "on_invoke")).toBe(true);
  });
});

describe("the overview's figures", () => {
  const at = (history_start: string | null): Report => ({ ...written, generated_at: "2026-10-03T09:00:00+00:00",
    usage: { ...written.usage!, days: 30, history_start } });

  it("says since the oldest kept turn, or the window's start once history is longer — never before what was counted", () => {
    expect(usageSince(at("2026-09-15T09:00:00+00:00"))).toBe("2026-09-15T09:00:00.000Z");
    expect(sinceHistoryStart(at("2026-09-15T09:00:00+00:00"))).toBe(true);
    expect(usageSince(at("2026-06-01T09:00:00+00:00"))).toBe("2026-09-03T09:00:00.000Z");
    expect(sinceHistoryStart(at("2026-06-01T09:00:00+00:00"))).toBe(false);
    expect(usageSince(at(null))).toBe("2026-09-03T09:00:00.000Z");
    expect(usageSince({ ...written, usage: null })).toBeNull();
  });

  it("takes the skills and agents from Python's summary, each name once", () => {
    const chosen = chosenOf(written.usage);
    expect(chosen.used).toEqual(written.usage!.summary.used.map((u) => u.entity));
    expect(chosen.ids.size).toBe(written.usage!.summary.used.length + written.usage!.summary.unused.length);
    expect(chosenOf(null).ids.size).toBe(0);
  });

  it("counts each check's severities, worst checks first", () => {
    const groups = groupByCheck(written.check.findings.filter((f) => !f.suppressed), written.check.summaries);
    for (const g of groups) {
      expect(g.mix.error + g.mix.warn + g.mix.info).toBe(g.findings.length);
      expect(g.mix[g.severity]).toBeGreaterThan(0);
    }
    expect(checkName("broken-reference")).toBe("Broken reference");
  });
});

describe("the shared helpers", () => {
  it("read tokens and testability one way", () => {
    expect(tokensOf(sources[0].content)).toBe((sources[0].content as { tokens: { value: number } }).tokens.value);
    expect(tokensOf({ type: "not_observed", sessions_examined: 2, reason: "no session" })).toBeNull();
    expect(isTestable(sources[0])).toBe(sources[0].ablation.mechanism !== "none");
    expect(isTestable({ ...sources[0], ablation: { mechanism: "none", blocked_reason: "managed", linked_sources: [] } })).toBe(false);
  });
});
