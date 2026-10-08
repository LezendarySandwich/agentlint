// Dismissing a finding and showing it again, and the Dismissed screen, against a fake server (docs/tech_impl/11-ui.md,
// "Views › 3. Findings" and "Views › 8. Dismissed").
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { Dismissed, Report, Scopes, Suppression, Suppressions } from "./contract";
import type { DismissApi } from "./Dismissed";
import { report as written, texts } from "./fixtures/report";
import { Live, type LiveApi } from "./Live";
import { withDismissal } from "./model";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const report: Report = written;
const OPEN = "27eaf196d40eec74"; // "A reference to nothing": shown
const OTHER = "c4e51f17120017c3"; // "Two things, one name": shown
const HIDDEN = "a5f7cf42e5e135f0"; // "Loud instructions": dismissed already
const scopes: Scopes = {
  harnesses: [{ name: "test-harness", label: "Test", version: "2.0" }], harness: "test-harness", default: "/work/project",
  home_dir: "/Users/u", projects: [{ path: "/work/project", sessions: 0, last: null }],
};

const entry = (over: Partial<Suppression> = {}): Suppression => ({
  id: HIDDEN, check: "demo-cost", title: "Loud instructions", at: "2026-10-02T09:00:00.000000+00:00", reason: "on purpose",
  projects: [{ project: "/Users/u/work/a", state: "active" }, { project: "/Users/u/work/b", state: "lapsed" },
             { project: "/srv/c", state: "moot" }],
  ...over,
});

/** A fake server: the report answers at once; each dismissal call recorded, and refused when told to. */
function fakeApi(listed: Suppression[] = [entry()]) {
  const calls: unknown[][] = [];
  let refuse: string | null = null;
  const refusing = () => { if (refuse) { const why = refuse; refuse = null; throw new Error(why); } };
  const dismiss: DismissApi = {
    suppress: vi.fn(async (finding: string, project: string, harness: string | null, reason: string): Promise<Dismissed> => {
      calls.push(["suppress", finding, project, harness, reason]);
      refusing();
      return { suppression: entry({ id: finding, reason }), note: "Dismissed in every project. It stays hidden in each until what it rests on there changes." };
    }),
    unsuppress: vi.fn(async (finding: string) => {
      calls.push(["unsuppress", finding]);
      refusing();
      return { finding_id: finding, was_dismissed: true };
    }),
    suppressions: vi.fn(async (): Promise<Suppressions> => ({ suppressions: listed, home_dir: "/Users/u" })),
  };
  const api: LiveApi = {
    report: vi.fn(async () => report),
    texts: vi.fn(async () => texts),
    scope: vi.fn(async () => scopes),
    sessions: () => ({ list: async () => [], read: async () => { throw new Error("none"); } }),
    records: () => ({ snapshots: async () => [], diff: async () => { throw new Error("none"); }, diagnostics: async () => [] }),
    dismiss,
  };
  return { api, calls, refuseNext: (why: string) => { refuse = why; } };
}

/** The live page, in the project (a reload, or a link), or on the home without one. */
const show = async (api: LiveApi, hash: string, project = true) => {
  window.history.replaceState(null, "", `/?token=t${project ? "&project=%2Fwork%2Fproject" : ""}${hash}`);
  await act(async () => { render(<Live api={api} />); });
};
const press = (key: string, target: Element = document.body) => act(() => { fireEvent.keyDown(target, { key }); });
const detail = () => screen.getByRole("complementary", { name: "Finding" });
const findingsCount = () => screen.getByRole("link", { name: /Findings/ }).querySelector(".count")?.textContent;

beforeEach(() => window.history.replaceState(null, "", "/"));
afterEach(cleanup);

describe("Dismiss, in a finding", () => {
  it("needs a reason, then dismisses it and moves it to Suppressed without reading the project again", async () => {
    const { api, calls } = fakeApi();
    await show(api, `#/findings/${OPEN}`);
    await screen.findByRole("complementary", { name: "Finding" });
    const before = Number(findingsCount());
    expect(within(detail()).queryByRole("button", { name: /copy suppress/ })).toBeNull(); // replaced
    press("s");
    const form = within(detail()).getByRole("form", { name: "Dismiss this finding" });
    const why = within(form).getByRole("textbox", { name: "Why dismiss it" });
    expect(document.activeElement).toBe(why);
    const submit = within(form).getByRole("button", { name: "Dismiss" });
    expect(submit.hasAttribute("disabled")).toBe(true);
    fireEvent.change(why, { target: { value: "   " } });
    expect(submit.hasAttribute("disabled")).toBe(true);
    fireEvent.submit(form); // Enter with nothing said: nothing sent
    expect(calls).toEqual([]);
    fireEvent.change(why, { target: { value: "  known, on purpose " } });
    expect(submit.hasAttribute("disabled")).toBe(false);
    await act(async () => { fireEvent.click(submit); });
    expect(calls).toEqual([["suppress", OPEN, "/work/project", "test-harness", "known, on purpose"]]);
    await waitFor(() => expect(detail().querySelector(".detail-meta")?.textContent).toContain("· suppressed"));
    expect(within(detail()).queryByRole("form")).toBeNull();
    expect(within(detail()).getByRole("button", { name: "Show again" })).toBeTruthy();
    expect(within(detail()).getByRole("status").textContent).toContain("Dismissed in every project");
    expect(Number(findingsCount())).toBe(before - 1); // the list moved it
    expect(screen.getByRole("button", { name: /Suppressed/ }).textContent).toContain("2");
    expect(api.report).toHaveBeenCalledTimes(1); // no reading again
    expect(api.texts).toHaveBeenCalledTimes(1); // nor its texts
  });

  it("shows the server's refusal in place and moves nothing", async () => {
    const { api, refuseNext } = fakeApi();
    await show(api, `#/findings/${OPEN}`);
    await screen.findByRole("complementary", { name: "Finding" });
    fireEvent.click(within(detail()).getByRole("button", { name: "s Dismiss" }));
    fireEvent.change(within(detail()).getByRole("textbox", { name: "Why dismiss it" }), { target: { value: "noise" } });
    refuseNext("27eaf196d40eec74 isn't found in /work/project now, so nothing was dismissed");
    await act(async () => { fireEvent.submit(within(detail()).getByRole("form")); });
    expect(within(detail()).getByRole("alert").textContent).toBe("Not dismissed: 27eaf196d40eec74 isn't found in /work/project now, so nothing was dismissed");
    expect(within(detail()).getByRole("form")).toBeTruthy(); // still open, the reason kept
    expect((within(detail()).getByRole("textbox") as HTMLInputElement).value).toBe("noise");
    expect(detail().querySelector(".detail-meta")?.textContent).not.toContain("suppressed");
  });

  it("closes the form on Escape, leaving the finding open", async () => {
    const { api } = fakeApi();
    await show(api, `#/findings/${OPEN}`);
    await screen.findByRole("complementary", { name: "Finding" });
    press("s");
    press("Escape", within(detail()).getByRole("textbox", { name: "Why dismiss it" }));
    expect(within(detail()).queryByRole("form")).toBeNull();
    expect(detail()).toBeTruthy();
  });

  it("shows a dismissed finding again", async () => {
    const { api, calls } = fakeApi();
    await show(api, `#/findings/${HIDDEN}`);
    await screen.findByRole("complementary", { name: "Finding" });
    const before = Number(findingsCount());
    expect(within(detail()).queryByRole("button", { name: /Dismiss/ })).toBeNull();
    press("s"); // nothing to dismiss
    expect(within(detail()).queryByRole("form")).toBeNull();
    await act(async () => { fireEvent.click(within(detail()).getByRole("button", { name: "Show again" })); });
    expect(calls).toEqual([["unsuppress", HIDDEN]]);
    await waitFor(() => expect(detail().querySelector(".detail-meta")?.textContent).not.toContain("suppressed"));
    expect(within(detail()).getByRole("button", { name: "s Dismiss" })).toBeTruthy();
    expect(Number(findingsCount())).toBe(before + 1);
  });

  it("says why it couldn't be shown again", async () => {
    const { api, refuseNext } = fakeApi();
    await show(api, `#/findings/${HIDDEN}`);
    await screen.findByRole("complementary", { name: "Finding" });
    refuseNext("the agentlint database: locked");
    await act(async () => { fireEvent.click(within(detail()).getByRole("button", { name: "Show again" })); });
    expect(within(detail()).getByRole("alert").textContent).toBe("Not shown again: the agentlint database: locked");
    expect(detail().querySelector(".detail-meta")?.textContent).toContain("· suppressed");
  });

  /** A promise settled when the test says, for an answer that lands after you have moved on. */
  const deferred = <T,>() => {
    let resolve!: (value: T) => void, reject!: (error: Error) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
  };
  const moveTo = (id: string) => act(async () => {
    window.location.hash = `#/findings/${id}`;
    await new Promise((settled) => setTimeout(settled, 0));
  });
  const dismissing = async (api: LiveApi) => {
    await show(api, `#/findings/${OPEN}`);
    await screen.findByRole("complementary", { name: "Finding" });
    press("s");
    fireEvent.change(within(detail()).getByRole("textbox", { name: "Why dismiss it" }), { target: { value: "noise" } });
    await act(async () => { fireEvent.submit(within(detail()).getByRole("form")); });
    await moveTo(OTHER);
    expect(detail().querySelector("h2")?.textContent).toBe("Two things, one name");
  };

  it("drops a refusal that lands after you moved to another finding", async () => {
    const { api } = fakeApi();
    const answer = deferred<Dismissed>();
    api.dismiss!.suppress = vi.fn(() => answer.promise);
    await dismissing(api);
    await act(async () => { answer.reject(new Error("the agentlint database: locked")); });
    expect(within(detail()).queryByRole("alert")).toBeNull(); // not on this finding, nor as "Not shown again"
    expect(within(detail()).getByRole("button", { name: "s Dismiss" }).hasAttribute("disabled")).toBe(false);
  });

  it("says a dismissal that lands after you moved on only in the list, for the finding it was about", async () => {
    const { api } = fakeApi();
    const answer = deferred<Dismissed>();
    api.dismiss!.suppress = vi.fn(() => answer.promise);
    await dismissing(api);
    const before = Number(findingsCount());
    await act(async () => { answer.resolve({ suppression: entry({ id: OPEN }), note: "Dismissed in every project." }); });
    expect(within(detail()).getByRole("status").textContent).toBe(""); // this finding wasn't dismissed
    expect(detail().querySelector(".detail-meta")?.textContent).not.toContain("suppressed");
    expect(Number(findingsCount())).toBe(before - 1); // the one that was moved to Suppressed
    await moveTo(OPEN);
    expect(detail().querySelector(".detail-meta")?.textContent).toContain("· suppressed");
  });

  it("gives focus back to the foot when the form closes and after Dismiss or Show again", async () => {
    const { api } = fakeApi();
    await show(api, `#/findings/${OPEN}`);
    await screen.findByRole("complementary", { name: "Finding" });
    press("s");
    press("Escape", within(detail()).getByRole("textbox", { name: "Why dismiss it" }));
    expect(document.activeElement).toBe(within(detail()).getByRole("button", { name: "s Dismiss" }));
    press("s");
    fireEvent.click(within(detail()).getByRole("button", { name: "Cancel" }));
    expect(document.activeElement).toBe(within(detail()).getByRole("button", { name: "s Dismiss" }));
    press("s");
    fireEvent.change(within(detail()).getByRole("textbox", { name: "Why dismiss it" }), { target: { value: "noise" } });
    await act(async () => { fireEvent.submit(within(detail()).getByRole("form")); });
    await waitFor(() => expect(document.activeElement).toBe(within(detail()).getByRole("button", { name: "Show again" })));
    await act(async () => { fireEvent.click(within(detail()).getByRole("button", { name: "Show again" })); });
    await waitFor(() => expect(document.activeElement).toBe(within(detail()).getByRole("button", { name: "s Dismiss" })));
  });
});

describe("the Dismissed screen", () => {
  it("lists every dismissal: the finding, its id, the date, the reason, and per project what it does", async () => {
    const gone = entry({ id: "0123456789abcdef", check: null, title: null, reason: "gone since", projects: [] });
    const { api } = fakeApi([entry(), gone]);
    await show(api, "#/dismissed");
    const items = await screen.findAllByRole("listitem", { name: /Loud instructions|No longer recorded/ });
    expect(screen.getByRole("heading", { name: "Dismissed" })).toBeTruthy();
    expect(document.querySelector(".settings-line")?.textContent).toContain("2 findings dismissed");
    const [first, second] = items;
    expect(first.querySelector(".dismissal-meta")?.textContent).toMatch(/^Demo cost · a5f7cf42e5e1 · dismissed /);
    expect(first.querySelector(".dismissal-reason")?.textContent).toBe("“on purpose”");
    const where = within(first).getByRole("list", { name: "Where" });
    expect([...where.querySelectorAll("li")].map((li) => li.textContent)).toEqual([
      "~/work/ahidden", "~/work/bshown again: what it rests on changed", "/srv/cno longer found"]);
    expect(within(second).getByText("No longer recorded in any project")).toBeTruthy();
    expect(second.textContent).toContain("Found in no project yet");
    expect(screen.getByRole("link", { name: /Dismissed/ }).getAttribute("aria-current")).toBe("page");
  });

  it("shows one again: gone from the list, and shown in the report in view", async () => {
    const { api, calls } = fakeApi();
    await show(api, "#/dismissed");
    const [item] = await screen.findAllByRole("listitem", { name: /Loud instructions/ });
    await waitFor(() => expect(findingsCount()).toBeTruthy());
    const before = Number(findingsCount());
    await act(async () => { fireEvent.click(within(item).getByRole("button", { name: "Show again" })); });
    expect(calls).toEqual([["unsuppress", HIDDEN]]);
    expect(screen.queryByRole("listitem", { name: /Loud instructions/ })).toBeNull();
    expect(Number(findingsCount())).toBe(before + 1);
  });

  it("keeps a dismissal the server wouldn't show again, and says why", async () => {
    const { api, refuseNext } = fakeApi();
    await show(api, "#/dismissed");
    const [item] = await screen.findAllByRole("listitem", { name: /Loud instructions/ });
    refuseNext("no finding a5f7cf42e5e135f0 is recorded or dismissed");
    await act(async () => { fireEvent.click(within(item).getByRole("button", { name: "Show again" })); });
    expect(within(item).getByRole("alert").textContent).toBe("Not shown again: no finding a5f7cf42e5e135f0 is recorded or dismissed");
    expect(within(item).getByRole("button", { name: "Show again" }).hasAttribute("disabled")).toBe(false);
  });

  it("has no key of its own: it sits under Findings in the sidebar, and Findings links to it", async () => {
    const { api } = fakeApi();
    await show(api, "#/findings");
    const nav = screen.getByRole("navigation", { name: "Views" });
    const names = within(nav).getAllByRole("link").map((link) => link.textContent);
    const at = names.indexOf("Dismissed");
    expect(names[at - 1]).toMatch(/^Findings/); // under it, keyless, like What changed under Configuration
    expect(within(nav).getByRole("link", { name: "Dismissed" }).className).toContain("sub");
    expect(names.some((name) => /^Dismissed\d/.test(name ?? ""))).toBe(false);
    expect(names[names.length - 1]).toBe("Settings9");
    const link = within(document.querySelector(".to-dismissed") as HTMLElement).getByRole("link", { name: "Dismissed" });
    await act(async () => { fireEvent.click(link); });
    expect(window.location.hash).toBe("#/dismissed");
    expect(await screen.findByRole("heading", { name: "Dismissed" })).toBeTruthy();
    press("9"); // 9 is Settings, not Dismissed
    expect(window.location.hash).toBe("#/settings");
  });

  it("opens without a project, from the home's URL, and says when nothing is dismissed", async () => {
    const { api } = fakeApi([]);
    await show(api, "#/dismissed", false);
    expect(await screen.findByText(/Nothing dismissed/)).toBeTruthy();
    expect(screen.queryByRole("listbox")).toBeNull(); // not the home
  });


  it("from the home, drops the default project's early report once a finding is shown again", async () => {
    const { api } = fakeApi();
    let shownAgain = false; // the server's report as it is when asked
    api.report = vi.fn(async () => (shownAgain ? withDismissal(report, HIDDEN, false) : report));
    api.dismiss!.unsuppress = vi.fn(async (finding: string) => { shownAgain = true; return { finding_id: finding, was_dismissed: true }; });
    await show(api, "#/dismissed", false);
    const [item] = await screen.findAllByRole("listitem", { name: /Loud instructions/ });
    expect(api.report).toHaveBeenCalledTimes(1); // the home's early answer, from before
    await act(async () => { fireEvent.click(within(item).getByRole("button", { name: "Show again" })); });
    await act(async () => { window.location.hash = "#/overview"; await new Promise((settled) => setTimeout(settled, 0)); });
    const list = await screen.findByRole("listbox", { name: "Projects, most recent first" });
    await act(async () => { fireEvent.keyDown(list, { key: "Enter" }); });
    await act(async () => { window.location.hash = `#/findings/${HIDDEN}`; await new Promise((settled) => setTimeout(settled, 0)); });
    await screen.findByRole("complementary", { name: "Finding" });
    expect(api.report).toHaveBeenCalledTimes(2); // read as it is now
    expect(detail().querySelector(".detail-meta")?.textContent).not.toContain("suppressed");
    expect(within(detail()).getByRole("button", { name: "s Dismiss" })).toBeTruthy();
  });

  it("sets a lapsed dismissal apart: hidden and no longer found are muted, lapsed in ink", () => {
    const style = document.createElement("style");
    style.textContent = readFileSync(join(__dirname, "styles.css"), "utf8");
    document.head.appendChild(style);
    const state = (className: string) => {
      const span = document.createElement("span");
      span.className = className;
      document.body.appendChild(span);
      return getComputedStyle(span).color;
    };
    try {
      expect(state("dismissal-state muted")).toBe(state("muted"));
      expect(state("dismissal-state")).not.toBe(state("muted"));
    } finally {
      style.remove();
    }
  });

  it("with no server behind the page, says where dismissals live", () => {
    window.history.replaceState(null, "", "/#/dismissed");
    render(<App report={report} loadTexts={async () => texts} />);
    expect(screen.getByText(/Dismissals live in agentlint ui/)).toBeTruthy();
  });
});
