// Diagnostics, on what Python really serves (src/fixtures/report.ts): agentlint's findings about itself, their own page,
// never in Findings, its counts or the overview.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { DiagnosticInfo, Report } from "./contract";
import type { RecordSource } from "./data";
import { diagnostics, report as written, texts } from "./fixtures/report";

const report: Report = written;
const listed: DiagnosticInfo[] = diagnostics;

beforeEach(() => window.history.replaceState(null, "", "/"));
afterEach(cleanup);

function records(answer: () => Promise<DiagnosticInfo[]> = async () => listed) {
  return { snapshots: async () => [], diff: async () => { throw new Error("none"); }, diagnostics: vi.fn(answer) } satisfies RecordSource;
}

const show = (hash: string, source: RecordSource | undefined = records()) => {
  window.history.replaceState(null, "", `/${hash}`);
  return render(<App report={report} loadTexts={async () => texts} records={source} />);
};

describe("Diagnostics", () => {
  it("is a page of its own in the sidebar, key 7, after the views about your configuration and before Tests and Settings", () => {
    show("#/overview");
    const nav = screen.getByRole("navigation", { name: "Views" });
    const links = within(nav).getAllByRole("link").map((link) => link.textContent);
    expect(links.slice(-6)).toEqual(["Sessions6", "Diagnostics7", "Tests8", "Results", "Jobs", "Settings9"]); // Results: Tests', no key; Jobs: no key
    act(() => { fireEvent.keyDown(document.body, { key: "7" }); });
    expect(window.location.hash).toBe("#/diagnostics");
  });

  it("lists them by kind, each folded until opened, with what it is about and when", async () => {
    show("#/diagnostics");
    expect(await screen.findByText("2")).toBeTruthy(); // the count in the strip
    const heads = screen.getAllByRole("button", { expanded: false }).filter((b) => b.classList.contains("group-head"));
    expect(heads.map((b) => b.querySelector(".group-name")?.textContent)).toEqual(["Generator empty", "Situation contaminated"]);
    expect(screen.queryByText("The generator wrote nothing for this target.")).toBeNull(); // folded
    fireEvent.click(heads[0]);
    expect(screen.getByText("The generator wrote nothing for this target.")).toBeTruthy();
    expect(screen.getByText("rules#2")).toBeTruthy();
    fireEvent.mouseEnter(screen.getByText("Generator empty"));
    expect(screen.getByRole("tooltip").textContent).toBe("generator_empty"); // the producer's own name
  });

  it("says there are none, and says why listing them failed", async () => {
    show("#/diagnostics", records(async () => []));
    expect(await screen.findByText(/No diagnostics: agentlint has noted nothing about its own work/)).toBeTruthy();
    cleanup();
    show("#/diagnostics", records(async () => { throw new Error("the agentlint database: locked"); }));
    expect(await screen.findByText(/Couldn't list the diagnostics: the agentlint database: locked/)).toBeTruthy();
  });

  it("never joins Findings, its counts or the overview", async () => {
    const source = records();
    show("#/findings", source);
    const nav = screen.getByRole("navigation", { name: "Views" });
    const shown = report.check.findings.filter((f) => !f.suppressed).length;
    expect(within(nav).getByRole("link", { name: /Findings/ }).textContent).toBe(`Findings${shown}2`);
    expect(document.querySelector("main")?.textContent).not.toMatch(/generator|contaminated|diagnostic/i);
    cleanup();
    show("#/overview", source);
    expect(document.querySelector("main")?.textContent).not.toMatch(/generator|contaminated|diagnostic/i); // the sidebar names the page only
    expect(source.diagnostics).not.toHaveBeenCalled(); // asked for only by its own page
  });
});
