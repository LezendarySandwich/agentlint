// Settings against a fake server: saved on change, the server's refusal shown beside the setting, Forget only after
// the typed word (docs/tech_impl/11-ui.md, "Settings").
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Report, Scopes, SettingEntry, SettingsAnswer } from "./contract";
import { report } from "./fixtures/report";
import { Live, type LiveApi } from "./Live";
import { FirstRun } from "./FirstRun";
import { SettingsView, type ControlApi } from "./Settings";
import { TEST_SETTINGS } from "./fixtures/tests";

export const ANSWER: SettingsAnswer = {
  settings: [
    { key: "harnesses", value: [], default: [], set: false, meaning: "Which harnesses to read.", problem: null, note: null,
      choices: ["claude-code", "codex"] },
    { key: "sessions", value: 10, default: 10, set: false, meaning: "Recent sessions read.", problem: null, note: null, range: [0, 1000] },
    { key: "keep_days", value: 30, default: 183, set: true, meaning: "Everything older is deleted.", problem: null, note: null,
      range: [1, 36500] },
  ],
  attention: [],
  sync: { installed: false, scheduler: "launchd", where: null, time: "13:07", error: null,
          last: { at: "2026-10-04T11:07:00.000000+00:00", ok: true, changed: 1, turns: 12 } },
  archive: { transcripts: 412, bytes_written: 312_400_000, bytes_stored: 61_000_000 },
  store: "/Users/u/.agentlint",
  home_dir: "/Users/u",
  version: "0.0.1",
  first_run: false,
  jobs_running: [],
};

/** A fake server: each write recorded; `refuse` makes the next save fail with the server's words. */
export function fakeControl(answer: SettingsAnswer = ANSWER) {
  const calls: unknown[][] = [];
  let refuse: string | null = null;
  let current = answer;
  const each = (change: (s: SettingEntry) => SettingEntry) =>
    ({ ...current, settings: current.settings.map(change), ...(current.test_settings ? { test_settings: current.test_settings.map(change) } : {}) });
  const control: ControlApi = {
    settings: vi.fn(async () => current),
    save: vi.fn(async (key: string, value: unknown) => {
      calls.push(["save", key, value]);
      if (refuse) { const why = refuse; refuse = null; throw new Error(why); }
      current = each((s) => s.key === key ? { ...s, value: value as number, set: true, problem: null } : s);
      return current;
    }),
    reset: vi.fn(async (key: string) => {
      calls.push(["reset", key]);
      current = each((s) => s.key === key ? { ...s, value: s.default, set: false, problem: null } : s);
      return current;
    }),
    schedule: vi.fn(async (on: boolean) => {
      calls.push(["schedule", on]);
      current = { ...current, sync: { ...current.sync, installed: on, where: on ? "/Users/u/Library/LaunchAgents/dev.agentlint.sync.plist" : null } };
      return current;
    }),
    forget: vi.fn(async (confirm: string) => {
      calls.push(["forget", confirm]);
      return { turns: 1234, transcripts: 412, undeleted: [] as string[], settings: current };
    }),
    welcomed: vi.fn(async () => { calls.push(["welcomed"]); return { first_run: false }; }),
  };
  return { control, calls, refuseNext: (why: string) => { refuse = why; } };
}

const shown = async (control?: ControlApi, at = "") => {
  const view = render(<SettingsView control={control} route={{ params: new URLSearchParams(at) }} />);
  await act(async () => { await Promise.resolve(); });
  return view;
};
const rowOf = (name: string) => screen.getByText(name).closest(".setting") as HTMLElement;

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

const SCOPES: Scopes = {
  harnesses: [{ name: "test-harness", label: "Test", version: "2.0" }], harness: "test-harness", default: "/work/project",
  home_dir: "/Users/u", projects: [{ path: "/work/project", sessions: 0, last: null }],
};

/** The live page with a server that answers at once. */
const live = async (control: ControlApi, url = "/?token=t") => {
  window.history.replaceState(null, "", url);
  const api: LiveApi = {
    report: vi.fn(async () => report as Report), texts: async () => ({}), scope: async () => SCOPES,
    sessions: () => ({ list: async () => [], read: async () => { throw new Error("none"); } }),
    records: () => ({ snapshots: async () => [], diff: async () => { throw new Error("none"); }, diagnostics: async () => [] }),
    control,
  };
  const view = render(<Live api={api} />);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  return { view, api };
};

describe("Settings", () => {
  it("shows every setting with its default, the daily sync and its last result, the archive and the version", async () => {
    await shown(fakeControl().control);
    expect(within(rowOf("Recent sessions read")).getByText("Default")).toBeTruthy();
    expect(within(rowOf("Recent sessions read")).getByText("Recent sessions read. From 0 to 1,000.")).toBeTruthy();
    expect(within(rowOf("Keep for")).getByRole("button", { name: "Reset to 183 days" })).toBeTruthy(); // set: its default named
    expect(screen.getByRole("switch", { name: "Daily sync" }).getAttribute("aria-checked")).toBe("false");
    expect(screen.getByText(/1 transcript archived, 12 turns of usage derived/)).toBeTruthy();
    expect(screen.getByText(/412 transcripts kept in ~\/\.agentlint — 312 MB as written, 61 MB stored/)).toBeTruthy();
    expect(screen.getByText("agentlint 0.0.1")).toBeTruthy();
  });

  it("saves a number when you leave the field or press Enter, never on each keystroke", async () => {
    const { control, calls } = fakeControl();
    await shown(control);
    const field = within(rowOf("Recent sessions read")).getByRole("textbox");
    fireEvent.change(field, { target: { value: "2" } });
    fireEvent.change(field, { target: { value: "25" } });
    expect(calls).toEqual([]);
    await act(async () => { fireEvent.blur(field); });
    expect(calls).toEqual([["save", "sessions", 25]]);
    fireEvent.change(field, { target: { value: "40" } });
    await act(async () => { fireEvent.keyDown(field, { key: "Enter" }); });
    expect(calls).toEqual([["save", "sessions", 25], ["save", "sessions", 40]]);
    await act(async () => { fireEvent.blur(field); }); // unchanged since: nothing sent
    expect(calls.length).toBe(2);
  });

  it("shows the server's refusal beside the setting, and nothing as saved", async () => {
    const { control, calls, refuseNext } = fakeControl();
    await shown(control);
    const field = within(rowOf("Recent sessions read")).getByRole("textbox");
    refuseNext("sessions must be a whole number from 0 to 1000");
    fireEvent.change(field, { target: { value: "5000" } });
    await act(async () => { fireEvent.blur(field); });
    expect(within(rowOf("Recent sessions read")).getByRole("alert").textContent).toBe("Not saved: sessions must be a whole number from 0 to 1000");
    fireEvent.change(field, { target: { value: "ten" } }); // not a number: sent as typed, for the server to judge
    await act(async () => { fireEvent.blur(field); });
    expect(calls.at(-1)).toEqual(["save", "sessions", "ten"]);
    expect(within(rowOf("Recent sessions read")).queryByRole("alert")).toBeNull(); // the fake took it: the refusal goes
  });

  it("resets a setting, ticks harnesses, and switches the daily sync", async () => {
    const { control, calls } = fakeControl();
    await shown(control);
    await act(async () => { fireEvent.click(within(rowOf("Keep for")).getByRole("button", { name: "Reset to 183 days" })); });
    expect(calls).toEqual([["reset", "keep_days"]]);
    await act(async () => { fireEvent.click(within(rowOf("Harnesses")).getByRole("checkbox", { name: "codex" })); });
    expect(calls.at(-1)).toEqual(["save", "harnesses", ["codex"]]);
    await act(async () => { fireEvent.click(screen.getByRole("switch", { name: "Daily sync" })); });
    expect(calls.at(-1)).toEqual(["schedule", true]);
    expect(screen.getByRole("switch", { name: "Daily sync" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText(/launchd runs agentlint sync each day at 13:07 \(~\/Library\/LaunchAgents\/dev\.agentlint\.sync\.plist\)/)).toBeTruthy();
  });

  it("drops a harness name set from the command line that agentlint can't read, as the server refuses one", async () => {
    const withUnknown = { ...ANSWER, settings: ANSWER.settings.map((s) => s.key === "harnesses" ? { ...s, value: ["opencode"], set: true } : s) };
    const { control, calls } = fakeControl(withUnknown);
    await shown(control);
    expect((within(rowOf("Harnesses")).getByRole("checkbox", { name: "opencode" }) as HTMLInputElement).checked).toBe(true);
    await act(async () => { fireEvent.click(within(rowOf("Harnesses")).getByRole("checkbox", { name: "codex" })); });
    expect(calls.at(-1)).toEqual(["save", "harnesses", ["codex"]]);
  });

  it("forgets only after the word is typed, listing what goes and what stays, and shows the result", async () => {
    const { control, calls } = fakeControl();
    await shown(control);
    fireEvent.click(screen.getByRole("button", { name: "Forget everything…" }));
    const dialog = screen.getByRole("dialog", { name: "Forget everything" });
    expect(within(dialog).getByText("all usage history")).toBeTruthy();
    expect(within(dialog).getByText("every test job and its runs")).toBeTruthy();
    expect(within(dialog).getByText("your settings")).toBeTruthy();
    expect(within(dialog).getByText("your dismissals (suppressed findings)")).toBeTruthy();
    const button = within(dialog).getByRole("button", { name: "Forget everything" }) as HTMLButtonElement;
    const field = within(dialog).getByRole("textbox", { name: "Type forget to confirm" });
    expect(button.disabled).toBe(true);
    for (const almost of ["forge", "Forget", "forget ", "yes"]) {
      fireEvent.change(field, { target: { value: almost } });
      expect(button.disabled, almost).toBe(true);
      fireEvent.submit(field.closest("form")!); // Enter in the field: nothing either
    }
    expect(calls).toEqual([]);
    fireEvent.change(field, { target: { value: "forget" } });
    expect(button.disabled).toBe(false);
    await act(async () => { fireEvent.click(button); });
    expect(calls).toEqual([["forget", "forget"]]);
    expect(within(dialog).getByRole("status").textContent).toMatch(/Forgotten: 1,234 turns of usage, 412 archived transcripts/);
    expect(within(dialog).queryByRole("alert")).toBeNull(); // every test run's directory went
  });

  it("names each test run's directory a forget couldn't delete", async () => {
    const { control } = fakeControl();
    control.forget = vi.fn(async () => ({ turns: 0, transcripts: 0, settings: ANSWER,
      undeleted: ["/h/runs/job-1/cmp-0001.baseline.0: PermissionError: denied", "/h/runs/job-1/trash/x: PermissionError: denied"] }));
    await shown(control);
    fireEvent.click(screen.getByRole("button", { name: "Forget everything…" }));
    const dialog = screen.getByRole("dialog", { name: "Forget everything" });
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "forget" } });
    await act(async () => { fireEvent.click(within(dialog).getByRole("button", { name: "Forget everything" })); });
    const alert = within(dialog).getByRole("alert");
    expect(alert.textContent).toMatch(/^2 test runs' directories couldn't be deleted; the next forget or test run tries again:/);
    expect(within(alert).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "/h/runs/job-1/cmp-0001.baseline.0: PermissionError: denied", "/h/runs/job-1/trash/x: PermissionError: denied"]);
  });

  it("won't forget while a test job runs, and shows a refusal from the server", async () => {
    const running = fakeControl({ ...ANSWER, jobs_running: [{ job_id: "j1", pid: 4242 }] });
    await shown(running.control);
    fireEvent.click(screen.getByRole("button", { name: "Forget everything…" }));
    let dialog = screen.getByRole("dialog", { name: "Forget everything" });
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "forget" } });
    expect((within(dialog).getByRole("button", { name: "Forget everything" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(dialog).getByRole("alert").textContent).toBe("Running now: 1 test job (j1). Nothing can be forgotten until no test job runs.");
    cleanup();
    await shown(fakeControl({ ...ANSWER, jobs_running: [{ job_id: "j1", pid: 4242 }, { job_id: "j2", pid: 4343 }] }).control);
    fireEvent.click(screen.getByRole("button", { name: "Forget everything…" }));
    dialog = screen.getByRole("dialog", { name: "Forget everything" });
    expect(within(dialog).getByRole("alert").textContent).toBe("Running now: 2 test jobs (j1, j2). Nothing can be forgotten until no test job runs.");
    cleanup();
    const refusing = fakeControl();
    refusing.control.forget = vi.fn(async () => { throw new Error("nothing deleted: a test job is running — j2 (process 7)"); });
    await shown(refusing.control);
    fireEvent.click(screen.getByRole("button", { name: "Forget everything…" }));
    dialog = screen.getByRole("dialog", { name: "Forget everything" });
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "forget" } });
    await act(async () => { fireEvent.click(within(dialog).getByRole("button", { name: "Forget everything" })); });
    expect(within(dialog).getByRole("alert").textContent).toMatch(/Nothing was deleted: nothing deleted: a test job is running — j2/);
  });

  it("puts focus back on Forget everything… when its dialog closes, however it closes", async () => {
    await shown(fakeControl().control);
    const opener = screen.getByRole("button", { name: "Forget everything…" });
    for (const close of ["Cancel", "Close"]) {
      opener.focus();
      await act(async () => { fireEvent.click(opener); });
      const dialog = screen.getByRole("dialog", { name: "Forget everything" });
      expect(document.activeElement).toBe(within(dialog).getByRole("textbox")); // in the dialog while it is open
      await act(async () => { fireEvent.click(within(dialog).getByRole("button", { name: close })); });
      expect(screen.queryByRole("dialog", { name: "Forget everything" })).toBeNull();
      expect(document.activeElement, close).toBe(opener);
    }
  });

  it("says what needs fixing first, and why a stored value isn't used", async () => {
    const answer: SettingsAnswer = {
      ...ANSWER, attention: ["the stored sessions must be a whole number from 0 to 1000"],
      settings: ANSWER.settings.map((s) => s.key === "sessions" ? { ...s, problem: "the stored sessions must be a whole number from 0 to 1000" } : s),
    };
    await shown(fakeControl(answer).control);
    expect(screen.getAllByRole("alert")[0].textContent).toBe("the stored sessions must be a whole number from 0 to 1000");
    expect(within(rowOf("Recent sessions read")).getByRole("alert").textContent).toMatch(/can't be used, so the default is in effect/);
  });

  it("puts a stored value that can't be used back to its default: Reset, or Enter on the default shown", async () => {
    const unusable = (): SettingsAnswer => ({
      ...ANSWER, attention: ["the stored keep_days must be a whole number from 1 to 36500"],
      settings: ANSWER.settings.map((s) => s.key === "keep_days"
        ? { ...s, value: 183, set: false, problem: "the stored keep_days must be a whole number from 1 to 36500" } : s),
    });
    const resetting = fakeControl(unusable());
    await shown(resetting.control);
    await act(async () => { fireEvent.click(within(rowOf("Keep for")).getByRole("button", { name: "Reset to 183 days" })); });
    expect(resetting.calls).toEqual([["reset", "keep_days"]]); // the server deletes the row
    expect(within(rowOf("Keep for")).queryByRole("alert")).toBeNull();
    expect(within(rowOf("Keep for")).getByText("Default")).toBeTruthy();
    cleanup();
    const keeping = fakeControl(unusable());
    await shown(keeping.control);
    const field = within(rowOf("Keep for")).getByRole("textbox") as HTMLInputElement;
    expect(field.value).toBe("183");
    fireEvent.focus(field);
    await act(async () => { fireEvent.blur(field); }); // tabbing past writes nothing
    expect(keeping.calls).toEqual([]);
    await act(async () => { fireEvent.keyDown(field, { key: "Enter" }); }); // Enter keeps the default shown
    expect(keeping.calls).toEqual([["save", "keep_days", 183]]);
    expect(within(rowOf("Keep for")).queryByRole("alert")).toBeNull();
    await act(async () => { fireEvent.keyDown(field, { key: "Enter" }); }); // usable now: unchanged sends nothing
    expect(keeping.calls.length).toBe(1);
  });

  it("with no server to change them on, says where settings live", async () => {
    await shown(undefined);
    expect(screen.getByText(/Settings live in agentlint ui/)).toBeTruthy();
  });
});

describe("Settings › Tests", () => {
  const withTests = { ...ANSWER, test_settings: TEST_SETTINGS };

  it("lists each test setting with its default, the removed-tools override among them", async () => {
    await shown(fakeControl(withTests).control);
    const section = screen.getByRole("region", { name: "Tests" });
    expect(within(section).getByText(/A test keeps what it started with/)).toBeTruthy();
    expect(within(section).getByText(/the targets you tick on Tests × situations per target × runs per situation/)).toBeTruthy();
    expect(within(rowOf("Situations per target")).getByText("Situations written for each target. From 1 to 10.")).toBeTruthy();
    expect(screen.queryByText(/Max runs per test/)).toBeNull(); // gone: a test's size is what you tick
    expect(within(rowOf("Time limit per run")).getByRole("textbox")).toHaveProperty("value", "300");
    expect(within(rowOf("Model")).getByRole("textbox").getAttribute("placeholder")).toBe("the model your sessions use");
    expect(within(rowOf("Tools removed")).getByRole("textbox")).toHaveProperty("value", "mcp__notes__post");
    expect(within(rowOf("Tools removed")).getByRole("button", { name: "Reset to none" })).toBeTruthy();
    expect(within(rowOf("Tools kept")).getByText("Default")).toBeTruthy();
  });

  it("saves a model as typed, and an emptied model goes back to your sessions'", async () => {
    const { control, calls } = fakeControl(withTests);
    await shown(control);
    const field = within(rowOf("Model")).getByRole("textbox");
    fireEvent.change(field, { target: { value: " some-model " } });
    expect(calls).toEqual([]);
    await act(async () => { fireEvent.keyDown(field, { key: "Enter" }); });
    expect(calls).toEqual([["save", "model", "some-model"]]);
    expect(within(rowOf("Model")).getByRole("button", { name: "Reset to the model your sessions use" })).toBeTruthy();
    fireEvent.change(field, { target: { value: "" } });
    await act(async () => { fireEvent.blur(field); });
    expect(calls).toEqual([["save", "model", "some-model"], ["reset", "model"]]);
    await act(async () => { fireEvent.blur(field); }); // unchanged and unset: nothing sent
    expect(calls.length).toBe(2);
  });

  it("saves a list of tools typed with commas or spaces, and shows the server's refusal", async () => {
    const { control, calls, refuseNext } = fakeControl(withTests);
    await shown(control);
    const field = within(rowOf("Tools kept")).getByRole("textbox");
    fireEvent.change(field, { target: { value: "mcp__a__send, mcp__b__book  mcp__c__x" } });
    await act(async () => { fireEvent.blur(field); });
    expect(calls).toEqual([["save", "keep_tools", ["mcp__a__send", "mcp__b__book", "mcp__c__x"]]]);
    refuseNext("keep_tools must be a list of tool names, as the harness names them (none starting with -)");
    fireEvent.change(field, { target: { value: "--model" } });
    await act(async () => { fireEvent.blur(field); });
    expect(within(rowOf("Tools kept")).getByRole("alert").textContent).toMatch(/^Not saved: keep_tools must be a list of tool names/);
    fireEvent.change(within(rowOf("Tools removed")).getByRole("textbox"), { target: { value: "" } });
    await act(async () => { fireEvent.blur(within(rowOf("Tools removed")).getByRole("textbox")); });
    expect(calls[calls.length - 1]).toEqual(["save", "remove_tools", []]);
  });

  it("isn't shown by a server that sends no test settings", async () => {
    await shown(fakeControl().control);
    expect(screen.queryByRole("region", { name: "Tests" })).toBeNull();
  });
});

describe("Settings in the live page", () => {
  it("is a view in the sidebar, key 9, the last, and a button on the home", async () => {
    await live(fakeControl().control);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Settings" })); }); // the home's
    expect(window.location.hash).toBe("#/settings");
    expect(screen.getByRole("heading", { name: "Settings" })).toBeTruthy();
    const link = screen.getByRole("link", { name: /Settings/ });
    expect(link.getAttribute("aria-current")).toBe("page");
    await act(async () => { fireEvent.click(screen.getByRole("link", { name: /Overview/ })); }); // no project yet: the home
    expect(screen.queryByRole("navigation", { name: "Views" })).toBeNull();
    cleanup();
    await live(fakeControl().control, "/?token=t&project=%2Fwork%2Fproject");
    const nav = screen.getByRole("navigation", { name: "Views" });
    const links = within(nav).getAllByRole("link");
    expect(links[links.length - 1].textContent).toBe("Settings9");
    await act(async () => { fireEvent.keyDown(document.body, { key: "9" }); });
    expect(screen.getByRole("heading", { name: "Settings" })).toBeTruthy();
  });

  it("opens on Settings when a setting needs fixing, wherever the page was opened", async () => {
    const answer = { ...ANSWER, attention: ["Your harnesses setting (codex) names no harness agentlint can read on this machine."] };
    await live(fakeControl(answer).control);
    expect(window.location.hash).toBe("#/settings");
    expect(screen.getAllByRole("alert")[0].textContent).toMatch(/names no harness agentlint can read/);
  });
});

describe("the first-run panel", () => {
  it("says what is read and kept, links to Privacy and Daily sync, and is shown once", async () => {
    const { control, calls } = fakeControl({ ...ANSWER, first_run: true });
    await live(control);
    const panel = screen.getByRole("dialog", { name: "Before you start" });
    expect(panel.textContent).toMatch(/reads your harness's configuration and its transcripts/);
    expect(panel.textContent).toMatch(/in ~\/\.agentlint, readable by you only, for 30 days\. Nothing leaves this machine/);
    expect(within(panel).getByRole("button", { name: "Settings › Privacy" })).toBeTruthy();
    expect(within(panel).getByRole("button", { name: "Settings › Daily sync" })).toBeTruthy();
    await act(async () => { fireEvent.keyDown(document.body, { key: "9" }); }); // the page beneath takes no keys
    expect(window.location.hash).toBe("");
    await act(async () => { fireEvent.click(within(panel).getByRole("button", { name: "Got it" })); });
    expect(screen.queryByRole("dialog", { name: "Before you start" })).toBeNull();
    expect(calls).toEqual([["welcomed"]]); // the server sets the flag: the CLI's notice won't show either
    cleanup();
    await live(fakeControl({ ...ANSWER, first_run: false }).control); // the next time: nothing
    expect(screen.queryByRole("dialog", { name: "Before you start" })).toBeNull();
  });

  it("puts focus back where it was when it closes", async () => {
    const before = document.createElement("button");
    document.body.append(before);
    before.focus();
    const view = render(<FirstRun answer={{ ...ANSWER, first_run: true }} onClose={() => {}} go={() => {}} />);
    expect(document.activeElement?.textContent).toBe("Got it");
    view.unmount(); // closed: the page stops rendering it
    expect(document.activeElement).toBe(before);
    before.remove();
  });

  it("closes, once, on Escape or on a link, which opens that part of Settings", async () => {
    const escaped = fakeControl({ ...ANSWER, first_run: true });
    await live(escaped.control);
    await act(async () => { fireEvent.keyDown(document.body, { key: "Escape" }); });
    expect(screen.queryByRole("dialog", { name: "Before you start" })).toBeNull();
    expect(escaped.calls).toEqual([["welcomed"]]);
    cleanup();
    const linked = fakeControl({ ...ANSWER, first_run: true });
    await live(linked.control);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Settings › Privacy" })); });
    expect(window.location.hash).toBe("#/settings?at=privacy");
    expect(screen.getByRole("heading", { name: "Settings" })).toBeTruthy();
    expect(linked.calls).toEqual([["welcomed"]]);
  });
});
