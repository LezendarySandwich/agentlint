// The app on a report Python really wrote (src/fixtures/report.ts, from tests/report/test_golden.py): every view,
// every source and finding opened, every always-on outcome. The hand-written fixture in App.test.tsx pins what
// the views say; this one pins that they read what Python writes.
import { fireEvent, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { App } from "./App";
import type { Report } from "./contract";
import { otherOutcomes, report as written, texts } from "./fixtures/report";
import { PAGES } from "./route";

const report: Report = written; // typed as the contract says, as the app receives it

beforeEach(() => window.history.replaceState(null, "", "/"));
afterEach(cleanup);

const show = (hash: string, shown: Report = report) => {
  window.history.replaceState(null, "", `/${hash}`);
  return render(<App report={shown} loadTexts={async () => texts} />);
};

describe("the report Python writes", () => {
  it("holds one of every content state, and a finding of each lifecycle", () => {
    expect(new Set(report.snapshot.sources.map((s) => s.content.type))).toEqual(
      new Set(["file_text", "observed_text", "size_only", "not_observed"]));
    expect(report.check.findings.some((f) => f.suppressed)).toBe(true);
    expect(report.check.findings.some((f) => f.suppression_lapsed)).toBe(true);
  });

  it.each(PAGES)("renders the %s view", (view) => {
    show(`#/${view}`);
    expect(document.body.textContent).not.toBe("");
  });

  it.each(report.snapshot.sources.map((s) => s.id))("opens source %s under its label", (id) => {
    show(`#/config/${encodeURIComponent(id)}`);
    expect(screen.getAllByText(report.labels[id], { exact: false }).length).toBeGreaterThan(0);
  });

  it.each(report.check.findings.map((f) => [f.check, f.id, f.title]))("opens the %s finding", (_check, id, title) => {
    show(`#/findings/${id}`);
    expect(screen.getAllByText(title, { exact: false }).length).toBeGreaterThan(0);
  });

  it("lays out a finding's members from its list: name, kind, package by its short name, what the agent saw", () => {
    const finding = report.check.findings.find((f) => f.members?.length)!;
    show(`#/findings/${finding.id}`);
    const detail = screen.getByRole("complementary", { name: "Finding" });
    const items = within(detail).getAllByRole("listitem").filter((li) => li.closest(".members"));
    // one column each: name, kind and package, how the agent sees it, the description, notes, a link to the source
    expect(items.map((li) => [...li.children].map((c) => c.textContent))).toEqual([
      ["notes-pkg:notes", "skill · notes", " described", "drafts release notes", "identical to Bash", "a symlink to /home/u/store/notes",
       "instructions file notes-pkg:notes"],
      ["Bash", "tool", " not known", "system prompt"],
    ]);
    fireEvent.mouseEnter(within(items[0]).getByText("notes"));
    expect(screen.getByRole("tooltip").textContent).toBe("notes-pkg"); // the id in the tooltip
    expect(within(items[0]).getByRole("link", { name: "instructions file notes-pkg:notes" })).toBeTruthy(); // into its source
    expect(within(items[0]).getByRole("img", { name: "The agent sees its name and description" })).toBeTruthy();
  });

  it("finds a finding by what its members say", () => {
    show("#/findings?q=drafts%20release");
    expect(screen.getByRole("link", { name: /Two things, one name/ })).toBeTruthy();
  });

  it.each([report.snapshot.always_on, ...otherOutcomes].map((o) => [o.type, o]))(
    "shows the overview with a %s total", (_type, outcome) => {
      show("#/overview", { ...report, snapshot: { ...report.snapshot, always_on: outcome } });
      expect(document.body.textContent).toMatch(/\d/);
    });
});
