import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { App } from "./App";
import type { Briefing, BriefingPart, BriefingReason, Conversation, Finding, Item, Report, SessionListing, Source } from "./contract";
import { counted, day, home, placed, shellPath } from "./format";
import { LIST_KEYS } from "./listNav";
import { VIEW_NAMES, type View } from "./route";
import { NAV_ICON, SeenIcon } from "./ui";

beforeEach(() => window.history.replaceState(null, "", "/"));
afterEach(cleanup);

const point = (name: string, label: string, mechanism: string) => ({ harness: "test", name, label, mechanism });
const tokens = (value: number) => ({ value, method: "estimated" as const });

function source(id: string, over: Partial<Source> = {}): Source {
  return {
    id,
    provenance: { point: point("instructions", "instructions file", "instruction_file"), file: `/Users/u/${id}.md`,
                  authority: "user", package: null, anchor: null },
    activation: { load: "always", condition: null },
    declared_hash: `sha256:${id}`,
    content: { type: "file_text", format: "markdown", text_ref: `ref-${id}`, content_hash: `h-${id}`, tokens: tokens(100),
               required_keys: [] },
    ablation: { mechanism: "span_excision", blocked_reason: null, linked_sources: [] },
    ...over,
  };
}

function listingPart(pkg: string, n: number): Source {
  return source(`listing:${pkg}`, {
    provenance: { point: { ...point("listing", "skill listing", "harness_generated"),
                           description: "The menu of skills, written by the harness from what you have installed." },
                  file: null,
                  authority: "package", package: pkg, anchor: null },
    content: { type: "observed_text", format: "plain", text_ref: `ref-listing-${pkg}`, content_hash: "h", tokens: tokens(n),
               seen_in: [], sessions_examined: 2 },
    ablation: { mechanism: "none", blocked_reason: "composed by the harness", linked_sources: [] },
  });
}

function finding(id: string, over: Partial<Finding> = {}): Finding {
  return {
    id, check: "duplicate-name", kind: "collision", severity: "warn", confidence: "certain", title: `title ${id}`,
    detail: `detail ${id}`, discriminator: id, sources: [], locations: [], figures: [], suggested_action: null,
    suppressed: false, suppression_lapsed: false, ...over,
  };
}

const report: Report = {
  schema_version: 1,
  generated_at: "2026-10-02T09:00:00+00:00",
  agentlint_version: "0.0.1",
  home_dir: "/Users/u",
  labels: {
    notes: "instructions file · ~/notes.md", policy: "managed policy", skill: "skill pkg:helper",
    "listing:a@mk": "skill listing · a@mk", "listing:b@mk": "skill listing · b@mk",
  },
  loads: {
    always: { in_first_turn: true }, per_message: { in_first_turn: true }, on_invoke: { in_first_turn: false },
    conditional: { in_first_turn: false }, not_in_context: { in_first_turn: false },
  },
  snapshot: {
    schema_version: 1, harness: "test", harness_version: "2.0", model: "m", cwd: "/Users/u/project",
    sources: [
      source("notes", { content: { type: "file_text", format: "markdown", text_ref: "ref-notes", content_hash: "h", tokens: tokens(4200),
                                  required_keys: [] } }),
      source("policy", { provenance: { point: point("managed", "managed policy", "config_file"), file: "/Library/policy.json",
                                       authority: "managed", package: null, anchor: null },
                         ablation: { mechanism: "none", blocked_reason: "managed by your organisation", linked_sources: [] } }),
      source("skill", { provenance: { point: point("skill", "skill", "package_entity"), file: "/Users/u/skill.md",
                                      authority: "package", package: "pkg@mk", anchor: null },
                        activation: { load: "on_invoke", condition: null } }),
      listingPart("a@mk", 300),
      listingPart("b@mk", 200),
    ],
    entities: [
      { id: "skill:pkg:helper", name: "pkg:helper", provided_by: "skill", description: "helps", aliases: [],
        kind: { harness: "test", name: "skill", label: "skill", role: "skill", namespace: "skills", separator: ":" } },
      { id: "agent:quiet", name: "quiet", provided_by: "notes", description: "never asked for", aliases: [],
        kind: { harness: "test", name: "agent", label: "agent", role: "agent", namespace: "agents", separator: null } },
    ],
    untraced: [{ id: "u-hello", point: point("prompt_hook", "prompt hook output", "hook_output"), label: 'hook output "hello" — not traced',
                 load: "per_message", content: { type: "observed_text", format: "plain", text_ref: "ref-untraced", content_hash: "h",
                                                 tokens: tokens(85), seen_in: [], sessions_examined: 2 }, key: "" }],
    sessions: [], visibility: [["skill:pkg:helper", "name_only"]],
    always_on: { type: "not_reconciled", attributed: 4885, reason: "no session",
                 attribution: [{ source_id: "notes", tokens: 4200, label: null }, { source_id: "policy", tokens: 100, label: null },
                               { source_id: "listing:a@mk", tokens: 300, label: null }, { source_id: "listing:b@mk", tokens: 200, label: null },
                               { source_id: null, tokens: 85, label: 'hook output "hello" — not traced' }] },
    config_hash: "c", fingerprint: "sha256:f",
  },
  contexts: [
    { session: { session_id: "s1", project: "/Users/u/project", harness_version: "2.0", model: "m",
                 started_at: "2026-10-02T08:00:00+00:00", interactive: true },
      seen: ["listing:a@mk", "listing:b@mk"], files: ["notes"], missing: ["policy"], untraced: ["u-hello"], observed_total: null },
    { session: { session_id: "s0", project: "/Users/u/project", harness_version: "2.0", model: "m",
                 started_at: "2026-09-30T08:00:00+00:00", interactive: true },
      seen: ["listing:a@mk"], files: ["notes"], missing: ["policy", "listing:b@mk"], untraced: [], observed_total: null },
  ],
  usage: { days: 30, turns: 12, history_start: "2026-09-15T09:00:00+00:00",
           entities: { "skills/pkg:helper": { invocations: 3, last_seen: "2026-10-01T09:00:00+00:00" },
                       "skills/clear": { invocations: 2, last_seen: "2026-10-01T09:00:00+00:00" } },
           summary: { used: [{ entity: "skill:pkg:helper", reference: "skills/pkg:helper" }],
                      unused: [{ entity: "agent:quiet", reference: "agents/quiet" }],
                      unmatched: { "skills/clear": 2 } } },
  not_recorded: null,
  check: {
    summaries: {
      "broken-reference": "references that provably point at nothing",
      "duplicate-name": "entities the agent chooses between that share a name",
      "emphasis-density": "sources that shout far more than the rest (suggestive)",
    },
    not_evaluated: { "hook-health": "no recent sessions to read" },
    findings: [
      finding("e1", { severity: "error", check: "broken-reference", kind: "broken", title: "Link to missing.md points at nothing",
                      sources: ["notes"], locations: [{ source_id: "notes", line: 2 }], suggested_action: "Fix the link." }),
      finding("w1", { title: "4 skills named \"sql-query\"" }),
      finding("i1", { severity: "info", confidence: "suggestive", check: "emphasis-density", kind: "emphasis", title: "loud source" }),
      finding("s1", { suppressed: true, title: "a dismissed one" }),
    ],
  },
};

const texts = {
  "ref-notes": "Run the tests before committing.\nSee missing.md for more.", "ref-policy": "{}", "ref-skill": "Help with things.",
  "ref-untraced": "hello", "ref-listing-a@mk": "- a:one", "ref-listing-b@mk": "- b:one",
};

const show = (hash = "") => {
  window.history.replaceState(null, "", `/${hash}`);
  return render(<App report={report} loadTexts={async () => texts} />);
};
const press = (key: string) => act(() => { fireEvent.keyDown(document.activeElement ?? document.body, { key }); });
/** A filter menu's button, by what it filters ("Package: Any package"). */
const menu = (label: string) => screen.getByRole("button", { name: new RegExp(`^${label}: `) });
/** Chooses in a filter menu with the mouse: open it, click the choice. */
const choose = (label: string, choice: string | RegExp | ((name: string, el: Element) => boolean)) => {
  fireEvent.click(menu(label));
  fireEvent.click(within(screen.getByRole("listbox", { name: label })).getByRole("option", { name: choice }));
};

describe("the overview", () => {
  const ledger = () => screen.getByRole("list", { name: "Overview" });
  const head = (name: string) => within(ledger()).getByRole("button", { name });
  const lines = (row: string) => [...document.getElementById(head(row).getAttribute("aria-controls")!)!.querySelectorAll(".line")]
    .map((l) => l.textContent);
  const hover = (text: string) => {
    fireEvent.mouseEnter(screen.getByText(text));
    return screen.getByRole("tooltip").textContent;
  };

  it("is three rows of one shape: no headline, no last session, no cards", () => {
    show();
    const rows = within(ledger()).getAllByRole("listitem");
    expect(rows.map((r) => r.querySelector(".lab")!.textContent)).toEqual(["Every session", "Findings", "Skills and agents"]);
    expect(rows.filter((r) => r.hasAttribute("aria-expanded"))).toEqual([]); // not allowed on a listitem: its head carries it
    expect(head("Findings").getAttribute("aria-expanded")).toBe("true");
    expect(head("Every session").getAttribute("aria-expanded")).toBe("false");
    expect(document.querySelector("main h1, main h2")).toBeNull();
    expect(screen.queryByText(/Start here|Latest session|Last session/)).toBeNull();
    expect(document.querySelector(".tile, .card")).toBeNull();
    expect(screen.queryByText(/fixed|\bnew\b/i)).toBeNull(); // judging an absence proved brittle: not shown
  });

  it("draws every session as the three groups, ≈ the total, a segment opening its group", () => {
    show();
    expect(hover("≈4.9k")).toBe("4,885 tokens every session, estimated: no session");
    const row = within(ledger()).getAllByRole("listitem")[0];
    expect(within(row).getAllByRole("button").map((b) => b.getAttribute("aria-label") ?? b.textContent))
      .toEqual(["Every session", "Yours sources", "Installed sources", "Harness sources", "Yours 4.2k", "Installed 500", "Harness 185"]);
    fireEvent.click(head("Every session"));
    expect(lines("Every session")).toEqual([
      expect.stringContaining("instructions file · ~/notes.md"), expect.stringContaining("skill listing · 2 parts"),
      expect.stringContaining("managed policy"), expect.stringContaining("hook output"),
    ]);
    fireEvent.click(within(row).getByRole("button", { name: "Installed sources" }));
    expect(window.location.hash).toBe("#/config?group=installed");
  });

  it("counts findings by severity, each a link to its filter, and lists the checks with their mix", () => {
    show();
    const row = within(ledger()).getAllByRole("listitem")[1];
    expect(within(row).getAllByRole("link").map((a) => a.getAttribute("aria-label") ?? a.textContent))
      .toEqual(["1 error", "1 warning", "1 info", "1 check not evaluated", "Broken reference 1", "Duplicate name 1", "Emphasis density 1"]);
    expect(lines("Findings")).toEqual(["Broken reference 1", "Duplicate name 1", "Emphasis density 1"]);
    expect(hover("Broken reference")).toBe("references that provably point at nothing (broken-reference)");
    fireEvent.click(within(row).getByRole("link", { name: "1 error" }));
    expect(window.location.hash).toBe("#/findings?severity=error");
    expect(screen.getByText("Link to missing.md points at nothing")).toBeTruthy();
    expect(screen.queryByText("4 skills named \"sql-query\"")).toBeNull();
  });

  it("leaves out a severity with none, and stays closed with only info", () => {
    const infoOnly = { ...report, check: { ...report.check, findings: report.check.findings.filter((f) => f.severity === "info") } };
    render(<App report={infoOnly} loadTexts={async () => texts} />);
    const row = within(ledger()).getAllByRole("listitem")[1];
    expect(within(row).queryByRole("link", { name: /warning|error/ })).toBeNull();
    expect(within(row).getByRole("link", { name: "1 info" })).toBeTruthy();
    expect(head("Findings").getAttribute("aria-expanded")).toBe("false");
  });

  it("shows the top five checks, then N more checks, which open in place", () => {
    const checks = ["a-one", "b-two", "c-three", "d-four", "e-five", "f-six", "g-seven"];
    const many = { ...report, check: { ...report.check, findings: checks.map((check, i) =>
      finding(`m${i}`, { check, severity: i === 6 ? "info" : "warn" })) } };
    render(<App report={many} loadTexts={async () => texts} />);
    expect(lines("Findings")).toEqual(["A one 1", "B two 1", "C three 1", "D four 1", "E five 1", "2 more checks 1 1"]);
    const more = within(ledger()).getByRole("button", { name: "2 more checks 1 warning 1 info" });
    expect(more.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(more);
    expect(more.getAttribute("aria-expanded")).toBe("true");
    expect(lines("Findings").slice(6)).toEqual(["F six 1", "G seven 1"]);
    expect(window.location.hash).toBe("");
  });

  it("walks with the keys: j and the arrows move, → opens, ← closes or returns to the row, Enter goes", () => {
    show();
    press("ArrowRight"); // the cursor starts on the first row
    expect(head("Every session").getAttribute("aria-expanded")).toBe("true");
    press("j");
    expect(document.activeElement?.textContent).toContain("instructions file · ~/notes.md"); // focus follows the cursor
    expect(document.activeElement?.classList.contains("cur")).toBe(true);
    press("ArrowLeft");
    expect(document.activeElement).toBe(head("Every session"));
    press("ArrowLeft");
    expect(head("Every session").getAttribute("aria-expanded")).toBe("false");
    press("ArrowDown");
    expect(document.activeElement).toBe(head("Findings"));
    press(" ");
    expect(head("Findings").getAttribute("aria-expanded")).toBe("false");
    press(" ");
    press("j");
    press("Enter");
    expect(window.location.hash).toBe("#/findings?check=broken-reference");
    window.history.replaceState(null, "", "/");
    cleanup();
    show();
    press("j");
    press("Enter"); // a row goes to its view
    expect(window.location.hash).toBe("#/findings");
    press("3"); // the digits still switch views
    expect(window.location.hash).toBe("#/config");
  });

  it("says since when usage reaches: the oldest kept turn, or the window's start once history is longer", () => {
    show();
    const used = within(ledger()).getAllByRole("listitem")[2];
    expect(used.textContent).toContain("1 of 2 used");
    expect(used.textContent).toContain(`since ${day("2026-09-15T09:00:00+00:00")}`);
    expect(document.body.textContent).not.toMatch(/in the last \d+ days/);
    cleanup();
    const longer = { ...report, usage: { ...report.usage!, history_start: "2026-06-01T09:00:00+00:00" } };
    render(<App report={longer} loadTexts={async () => texts} />);
    expect(within(ledger()).getAllByRole("listitem")[2].textContent).toContain(`since ${day("2026-09-02T09:00:00+00:00")}`);
  });

  it("opens lists the size of the figures: the row's choices, and those never used", () => {
    const entity = (id: string, name: string, kind: string, role: string, namespace: string) =>
      ({ id, name, provided_by: "notes", description: null, aliases: [],
         kind: { harness: "test", name: kind, label: kind, role, namespace, separator: null } });
    const wider = { ...report, snapshot: { ...report.snapshot, entities: [...report.snapshot.entities,
      entity("tool:Bash", "Bash", "tool", "tool", "tools"), // never used, but not one the agent chooses between
      entity("command:quiet", "quiet", "command", "command", "agents")] } }; // shares its name with the agent: one choice
    render(<App report={wider} loadTexts={async () => texts} />);
    fireEvent.click(head("Skills and agents"));
    fireEvent.click(within(ledger()).getByRole("link", { name: "1 never used" }));
    expect(window.location.hash).toBe("#/entities?chosen=1&unused=1");
    expect(screen.getByText("1 of 2", { selector: ".counter" })).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name: /Overview/ }));
    for (let i = 0; i < 5; i++) press("j"); // past Every session, Findings and its three checks
    expect(document.activeElement).toBe(head("Skills and agents"));
    press("Enter");
    expect(window.location.hash).toBe("#/entities?chosen=1");
    expect(screen.getByText("2 of 2", { selector: ".counter" })).toBeTruthy(); // "1 of 2 used": the same two
  });

  it("never claims an all-clear while findings are suppressed or checks never ran", () => {
    const suppressed = { ...report, check: { ...report.check, findings: report.check.findings.map((f) => ({ ...f, suppressed: true })) } };
    render(<App report={suppressed} loadTexts={async () => texts} />);
    const row = () => within(ledger()).getAllByRole("listitem")[1];
    expect(row().textContent).toContain("Nothing open · 4 suppressed");
    expect(row().textContent).toContain("1 check not evaluated");
    expect(row().textContent).not.toMatch(/Nothing found|broken reference/i);
    expect(head("Findings").hasAttribute("aria-expanded")).toBe(false); // nothing open: the row doesn't open
    cleanup();
    const none = { ...report, check: { ...report.check, findings: [] } };
    render(<App report={none} loadTexts={async () => texts} />);
    expect(row().textContent).toContain("No findings from the checks that ran");
    expect(row().textContent).not.toContain("Nothing found");
    cleanup();
    render(<App report={{ ...none, check: { ...none.check, not_evaluated: {} } }} loadTexts={async () => texts} />);
    expect(row().textContent).toContain("Nothing found");
  });

  it("says on Findings why a reading wasn't recorded, and nothing when it was", () => {
    const note = "Not recorded: agentlint ui was started with --sessions 3, which isn't your sessions setting (10), so no reading here is stored or compared with stored ones.";
    window.history.replaceState(null, "", "/#/findings");
    render(<App report={{ ...report, not_recorded: note }} loadTexts={async () => texts} />);
    expect(screen.getByText(note).closest(".strip")).toBeTruthy();
    cleanup();
    window.history.replaceState(null, "", "/#/findings");
    render(<App report={report} loadTexts={async () => texts} />);
    expect(screen.queryByText(/Not recorded/)).toBeNull();
    window.history.replaceState(null, "", "/");
  });

  it("leaves out Skills and agents without usage, and draws every session from the declared text", () => {
    render(<App report={{ ...report, usage: null, contexts: [] }} loadTexts={async () => texts} />);
    expect(within(ledger()).getAllByRole("listitem").map((r) => r.querySelector(".lab")!.textContent)).toEqual(["Every session", "Findings"]);
    expect(screen.getByText("≈4.9k")).toBeTruthy();
  });

  it("is drawn as its own skeleton before the first report: three rows, no figures", () => {
    render(<App report={null} loadTexts={async () => texts} />);
    const skeleton = document.querySelector(".overview.skeleton")!;
    expect(skeleton.querySelectorAll(".lrow").length).toBe(3);
    expect(skeleton.textContent).toBe("");
    expect(skeleton.getAttribute("aria-hidden")).toBe("true");
  });
});

describe("findings", () => {
  const head = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name}`) });
  const cells = (row: Element) => [...row.children].map((c) => (c.querySelector("svg.severity") ? "severity" : c.className));

  it("groups by check under readable names, keeps lifecycle visible, and opens one beside the list", async () => {
    show("#/findings");
    expect(head("Broken reference").textContent).toBe("Broken referencereferences that provably point at nothing1");
    expect(head("Broken reference").getAttribute("aria-expanded")).toBe("false");
    fireEvent.mouseEnter(screen.getByText("Broken reference"));
    expect(screen.getByRole("tooltip").textContent).toBe("broken-reference"); // the id, in the head's tooltip
    expect(within(head("Duplicate name")).getByRole("img", { name: "1 warning" })).toBeTruthy(); // its severity mix
    expect(screen.queryByText("Link to missing.md points at nothing")).toBeNull(); // every group starts folded: the outline fits
    expect(screen.queryByText("loud source")).toBeNull();
    for (const name of ["Broken reference", "Duplicate name", "Emphasis density"]) fireEvent.click(head(name));
    expect(screen.getByText("loud source")).toBeTruthy();
    expect(screen.getByRole("img", { name: /Suggestive/ })).toBeTruthy();
    expect(screen.queryByRole("img", { name: /New since/ })).toBeNull(); // judging what is new proved brittle: not shown
    expect(screen.queryByText(/fixed|\bnew\b/i)).toBeNull();
    expect(screen.queryByText("a dismissed one")).toBeNull();
    expect(screen.getByText(/no recent sessions to read/)).toBeTruthy();
    expect(document.querySelector(".not-evaluated code")?.textContent).toBe("hook-health");

    fireEvent.click(screen.getByRole("link", { name: /Link to missing.md/ }));
    const detail = screen.getByRole("complementary", { name: "Finding" });
    expect(within(detail).getByText("Fix the link.")).toBeTruthy();
    expect(within(detail).getByRole("link", { name: "instructions file · ~/notes.md" })).toBeTruthy();
    fireEvent.click(within(detail).getByRole("link", { name: "~/notes.md:2" })); // into the source, at the line
    expect(window.location.hash).toBe("#/config/source/notes?line=2");
    const opened = screen.getByRole("complementary", { name: "Source" });
    await waitFor(() => expect(opened.querySelector(".line.pointed")?.textContent).toBe("See missing.md for more."));
    expect(within(opened).getByRole("link", { name: "Link to missing.md points at nothing" })).toBeTruthy(); // and back
  });

  it("counts by severity in one line, each a filter the second click clears", () => {
    show("#/findings");
    expect(document.querySelector(".toggle")).toBeNull();
    const bar = document.querySelector(".findings .toolbar")!;
    expect(within(bar as HTMLElement).getByRole("heading", { name: "Findings" })).toBeTruthy();
    const counts = within(bar as HTMLElement).getAllByRole("link");
    expect(counts.map((a) => a.getAttribute("aria-label"))).toEqual(["1 error", "1 warning", "1 info"]); // unsuppressed only
    fireEvent.click(counts[0]);
    expect(window.location.hash).toBe("#/findings?severity=error");
    const error = screen.getByRole("link", { name: "1 error" });
    expect(error.getAttribute("aria-current")).toBe("true");
    expect(screen.getByRole("link", { name: "1 warning" }).getAttribute("aria-current")).toBeNull();
    expect(screen.getAllByRole("link", { name: /^1 / })).toHaveLength(3); // the counts stay whatever the filter
    fireEvent.click(error);
    expect(window.location.hash).toBe("#/findings");
    expect(screen.getByRole("link", { name: "1 error" }).getAttribute("aria-current")).toBeNull();
  });

  it("names a check filter by its readable name, removable", () => {
    show("#/findings?check=duplicate-name");
    fireEvent.click(screen.getByRole("button", { name: "Show every check, not only Duplicate name" }));
    expect(window.location.hash).toBe("#/findings");
  });

  it("lays each row out as severity, title, flags, package, file:line and +N; the package gives way to the detail", () => {
    const pkg = { ...report, package_labels: { "pkg@mk": "pkg" },
                  check: { ...report.check, findings: [finding("p1", { title: "two helpers", sources: ["skill"], confidence: "suggestive",
                    suppression_lapsed: true, locations: [{ source_id: "skill", line: 4 }, { source_id: "notes", line: null }] })] } };
    window.history.replaceState(null, "", "/#/findings");
    render(<App report={pkg} loadTexts={async () => texts} />);
    fireEvent.click(head("Duplicate name"));
    const row = document.querySelector(".findings .row")!;
    expect(cells(row)).toEqual(["severity", "title", "flags", "pk", "where", "plus"]);
    expect([...row.children].slice(1).map((c) => c.textContent)).toEqual(["two helpers", "", "pkg", "skill.md:4", "+1"]);
    expect(within(row as HTMLElement).getByRole("img", { name: /Suggestive/ })).toBeTruthy();
    expect(within(row as HTMLElement).getByRole("img", { name: /dismissed this before/ })).toBeTruthy();
    fireEvent.mouseEnter(screen.getByText("pkg"));
    expect(screen.getByRole("tooltip").textContent).toBe("pkg@mk");
    fireEvent.mouseLeave(screen.getByText("pkg"));
    fireEvent.mouseEnter(screen.getByText("+1"));
    expect(screen.getByRole("tooltip").textContent).toBe("1 more location");
    fireEvent.click(row);
    expect(cells(document.querySelector(".findings .row")!)).toEqual(["severity", "title", "flags", "where", "plus"]);
  });

  it("shows a hook-health row's package and file from the hook it rests on, and no excerpt of what the hook printed", async () => {
    const id = "test:hook:brain@acme-market:plugin.json#/hooks/UserPromptSubmit/0/hooks/0";
    const file = "/Users/u/.claude/plugins/cache/acme-market/brain/1.0.0/.claude-plugin/plugin.json";
    const hook = source(id, {
      provenance: { point: point("prompt_hook", "prompt hook output", "hook_output"), file, authority: "package",
                    package: "brain@acme-market", anchor: "/hooks/UserPromptSubmit/0/hooks/0" },
      activation: { load: "per_message", condition: null },
      content: { type: "observed_text", format: "plain", text_ref: "ref-hook", content_hash: "h", tokens: tokens(40),
                 seen_in: [], sessions_examined: 2 },
    });
    const killed = finding("h1", {
      check: "hook-health", kind: "health", title: "A UserPromptSubmit hook was killed at its timeout 5 times in 2 sessions (brain@acme-market)",
      sources: [id], locations: [{ source_id: id, line: null }],
    });
    const hooked = { ...report, package_labels: { "brain@acme-market": "brain" },
                     snapshot: { ...report.snapshot, sources: [...report.snapshot.sources, hook] },
                     check: { ...report.check, not_evaluated: {}, findings: [killed] } };
    window.history.replaceState(null, "", "/#/findings");
    render(<App report={hooked} loadTexts={async () => ({ ...texts, "ref-hook": "Recalled 3 memories for this prompt." })} />);
    fireEvent.click(head("Hook health"));
    const row = document.querySelector(".findings .row")!;
    expect([...row.children].slice(1).map((c) => c.textContent)).toEqual([killed.title, "", "brain", "plugin.json", ""]);
    fireEvent.click(row);
    const detail = screen.getByRole("complementary", { name: "Finding" });
    await act(async () => {}); // the texts have unpacked
    expect(within(detail).queryByRole("group")).toBeNull(); // no excerpt: the hook's text is what it printed, not its JSON
    expect(detail.textContent).not.toContain("Recalled 3 memories");
    const where = within(detail).getByRole("link", { name: /^…\/\.claude-plugin\/plugin\.json#\/hooks\/UserPromptSubmit\/0\/hooks\/0$/ });
    fireEvent.click(where); // to the hook in Configuration, at no line
    expect(window.location.hash).toBe(`#/config/source/${encodeURIComponent(id)}`);
  });

  it("shows confidence only when suggestive", () => {
    const measured = { ...report, check: { ...report.check, findings: [finding("m1", { confidence: "measured" })] } };
    window.history.replaceState(null, "", "/#/findings");
    render(<App report={measured} loadTexts={async () => texts} />);
    fireEvent.click(head("Duplicate name"));
    expect(screen.queryByRole("img", { name: /Measured/ })).toBeNull();
  });

  it("says all clear only when there is nothing, and with everything suppressed shows just that group", () => {
    const none = { ...report, check: { ...report.check, not_evaluated: {}, findings: [] } };
    window.history.replaceState(null, "", "/#/findings");
    render(<App report={none} loadTexts={async () => texts} />);
    expect(screen.getByRole("img", { name: "Nothing found" })).toBeTruthy();
    expect(screen.getByText(/No findings\. Nothing in this configuration/)).toBeTruthy();
    expect(document.querySelector(".findings .counts")?.children).toHaveLength(0);
    cleanup();
    const hidden = { ...report, check: { ...report.check, findings: report.check.findings.map((f) => ({ ...f, suppressed: true })) } };
    render(<App report={hidden} loadTexts={async () => texts} />);
    expect(screen.queryByText(/No findings/)).toBeNull();
    expect(screen.queryByRole("img", { name: "Nothing found" })).toBeNull();
    expect(screen.getAllByRole("button", { expanded: false }).map((b) => b.textContent)).toEqual([expect.stringMatching(/^\s*Suppressed/)]);
  });

  it("opens a finding to its title, → the action, five lines around its line, why, and a property list", async () => {
    show("#/findings/e1");
    const detail = screen.getByRole("complementary", { name: "Finding" });
    expect(detail.querySelector(".detail-meta")?.textContent).toBe("Broken reference");
    expect(within(detail).getByRole("heading", { name: "Link to missing.md points at nothing" })).toBeTruthy();
    expect(detail.querySelector(".act")?.textContent).toBe("→ Fix the link.");
    const excerpt = await within(detail).findByRole("group", { name: "notes.md lines 1 to 2" });
    await waitFor(() => expect(excerpt.querySelector(".pointed")?.textContent).toBe("2See missing.md for more."));
    expect(excerpt.querySelector(".pointed")?.classList.contains("sev-error")).toBe(true);
    expect(detail.querySelector(".why")?.textContent).toBe("detail e1");
    expect([...detail.querySelectorAll("dl.props dt")].map((d) => d.textContent)).toEqual(["Check", "Where", "Rests on"]);
    expect(detail.querySelector("dl.props dd")?.textContent).toBe("Broken reference broken-reference");
    expect(detail.querySelector(".chip, .figure, .figures, .chips")).toBeNull(); // no chip rows, no figure boxes
    expect(within(detail).queryByText(/Since/)).toBeNull(); // only history could say it: not shown
    fireEvent.click(excerpt);
    expect(window.location.hash).toBe("#/config/source/notes?line=2");
  });

  it("holds the excerpt's place while the texts unpack, and shows none for a text the report lacks", async () => {
    let unpack: (t: typeof texts) => void = () => {};
    window.history.replaceState(null, "", "/#/findings/e1");
    render(<App report={report} loadTexts={() => new Promise((done) => { unpack = done; })} />);
    const holding = screen.getByRole("group", { name: "notes.md lines 1 to 5" });
    expect(holding.classList.contains("holding")).toBe(true);
    await act(async () => unpack({ ...texts, "ref-notes": undefined as unknown as string }));
    expect(screen.queryByRole("group", { name: /notes.md lines/ })).toBeNull();
  });

  it("copies the suppress command on s and the id on c, says so, and closes on Esc", async () => {
    let copied = "";
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t: string) => { copied = t; } } });
    show("#/findings/e1");
    const detail = screen.getByRole("complementary", { name: "Finding" });
    press("s");
    await waitFor(() => expect(copied).toBe(`agentlint suppress e1 --cwd ~/project --reason "…"`));
    expect(within(detail).getByRole("status").textContent).toBe("Copied the suppress command");
    press("c");
    await waitFor(() => expect(copied).toBe("e1"));
    expect(within(detail).getByRole("status").textContent).toBe("Copied the id");
    fireEvent.mouseEnter(within(detail).getByRole("button", { name: "s copy suppress" }));
    expect(screen.getByRole("tooltip").textContent).toBe(`agentlint suppress e1 --cwd ~/project --reason "…"`);
    press("Escape");
    expect(screen.queryByRole("complementary")).toBeNull();
    copied = "";
    press("c"); // with nothing open, the keys stay free
    await new Promise((r) => setTimeout(r, 0));
    expect(copied).toBe("");
  });

  it("offers no suppress for a suppressed finding, and says where it stands", () => {
    show("#/findings/s1");
    const detail = screen.getByRole("complementary", { name: "Finding" });
    expect(detail.querySelector(".detail-meta")?.textContent).toBe("Duplicate name· suppressed");
    expect(within(detail).queryByRole("button", { name: /copy suppress/ })).toBeNull();
  });

  it("lists confidence where it isn't certain, figures the text doesn't already say, and more in place", () => {
    const deep = source("deep", { provenance: { ...report.snapshot.sources[0].provenance, file: "/Users/u/deep/er/dir/skill.md" } });
    const busy = { ...report, snapshot: { ...report.snapshot, sources: [...report.snapshot.sources, deep] },
                   check: { ...report.check, findings: [finding("f1", {
      confidence: "suggestive", title: "3 skills named x", detail: "seen in 2 sessions",
      figures: [{ label: "members", value: 3, unit: "names", unit_one: "name" }, { label: "sessions", value: 2, unit: "sessions", unit_one: "session" },
                { label: "louder than", value: 99, unit: "percent", unit_one: "percent" }, { label: "examined", value: 10, unit: "examined", unit_one: "examined" },
                { label: "timeouts", value: 1, unit: "runs", unit_one: "run" }],
      sources: ["notes", "policy", "skill", "listing:a@mk"],
      locations: [{ source_id: "deep", line: null }, { source_id: "notes", line: null }] })] } };
    window.history.replaceState(null, "", "/#/findings/f1");
    render(<App report={busy} loadTexts={async () => texts} />);
    const detail = screen.getByRole("complementary", { name: "Finding" });
    const props = Object.fromEntries([...detail.querySelectorAll("dl.props dt")].map((d) => [d.textContent, d.nextElementSibling!]));
    // "seen in 2 sessions" says sessions 2; "3 skills" never names members, so that figure stays
    expect(Object.keys(props)).toEqual(["Check", "Where", "Rests on", "Confidence", "members", "louder than", "examined", "timeouts"]);
    expect(props["louder than"].textContent).toBe("99 percent");
    expect(props.timeouts.textContent).toBe("1 run"); // the singular Python gives, never "1 runs"
    expect(props.members.textContent).toBe("3 names");
    expect(props.examined.textContent).toBe("10"); // never "10 examined" after "examined"
    expect(props.Confidence.textContent).toMatch(/^Suggestive/);
    expect(within(props.Where as HTMLElement).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["…/dir/skill.md"]);
    fireEvent.click(within(detail).getByRole("button", { name: "1 more location" }));
    expect(within(props.Where as HTMLElement).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["…/dir/skill.md", "~/notes.md"]);
    expect(within(props["Rests on"] as HTMLElement).getAllByRole("link")).toHaveLength(3);
    fireEvent.click(within(detail).getByRole("button", { name: "1 more" }));
    expect(within(props["Rests on"] as HTMLElement).getAllByRole("link")).toHaveLength(4);
  });

  it("hides a figure only where one line of the text says its label and its number together, the label as written", () => {
    const packages = { ...report, check: { ...report.check, findings: [finding("f1", {
      title: "3 packages add ≈4,356 tokens to every session",
      detail: "≈ 4,240  acme-tools@acme-market  (agent listing entries 106; see power-engineer)\n… 2 more packages, ≈116 tokens",
      figures: [{ label: "acme-tools@acme-market", value: 4240, unit: "tokens", unit_one: "token" },
                { label: "thrift@acme-market", value: 106, unit: "tokens", unit_one: "token" },
                { label: "sl@acme-market", value: 10, unit: "tokens", unit_one: "token" }],
    })] } };
    window.history.replaceState(null, "", "/#/findings/f1");
    render(<App report={packages} loadTexts={async () => texts} />);
    const detail = screen.getByRole("complementary", { name: "Finding" });
    const props = Object.fromEntries([...detail.querySelectorAll("dl.props dt")].map((d) => [d.textContent, d.nextElementSibling!]));
    expect(Object.keys(props)).toEqual(["Check", "thrift@acme-market", "sl@acme-market"]); // 106 and 10 sit in other text
    expect(props["sl@acme-market"].textContent).toBe("10 tokens");
  });

  it("moves with the keys", () => {
    show("#/findings");
    fireEvent.click(head("Broken reference"));
    fireEvent.click(head("Duplicate name"));
    press("j");
    press("Enter");
    expect(within(screen.getByRole("complementary", { name: "Finding" })).getByRole("heading", { name: /sql-query/ })).toBeTruthy();
    press("k"); // the open finding follows the cursor
    expect(within(screen.getByRole("complementary", { name: "Finding" })).getByRole("heading", { name: /missing.md/ })).toBeTruthy();
    press("Escape");
    expect(screen.queryByRole("complementary")).toBeNull();
    press("/");
    expect(document.activeElement).toBe(screen.getByLabelText("Filter findings"));
    press("3");
    expect(window.location.hash).toBe("#/findings"); // typing in the filter doesn't switch views
  });
});

describe("configuration", () => {
  it("lists sources by when they load, most expensive first, and finds them by text or path", async () => {
    show("#/config");
    const rows = () => [...document.querySelectorAll(".config .row")].map((r) => r.textContent ?? "");
    expect(rows()).toEqual([]); // every group starts folded
    fireEvent.click(screen.getByRole("button", { name: /Every session/ }));
    fireEvent.click(screen.getByRole("button", { name: /Every message/ }));
    expect(rows()[0]).toContain("instructions file · ~/notes.md");
    expect(screen.getByRole("link", { name: /skill listing · 2 parts/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /hook output "hello"/ })).toBeTruthy(); // untraced text is listed too
    expect(screen.getByRole("img", { name: /Untraced/ })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /skill pkg:helper/ })).toBeNull(); // "when invoked" starts folded
    expect(screen.getByRole("img", { name: "Can't be tested by removal: managed by your organisation" })).toBeTruthy();

    const search = await screen.findByLabelText("Search names, paths and text");
    fireEvent.change(search, { target: { value: "before committing" } });
    await waitFor(() => expect(rows()).toHaveLength(1)); // found by its text
    fireEvent.change(search, { target: { value: "~/skill.md" } });
    await waitFor(() => expect(rows()).toEqual([expect.stringContaining("skill pkg:helper")])); // by the path as shown
    fireEvent.change(search, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /^Harness \d/ })); // the budget's labels filter by group
    await waitFor(() => expect(rows()).toEqual([expect.stringContaining("managed policy"), expect.stringContaining('hook output "hello"')]));
    expect(window.location.hash).toContain("group=harness");
    choose("Who put it there", /^managed/); // the six stay
    await waitFor(() => expect(rows()).toEqual([expect.stringContaining("managed policy")]));
    expect(window.location.hash).not.toContain("group=");
  });

  it("draws a listing split by its parts, the same split the budget bar counts", async () => {
    show("#/config");
    fireEvent.click(screen.getByRole("button", { name: /Every session/ }));
    const listing = screen.getByRole("link", { name: /skill listing · 2 parts/ });
    expect([...listing.querySelectorAll(".cost-bar .seg")].map((s) => s.className)).toEqual(["seg g-installed"]); // package parts: installed
    fireEvent.click(screen.getByRole("link", { name: /skill listing · 2 parts/ }));
    const detail = screen.getByRole("complementary", { name: "Listing" });
    expect(within(detail).getAllByRole("img", { name: /^Installed · package/ })).toHaveLength(2); // each part's swatch
  });

  it("opens a listing to its parts, and a source to what it provides", async () => {
    show("#/config");
    fireEvent.click(screen.getByRole("button", { name: /Every session/ }));
    expect(screen.getByRole("img", { name: /written by the harness/ })).toBeTruthy(); // what this is, for text you didn't write
    fireEvent.click(screen.getByRole("link", { name: /skill listing · 2 parts/ }));
    const listing = screen.getByRole("complementary", { name: "Listing" });
    expect(within(listing).getByText("500 in all")).toBeTruthy();
    expect(within(listing).getByText(/The menu of skills, written by the harness/)).toBeTruthy();
    expect(within(listing).getAllByRole("link").map((a) => a.textContent)).toEqual(["skill listing · a@mk", "skill listing · b@mk"]);
    fireEvent.click(within(listing).getByRole("link", { name: "skill listing · b@mk" }));
    expect(screen.getByRole("complementary", { name: "Source" })).toBeTruthy();

    cleanup();
    show("#/config/source/skill");
    const detail = screen.getByRole("complementary", { name: "Source" });
    expect(within(detail).getByText("pkg:helper")).toBeTruthy();
    await waitFor(() => expect(within(detail).getByText("Help with things.")).toBeTruthy());
  });
});

describe("a bad link", () => {
  it("ignores a filter value no filter has, and opens nothing for an id that doesn't decode", () => {
    show("#/findings?severity=loud");
    expect(document.querySelector(".counts [aria-current]")).toBeNull();
    expect(screen.getAllByRole("button", { name: /Broken reference|Duplicate name|Emphasis density/ }).length).toBeGreaterThan(1);
    cleanup();
    show("#/findings/%E0%A4%A");
    expect(screen.getByRole("heading", { name: "Findings" })).toBeTruthy();
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  it("shows why a view failed, in place, and tries again on moving", () => {
    const broken = { ...report, check: { ...report.check, findings: [{ ...report.check.findings[0], locations: null as never }] } };
    window.history.replaceState(null, "", "/#/findings");
    const quiet = console.error;
    console.error = () => {}; // React reports the caught error too
    try {
      render(<App report={broken} loadTexts={async () => texts} />);
      expect(screen.getByRole("alert").textContent).toMatch(/^This view failed: /);
      expect(screen.getByRole("navigation", { name: "Views" })).toBeTruthy(); // the frame stays
      press("1");
      expect(screen.queryByRole("alert")).toBeNull();
    } finally {
      console.error = quiet;
    }
  });
});

describe("configuration links", () => {
  it("name what they open by its kind: a source, a listing, untraced text by its stable id", () => {
    show("#/config/untraced/u-hello");
    expect(within(screen.getByRole("complementary", { name: "Untraced text" })).getByRole("heading", { level: 2 }).textContent).toMatch(/hook output "hello"/);
    cleanup();
    show("#/config/listing/always%7Clisting");
    expect(screen.getByRole("complementary", { name: "Listing" })).toBeTruthy();
    cleanup();
    show("#/config/source/listing%3Aa%40mk"); // a source whose id starts "listing:" is a source, never a listing
    expect(screen.getByRole("complementary", { name: "Source" })).toBeTruthy();
    cleanup();
    show("#/config/notes"); // a bare id, as links were written before: a source's
    expect(within(screen.getByRole("complementary", { name: "Source" })).getByRole("heading", { level: 2 }).textContent).toContain("notes.md");
    cleanup();
    show("#/context");
    fireEvent.click(screen.getByRole("button", { name: /Explained by nothing configured/ }));
    expect(screen.getByRole("link", { name: /hook output "hello"/ }).getAttribute("href")).toBe("#/config/untraced/u-hello");
  });
});

describe("detail panes", () => {
  const props = (pane: HTMLElement) => Object.fromEntries([...pane.querySelectorAll("dl.props dt")].map((dt) =>
    [dt.textContent, (dt.nextElementSibling?.textContent ?? "").trim()]));

  it("are property lists: no chips, no tinted boxes, no helper text", () => {
    for (const hash of ["#/config/source/notes", "#/config/source/skill", "#/config/source/listing:a@mk", "#/entities/skill:pkg:helper", "#/findings/e1"]) {
      show(hash);
      expect(document.querySelector(".chip, .chips, .what-text")).toBeNull();
      cleanup();
    }
    show("#/config/source/skill");
    const source = props(screen.getByRole("complementary", { name: "Source" }));
    expect(source.Who).toBe("Installed · pkg@mk");
    expect(source.Loads).toBe("when invoked");
    expect(source.Tokens).toBe("≈100");
    expect(source.Testable).toBe("by span excision");
    cleanup();
    show("#/config");
    fireEvent.click(screen.getByRole("button", { name: /Every session/ }));
    fireEvent.click(screen.getByRole("link", { name: /skill listing · 2 parts/ }));
    expect(screen.queryByText(/each package's/i)).toBeNull();
    expect(props(screen.getByRole("complementary", { name: "Listing" })).Tokens).toBe("500 in all");
  });

  it("packs a row's findings into a mix that reads as words, with one tooltip", () => {
    show("#/config");
    fireEvent.click(screen.getByRole("button", { name: /Every session/ }));
    const mix = document.querySelector(".config .finding-count")!;
    expect(within(mix as HTMLElement).getByRole("img").getAttribute("aria-label")).toBe("1 error");
    fireEvent.mouseEnter(mix);
    expect(screen.getAllByRole("tooltip").map((t) => t.textContent)).toEqual(["1 finding rests on this source"]);
  });

  it("switches formatted and raw text with one segmented control", async () => {
    show("#/config/source/notes");
    const modes = await screen.findByRole("group", { name: "Show the text" });
    expect(modes.className).toBe("segmented");
    expect(within(modes).getAllByRole("button").map((b) => [b.textContent, b.getAttribute("aria-pressed")]))
      .toEqual([["Formatted", "true"], ["Raw, numbered", "false"]]);
  });
});

describe("filter menus", () => {
  it("replaces every native select: a button opening a filterable list", () => {
    for (const view of ["overview", "findings", "config", "entities", "context", "sessions"]) {
      show(`#/${view}`);
      expect(document.querySelector("select")).toBeNull();
      cleanup();
    }
  });

  it("is chosen by keyboard: typing narrows, the list moves with j and k, Enter chooses, into the URL", () => {
    show("#/config");
    const button = menu("Package");
    expect(button.textContent).toBe("Any package");
    fireEvent.click(button);
    const list = screen.getByRole("listbox", { name: "Package" });
    expect(within(list).getAllByRole("option").map((o) => o.textContent)).toEqual(["Any package", "a@mk", "b@mk", "pkg@mk"]);
    const filter = screen.getByLabelText("Filter: Package");
    expect(document.activeElement).toBe(filter);
    fireEvent.change(filter, { target: { value: "mk" } });
    expect(within(list).getAllByRole("option").map((o) => o.textContent)).toEqual(["a@mk", "b@mk", "pkg@mk"]); // typing narrows
    fireEvent.keyDown(filter, { key: "ArrowDown" });
    expect(document.activeElement).toBe(list);
    press("j");
    press("j");
    press("k");
    press("Enter");
    expect(new URLSearchParams(window.location.hash.split("?")[1]).get("package")).toBe("b@mk");
    expect(screen.queryByRole("listbox", { name: "Package" })).toBeNull();
    expect(document.activeElement).toBe(menu("Package"));
    expect(menu("Package").textContent).toBe("b@mk");
  });

  it("closes on Esc without choosing, and leaves the view's keys alone while open", () => {
    show("#/entities");
    fireEvent.click(menu("Kind"));
    fireEvent.change(screen.getByLabelText("Filter: Kind"), { target: { value: "ag" } });
    press("Escape");
    expect(screen.queryByRole("listbox", { name: "Kind" })).toBeNull();
    expect(document.activeElement).toBe(menu("Kind"));
    expect(window.location.hash).toBe("#/entities");
    choose("Kind", "agent");
    expect(window.location.hash).toBe("#/entities?kind=agent");
    choose("Kind", "Every kind");
    expect(window.location.hash).toBe("#/entities");
  });
});

describe("skills and agents", () => {
  it("puts what your turns use first, and what they never touch a keypress away", () => {
    show("#/entities");
    expect(screen.getByText(`of 2 skills and agents used since ${day("2026-09-15T09:00:00+00:00")}`)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/in the last \d+ days/);
    const rows = () => [...document.querySelectorAll(".entities-view .row")].map((r) => r.textContent ?? "");
    fireEvent.click(screen.getByRole("button", { name: new RegExp(`Used since ${day("2026-09-15T09:00:00+00:00")}`) }));
    expect(rows()).toEqual([expect.stringContaining("pkg:helper")]); // "never used" stays folded
    expect(screen.getByRole("img", { name: "The agent sees only its name" })).toBeTruthy();
    expect(screen.getByText(/Used, but not in this configuration/)).toBeTruthy(); // a built-in command, kept apart
    fireEvent.click(screen.getByRole("button", { name: "Only what your turns never used" }));
    expect(rows()).toEqual([expect.stringContaining("quiet")]);
    fireEvent.click(screen.getByRole("link", { name: /quiet/ }));
    const detail = screen.getByRole("complementary", { name: "Entity" });
    // the oldest kept turn is later than the window's start: nothing older exists to have been used
    expect(within(detail).getByText(`Never used since ${day("2026-09-15T09:00:00+00:00")} — as far back as your kept history goes.`)).toBeTruthy();
    fireEvent.click(within(detail).getByRole("link", { name: "instructions file · ~/notes.md" }));
    expect(window.location.hash).toBe("#/config/source/notes");
  });
});

describe("session context", () => {
  it("shows what a session loaded beside what is configured to load, the latest first, any recent one picked", () => {
    show("#/context");
    fireEvent.click(menu("Session"));
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([expect.stringMatching(/the latest/), expect.not.stringMatching(/latest/)]);
    press("Escape");
    expect(screen.getByRole("button", { name: /Shown in this session.*2/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Read from files.*1/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Explained by nothing configured/ }));
    expect(screen.getByRole("link", { name: /hook output "hello"/ })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /managed policy/ })).toBeNull(); // "configured, not shown" starts folded
    fireEvent.click(screen.getByRole("button", { name: /Configured, but not shown/ }));
    expect(screen.getByRole("link", { name: /managed policy/ })).toBeTruthy();
    choose("Session", (_, el) => !el.textContent?.includes("latest")); // an earlier session: it showed less
    expect(screen.getByRole("button", { name: /Shown in this session.*1/ })).toBeTruthy();
  });
});

describe("sessions", () => {
  const info: SessionListing = { header: { session_id: "s1", project: "/Users/u/project", harness_version: "2.0", model: "m",
    started_at: "2026-10-02T08:00:00+00:00", interactive: true }, title: "Fix the flaky test",
    ended_at: "2026-10-02T08:42:00+00:00", prompts: 2, size: 1000 };
  const at = "2026-10-02T08:01:00+00:00";
  const tool = (name: string, summary: string, over = {}): Item => ({ type: "tool_call", at, name, summary, input: "{}",
    result: null, error: false, entities: [], subagent_ref: null, ...over });
  const conversation: Conversation = { title: info.title, texts: {}, items: [
    { type: "prompt", at, text: "Why does the test flake?" },
    { type: "thinking", at, text: "Probably a race." },
    tool("Bash", "pytest -x tests/test_flaky.py", { input: "{\"command\": \"pytest\"}", result: { text: "1 failed", omitted: 50 },
                                                     entities: ["tools/Bash", "skills/pkg:helper"] }),
    tool("Agent", "code-search · find the race", { result: { text: "found it", omitted: 0 }, subagent_ref: "agent:a1",
                                                   entities: ["tools/Agent"] }),
    { type: "injected", at, by: "expanded by the harness", text: { text: "Help with things.", omitted: 0 } },
    { type: "reply", at, text: "It's a race.\n```\nsleep(1)\n```\nRemove the sleep." },
    { type: "note", at, kind: "compacted", text: { text: "summarised", omitted: 0 }, entities: [] },
    { type: "note", at, kind: "hook_error", text: { text: "Stop failed: no such file", omitted: 0 }, entities: [] },
    { type: "prompt", at, text: "Thanks" },
  ] };
  const live = {
    list: async () => [info], read: async () => conversation,
    subagents: async (_id: string, ref: string) => (ref === "agent:a1"
      ? [{ id: "agent-a1", title: "Find the race", entries: 2 }, { id: "agent-a2", title: "Find the race", entries: 3 }] : []),
    subagent: async (): Promise<Conversation> => ({ title: "Find the race", texts: {}, items: [{ type: "prompt", at, text: "Find the race in the test" },
                                                                   { type: "reply", at, text: "It is the shared fixture." }] }),
  };

  it("lists your sessions and reads one back, folded where it should be", async () => {
    window.history.replaceState(null, "", "/#/sessions");
    render(<App report={report} loadTexts={async () => texts} sessions={live} />);
    fireEvent.click(await screen.findByRole("link", { name: /Fix the flaky test/ }));
    expect(document.querySelector(".sessions .strip")?.textContent).toBe("1 session"); // never "1 sessions"
    const reader = await screen.findByRole("article", { name: "Session" });
    await waitFor(() => expect(within(reader).getByText("Why does the test flake?")).toBeTruthy());
    expect(within(reader).getByText("sleep(1)").closest("pre")).toBeTruthy(); // a reply's code set apart, as Markdown
    expect(within(reader).queryByText("Probably a race.")).toBeNull(); // thinking only when asked
    expect(within(reader).queryByText("Help with things.")).toBeNull(); // injected only when asked
    expect(within(reader).queryByText("1 failed")).toBeNull(); // a tool call is one line until opened
    fireEvent.click(within(reader).getByRole("button", { name: /Bash.*pytest -x/ }));
    expect(within(reader).getByText("1 failed")).toBeTruthy();
    expect(within(reader).getByText(/50 more characters/)).toBeTruthy(); // the cut travels with its text
    expect(within(reader).getByText("Stop failed: no such file").closest(".note.failed")).toBeTruthy(); // a hook error, in red
    expect(within(reader).getByRole("link", { name: "pkg:helper" })).toBeTruthy(); // the skill it used, linked
    fireEvent.click(within(reader).getByRole("button", { name: "Show the agent's thinking" }));
    fireEvent.click(within(reader).getByRole("button", { name: "thinking", expanded: false }));
    expect(within(reader).getByText("Probably a race.")).toBeTruthy();
    expect(within(reader).getByText(/compacted here/)).toBeTruthy();
    fireEvent.click(within(reader).getByRole("button", { name: /Agent.*code-search/ }));
    // the subagents it started: the same title told apart by id
    fireEvent.click(await within(reader).findByRole("button", { name: /Find the race.*agent-a1/ }));
    expect(within(reader).getByRole("button", { name: /Find the race.*agent-a2/ })).toBeTruthy();
    expect(await within(reader).findByText("It is the shared fixture.")).toBeTruthy();
    expect(within(reader).getByText(/Asked of it/)).toBeTruthy(); // its prompt came from the agent, not you
    fireEvent.change(within(reader).getByLabelText("Search this session"), { target: { value: "thanks" } });
    await waitFor(() => expect(within(reader).queryByText("Why does the test flake?")).toBeNull());
  });

  it("says how much of a cut note it left out, as it does for a tool's output", async () => {
    const shell: Conversation = { title: info.title, texts: {}, items: [
      { type: "prompt", at, text: "Show me the log" },
      { type: "note", at, kind: "shell", text: { text: "line one of the log", omitted: 2000 }, entities: [] },
      { type: "note", at, kind: "hook_error", text: { text: "Stop failed: Traceback", omitted: 1497 }, entities: [] },
    ] };
    window.history.replaceState(null, "", "/#/sessions");
    render(<App report={report} loadTexts={async () => texts} sessions={{ ...live, read: async () => shell }} />);
    fireEvent.click(await screen.findByRole("link", { name: /Fix the flaky test/ }));
    const reader = await screen.findByRole("article", { name: "Session" });
    await waitFor(() => expect(within(reader).getByText("line one of the log")).toBeTruthy());
    expect(within(reader).getByText(/2,000 more characters/).closest(".note")).toBeTruthy(); // cut shell output isn't the whole
    expect(within(reader).getByText(/1,497 more characters/).closest(".note.failed")).toBeTruthy();
  });

  // 650 entries paged through More: give it time on a loaded machine (it once ran past the 5 s default there).
  it("move by the list keys, page by the shared More, and keep j/k for prompts while reading", { timeout: 30_000 }, async () => {
    const other: SessionListing = { ...info, header: { ...info.header, session_id: "s2" }, title: "A long session" };
    const long: Conversation = { title: other.title, texts: {}, items: [{ type: "prompt", at, text: "Read everything" },
      ...Array.from({ length: 649 }, (_, i) => tool("Read", `file${i}.py`))] };
    window.history.replaceState(null, "", "/#/sessions");
    render(<App report={report} loadTexts={async () => texts} sessions={{ ...live, list: async () => [info, other],
      read: async (id: string) => (id === "s2" ? long : conversation) }} />);
    await screen.findByRole("link", { name: /A long session/ });
    press("j");
    press("Enter");
    expect(window.location.hash).toBe("#/sessions/s2");
    const reader = await screen.findByRole("article", { name: "Session" });
    expect(await within(reader).findByText("Read everything")).toBeTruthy();
    expect(within(reader).getByText("Read everything").closest(".msg")!.className).toBe("msg prompt"); // set apart by weight, no tint
    expect(reader.querySelectorAll(".tool")).toHaveLength(299);
    const more = () => reader.querySelector<HTMLButtonElement>("button.more-row"); // byRole is slow over 600 buttons
    expect(more()!.textContent).toBe("300 more"); // the shared More: says what a click shows
    fireEvent.click(more()!);
    expect(more()!.textContent).toBe("50 more");
    fireEvent.click(more()!);
    expect(reader.querySelectorAll(".tool")).toHaveLength(649);
    expect(document.querySelector(".more-row, .more-button, .reader .chip")).toBeNull();
    reader.querySelector<HTMLButtonElement>(".tool .fold")!.focus();
    press("Enter"); // Enter on a call opens the call, not the cursor's row
    expect(within(reader).getByText("input")).toBeTruthy();
    press("j"); // from prompt to prompt, in the session open
    expect(window.location.hash).toBe("#/sessions/s2");
    press("ArrowUp"); // the arrows still move the list, and the reader follows
    expect(window.location.hash).toBe("#/sessions/s1");
    const search = await screen.findByLabelText("Search this session");
    fireEvent.change(search, { target: { value: "thanks" } });
    fireEvent.keyDown(search, { key: "Escape" }); // the search clears first
    expect((screen.getByLabelText("Search this session") as HTMLInputElement).value).toBe("");
    press("Escape"); // then the reader closes
    expect(window.location.hash).toBe("#/sessions");
    expect(screen.queryByRole("article", { name: "Session" })).toBeNull();
  });

  it("say so when there are none to list", () => {
    show("#/sessions");
    expect(screen.getByText("No sessions to list.")).toBeTruthy();
  });
});

const TOOLS_TEXT = JSON.stringify([{ name: "Bash", description: "Run a command with `bash`.\n\n## Usage\n- Prefer `rg` over grep, and keep each command short enough to read at a glance.",
  input_schema: { properties: { timeout: { type: "number", exclusiveMinimum: 0 } } } }]);

describe("what the harness gave the model: briefings", () => {
  const info: SessionListing = { header: { session_id: "s1", project: "/Users/u/project", harness_version: "2.1.285", model: "m",
    started_at: "2026-10-02T08:00:00+00:00", interactive: true }, title: "Briefed", ended_at: "2026-10-02T08:30:00+00:00", prompts: 2, size: 1 };
  const at = "2026-10-02T08:01:00+00:00";
  const pt = (name: string, label: string, mechanism: string, description = "") => ({ harness: "test", name, label, mechanism, description });
  const part = (over: Partial<BriefingPart> & Pick<BriefingPart, "label" | "text_ref" | "tokens">): BriefingPart => ({
    point: pt("system_prompt", "system prompt", "harness_builtin", "The harness's own prompt."), detail: "", count: null,
    authority: "harness", shares: [], source_id: null, ...over });
  const briefing = (reason: BriefingReason, parts: BriefingPart[], over: Partial<Briefing> = {}): Briefing => ({
    type: "briefing", at, reason, recorded: true, note: null, parts,
    tokens: tokens(parts.reduce((n, p) => n + p.tokens.value, 0)), ...over });
  const system = part({ label: "System prompt", text_ref: "sha256:sys", tokens: tokens(1857) });
  const listing = part({ label: "Skill listing", point: pt("skill_listing", "skill listing", "harness_generated"), count: 3, authority: null,
    shares: [{ label: "pk", authority: "package", tokens: tokens(400), source_id: "listing:a@mk" },
             { label: "the listing's own part", authority: "runtime", tokens: tokens(100), source_id: null }],
    source_id: "not-in-the-report", text_ref: "sha256:skills", tokens: tokens(500) });
  const hook = part({ label: "SessionStart hook output", point: pt("session_start_hook", "hook: SessionStart", "hook_output"),
    detail: "SessionStart:startup", authority: null, text_ref: "sha256:hook", tokens: tokens(40) });
  const start = briefing("start", [
    system,
    part({ label: "Tool definitions", point: pt("tool_definitions", "tool definitions", "harness_builtin"), count: 12,
           text_ref: "sha256:tools", tokens: tokens(17298) }),
    part({ label: "AGENTS.md", point: pt("instructions", "instructions file", "instruction_file"), detail: "/Users/u/project/AGENTS.md",
           authority: "project", source_id: "notes", text_ref: "sha256:agents", tokens: tokens(1987) }),
    part({ label: "Managed policy", point: pt("managed", "managed policy", "config_file"), authority: "managed",
           text_ref: "sha256:policy", tokens: tokens(77) }),
    listing,
    part({ label: "Environment · date · model · git status", authority: "runtime", text_ref: "sha256:env", tokens: tokens(300),
           point: pt("session_context", "session context (environment, date, git status)", "harness_generated") }),
    hook,
  ]);
  const texts: Record<string, string> = {
    "sha256:sys": "You are a coding agent.\n  Keep it short.", "sha256:sys2": "You are a careful coding agent.",
    "sha256:tools": TOOLS_TEXT, "sha256:agents": "# Agents\n\nRun the tests.  ",
    "sha256:policy": "Be careful.", "sha256:skills": "- pk:one\n- own", "sha256:env": "Working directory: /Users/u/project",
    "sha256:hook": "hook says hello", "sha256:files": "contents of a.py",
  };
  const conversation: Conversation = { title: info.title, texts, items: [
    start,
    { type: "prompt", at, text: "Hello" },
    { type: "reply", at, text: "Hi" },
    { type: "note", at, kind: "compacted", text: { text: "", omitted: 0 }, entities: [] },
    briefing("compacted", [system, part({ label: "Files re-attached", point: pt("compaction_files", "files re-attached", "harness_generated"),
                                          detail: "a.py", count: 1, authority: "runtime", text_ref: "sha256:files", tokens: tokens(9) })]),
    { type: "injected", at, by: "the summary carried over at compaction", text: { text: "We said hello.", omitted: 0 } },
    { type: "prompt", at, text: "Again" },
    briefing("changed", [part({ label: "System prompt", text_ref: "sha256:sys2", tokens: tokens(2000) })], { note: "After /model." }),
    { type: "reply", at, text: "Hi again" },
  ] };
  const open = async (read: Conversation = conversation, extra = {}) => {
    window.history.replaceState(null, "", "/#/sessions/s1");
    render(<App report={report} loadTexts={async () => ({})} sessions={{ list: async () => [info], read: async () => read, ...extra }} />);
    const reader = await screen.findByRole("article", { name: "Session" });
    await waitFor(() => expect(reader.querySelector(".briefing")).toBeTruthy());
    return reader;
  };
  const head = (reader: HTMLElement, title: string) => within(reader).getByRole("button", { name: new RegExp(`^${title}`) });
  const row = (reader: HTMLElement, label: string | RegExp) => within(reader).getByRole("button", { name: label });

  it("opens every transcript folded, with the total and each part's tokens on one line, whatever the toggles", async () => {
    const reader = await open();
    const first = reader.querySelector(".reader-body")!.firstElementChild!;
    expect(first.className).toBe("briefing start"); // at the top, before the first prompt
    const fold = head(reader, "Before your first prompt");
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    expect(fold.textContent).toBe("Before your first prompt≈22.1k tokens — system prompt 1.9k · tool definitions 17.3k · instructions 2.1k"
      + " · skill listing 500 · session context 300 · hook output 40");
    expect(within(reader).queryByText("AGENTS.md")).toBeNull(); // folded
    expect(within(reader).getByRole("button", { name: "Show what the harness and hooks injected" }).getAttribute("aria-pressed")).toBe("false");
    fold.focus();
    press("Enter"); // the keyboard opens it as it opens a call
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    expect(within(reader).getByText("AGENTS.md")).toBeTruthy();
  });

  it("lists its parts, each with its group as the Context view groups it and its tokens", async () => {
    const reader = await open();
    fireEvent.click(head(reader, "Before your first prompt"));
    const agents = row(reader, /^AGENTS\.md/);
    expect(agents.textContent).toContain("~/project/AGENTS.md"); // the path in the ~ form
    expect(agents.textContent).toMatch(/Yours2k$/);
    expect(row(reader, /^System prompt/).textContent).toMatch(/Harness1\.9k$/);
    expect(row(reader, /^Tool definitions \(12\)/).textContent).toMatch(/Harness17\.3k$/);
    expect(row(reader, /^Managed policy/).textContent).toMatch(/Harness77$/); // managed counts as harness
    const skills = row(reader, /^Skill listing \(3\)/);
    expect(skills.textContent).toMatch(/mixed500$/); // it spans groups: said so, not given to one
    fireEvent.mouseEnter(skills.querySelector(".part-group")!);
    expect(screen.getByRole("tooltip").textContent).toBe("Mixed — pk: Installed ≈400 · the listing's own part: Harness ≈100");
    fireEvent.mouseLeave(skills.querySelector(".part-group")!);
    const hookRow = row(reader, /^SessionStart hook output/);
    expect(hookRow.textContent).toContain("SessionStart:startup");
    expect(within(hookRow).getByRole("img", { name: /^Untraced/ })).toBeTruthy(); // untraced: harness, named apart
  });

  it("opens a part to its exact recorded text, linked to its source in Configuration when the report has it", async () => {
    const reader = await open();
    fireEvent.click(head(reader, "Before your first prompt"));
    fireEvent.click(row(reader, /^AGENTS\.md/));
    const shown = [...reader.querySelectorAll("pre.code")].map((p) => p.textContent);
    expect(shown).toContain("# Agents\n\nRun the tests.  "); // exact: not rendered, not trimmed
    const link = within(reader).getByRole("link", { name: "Its source in Configuration" });
    expect(link.getAttribute("href")).toBe("#/config/source/notes");
    fireEvent.click(row(reader, /^Skill listing/));
    expect(within(reader).getAllByRole("link", { name: "Its source in Configuration" })).toHaveLength(1); // its id isn't in the report
    expect(within(reader).getByRole("link", { name: "in Configuration" }).getAttribute("href")).toBe("#/config/source/listing%3Aa%40mk");
    fireEvent.click(row(reader, /^Tool definitions/));
    expect(reader.querySelector(".json-tree")).toBeNull(); // the definitions are JSON, shown first exactly as recorded
    expect([...reader.querySelectorAll("pre.code")].map((p) => p.textContent)).toContain(TOOLS_TEXT);
    fireEvent.click(within(reader).getByRole("button", { name: "Tree" })); // a tree on request, its strings verbatim
    const tree = reader.querySelector(".json-tree")!;
    fireEvent.click(within(tree as HTMLElement).getByRole("button", { name: /Bash/ }));
    expect(tree.querySelector(".markdown, h2, code")).toBeNull(); // never rendered as Markdown
    expect(tree.textContent).toContain("Run a command with `bash`.\n\n## Usage\n- Prefer `rg` over grep");
    fireEvent.click(within(reader).getByRole("button", { name: "Exact text" }));
    expect(reader.querySelector(".json-tree")).toBeNull();
    fireEvent.click(link);
    expect(window.location.hash).toBe("#/config/source/notes");
  });

  it("finds a word that only a part's text holds, and marks it there when the part is opened", async () => {
    const reader = await open();
    fireEvent.change(within(reader).getByLabelText("Search this session"), { target: { value: "exclusiveMinimum" } });
    fireEvent.click(head(reader, "Before your first prompt"));
    fireEvent.click(row(reader, /^Tool definitions/));
    const marks = [...reader.querySelectorAll("pre.code mark")].map((m) => m.textContent);
    expect(marks).toEqual(["exclusiveMinimum"]);
  });

  it("follows every compacted divider with what the context was rebuilt with, the summary after it as before", async () => {
    const reader = await open();
    const divider = within(reader).getByText(/compacted here/);
    const after = divider.nextElementSibling!;
    expect(after.className).toBe("briefing compacted");
    expect(within(after as HTMLElement).getByRole("button", { name: /^After compacting/ }).textContent)
      .toBe("After compacting≈1.9k tokens — system prompt 1.9k · files re-attached 9");
    fireEvent.click(within(reader).getByRole("button", { name: "Show what the harness and hooks injected" }));
    expect(after.nextElementSibling!.className).toBe("injected"); // the summary carried over, unchanged, right after
  });

  it("says where the harness changed its prompt, holding only what changed", async () => {
    const reader = await open();
    const changed = reader.querySelector(".briefing.changed")!;
    expect(changed.previousElementSibling!.textContent).toContain("Again");
    const fold = within(changed as HTMLElement).getByRole("button", { name: /^The harness changed its prompt/ });
    expect(fold.textContent).toBe("The harness changed its prompt≈2k tokens — system prompt 2k");
    expect(within(changed as HTMLElement).getByText("After /model.")).toBeTruthy(); // why, shown folded
    fireEvent.click(fold);
    fireEvent.click(within(changed as HTMLElement).getByRole("button", { name: /^System prompt/ }));
    expect(changed.querySelector("pre.code")!.textContent).toBe("You are a careful coding agent.");
  });

  it("says when this harness version recorded no prompt, and shows what it did record", async () => {
    const old = briefing("start", [listing], { recorded: false, note: "Not recorded by this harness version (2.1.266)." });
    const reader = await open({ title: info.title, texts, items: [old, { type: "prompt", at, text: "Hello" }] });
    expect(within(reader).getByText("Not recorded by this harness version (2.1.266).")).toBeTruthy(); // folded, still said
    expect(head(reader, "Before your first prompt").textContent).toBe("Before your first prompt≈500 tokens — skill listing 500");
    const never = briefing("start", [], { recorded: false, note: "The model never answered here, so no prompt was recorded." });
    cleanup();
    const empty = await open({ title: info.title, texts: {}, items: [never] });
    expect(head(empty, "Before your first prompt").textContent).toBe("Before your first promptnothing recorded");
    expect(within(empty).getByText(/The model never answered here/)).toBeTruthy();
  });

  it("says an interrupted start's abandoned parts are left out", async () => {
    const note = "The first prompt was abandoned before the model answered: what was recorded only with it may not have reached the model, and is left out.";
    const reader = await open({ title: info.title, texts, items: [briefing("start", [system], { note }), { type: "prompt", at, text: "Hello" }] });
    expect(within(reader).getByText(note).closest(".briefing")!.className).toBe("briefing start");
  });

  it("shows SessionStart hook output once, in the block, with the injected entries switched on", async () => {
    const reader = await open();
    fireEvent.click(within(reader).getByRole("button", { name: "Show what the harness and hooks injected" }));
    fireEvent.click(head(reader, "Before your first prompt"));
    fireEvent.click(row(reader, /^SessionStart hook output/));
    expect(within(reader).getAllByText("hook says hello")).toHaveLength(1);
  });

  it("opens a subagent's transcript with its own block, read from its own texts", async () => {
    const agentStart = briefing("start", [part({ label: "System prompt", text_ref: "sha256:agent", tokens: tokens(900) })]);
    const parent: Conversation = { title: info.title, texts, items: [start, { type: "prompt", at, text: "Go" },
      { type: "tool_call", at, name: "Agent", summary: "search", input: "{}", result: null, error: false, entities: [], subagent_ref: "agent:a1" }] };
    const reader = await open(parent, {
      subagents: async () => [{ id: "agent-a1", title: "Search", entries: 3 }],
      subagent: async (): Promise<Conversation> => ({ title: "Search", texts: { "sha256:agent": "You are a subagent." }, items: [agentStart] }),
    });
    fireEvent.click(within(reader).getByRole("button", { name: /Agent.*search/ }));
    fireEvent.click(await within(reader).findByRole("button", { name: /Search.*agent-a1/ }));
    await waitFor(() => expect(reader.querySelector(".nested .briefing.start")).toBeTruthy());
    const nested = reader.querySelector<HTMLElement>(".nested .briefing.start")!;
    fireEvent.click(within(nested).getByRole("button", { name: /^Before your first prompt/ }));
    fireEvent.click(within(nested).getByRole("button", { name: /^System prompt/ }));
    expect(nested.querySelector("pre.code")!.textContent).toBe("You are a subagent.");
  });
});

describe("long lists", () => {
  it("show ten rows and a way to see the rest, so every group stays in sight", () => {
    const many = Array.from({ length: 12 }, (_, i) => finding(`m${i}`, { check: "emphasis-density", severity: "info", kind: "emphasis",
                                                                         title: `loud source ${String(i).padStart(2, "0")}` }));
    window.history.replaceState(null, "", "/#/findings");
    render(<App report={{ ...report, check: { ...report.check, findings: many } }} loadTexts={async () => texts} />);
    fireEvent.click(screen.getByRole("button", { name: /^Emphasis density/ }));
    expect(screen.getAllByText(/^loud source/)).toHaveLength(10);
    fireEvent.click(screen.getByRole("button", { name: "2 more" }));
    expect(screen.getAllByText(/^loud source/)).toHaveLength(12);
  });
});

describe("tooltips", () => {
  it("show one at a time: the innermost hovered wins", () => {
    show("#/config");
    fireEvent.click(screen.getByRole("button", { name: /Every session/ }));
    const chip = document.querySelector(".finding-count")!;
    fireEvent.mouseEnter(chip);
    expect(screen.getAllByRole("tooltip").map((t) => t.textContent)).toEqual(["1 finding rests on this source"]);
    const what = screen.getByRole("img", { name: /written by the harness/ }).parentElement!;
    fireEvent.mouseEnter(what);
    expect(screen.getAllByRole("tooltip")).toHaveLength(1); // the earlier one gave way
    fireEvent.mouseLeave(what);
    fireEvent.mouseLeave(chip);
    expect(screen.queryByRole("tooltip")).toBeNull(); // leaving hides it: no stale tooltip held anywhere
    fireEvent.mouseEnter(chip);
    fireEvent.click(screen.getByRole("button", { name: /Every session/ })); // folded away while showing
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Every session/ }));
    fireEvent.mouseEnter(document.querySelector(".finding-count")!);
    expect(screen.getAllByRole("tooltip").map((t) => t.textContent)).toEqual(["1 finding rests on this source"]);
  });
});

describe("the breadcrumb", () => {
  const live = (over = {}) => {
    const moves: string[] = [];
    const scope = {
      harness: { name: "test", label: "Test", version: "2.0" }, project: "/Users/u/project", started: "/Users/u/project",
      projects: [{ path: "/Users/u/project", sessions: 2, last: null }, { path: "/Users/u/other", sessions: 5, last: null }],
      homeDir: "/Users/u",
      choose: (project: string) => { moves.push(project); }, home: () => { moves.push("home"); },
      error: null, retry: () => { moves.push("retry"); }, ...over,
    };
    return { scope, moves };
  };
  const crumb = () => document.querySelector(".crumb") as HTMLElement;
  const crumbButton = () => screen.getByRole("button", { name: /^project/ });

  it("names the harness and project from data, with what each is in its tooltip", () => {
    const { scope } = live();
    render(<App report={report} loadTexts={async () => texts} scope={scope} onReread={() => {}} />);
    expect(crumb().textContent).toBe("Test/project"); // the server's label "Test", not the snapshot's name "test"
    fireEvent.mouseEnter(screen.getByText("Test"));
    expect(screen.getByRole("tooltip").textContent).toBe("Harness · Test 2.0 · m · 0 recent sessions read · read " +
      new Date(report.generated_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }));
    fireEvent.mouseEnter(crumbButton().parentElement!);
    expect(screen.getAllByRole("tooltip").map((t) => t.textContent)).toEqual(["Project · /Users/u/project · p to switch"]);
    expect(crumbButton().getAttribute("aria-haspopup")).toBe("listbox");
    expect(document.querySelector("select")).toBeNull();
  });

  it("opens the project list on p without moving, closes it on Esc, and switches keeping the view", () => {
    const { scope, moves } = live();
    window.history.replaceState(null, "", "/#/findings?severity=warn");
    render(<App report={report} loadTexts={async () => texts} scope={scope} onReread={() => {}} />);
    press("p");
    expect(window.location.hash).toBe("#/findings?severity=warn");
    expect(crumbButton().getAttribute("aria-expanded")).toBe("true");
    const filter = screen.getByLabelText("Filter projects");
    expect(document.activeElement).toBe(filter); // opening puts focus in the filter
    press("Escape");
    expect(screen.queryByRole("dialog", { name: "Switch project" })).toBeNull();
    expect(document.activeElement).toBe(crumbButton()); // and closing returns it to the crumb
    fireEvent.click(crumbButton());
    const list = screen.getByRole("listbox", { name: "Projects, most recent first" });
    press("ArrowDown"); // from the filter into the list
    expect(document.activeElement).toBe(list);
    press("j");
    press("Enter");
    expect(moves).toEqual(["/Users/u/other"]);
    expect(screen.queryByRole("dialog", { name: "Switch project" })).toBeNull();
    expect(window.location.hash).toBe("#/findings?severity=warn");
  });

  it("keeps the view's keys out of the open list: j moves the list, Esc closes only it", () => {
    const { scope } = live();
    window.history.replaceState(null, "", "/#/findings/w1");
    render(<App report={report} loadTexts={async () => texts} scope={scope} onReread={() => {}} />);
    expect(screen.getByRole("complementary")).toBeTruthy(); // a finding open beside the list
    press("p");
    press("ArrowDown");
    const list = screen.getByRole("listbox", { name: "Projects, most recent first" });
    const current = () => within(list).getAllByRole("option").findIndex((o) => o.getAttribute("aria-selected") === "true");
    expect(current()).toBe(0);
    press("j");
    expect(current()).toBe(1);
    expect(window.location.hash).toBe("#/findings/w1"); // the findings cursor never moved
    press("Escape");
    expect(screen.queryByRole("dialog", { name: "Switch project" })).toBeNull();
    expect(screen.getByRole("complementary")).toBeTruthy(); // the detail stays
    expect(document.activeElement).toBe(crumbButton());
  });

  it("returns home on 0 and on the wordmark", () => {
    const { scope, moves } = live();
    render(<App report={report} loadTexts={async () => texts} scope={scope} onReread={() => {}} />);
    press("0");
    fireEvent.click(screen.getByRole("button", { name: /agentlint: the home/ }));
    expect(moves).toEqual(["home", "home"]);
  });

  it("names the harness by the report's own label until the server's list answers, else the snapshot's name", () => {
    show("#/findings");
    expect(crumb().textContent).toBe("test/project"); // no label in this report: the snapshot's name
    cleanup();
    render(<App report={{ ...report, harness_label: "Test harness" }} loadTexts={async () => texts} />);
    expect(crumb().textContent).toBe("Test harness/project");
  });

  it("counts findings in the sidebar without the worst one's colour", () => {
    show();
    const findings = screen.getByRole("link", { name: /Findings/ });
    const shown = findings.querySelector(".count")!;
    expect(shown.textContent).toBe("3");
    expect(shown.className).toBe("count");
    expect(findings.closest("nav")!.querySelectorAll("[class*='sev-']")).toHaveLength(0);
    expect(screen.getByRole("link", { name: /Configuration/ }).querySelector(".count")).toBeNull(); // inventory counts went
  });
});

describe("texts, per scope", () => {
  it("asks for the texts of the report shown, again when it changes; the old ones go, a late answer is dropped", async () => {
    const asked: string[] = [];
    const late: ((t: Record<string, string>) => void)[] = [];
    const loadTexts = (r: Report) => {
      asked.push(r.snapshot.cwd);
      return r.snapshot.cwd === "/Users/u/project" && asked.length > 1
        ? new Promise<Record<string, string>>((resolve) => { late.push(resolve); })
        : Promise.resolve(r.snapshot.cwd === "/Users/u/project" ? texts : { "ref-notes": "The other project's notes." });
    };
    window.history.replaceState(null, "", "/#/config/notes");
    const view = render(<App report={report} loadTexts={loadTexts} />);
    expect(await screen.findByText(/Run the tests before committing/)).toBeTruthy();
    const other = { ...report, snapshot: { ...report.snapshot, cwd: "/Users/u/other" } };
    view.rerender(<App report={other} loadTexts={loadTexts} />);
    expect(screen.queryByText(/Run the tests before committing/)).toBeNull(); // the first project's texts, gone at once
    expect(await screen.findByText(/The other project's notes/)).toBeTruthy();
    expect(asked).toEqual(["/Users/u/project", "/Users/u/other"]);
    const back = { ...report };
    view.rerender(<App report={back} loadTexts={loadTexts} />); // asked again, answered late …
    view.rerender(<App report={other} loadTexts={loadTexts} />); // … after the page has moved on
    expect(await screen.findByText(/The other project's notes/)).toBeTruthy();
    await act(async () => late[0](texts));
    expect(screen.queryByText(/Run the tests before committing/)).toBeNull(); // that answer was for a report no longer shown
  });

  it("says to read the project again when the server no longer keeps its texts", async () => {
    window.history.replaceState(null, "", "/#/config/notes");
    render(<App report={report} onReread={() => {}}
                loadTexts={async () => { throw new Error("this project's texts aren't kept"); }} />);
    expect(await screen.findByText("this project's texts aren't kept. Press r to read this project again.")).toBeTruthy();
  });
});

describe("text, as its format reads best", () => {
  const withText = (format: string, text: string, line: string | null = null) => {
    const textRef = `ref-${format}`;
    const doc = source("doc", { content: { type: "file_text", format, text_ref: textRef, content_hash: "h", tokens: tokens(50),
                                     required_keys: [] } });
    const data = { ...report, snapshot: { ...report.snapshot, sources: [...report.snapshot.sources, doc] }, labels: { ...report.labels, doc: "doc" } };
    window.history.replaceState(null, "", `/#/config/doc${line ? `?line=${line}` : ""}`);
    render(<App report={data} loadTexts={async () => ({ ...texts, [textRef]: text })} />);
    return screen.findByRole("complementary", { name: "Source" });
  };

  it("shows JSON as a tree whose strings keep their line breaks", async () => {
    const detail = await withText("json", JSON.stringify([{ name: "Agent", description: "Launch an agent.\n\n## When to use\nOften." }, { name: "Bash" }]));
    const agent = await within(detail).findByRole("button", { name: /0.*Agent/ }); // folded to its name
    fireEvent.click(agent);
    expect(within(detail).getByRole("heading", { name: "When to use" }).closest(".json-string")).toBeTruthy(); // a description, as Markdown
    fireEvent.click(within(detail).getByRole("button", { name: "Raw, numbered" }));
    expect(detail.querySelector("pre.numbered")).toBeTruthy();
  });

  it("formats Markdown safely: frontmatter as a table, no raw HTML, no images fetched", async () => {
    const detail = await withText("frontmatter_doc",
      "---\nname: helper\ndescription: helps\n---\n# Use it\n\nSome **bold** words.\n\n<script>alert(1)</script>\n\n![tracker](https://example.com/x.png)\n");
    expect(await within(detail).findByRole("heading", { name: "Use it" })).toBeTruthy();
    expect(within(detail).getByText("helps")).toBeTruthy(); // the frontmatter, as a table
    expect(detail.querySelector("script, img")).toBeNull();
    expect(within(detail).getByText("[image: tracker]")).toBeTruthy();
  });

  it("starts raw where a finding points at a line, or for plain text", async () => {
    const detail = await withText("markdown", "# Title\nline two\n", "2");
    await waitFor(() => expect(detail.querySelector(".line.pointed")?.textContent).toBe("line two"));
    cleanup();
    const plain = await withText("plain", "- a: one\n- b: two\n");
    await waitFor(() => expect(plain.querySelector("pre.numbered")).toBeTruthy());
    fireEvent.click(within(plain).getByRole("button", { name: "Formatted" }));
    expect(within(plain).getAllByRole("listitem")).toHaveLength(2);
  });
});

describe("the frame", () => {
  it("explains every icon on ?, and gets out of the way on Esc", () => {
    show("#/findings");
    press("?");
    const help = screen.getByRole("dialog", { name: "Shortcuts and icons" });
    expect(help.tagName).toBe("DIALOG"); // the browser's own: modal, with the scrim as its backdrop
    expect(within(help).getByText(/Suggestive — a pattern worth a look/)).toBeTruthy();
    const views = [...help.querySelectorAll(".legend-row")].map((row) => row.textContent?.replace(/\s+/g, " ").trim());
    expect(views).toContain("4 Skills and agents"); // each view on its own line, beside its key
    expect(views).toContain("What changed"); // one with no key of its own too, with no key cap
    for (const view of Object.keys(NAV_ICON) as View[]) { // every view's icon is explained
      const row = [...help.querySelectorAll(".legend-row")].find((r) => r.textContent?.trim().endsWith(VIEW_NAMES[view]));
      expect(row?.querySelector("svg"), view).toBeTruthy();
    }
    press("j"); // swallowed while help is open
    press("Escape");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  it("lists a list's keys from the handler that reads them, and moves the same way in every list", () => {
    show("#/findings");
    press("?");
    const rows = [...screen.getByRole("dialog", { name: "Shortcuts and icons" }).querySelectorAll(".legend-row")]
      .map((r) => r.textContent?.replace(/\s+/g, " ").trim());
    for (const [keys, words] of LIST_KEYS) expect(rows).toContain(`${keys.join("")} ${words}`);
    press("Escape");
    cleanup();
    for (const [hash, head] of [["#/findings", /Broken reference/], ["#/config", /Every session/], ["#/entities", /Used since/]] as const) {
      show(hash);
      fireEvent.click(screen.getByRole("button", { name: head }));
      const cursor = () => document.querySelector(".row.cursor")?.getAttribute("href");
      const first = cursor();
      press("j");
      press("k");
      expect(cursor()).toBe(first);
      press("Enter");
      expect(window.location.hash).toBe(first);
      press("Escape");
      expect(screen.queryByRole("complementary")).toBeNull();
      cleanup();
    }
  });

  it("lists each glyph once in the legend, and puts none on a heading", async () => {
    show("#/findings");
    press("?");
    const help = screen.getByRole("dialog", { name: "Shortcuts and icons" });
    const drawn = [...help.querySelectorAll(".legend-row")].flatMap((row) => [...row.querySelectorAll("svg")].map((svg) => svg.outerHTML));
    expect(drawn.length).toBeGreaterThan(30);
    expect(new Set(drawn).size).toBe(drawn.length); // one glyph per meaning: no two legend rows share one
    press("Escape");
    const headings = "h2 svg, h3 svg, .card > header > svg, .strip > svg";
    for (const hash of ["", "#/findings/e1", "#/config/source/notes", "#/config/listing/always|listing", "#/entities", "#/context"]) {
      cleanup();
      show(hash);
      for (const head of screen.queryAllByRole("button", { expanded: false })) if (head.classList.contains("group-head")) fireEvent.click(head);
      await waitFor(() => expect(document.querySelector(".app")).toBeTruthy());
      expect([...document.querySelectorAll(headings)].map((svg) => svg.outerHTML), hash || "overview").toEqual([]);
    }
  });

  it("draws the frame, never a page of its own, while it has no report", () => {
    render(<App report={null} loadTexts={async () => ({})} />);
    expect(screen.getByRole("navigation", { name: "Views" })).toBeTruthy();
    expect(document.querySelector(".overview.skeleton")).toBeTruthy();
  });
});

describe("the suppress command", () => {
  it("names the project as one shell word", () => {
    expect(shellPath("/Users/u/project", "/Users/u")).toBe("~/project");
    expect(shellPath("/Users/u/my project", "/Users/u")).toBe("'/Users/u/my project'"); // quoted, so no ~
    expect(shellPath("/srv/it's", "/Users/u")).toBe("'/srv/it'\\''s'");
  });

  it("writes ~ only for the user's own home directory, never the first /Users/<x> of the path", () => {
    expect(shellPath("/Users/Shared/proj", "/Users/u")).toBe("/Users/Shared/proj"); // the shell's ~ is /Users/u
    expect(shellPath("/Users/ux/proj", "/Users/u")).toBe("/Users/ux/proj"); // a prefix, not a parent
    expect(shellPath("/home/me/proj", "/home/me/")).toBe("~/proj");
    expect(shellPath("/Users/u/project", undefined)).toBe("/Users/u/project"); // no home known: whole
    expect(home("/Users/Shared/proj/AGENTS.md", "/Users/u")).toBe("/Users/Shared/proj/AGENTS.md");
    expect(placed("/Users/Shared/proj", "/Users/u")).toEqual({ name: "proj", parent: "/Users/Shared" });
    expect(placed("/Users/u/projects/x", "/Users/u")).toEqual({ name: "x", parent: "~/projects" });
  });

  it("copies a command naming a project outside the home directory as it is", async () => {
    let copied = "";
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t: string) => { copied = t; } } });
    window.history.replaceState(null, "", "/#/findings/e1");
    const shared = { ...report, snapshot: { ...report.snapshot, cwd: "/Users/Shared/proj" } };
    render(<App report={shared} loadTexts={async () => texts} />);
    press("s");
    await waitFor(() => expect(copied).toBe(`agentlint suppress e1 --cwd /Users/Shared/proj --reason "…"`));
  });
});

describe("round 8: what the lists and details say", () => {
  const counter = () => document.querySelector(".config .counter")?.textContent;

  it("counts sources as sources: untraced text is listed, not counted as one", () => {
    show("#/config");
    expect(counter()).toBe("5 of 5");
    expect(screen.getByRole("button", { name: /Every message/ }).textContent).toContain("1 untraced");
    expect(screen.getByRole("button", { name: /Every message/ }).textContent).not.toContain("sources");
  });

  const many = Array.from({ length: 12 }, (_, i) => finding(`m${i}`, { check: "emphasis-density", severity: "info", kind: "emphasis",
                                                                       title: `loud source ${String(i).padStart(2, "0")}` }));
  const manyReport = { ...report, check: { ...report.check, findings: many } };
  const rows = () => [...document.querySelectorAll(".findings .row")].map((r) => r.textContent ?? "");

  it("opens the group holding what you opened far enough to show it, and leaves it foldable", () => {
    window.history.replaceState(null, "", "/#/findings");
    render(<App report={manyReport} loadTexts={async () => texts} />);
    const head = screen.getByRole("button", { name: /^Emphasis density/ });
    fireEvent.click(head);
    fireEvent.click(screen.getByRole("link", { name: /loud source 04/ }));
    expect(rows()).toHaveLength(10); // a row clicked in the list doesn't unfold the rest
    fireEvent.click(head);
    expect(head.getAttribute("aria-expanded")).toBe("false"); // the group holding the open row still folds
    expect(rows()).toHaveLength(0);
    cleanup();

    window.history.replaceState(null, "", "/#/findings/m11"); // opened from elsewhere, past the first ten
    render(<App report={manyReport} loadTexts={async () => texts} />);
    expect(rows()).toHaveLength(12);
    expect(document.querySelector(".findings .row.cursor")?.textContent).toContain("loud source 11");
    press("Escape");
    expect(rows()).toHaveLength(12); // closing doesn't re-fold under the cursor
    expect(document.querySelector(".findings .row.cursor")?.textContent).toContain("loud source 11");
  });

  it("leaves a listing whose part is open foldable", () => {
    show("#/config/source/listing:a@mk");
    const parts = screen.getByRole("button", { name: "Parts" });
    expect(parts.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(parts);
    expect(parts.getAttribute("aria-expanded")).toBe("false");
  });

  it("opens a listing's parts with its chevron, beside its link, and with → and ←", () => {
    show("#/config");
    fireEvent.click(screen.getByRole("button", { name: /Every session/ }));
    const parts = screen.getByRole("button", { name: "Parts" });
    expect(parts.closest("a")).toBeNull(); // the chevron is not inside the row's link
    const names = () => [...document.querySelectorAll(".config .row")].map((r) => r.querySelector(".title")?.textContent);
    expect(names()).not.toContain("skill listing · b@mk");
    fireEvent.click(parts);
    expect(names()).toContain("skill listing · b@mk");
    fireEvent.click(parts);
    while (!document.querySelector(".config .row.cursor")?.textContent?.includes("2 parts")) press("j");
    press("ArrowRight");
    expect(parts.getAttribute("aria-expanded")).toBe("true");
    press("j"); // onto a part: ← closes its listing and puts the cursor back on it
    press("ArrowLeft");
    expect(parts.getAttribute("aria-expanded")).toBe("false");
    expect(document.querySelector(".config .row.cursor")?.textContent).toContain("2 parts");
  });

  it("puts nothing interactive inside a link, in any view", async () => {
    for (const hash of ["#/overview", "#/findings/e1", "#/config/source/listing:a@mk", "#/config/source/notes", "#/entities", "#/context", "#/sessions"]) {
      show(hash);
      for (const name of [/Every session/, /Every message/, /When invoked/, /Used since/, /Never used/, /Shown in this session/]) {
        const head = screen.queryByRole("button", { name });
        if (head && head.getAttribute("aria-expanded") === "false") fireEvent.click(head);
      }
      expect(document.querySelector("a button, a a, a input, a select, a [tabindex]")).toBeNull();
      cleanup();
    }
  });

  it("marks a finding's line only in the source it pointed into", () => {
    show("#/config/source/notes?line=2");
    expect(document.querySelector(".config .row")?.getAttribute("href")).not.toContain("line=");
    press("j");
    expect(window.location.hash).not.toContain("line=");
    press("Escape");
    expect(window.location.hash).toBe("#/config");
  });

  it("filters the suppressed group like the rest", () => {
    show("#/findings?severity=error");
    expect(screen.queryByRole("button", { name: /Suppressed/ })).toBeNull();
    expect(screen.queryByText("a dismissed one")).toBeNull();
    cleanup();
    show("#/findings?q=dismissed");
    expect(screen.queryByText("Nothing matches these filters.")).toBeNull();
    expect(screen.getByText("a dismissed one")).toBeTruthy();
    cleanup();
    show("#/findings?q=no-such-text");
    expect(screen.getByText("Nothing matches these filters.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Suppressed/ })).toBeNull();
  });

  it("says a visibility no session showed is unknown, not 'not shown'", () => {
    show("#/config/source/notes");
    const detail = screen.getByRole("complementary", { name: "Source" });
    expect(within(detail).getByRole("img", { name: "No recent session showed whether the agent sees it" })).toBeTruthy();
    expect(within(detail).queryByTitle(/not shown/)).toBeNull();
  });

  it("explains untraced hook output with what-this-is, and in its detail", () => {
    const described = { ...report, snapshot: { ...report.snapshot, untraced: report.snapshot.untraced.map((u) => (
      { ...u, point: { ...u.point, description: "What a prompt hook printed." } })) } };
    window.history.replaceState(null, "", "/#/config?load=per_message");
    render(<App report={described} loadTexts={async () => texts} />);
    expect(screen.getByRole("img", { name: "What a prompt hook printed." })).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name: /hook output "hello"/ }));
    expect(within(screen.getByRole("complementary", { name: "Untraced text" })).getByText("What a prompt hook printed.")).toBeTruthy();
  });

  it("offers N more on the session's untraced group", () => {
    const twelve = Array.from({ length: 12 }, (_, i) => ({ ...report.snapshot.untraced[0], id: `u${i}` }));
    const busy = { ...report, snapshot: { ...report.snapshot, untraced: twelve },
                   contexts: [{ ...report.contexts[0], untraced: twelve.map((u) => u.id) }] };
    window.history.replaceState(null, "", "/#/context");
    render(<App report={busy} loadTexts={async () => texts} />);
    fireEvent.click(screen.getByRole("button", { name: /Explained by nothing configured/ }));
    expect(screen.getAllByRole("link", { name: /hook output "hello"/ })).toHaveLength(10);
    fireEvent.click(screen.getByRole("button", { name: "2 more" }));
    expect(screen.getAllByRole("link", { name: /hook output "hello"/ })).toHaveLength(12);
  });

  it("links a location to its source by id, shows the place inside the file, and copies the file's path", async () => {
    const hook = source("hook", { provenance: { point: point("hook", "hook", "hook_output"), file: "/Users/u/plugin.json",
                                                authority: "package", package: "p@mk", anchor: "/hooks/SessionStart/0" } });
    const other = source("plugin", { provenance: { point: point("manifest", "manifest", "config_file"), file: "/Users/u/plugin.json",
                                                   authority: "package", package: "p@mk", anchor: null } });
    const pointed = { ...report, snapshot: { ...report.snapshot, sources: [...report.snapshot.sources, other, hook] },
                      check: { ...report.check, findings: [finding("j1", { locations: [{ source_id: "hook", line: null }], sources: ["hook"] })] } };
    let copied = "";
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t: string) => { copied = t; } } });
    window.history.replaceState(null, "", "/#/findings/j1");
    render(<App report={pointed} loadTexts={async () => texts} />);
    const detail = screen.getByRole("complementary", { name: "Finding" });
    fireEvent.click(within(detail).getByRole("button", { name: "Copy the path" }));
    await waitFor(() => expect(copied).toBe("/Users/u/plugin.json"));
    fireEvent.click(within(detail).getByRole("link", { name: "~/plugin.json#/hooks/SessionStart/0" }));
    expect(window.location.hash).toBe("#/config/source/hook"); // the hook itself, not the first source in that file
  });

  it("names a location in no file by its source's label, with nothing to copy, and one no longer configured by its id", () => {
    const pointed = { ...report, check: { ...report.check, findings: [finding("j2", {
      locations: [{ source_id: "listing:a@mk", line: null }, { source_id: "gone", line: 3 }] })] } };
    window.history.replaceState(null, "", "/#/findings/j2");
    render(<App report={pointed} loadTexts={async () => texts} />);
    const detail = screen.getByRole("complementary", { name: "Finding" });
    expect(within(detail).queryByRole("button", { name: "Copy the path" })).toBeNull();
    fireEvent.click(within(detail).getByRole("button", { name: "1 more location" }));
    const where = [...detail.querySelectorAll("dl.props dt")].find((d) => d.textContent === "Where")!.nextElementSibling!;
    expect(within(where as HTMLElement).getAllByRole("listitem").map((li) => li.textContent))
      .toEqual(["skill listing · a@mk", "gone (no longer configured)"]);
    fireEvent.click(within(where as HTMLElement).getByRole("link", { name: "skill listing · a@mk" }));
    expect(window.location.hash).toBe("#/config/source/listing%3Aa%40mk");
  });
});

describe("round 12: a count of 1 in the singular", () => {
  it("says the noun it was given for 1, and the plural it was given otherwise", () => {
    expect(counted(1, "prompt", "prompts")).toBe("1 prompt");
    expect(counted(0, "prompt", "prompts")).toBe("0 prompts");
    expect(counted(1234, "source", "sources")).toBe("1,234 sources");
  });

  it("a load group with one source says \"1 source\"", () => {
    show("#/config");
    const heads = [...document.querySelectorAll(".config .group-summary")].map((b) => b.textContent ?? "");
    expect(heads.some((t) => /\b1 source\b/.test(t))).toBe(true);
    expect(heads.some((t) => /\b1 sources\b/.test(t))).toBe(false);
  });

  it("a session of one prompt says \"1 prompt\" in its row and its reader", async () => {
    const one: SessionListing = { header: { session_id: "s1", project: "/Users/u/project", harness_version: "2.0", model: "m",
      started_at: "2026-10-02T08:00:00+00:00", interactive: true }, title: "One question",
      ended_at: "2026-10-02T08:05:00+00:00", prompts: 1, size: 100 };
    const at = "2026-10-02T08:01:00+00:00";
    const live = { list: async () => [one], subagents: async () => [], subagent: async (): Promise<Conversation> => ({ title: "", texts: {}, items: [] }),
                   read: async (): Promise<Conversation> => ({ title: one.title, texts: {}, items: [{ type: "prompt", at, text: "Why?" }] }) };
    window.history.replaceState(null, "", "/#/sessions");
    render(<App report={report} loadTexts={async () => texts} sessions={live} />);
    const row = await screen.findByRole("link", { name: /One question/ });
    expect(row.textContent).toContain("1 prompt");
    expect(row.textContent).not.toContain("1 prompts");
    fireEvent.click(row);
    const reader = await screen.findByRole("article", { name: "Session" });
    await waitFor(() => expect(reader.textContent).toContain("1 prompt"));
    expect(reader.textContent).not.toContain("1 prompts");
  });
});

describe("round 13: every count of 1 in the singular", () => {
  const since = day("2026-09-15T09:00:00+00:00");
  const once = { ...report.contexts[0].session };
  const seenOnce = { seen_in: [once], sessions_examined: 1 };
  const single: Report = {
    ...report,
    snapshot: {
      ...report.snapshot,
      sources: report.snapshot.sources.map((s) => s.content.type === "observed_text" ? { ...s, content: { ...s.content, ...seenOnce } } : s),
      untraced: report.snapshot.untraced.map((u) => ({ ...u, content: { ...u.content, ...seenOnce } })),
      entities: report.snapshot.entities.slice(0, 1),
    },
    usage: { ...report.usage!, turns: 1, summary: { ...report.usage!.summary, unused: [] } },
  };
  const showSingle = (hash: string) => {
    window.history.replaceState(null, "", `/${hash}`);
    return render(<App report={single} loadTexts={async () => texts} />);
  };
  const seenRow = (detail: HTMLElement) =>
    [...detail.querySelectorAll("dl.props dt")].find((d) => d.textContent === "Seen")!.nextElementSibling!.textContent;

  it("the Seen mark, as Help shows it, says \"1 of the 1 recent session read\"", () => {
    render(<SeenIcon seen={1} of={1} />);
    expect(screen.getByRole("img").getAttribute("aria-label")).toBe("Its text was seen in 1 of the 1 recent session read");
  });

  it("a source and untraced text seen in the one session read say so in the singular", () => {
    showSingle("#/config/source/listing%3Aa%40mk");
    expect(seenRow(screen.getByRole("complementary", { name: "Source" }))).toBe("in 1 of the 1 recent session read");
    cleanup();
    showSingle("#/config/untraced/u-hello");
    expect(seenRow(screen.getByRole("complementary", { name: "Untraced text" }))).toBe("in 1 of the 1 recent session read");
  });

  it("the overview says \"the other 1 was never used\" when one skill or agent went unused", () => {
    show("");
    fireEvent.mouseEnter(screen.getByText("1 of 2 used"));
    expect(screen.getByRole("tooltip").textContent).toBe(`Skills and agents your turns used since ${since}; the other 1 was never used`);
  });

  it("the skills and agents strip says \"1 skill or agent\" and \"your 1 turn\"", () => {
    showSingle("#/entities");
    const strip = document.querySelector(".entities-view .strip")!.textContent;
    expect(strip).toBe(`1 of 1 skill or agent used since ${since}· across your 1 turn`);
  });
});
