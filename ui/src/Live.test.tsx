// The live page against a fake server: reading never blanks the page (docs/tech_impl/11-ui.md, "Loading").
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Report, Scopes } from "./contract";
import { report as written, texts } from "./fixtures/report";
import { Live, type LiveApi, type Where } from "./Live";

const report: Report = written;
const NOW = Date.parse("2026-10-03T12:00:00+00:00");
const scopes: Scopes = {
  harnesses: [{ name: "test-harness", label: "Test", version: "2.0" }], harness: "test-harness", default: "/work/project",
  home_dir: "/Users/u",
  projects: [
    { path: "/work/project", sessions: 0, last: null }, // where agentlint ui was started: listed first, with or without sessions
    { path: "/Users/u/projects/recent", sessions: 1, last: "2026-10-03T11:00:00+00:00" },
    { path: "/Users/u/older", sessions: 9, last: "2026-09-21T12:00:00+00:00" },
  ],
};
const other: Scopes = {
  harnesses: [...scopes.harnesses, { name: "other-harness", label: "Other", version: null }], harness: "other-harness",
  default: "/work/project", home_dir: "/Users/u",
  projects: [{ path: "/work/project", sessions: 0, last: null }, { path: "/srv/elsewhere", sessions: 3, last: null }],
};

/** A promise settled from outside, so a test decides when the server answers. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

/** A fake server whose readings answer only when told to. */
function fakeApi(scope: (harness: string | null) => Promise<Scopes> = async () => scopes) {
  const readings: { where: Where; answer: ReturnType<typeof deferred<Report>> }[] = [];
  const api: LiveApi = {
    report: vi.fn((where: Where) => {
      const answer = deferred<Report>();
      readings.push({ where, answer });
      return answer.promise;
    }),
    texts: vi.fn(async () => texts),
    scope: vi.fn(scope),
    sessions: () => ({ list: async () => [], read: async () => { throw new Error("none"); } }),
    records: () => ({ snapshots: async () => [], diff: async () => { throw new Error("none"); }, diagnostics: async () => [] }),
  };
  return { api, readings };
}

/** The page opened in a project (a reload, or a link): no home. */
const show = (api: LiveApi, hash = "") => {
  window.history.replaceState(null, "", `/?token=t&project=%2Fwork%2Fproject${hash}`);
  return render(<Live api={api} />);
};
const press = (key: string) => act(() => { fireEvent.keyDown(document.body, { key }); });
const wait = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const settle = () => act(async () => { await Promise.resolve(); });

beforeEach(() => {
  vi.useFakeTimers({ now: NOW });
  window.history.replaceState(null, "", "/");
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("reading", () => {
  it("draws the frame first: the sidebar, the line and the overview's layout, never a blank page", () => {
    show(fakeApi().api);
    expect(screen.getByRole("navigation", { name: "Views" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Findings/ })).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "Reading your configuration" })).toBeTruthy();
    expect(document.querySelector("main")!.getAttribute("aria-busy")).toBe("true");
    expect(document.querySelector(".overview.skeleton")).toBeTruthy();
    expect(screen.queryByText(/Reading your configuration…/)).toBeNull();
  });

  it("shows the seconds once 10 have passed, and drops them when the report lands", async () => {
    const { api, readings } = fakeApi();
    show(api);
    await wait(9000);
    expect(screen.queryByText(/\d+ s$/)).toBeNull();
    await wait(2000);
    expect(screen.getByText("11 s")).toBeTruthy();
    await act(async () => readings[0].answer.resolve(report));
    expect(screen.queryByText("11 s")).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(document.querySelector(".overview.skeleton")).toBeNull();
    expect(document.querySelector("main")!.getAttribute("aria-busy")).toBe("false");
  });

  it("keeps the view on r, dimmed and busy, until the new report replaces it", async () => {
    const { api, readings } = fakeApi();
    show(api);
    await act(async () => readings[0].answer.resolve(report));
    const view = document.querySelector(".overview")!;
    press("r");
    expect(api.report).toHaveBeenCalledTimes(2);
    expect(document.querySelector(".overview")).toBe(view); // the same element: nothing blanked
    expect(document.querySelector("main")!.classList.contains("dim")).toBe(true);
    expect(document.querySelector("main")!.getAttribute("aria-busy")).toBe("true");
    press("r"); // a reading under way is not started again
    expect(api.report).toHaveBeenCalledTimes(2);
    const newer = { ...report, generated_at: "2026-10-03T10:00:00+00:00" };
    await act(async () => readings[1].answer.resolve(newer));
    expect(document.querySelector("main")!.classList.contains("dim")).toBe(false);
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("shows a failure inside the frame, in the server's words, and r tries again", async () => {
    const { api, readings } = fakeApi();
    show(api);
    await act(async () => readings[0].answer.reject(new Error("not a project agentlint knows: /etc")));
    const alert = screen.getByRole("alert");
    expect(alert.closest("main")).toBeTruthy();
    expect(alert.textContent).toContain("not a project agentlint knows: /etc");
    expect(alert.textContent).toContain("Press r to try again");
    expect(screen.getByRole("navigation", { name: "Views" })).toBeTruthy();
    press("r");
    expect(api.report).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).toBeNull(); // trying again: the line, not the old error
    await act(async () => readings[1].answer.resolve(report));
    expect(document.querySelector(".overview:not(.skeleton)")).toBeTruthy();
  });

  it("keeps the last report, dimmed, under an error from reading it again", async () => {
    const { api, readings } = fakeApi();
    show(api);
    await act(async () => readings[0].answer.resolve(report));
    press("r");
    await act(async () => readings[1].answer.reject(new Error("the reading failed")));
    expect(screen.getByRole("alert").textContent).toContain("the reading failed");
    expect(document.querySelector(".overview:not(.skeleton)")).toBeTruthy();
    expect(document.querySelector("main")!.classList.contains("dim")).toBe(true);
  });

  it("drops a late answer: an older reading never replaces a newer report", async () => {
    const { api, readings } = fakeApi();
    show(api, "#/sessions");
    act(() => { // Forward to another project, before the first has answered
      window.history.pushState(null, "", "/?token=t&project=%2Fwork%2Fother#/sessions");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(readings.map((r) => r.where.project)).toEqual(["/work/project", "/work/other"]);
    const other = { ...report, snapshot: { ...report.snapshot, cwd: "/work/other" } };
    await act(async () => readings[1].answer.resolve(other));
    await act(async () => readings[0].answer.resolve(report)); // the first reading answers last
    await settle();
    expect(api.texts).toHaveBeenLastCalledWith(other);
    expect(api.texts).not.toHaveBeenCalledWith(report);
  });
});

/** The page opened at the address agentlint ui prints: the home. */
const openHome = async (api: LiveApi) => {
  window.history.replaceState(null, "", "/?token=t");
  const view = render(<Live api={api} />);
  await settle();
  return view;
};
const projectList = () => screen.getByRole("listbox", { name: "Projects, most recent first" });
const harnessList = () => screen.getByRole("listbox", { name: "Harness" });
const rows = (list: HTMLElement) => within(list).getAllByRole("option").map((o) => o.textContent);
const currentRow = (list: HTMLElement) => within(list).getAllByRole("option").find((o) => o.getAttribute("aria-selected") === "true")?.textContent;
const key = (target: Element, k: string, over = {}) => act(() => { fireEvent.keyDown(target, { key: k, ...over }); });

describe("the home", () => {
  it("lists the one harness as a list like the projects', never plain text or a disabled control", async () => {
    await openHome(fakeApi().api);
    expect(screen.getByText("agentlint").className).toBe("wordmark");
    const harnesses = harnessList();
    expect(harnesses.tabIndex).toBe(0);
    expect(within(harnesses).getAllByRole("option").map((o) => [o.textContent, o.getAttribute("aria-selected")])).toEqual([["Test2.0", "true"]]);
    expect(harnesses.getAttribute("aria-activedescendant")).toBe(within(harnesses).getByRole("option").id);
    expect(document.querySelector("select, [disabled]")).toBeNull();
    expect(document.activeElement).toBe(projectList()); // focus starts in the project list
    expect(screen.queryByRole("navigation")).toBeNull(); // no frame until a project is chosen
  });

  it("lists projects as served: where it started first, pinned and preselected, then the most recent", async () => {
    await openHome(fakeApi().api);
    expect(rows(projectList())).toEqual(["project/workNo sessions", "recent~/projects1 session1 h", "older~9 sessions12 d"]);
    expect(currentRow(projectList())).toBe("project/workNo sessions");
    const pinned = within(projectList()).getAllByRole("option")[0];
    expect(within(pinned).getByRole("img", { name: "Where agentlint ui was started" })).toBeTruthy();
    expect(within(projectList()).getAllByRole("img")).toHaveLength(1);
  });

  it("writes ~ only for the user's home directory: /Users/Shared and another user's directory stay whole", async () => {
    const shared: Scopes = { ...scopes, projects: [scopes.projects[0], { path: "/Users/Shared/proj", sessions: 2, last: null },
                                                   { path: "/Users/ux/mine", sessions: 1, last: null }] };
    await openHome(fakeApi(async () => shared).api);
    expect(rows(projectList())).toEqual(["project/workNo sessions", "proj/Users/Shared2 sessions", "mine/Users/ux1 session"]);
    fireEvent.change(screen.getByPlaceholderText("Type to filter"), { target: { value: "~/" } });
    expect(within(projectList()).queryAllByRole("option")).toHaveLength(0); // no path here is under ~
  });

  it("moves with the arrows and j/k, Tabs between the lists, and leaves j to a filter being typed in", async () => {
    await openHome(fakeApi().api);
    const list = projectList();
    key(list, "ArrowDown");
    expect(currentRow(list)).toMatch(/^recent/);
    key(list, "j");
    expect(currentRow(list)).toMatch(/^older/);
    key(list, "j"); // the last row stays the last
    expect(currentRow(list)).toMatch(/^older/);
    key(list, "k");
    key(list, "ArrowUp");
    expect(currentRow(list)).toMatch(/^project/);
    key(list, "Tab");
    expect(document.activeElement).toBe(harnessList());
    key(harnessList(), "Tab");
    expect(document.activeElement).toBe(list);
    key(list, "Tab", { shiftKey: true }); // backwards: the filter, between the lists in the natural order (not handled)
    key(harnessList(), "Tab", { shiftKey: true }); // backwards from the first list wraps to the last
    expect(document.activeElement).toBe(list);
    fireEvent.click(screen.getByText("Harness"));
    expect(document.activeElement).toBe(harnessList());
    const filter = screen.getByPlaceholderText("Type to filter");
    key(document.body, "/");
    expect(document.activeElement).toBe(filter);
    key(filter, "j");
    expect(currentRow(list)).toMatch(/^project/);
    fireEvent.change(filter, { target: { value: "old" } });
    expect(rows(projectList())).toEqual(["older~9 sessions12 d"]);
    expect(currentRow(projectList())).toMatch(/^older/); // the first match is the current row
    fireEvent.change(filter, { target: { value: "zzz" } });
    expect(screen.getByText("No project matches")).toBeTruthy();
    key(filter, "Enter"); // nothing to open
    expect(window.location.search).toBe("?token=t");
  });

  it("asks for the default project's report as it appears, and choosing it asks no second time", async () => {
    const { api, readings } = fakeApi();
    await openHome(api);
    expect(api.report).toHaveBeenCalledTimes(1);
    expect(readings[0].where).toEqual({ harness: null, project: null });
    key(projectList(), "Enter");
    expect(api.report).toHaveBeenCalledTimes(1);
    expect(new URLSearchParams(window.location.search).get("project")).toBe("/work/project");
    expect(new URLSearchParams(window.location.search).get("token")).toBe("t");
    expect(window.location.hash).toBe("#/overview");
    expect(screen.getByRole("progressbar")).toBeTruthy(); // the frame, waiting on the answer already asked for
    await act(async () => readings[0].answer.resolve(report));
    expect(document.querySelector(".overview:not(.skeleton)")).toBeTruthy();
  });

  it("opens another project with a click, returns home on Back with that project current, and a reload stays", async () => {
    const { api, readings } = fakeApi();
    await openHome(api);
    const recent = within(projectList()).getAllByRole("option")[1];
    fireEvent.click(recent); // a click makes it current …
    expect(currentRow(projectList())).toMatch(/^recent/);
    fireEvent.click(recent); // … and a click on the current row opens it
    expect(readings.map((r) => r.where)).toEqual([{ harness: null, project: null }, { harness: "test-harness", project: "/Users/u/projects/recent" }]);
    expect(screen.getByRole("navigation", { name: "Views" })).toBeTruthy();
    act(() => {
      window.history.back();
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    await settle();
    expect(window.location.search).toBe("?token=t");
    expect(document.activeElement).toBe(projectList());
    expect(currentRow(projectList())).toMatch(/^recent/); // the project just left
    cleanup();
    show(api); // a reload in a project: the project, not the home
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.getByRole("navigation", { name: "Views" })).toBeTruthy();
  });

  it("lists each harness's own projects, and reads with that harness", async () => {
    const { api, readings } = fakeApi(async (harness) => (harness === "other-harness" ? other : { ...scopes, harnesses: other.harnesses }));
    await openHome(api);
    expect(rows(harnessList())).toEqual(["Test2.0", "Other"]);
    key(harnessList(), "ArrowDown");
    await settle();
    expect(api.scope).toHaveBeenLastCalledWith("other-harness");
    expect(rows(projectList())).toEqual(["project/workNo sessions", "elsewhere/srv3 sessions"]);
    key(projectList(), "j");
    key(projectList(), "Enter");
    expect(readings[readings.length - 1].where).toEqual({ harness: "other-harness", project: "/srv/elsewhere" });
    expect(new URLSearchParams(window.location.search).get("harness")).toBe("other-harness");
  });

  it("says why when the projects can't be listed, and r asks again", async () => {
    let fail = true;
    const { api } = fakeApi(async () => { if (fail) throw new Error("the harness list failed"); return scopes; });
    await openHome(api);
    expect(screen.getByRole("alert").textContent).toContain("the harness list failed");
    expect(screen.getByRole("alert").textContent).toContain("Press r to try again");
    fail = false;
    press("r");
    await settle();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(rows(projectList())).toHaveLength(3);
  });

  it("uses the early answer only for the first project opened: after another, the start project is read again", async () => {
    const { api, readings } = fakeApi();
    await openHome(api);
    key(projectList(), "j");
    key(projectList(), "Enter"); // another project first: the early answer is dropped
    expect(readings[1].where.project).toBe("/Users/u/projects/recent");
    await act(async () => readings[1].answer.resolve(report));
    press("p");
    const list = screen.getByRole("listbox", { name: "Projects, most recent first" });
    act(() => { list.focus(); });
    key(list, "k");
    key(list, "Enter");
    expect(readings.map((r) => r.where)).toEqual([{ harness: null, project: null },
      { harness: "test-harness", project: "/Users/u/projects/recent" }, { harness: "test-harness", project: "/work/project" }]);
  });

  it("opens the keys on ?, and does nothing on a digit", async () => {
    await openHome(fakeApi().api);
    press("2");
    expect(window.location.hash).toBe("");
    press("?");
    expect(screen.getByRole("dialog", { name: "Shortcuts and icons" })).toBeTruthy();
  });
});

describe("switching project", () => {
  it("switches in place on p: the view kept and dimmed, the new project read, a history entry pushed", async () => {
    const { api, readings } = fakeApi();
    const finding = report.check.findings[0].id;
    show(api, `#/findings/${encodeURIComponent(finding)}`);
    await settle();
    await act(async () => readings[0].answer.resolve(report));
    expect(screen.getByRole("complementary")).toBeTruthy();
    const view = document.querySelector(".main > :not(.progress)")!;
    press("p");
    const list = screen.getByRole("listbox", { name: "Projects, most recent first" });
    act(() => { list.focus(); });
    key(list, "j");
    key(list, "Enter");
    expect(readings[1].where).toEqual({ harness: "test-harness", project: "/Users/u/projects/recent" });
    expect(new URLSearchParams(window.location.search).get("project")).toBe("/Users/u/projects/recent");
    expect(window.location.hash).toBe(`#/findings/${encodeURIComponent(finding)}`); // the view and what was open, kept
    expect(document.querySelector(".main > :not(.progress, .elapsed)")).toBe(view); // the old view, dimmed, under the line
    expect(document.querySelector("main")!.classList.contains("dim")).toBe(true);
    expect(document.querySelector(".crumb")!.textContent).toBe("Test/recent"); // the crumb names where you are going
    const moved = { ...report, snapshot: { ...report.snapshot, cwd: "/Users/u/projects/recent" },
                    check: { ...report.check, findings: report.check.findings.slice(1) } };
    await act(async () => readings[1].answer.resolve(moved));
    expect(screen.queryByRole("complementary")).toBeNull(); // the finding isn't in this project: closed, no error
    expect(api.texts).toHaveBeenLastCalledWith(moved);
    act(() => { window.history.back(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(new URLSearchParams(window.location.search).get("project")).toBe("/work/project"); // Back: the project before
    expect(readings[2].where.project).toBe("/work/project");
  });

  it("says why in the switcher when the projects can't be listed, with a way to retry, and r asks again", async () => {
    let fail = true;
    const { api, readings } = fakeApi(async () => { if (fail) throw new Error("server said no"); return scopes; });
    show(api);
    await settle();
    await act(async () => readings[0].answer.resolve(report));
    press("p");
    const pop = () => screen.getByRole("dialog", { name: "Switch project" });
    expect(within(pop()).getByRole("alert").textContent).toContain("server said no");
    expect(within(pop()).queryByText("Listing projects…")).toBeNull();
    await act(async () => { fireEvent.click(within(pop()).getByRole("button", { name: /try again/i })); });
    expect(api.scope).toHaveBeenCalledTimes(2); // asked again, failing again: still said
    expect(within(pop()).getByRole("alert").textContent).toContain("server said no");
    act(() => { fireEvent.keyDown(pop(), { key: "Escape" }); });
    fail = false;
    press("r"); // the report and, while they are failing, the scopes
    await settle();
    expect(api.scope).toHaveBeenCalledTimes(3);
    expect(api.report).toHaveBeenCalledTimes(2);
    press("p");
    expect(within(pop()).queryByRole("alert")).toBeNull();
    expect(within(pop()).getAllByRole("option")).toHaveLength(3);
  });

  it("ignores the foot's 'read … r' button while a reading is under way, as it ignores r", async () => {
    const { api, readings } = fakeApi();
    show(api);
    await settle();
    await act(async () => readings[0].answer.resolve(report));
    const button = () => screen.getByRole("button", { name: /^read / });
    fireEvent.click(button());
    expect(api.report).toHaveBeenCalledTimes(2);
    fireEvent.click(button());
    fireEvent.click(button());
    expect(api.report).toHaveBeenCalledTimes(2);
  });

  it("goes home on 0, and Back returns to the project", async () => {
    const { api, readings } = fakeApi();
    show(api);
    await settle();
    await act(async () => readings[0].answer.resolve(report));
    press("0");
    expect(new URLSearchParams(window.location.search).get("project")).toBeNull();
    expect(currentRow(projectList())).toMatch(/^project/); // the project just left
    act(() => { window.history.back(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(screen.getByRole("navigation", { name: "Views" })).toBeTruthy();
    expect(new URLSearchParams(window.location.search).get("project")).toBe("/work/project");
  });
});
