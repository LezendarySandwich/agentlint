// Findings › Read instructions against a fake server: the sources with how much of each is read, the filter, Select all
// and Clear, a reading of what is selected — only its unread sections — and its progress (12-directives.md).
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DirectiveSource, DirectivesAnswer, ReadEntry } from "./contract";
import { InstructionsView } from "./Instructions";
import type { TestsApi } from "./testsApi";

const source = (over: Partial<DirectiveSource>): DirectiveSource => ({
  id: "s", name: "AGENTS.md · ~/work/project/AGENTS.md", load: "always", authority: "project", package: null,
  sections: 4, read: 0, stale: 0, tokens: 2100, state: "not read", ...over,
});

const SOURCES = [
  source({ id: "agents", stale: 4, state: "stale" }),
  source({ id: "hook", name: "hook: SessionStart · acme@acme-market", package: "acme@acme-market", sections: 1, read: 1, tokens: 0, state: "read" }),
  source({ id: "skill", name: "skill sql-query", load: "on_invoke", package: "data@acme-market", sections: 10, read: 7, tokens: 900, state: "partly read" }),
];

const READING: ReadEntry = {
  read_id: "2026-10-07T163504Z-a6a351", kind: "reading", project: "/work/project", harness: "test-harness", status: "open",
  standing: "running", created: "2026-10-07T16:35:04+00:00", finished: null, sources: 3,
  sections: { planned: 120, pending: 71, reading: 4, read: 43, failed: 2 }, runner: { alive: true }, log_tail: [],
};

function fake(answer: DirectivesAnswer) {
  const read = vi.fn(async (ids: string[]) => ({ project: "/work/project", read: ids.length ? READING : null }));
  const tests = { directives: vi.fn(async () => answer), readDirectives: read } as unknown as TestsApi;
  return { tests, read };
}

async function show(tests: TestsApi) {
  render(<InstructionsView tests={tests} go={() => undefined} />);
  await act(async () => { await Promise.resolve(); });
}

afterEach(cleanup);

describe("Read instructions", () => {
  it("lists each source with how much is read, and reads only what is selected and not read yet", async () => {
    const { tests, read } = fake({ project: "/work/project", version: 2, sources: SOURCES, reading: null });
    await show(tests);
    expect(screen.getByLabelText("Summary").textContent).toBe(
      "3 sources · 8 of 15 sections read · 4 stale: read by an older version of the reader, to read again · reader version 2");
    const rows = [...screen.getByRole("table", { name: "Sources" }).querySelectorAll("tbody tr")]
      .map((tr) => [...tr.querySelectorAll("td")].slice(1).map((td) => td.textContent));
    expect(rows).toEqual([  // every session first, the unread first within it
      ["AGENTS.md · ~/work/project/AGENTS.md", "every session", "0 of 4 · 4 stale", "2,100 tokens"],
      ["hook: SessionStart · acme@acme-market", "every session", "all", "—"],
      ["skill sql-query", "when invoked", "7 of 10", "900 tokens"]]);
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(screen.getByText(/3 sources selected · 7 sections to read, about 3,000 tokens/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Read selected" })); });
    expect(read).toHaveBeenCalledWith(["agents", "hook", "skill"]);
  });

  it("filters, clears, and says when everything selected is read already", async () => {
    const { tests, read } = fake({ project: "/work/project", version: 2, sources: SOURCES, reading: null });
    await show(tests);
    fireEvent.change(screen.getByLabelText("Filter sources"), { target: { value: "SessionStart" } });
    fireEvent.click(screen.getByRole("button", { name: "All 1 shown" }));
    expect(screen.getByText(/1 source selected · 0 sections to read/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Read selected" }));
    expect(screen.getByRole("alert").textContent).toBe("Everything selected is read already: its text hasn't changed since.");
    expect(read).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "None" }));
    expect(screen.getByText("Nothing selected")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Filter sources"), { target: { value: "" } });
    fireEvent.click(within(screen.getByRole("radiogroup", { name: "Loads" })).getByRole("radio", { name: "Loaded when invoked" }));
    expect(screen.getByRole("table", { name: "Sources" }).querySelectorAll("tbody tr")).toHaveLength(1);
  });

  it("blocks Read selected at once, and while a reading runs links to its job", async () => {
    let finish: (value: { project: string; read: ReadEntry | null }) => void = () => undefined;
    const answer: DirectivesAnswer = { project: "/work/project", version: 2, sources: SOURCES, reading: null };
    const tests = { directives: vi.fn(async () => answer),
                    readDirectives: vi.fn(() => new Promise((done) => { finish = done; })) } as unknown as TestsApi;
    await show(tests);
    fireEvent.click(screen.getByRole("checkbox", { name: /Select AGENTS.md/ }));
    fireEvent.click(screen.getByRole("button", { name: "Read selected" }));
    const button = screen.getByRole("button", { name: "Starting…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true); // before the server answers
    answer.reading = READING;
    await act(async () => { finish({ project: "/work/project", read: READING }); await Promise.resolve(); });
    expect((screen.getByRole("button", { name: "Reading…" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByLabelText("Reading").textContent).toMatch(/^Reading now: 43 of 120 sections · 2 failed · open the job/);
    expect(screen.getByRole("link", { name: "see it in Jobs" }).getAttribute("href")).toBe(`#/jobs/${READING.read_id}`);
  });
});
