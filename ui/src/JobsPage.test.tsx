// Jobs against a fake server: tests and readings in one list, the running first; a reading opened to its sections,
// stopped and resumed.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JobsAnswer, ReadDetail, ReadEntry } from "./contract";
import { jobsAnswer } from "./fixtures/results";
import { JobsView } from "./JobsPage";
import type { TestsApi } from "./testsApi";

const READ: ReadEntry = {
  read_id: "2026-10-07T163504Z-a6a351", kind: "reading", project: "/work/project", harness: "test-harness", status: "open",
  standing: "running", created: "2030-10-07T16:35:04+00:00", finished: null, sources: 3,
  sections: { planned: 4, pending: 1, reading: 1, read: 1, failed: 1 }, runner: { alive: true }, log_tail: [],
};
const DETAIL: ReadDetail = { ...READ, sections_list: [
  { source_id: "a", source: "AGENTS.md", line: 5, loads: "x", status: "read", directives: 2, message: null },
  { source_id: "a", source: "AGENTS.md", line: 9, loads: "x", status: "reading", directives: null, message: null },
  { source_id: "b", source: "skill sql-query", line: 1, loads: "x", status: "failed", directives: null, message: "the call timed out" },
  { source_id: "b", source: "skill sql-query", line: 40, loads: "x", status: "pending", directives: null, message: null },
] };

function fake(over: Partial<TestsApi> = {}) {
  const tests = {
    jobs: vi.fn(async (): Promise<JobsAnswer> => jobsAnswer), reads: vi.fn(async () => ({ reads: [READ] })),
    readDetail: vi.fn(async () => DETAIL), stopRead: vi.fn(async () => ({ read: READ })), resumeRead: vi.fn(async () => ({ read: READ })),
    ...over,
  } as unknown as TestsApi;
  return tests;
}

async function show(tests: TestsApi, id: string | null = null) {
  const go = vi.fn();
  render(<JobsView tests={tests} route={{ id }} go={go} />);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  return go;
}

afterEach(cleanup);

describe("Jobs", () => {
  it("lists tests and readings together, the running first, each opening to what is inside it", async () => {
    const go = await show(fake());
    const rows = [...screen.getByRole("table", { name: "Jobs" }).querySelectorAll("tbody tr")];
    expect(rows).toHaveLength(jobsAnswer.jobs.length + 1);
    const first = [...rows[0].querySelectorAll("td")].map((td) => td.textContent);
    expect(first.slice(1)).toEqual(["Reading instructions", "3 sources", "1 of 4 sections · 1 failed", "running"]);
    expect(rows.slice(1).every((tr) => tr.querySelectorAll("td")[1].textContent === "Test")).toBe(true);
    fireEvent.click(rows[0]);
    expect(go).toHaveBeenCalledWith(`#/jobs/${READ.read_id}`);
    fireEvent.click(rows[1]);
    expect(go).toHaveBeenLastCalledWith(expect.stringMatching(/^#\/results\//)); // a test opens in Tests › Results
  });

  it("opens a reading to its sections, and stops it", async () => {
    const tests = fake();
    await show(tests, READ.read_id);
    expect(screen.getByLabelText("Summary").textContent).toMatch(/^3 sources · 1 of 4 sections read · 1 reading now · 1 failed · running/);
    const rows = [...screen.getByRole("table", { name: "Sections" }).querySelectorAll("tbody tr")]
      .map((tr) => [...tr.querySelectorAll("td")].map((td) => td.textContent));
    expect(rows).toEqual([["AGENTS.md", "5", "Read", "2 directives"], ["AGENTS.md", "9", "Reading now", ""],
      ["skill sql-query", "1", "Failed", "the call timed out"], ["skill sql-query", "40", "Waiting", ""]]);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Stop" })); });
    expect(tests.stopRead).toHaveBeenCalledWith(READ.read_id);
  });

  it("offers Resume for a stopped reading with sections left", async () => {
    const stopped = { ...DETAIL, standing: "stopped: Resume reads the rest", status: "stopped" as const, runner: { alive: false } };
    const tests = fake({ readDetail: vi.fn(async () => stopped) } as Partial<TestsApi>);
    await show(tests, READ.read_id);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Resume" })); });
    expect(tests.resumeRead).toHaveBeenCalledWith(READ.read_id);
  });
});
