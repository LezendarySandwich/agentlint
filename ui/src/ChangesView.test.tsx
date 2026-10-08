// Configuration › What changed, on what Python really serves (src/fixtures/report.ts: the stored snapshots and the
// comparison ui/records.py writes): the list, a comparison opened beside it, a chosen one, and what it says when
// there is nothing to compare.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { Report, SnapshotDiff, SnapshotInfo } from "./contract";
import type { RecordSource } from "./data";
import { report as written, snapshotDiff, snapshots, texts } from "./fixtures/report";

const report: Report = written;
const [latest, earlier] = snapshots as SnapshotInfo[];
const compared: SnapshotDiff = snapshotDiff;

beforeEach(() => window.history.replaceState(null, "", "/"));
afterEach(cleanup);

function records(listed: SnapshotInfo[] = [latest, earlier], answer: (from: string, to: string) => Promise<SnapshotDiff> = async () => compared) {
  return { snapshots: vi.fn(async () => listed), diff: vi.fn(answer), diagnostics: vi.fn(async () => []) } satisfies RecordSource;
}

const show = (hash: string, source: RecordSource | undefined = records(), shown: Report = report) => {
  window.history.replaceState(null, "", `/${hash}`);
  return render(<App report={shown} loadTexts={async () => texts} records={source} />);
};

const NOTE = "Not recorded: agentlint ui was started with --sessions 3, which isn't your sessions setting (10), so no " +
  "reading here is stored or compared with stored ones — nothing is added to What changed or to the findings' history.";

describe("What changed", () => {
  it("lives under Configuration in the sidebar, with no key of its own", () => {
    show("#/changes");
    const nav = screen.getByRole("navigation", { name: "Views" });
    const links = within(nav).getAllByRole("link").map((a) => a.textContent);
    const at = links.findIndex((text) => text?.startsWith("What changed"));
    expect(links[at - 1]).toMatch(/^Configuration/);
    expect(links[at]).toBe("What changed"); // no key cap
    expect(within(nav).getByRole("link", { name: "What changed" }).getAttribute("aria-current")).toBe("page");
  });

  it("lists the stored configurations, the most recent first, the one last read marked now", async () => {
    show("#/changes");
    const rows = await screen.findAllByRole("link", { name: /sources/ });
    expect(rows.map((row) => row.querySelector(".sub")?.textContent)).toEqual([
      "8 sources · ≈1,238 always-on tokens · harness 2.0", "8 sources · ≈1,214 always-on tokens · harness 2.0"]);
    expect(rows[0].textContent).toContain(" · now"); // the report's own fingerprint
    expect(rows[1].textContent).not.toContain(" · now");
    expect(rows[0].textContent).toContain("+24"); // always-on tokens since the one before
    expect(rows[0].textContent).toContain(latest.fingerprint.slice(7, 19));
  });

  it("opens a configuration to what changed from the one before, each source still configured linking to Configuration", async () => {
    const source = records();
    show(`#/changes/${encodeURIComponent(latest.fingerprint)}`, source);
    const detail = await screen.findByRole("complementary", { name: "What changed" });
    await within(detail).findByText("Changed");
    expect(source.diff).toHaveBeenCalledWith(earlier.fingerprint, latest.fingerprint);
    expect(within(detail).getByText(/≈1,214 → ≈1,238 tokens \(\+24\)/)).toBeTruthy();
    expect(within(detail).getByText(/Of that, \+7 is text seen only then or only now/)).toBeTruthy();
    for (const heading of ["Changed", "Added", "Removed", "Delivered text changed", "Untraced text", "Defined now, not then"])
      expect(within(detail).getByText(heading)).toBeTruthy();
    const rules = within(detail).getByRole("link", { name: "instructions file · /home/u/rules" });
    expect(rules.getAttribute("href")).toBe("#/config/source/rules");
    expect(within(detail).queryByRole("link", { name: /old-notes/ })).toBeNull(); // removed: no entry to open
    expect(within(detail).getByText("instructions file · /home/u/old-notes").closest("li")?.textContent).toContain("−4");
    expect(within(detail).queryByRole("link", { name: /not traced/ })).toBeNull(); // untraced text is named by its hash
    expect(within(detail).getByText("skill listing · notes-pkg").closest("li")?.textContent).toMatch(/\?.*newly observed/);
    fireEvent.click(rules);
    expect(window.location.hash).toBe("#/config/source/rules");
  });

  it("compares with a configuration you choose", async () => {
    const third: SnapshotInfo = { ...earlier, fingerprint: "sha256:" + "e".repeat(64), taken_at: "2026-09-20T09:00:00+00:00" };
    const source = records([latest, earlier, third]);
    show(`#/changes/${encodeURIComponent(latest.fingerprint)}`, source);
    const detail = await screen.findByRole("complementary", { name: "What changed" });
    await within(detail).findByText("Changed");
    fireEvent.click(within(detail).getByRole("button", { name: /Compared with/ }));
    fireEvent.click(await screen.findByRole("option", { name: /eeeeeeeeeeee/ }));
    await waitFor(() => expect(source.diff).toHaveBeenLastCalledWith(third.fingerprint, latest.fingerprint));
    expect(window.location.hash).toBe(`#/changes/${encodeURIComponent(latest.fingerprint)}?from=${encodeURIComponent(third.fingerprint)}`);
  });

  it("compares with the one before when the one chosen is no longer stored", async () => {
    const source = records();
    show(`#/changes/${encodeURIComponent(latest.fingerprint)}?from=sha256%3Agone`, source);
    await screen.findByText("Changed");
    expect(source.diff).toHaveBeenCalledWith(earlier.fingerprint, latest.fingerprint);
  });

  it("says when there is nothing before it, nothing stored, or a format it can't read back", async () => {
    show(`#/changes/${encodeURIComponent(earlier.fingerprint)}`, records([earlier]));
    expect(await screen.findByText(/The only configuration of this project agentlint stored/)).toBeTruthy();
    cleanup();
    show("#/changes", records([]));
    expect(await screen.findByText(/No configuration of this project is stored yet/)).toBeTruthy();
    cleanup();
    const old: SnapshotInfo = { ...earlier, readable: false, sources: null, tokens: null, harness_version: null };
    const source = records([latest, old]);
    show(`#/changes/${encodeURIComponent(old.fingerprint)}`, source);
    expect(await screen.findByText(/stored by an agentlint whose format this one can't read back, so it can't be compared/)).toBeTruthy();
    expect(source.diff).not.toHaveBeenCalled();
  });

  it("never calls the one listed last the first stored: one stored before it may have been read again since", async () => {
    // read A (earlier), then B (latest), then A again: A's row moves up, B is listed last though A was stored first
    const reread: SnapshotInfo = { ...earlier, taken_at: "2026-10-04T09:00:00+00:00" };
    const source = records([reread, latest]);
    show(`#/changes/${encodeURIComponent(latest.fingerprint)}`, source);
    const detail = await screen.findByRole("complementary", { name: "What changed" });
    expect(within(detail).getByText(/No other configuration was last read before this one.*choose one under Compared with/)).toBeTruthy();
    expect(detail.textContent).not.toMatch(/first/);
    expect(source.diff).not.toHaveBeenCalled();
    fireEvent.click(within(detail).getByRole("button", { name: /Compared with/ }));
    fireEvent.click(await screen.findByRole("option", { name: new RegExp(earlier.fingerprint.slice(7, 19)) }));
    await waitFor(() => expect(source.diff).toHaveBeenCalledWith(earlier.fingerprint, latest.fingerprint));
  });

  it("notes untraced text left out only when the server says some was, as show --diff does", async () => {
    show(`#/changes/${encodeURIComponent(latest.fingerprint)}`, records(undefined, async () => ({ ...compared, untraced_left_out: false })));
    let detail = await screen.findByRole("complementary", { name: "What changed" });
    await within(detail).findByText("Changed");
    expect(detail.textContent).not.toContain("Leaves out untraced text");
    cleanup();
    show(`#/changes/${encodeURIComponent(latest.fingerprint)}`, records(undefined, async () => ({ ...compared, untraced_left_out: true })));
    detail = await screen.findByRole("complementary", { name: "What changed" });
    expect(await within(detail).findByText(/Leaves out untraced text: one of the readings had no session from this project/)).toBeTruthy();
  });

  it("shows a comparison that failed in the server's words", async () => {
    show(`#/changes/${encodeURIComponent(latest.fingerprint)}`, records(undefined, async () => { throw new Error("the snapshot … can't be compared"); }));
    expect((await screen.findByRole("alert")).textContent).toContain("can't be compared");
  });

  it("says why a UI that records nothing will store nothing, never that the next reading will be the first", async () => {
    show("#/changes", records([]));
    expect((await screen.findByText(/No configuration of this project is stored yet/)).textContent).toMatch(/the next reading will be the first/);
    cleanup();
    show("#/changes", records([]), { ...report, not_recorded: NOTE });
    const empty = await screen.findByText(/No configuration of this project is stored yet/);
    expect(empty.textContent).toContain(NOTE);
    expect(empty.textContent).not.toMatch(/next reading/);
    cleanup();
    show("#/changes", records([latest]), { ...report, not_recorded: NOTE }); // one stored before: no promise of a next one
    expect(await screen.findByText(NOTE)).toBeTruthy();
    expect(screen.queryByText(/the next change will have something to compare with/)).toBeNull();
    cleanup();
    show("#/changes", records([latest])); // recorded: the next change will be stored
    expect(await screen.findByText(/the next change will have something to compare with/)).toBeTruthy();
    expect(screen.queryByText(/Not recorded/)).toBeNull();
  });

  it("moves with j and k, opens with Enter and closes with Esc", async () => {
    show("#/changes");
    await screen.findAllByRole("link", { name: /sources/ });
    const press = (key: string) => act(() => { fireEvent.keyDown(document.body, { key }); });
    press("j");
    press("Enter");
    expect(window.location.hash).toBe(`#/changes/${encodeURIComponent(earlier.fingerprint)}`);
    await screen.findByRole("complementary", { name: "What changed" });
    press("Escape");
    expect(window.location.hash).toBe("#/changes");
  });
});
