// Tests against a fake server: the plan, situations written and reviewed, Start with its one-time onboarding, a job's
// progress asked every few seconds, Stop / Resume / Salvage, and each job's results (docs/tech_impl/11-ui.md, "Tests").
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type {
  JobEntry, JobsAnswer, OnboardingAnswer, PlanAnswer, PlanJob, Report, ResultsAnswer, RunResult, Scopes, Situation,
  SituationsAnswer,
} from "./contract";
import { report as written } from "./fixtures/report";
import { effectResultsAnswer, jobsAnswer, resultsAnswer, strengthResultsAnswer } from "./fixtures/results";
import { Live, type LiveApi } from "./Live";
import { TEST_SETTINGS } from "./fixtures/tests";
import { POLL_MS, type TestsApi } from "./testsApi";
import { REFUSED_WORDS, SAVING_WORDS, UNREAD_WORDS } from "./Tests";
import { SIZE_UNREAD, TICKS_REFUSED, TICKS_SAVING } from "./Jobs";

const report: Report = written;
const scopes: Scopes = {
  harnesses: [{ name: "test-harness", label: "Test", version: "2.0" }], harness: "test-harness", default: "/work/project",
  home_dir: "/Users/u", projects: [{ path: "/work/project", sessions: 0, last: null }],
};
const NESTED = "agentlint is running inside a Test session, which can't start the harness for a test; start agentlint ui from a plain terminal";
const T1 = "test-harness:hook:one", T3 = "test-harness:skill:notes";
const T2 = "test-harness:instructions:/work/project/RULES.md#section:/Code style"; // a space, as real target ids hold
const none = { kept: 0, dropped: 0, edited: 0 };

const TOOLS = [
  { name: "mcp__notes__post", removed: true, why: "acts: post" },
  { name: "mcp__notes__read", removed: false, why: "reads only" },
  ...Array.from({ length: 9 }, (_, i) => ({ name: `mcp__mail__send_${i}`, removed: true, why: "acts: send" })),
];

const situation = (id: string, target: string, review: Situation["review"], text: string): Situation => ({
  id, target, text, generated: text, edited: review === "edited", matters: "which_tools",
  matters_meaning: "which tool, skill or agent the agent picks among those that could do the job", matters_thing: "skill:notes",
  review, model: "small-model", created: "2026-10-05T09:00:00+00:00", reviewed: null,
});

const NAMES: Record<string, string> = { [T1]: "hook: SessionStart · one", [T2]: "RULES.md § \"Style\"", [T3]: "skill: notes" };
const NOTHING_TICKED = "no target is ticked: tick the targets to test in the plan";
const nothingKept = (target: string) => `no situation is kept for ${NAMES[target]}: write situations for it and keep some, or untick it`;
const firstLists = (): Record<string, Situation[]> => ({
  [T2]: [situation("aaaaaaaaaaaaaaa1", T2, "kept", "Tidy the imports in a file."), situation("aaaaaaaaaaaaaaa2", T2, "dropped", "Rename a function across the project.")],
});

/** The job the server sizes for these ticks (ui/testing.py's job_plan and job_answer): at most 3 kept situations of each
 * ticked target, in the ranked order, both steps (D9) — 3 runs each, then 3 with and 3 without: up to 9, one run count
 * for every step — every target here removable, 53 s a run. */
const EACH = 3 + 2 * 3;
function sized(ticked: string[], lists: Record<string, Situation[]>): PlanJob {
  const order = [T1, T2, T3].filter((t) => ticked.includes(t));
  const kept = order.map((t) => Math.min(3, (lists[t] ?? []).filter((s) => s.review === "kept" || s.review === "edited").length));
  const situations = kept.reduce((a, b) => a + b, 0);
  return {
    targets: order, situations, runs: situations * EACH, most: order.length * 3 * EACH, situations_per_target: 3,
    runs_per_situation: 3,
    short: order.map((t, i) => ({ id: t, name: NAMES[t], kept: kept[i] })).filter((t) => t.kept < 3), consistency_only: [],
    seconds: situations * EACH * 53, seconds_per_run: 53, seconds_from: "E1's 53 s run",
    blocked: [...(order.length ? [] : [NOTHING_TICKED]), ...order.filter((_, i) => kept[i] === 0).map(nothingKept)],
  };
}

export const PLAN: PlanAnswer = {
  harness: "test-harness", project: "/work/project", phase: "stability",
  counts: { situations_per_target: 3, runs_per_situation: 3 },
  targets: [
    { id: T1, name: NAMES[T1], tokens: 1368, load: "always", situations: none, kept: 0 },
    { id: T2, name: NAMES[T2], tokens: 284, load: "always", situations: { ...none, kept: 1, dropped: 1 }, kept: 1 },
    { id: T3, name: NAMES[T3], tokens: 40, load: "on_invoke", situations: none, kept: 0 },
  ],
  skipped: [{ id: "test-harness:settings:managed", name: "settings file · managed", why: "can't be tested by removal", detail: "your organisation sets it" }],
  ticked: [T2],
  job: sized([T2], firstLists()),
  tools: TOOLS,
};

export const job = (over: Partial<JobEntry> = {}): JobEntry => ({
  job_id: "20261005-0900-abcdef", project: "/work/project", harness: "test-harness", status: "open",
  standing: "running", targets: [{ id: T2, name: NAMES[T2] }], situations: 1,
  screens: { agreed: 0, disagreed: 0, "no action": 0, incomplete: 1, "mixed environment": 0 },
  created: "2026-10-05T09:00:00+00:00", last_progress: null, model: "big-model", harness_version: "2.0", run_ceiling: 18,
  timeout_s: 300, runs: { recorded: 1, planned: 3 }, launched_without_a_run: 0, comparisons: { incomplete: 1 },
  in_flight: null, runner: { alive: true, pid: 4242, stopping: false }, keep_awake: true, removed_tools: ["mcp__notes__post"],
  log: "/Users/u/.agentlint/jobs/20261005-0900-abcdef.log", log_tail: ["run c0:0 answered in 41 s"], ...over,
});

const KEEP_AWAKE = "While a test runs, agentlint keeps this Mac from idle-sleeping (caffeinate -i), and lets it sleep again when the test ends or stops.";

export const ONBOARDING: OnboardingAnswer = {
  accepted: false, blocked: [], runs: 3, situations: 1, seconds: 159, seconds_per_run: 53, seconds_from: "E1's 53 s run",
  job: PLAN.job, model: { value: "big-model", from: "sessions" },
  removed_tools: TOOLS,
  policy: "Test runs follow your organisation's guardrails, as your own sessions do; no permission mode is changed.",
  hooks: "Your hooks run in every test run, as in your own sessions.",
  telemetry: "A test run is a real session of your harness, so the harness's own telemetry may see the situations.",
  keep_awake: KEEP_AWAKE, settings: TEST_SETTINGS,
};

/** A fake server for one project: every call recorded; `refuse` makes the next write fail with the server's words. It
 * keeps the ticks, as the server does, and sizes their job from the situations kept now. */
export function fakeTests(over: { jobs?: JobsAnswer; plan?: PlanAnswer; results?: ResultsAnswer; ticked?: string[] } = {}) {
  const calls: unknown[][] = [];
  let refuse: string | null = null;
  const refusing = () => { if (refuse) { const why = refuse; refuse = null; throw new Error(why); } };
  const lists: Record<string, Situation[]> = firstLists();
  let ticked = over.ticked ?? PLAN.ticked;
  const generating = new Set<string>();
  let jobs: JobsAnswer = over.jobs ?? { jobs: [], accepted: false, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" };
  const tests: TestsApi = {
    plan: vi.fn(async () => { calls.push(["plan"]); return over.plan ?? { ...PLAN, ticked, job: sized(ticked, lists) }; }),
    tick: vi.fn(async (targets: string[]) => {
      calls.push(["tick", targets]);
      refusing();
      ticked = targets;
      return { ticked, job: sized(ticked, lists) };
    }),
    situations: vi.fn(async (target: string): Promise<SituationsAnswer> => ({
      target, generating: generating.has(target), situations: lists[target] ?? [],
      diagnostics: target === T3 ? [{ kind: "generator_empty", message: "The generator kept nothing.", created: null }] : [],
    })),
    generate: vi.fn(async (targets: string[], again: boolean) => {
      calls.push(["generate", targets, again]);
      refusing();
      for (const t of targets) generating.add(t);
      return { generating: targets, project: "/work/project" };
    }),
    review: vi.fn(async (id: string, decision: "keep" | "drop" | "edit", text?: string) => {
      calls.push(["review", id, decision, ...(text === undefined ? [] : [text])]);
      refusing();
      const target = Object.keys(lists).find((t) => lists[t].some((s) => s.id === id))!;
      const was = lists[target].find((s) => s.id === id)!;
      const next: Situation = { ...was, review: decision === "keep" ? "kept" : decision === "drop" ? "dropped" : "edited",
                                text: text ?? was.text, edited: decision === "edit" };
      lists[target] = lists[target].map((s) => (s.id === id ? next : s));
      return text?.includes("RULES.md") ? { ...next, names_its_target: ["RULES.md"] } : next;
    }),
    onboarding: vi.fn(async () => {
      calls.push(["onboarding"]);
      const job = sized(ticked, lists);
      return { ...ONBOARDING, runs: job.runs, situations: job.situations, seconds: job.seconds, job };
    }),
    accept: vi.fn(async () => { calls.push(["accept"]); refusing(); jobs = { ...jobs, accepted: true }; return { accepted: true }; }),
    jobs: vi.fn(async () => jobs),
    start: vi.fn(async (targets: string[]) => {
      calls.push(["start", targets]);
      refusing();
      const made = job();
      jobs = { ...jobs, jobs: [made, ...jobs.jobs] };
      return { job: made };
    }),
    stop: vi.fn(async (id: string) => {
      calls.push(["stop", id]);
      jobs = { ...jobs, jobs: jobs.jobs.map((j) => (j.job_id === id ? { ...j, standing: "stopping", runner: { ...j.runner, stopping: true } } : j)) };
      return { job: jobs.jobs[0] };
    }),
    resume: vi.fn(async (id: string) => {
      calls.push(["resume", id]);
      jobs = { ...jobs, jobs: jobs.jobs.map((j) => (j.job_id === id ? { ...j, standing: "running", runner: { alive: true, pid: 4343, stopping: false } } : j)) };
      return { job: jobs.jobs[0] };
    }),
    salvage: vi.fn(async (id: string) => {
      calls.push(["salvage", id]);
      jobs = { ...jobs, jobs: jobs.jobs.map((j) => (j.job_id === id ? { ...j, status: "salvaged" as const, standing: "closed: what completed is kept" } : j)) };
      return { job: jobs.jobs[0], undeleted: [], comparisons: [] };
    }),
    results: vi.fn(async (id: string) => { calls.push(["results", id]); return over.results!; }),
  };
  return {
    tests, calls, lists, ticked: () => ticked,
    refuseNext: (why: string) => { refuse = why; },
    finishWriting: (target: string, written: Situation[]) => { generating.delete(target); lists[target] = written; },
    setJobs: (next: JobsAnswer) => { jobs = next; },
  };
}

/** Promises settle; the fake answers at once. */
const settle = async () => { for (let i = 0; i < 6; i++) await act(async () => { await Promise.resolve(); }); };

/** The Tests screen in the frame, at `hash`. */
const show = async (tests: TestsApi | undefined, hash = "#/tests") => {
  window.history.replaceState(null, "", `/${hash}`);
  const view = render(<App report={report} loadTexts={async () => ({})} tests={tests} />);
  await settle();
  return view;
};
const section = (name: string) => screen.getByRole("region", { name });
/** Opens a target's folded situations in Tests › Situations: its name is the fold. */
const openTarget = (name: string) => {
  const target = within(section("Situations")).getByRole("listitem", { name });
  fireEvent.click(within(target).getByRole("button", { name }));
  return target;
};
const PAUSED = "paused: Resume goes on where it stopped";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  window.history.replaceState(null, "", "/");
});

describe("Tests in the frame", () => {
  it("is a view in the sidebar, key 8, before Settings on 9", async () => {
    await show(fakeTests().tests, "#/overview");
    const nav = screen.getByRole("navigation", { name: "Views" });
    const names = within(nav).getAllByRole("link").map((link) => link.textContent);
    expect(names.slice(-4)).toEqual(["Tests8", "Results", "Jobs", "Settings9"]); // Results: Tests', with no key; Jobs: its own, no key
    await act(async () => { fireEvent.keyDown(document.body, { key: "8" }); });
    await settle();
    expect(window.location.hash).toBe("#/tests");
    expect(screen.getByRole("heading", { name: "Tests" })).toBeTruthy();
    await act(async () => { fireEvent.keyDown(document.body, { key: "9" }); });
    expect(window.location.hash).toBe("#/settings");
  });

  it("with no server to run anything on, says where tests run", async () => {
    await show(undefined);
    expect(screen.getByText(/Tests run from agentlint ui/)).toBeTruthy();
  });
});

describe("the plan", () => {
  it("lists every testable target in the ranked order, the ticked ones checked, and those not tested with why", async () => {
    await show(fakeTests().tests);
    const plan = section("Plan");
    const rows = within(within(plan).getByRole("list", { name: "Targets" })).getAllByRole("listitem");
    expect(rows.map((row) => row.querySelector(".plan-name")?.textContent)).toEqual([NAMES[T1], NAMES[T2], NAMES[T3]]);
    expect(rows[0].textContent).toMatch(/^1/);
    expect(rows[0].textContent).toMatch(/≈1,368 tokens · every session/);
    expect(rows[0].textContent).toMatch(/none written/);
    expect(rows[1].textContent).toMatch(/1 kept · 1 dropped/);
    expect((screen.getByRole("checkbox", { name: NAMES[T2] }) as HTMLInputElement).checked).toBe(true); // the server's ticks
    expect((screen.getByRole("checkbox", { name: NAMES[T1] }) as HTMLInputElement).checked).toBe(false);
    expect(within(plan).getByRole("status", { name: "Test size" }).textContent).toBe(
      "1 target, 1 situation kept: up to 9 runs, about 8 min. 53 s a run, from E1's 53 s run.");
    expect(plan.textContent).toMatch(/Each situation: 3 runs on your configuration, counting the runs that do what it expects; where at least one does, 3 runs without the target, counted the same\./);
    expect(plan.textContent).toMatch(/Fewer kept than 3: RULES\.md § "Style" \(1\)\./);
    expect(within(plan).queryByRole("list", { name: "Not tested" })).toBeNull(); // folded
    fireEvent.click(within(plan).getByRole("button", { name: "Not tested — 1 target" }));
    expect(within(plan).getByRole("list", { name: "Not tested" }).textContent).toMatch(/settings file · managed — can't be tested by removal: your organisation sets it/);
    expect(plan.textContent).not.toMatch(/ranker's order/); // the explanation is the heading's tooltip
    fireEvent.mouseEnter(within(plan).getByText("Plan"));
    expect(screen.getByRole("tooltip").textContent).toMatch(/^Tick the targets to test\. They are in the ranker's order/);
    expect(plan.textContent).not.toMatch(/max runs per test/i);
  });

  it("changes the total as you tick, sending the ticks to be kept, and says why Start can't run them", async () => {
    const fake = fakeTests({ jobs: { jobs: [], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" } });
    await show(fake.tests);
    const size = () => within(section("Plan")).getByRole("status", { name: "Test size" }).textContent;
    const start = () => within(section("Jobs")).getByRole("button", { name: "Start" }) as HTMLButtonElement;
    const reasons = () => within(section("Jobs")).queryByRole("list", { name: "Why Start is disabled" })?.textContent ?? "";
    expect(start().disabled).toBe(false);
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T1] })); });
    await settle();
    expect(fake.calls).toContainEqual(["tick", [T1, T2]]); // in the ranked order
    expect(size()).toMatch(/^2 targets, 1 situation kept: up to 9 runs, about 8 min\./);
    expect(section("Plan").textContent).toMatch(/Fewer kept than 3: hook: SessionStart · one \(none\), RULES\.md § "Style" \(1\)\./);
    expect(start().disabled).toBe(true);
    expect(reasons()).toBe(nothingKept(T1));
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T1] })); });
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T2] })); });
    await settle();
    expect(fake.calls.slice(-2)).toEqual([["tick", [T2]], ["tick", []]]);
    expect(size()).toBe("No target is ticked: tick the targets to test.");
    expect(start().disabled).toBe(true);
    expect(reasons()).toBe(NOTHING_TICKED);
  });

  it("sends ticks one at a time and shows only the newest ticks' total, however late an older answer comes", async () => {
    const fake = fakeTests({ jobs: { jobs: [], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" } });
    const answer = fake.tests.tick;
    let release: () => void = () => {};
    const late = new Promise<void>((resolve) => { release = resolve; });
    // The first tick's answer is held back; anything after it would be answered at once.
    (fake.tests.tick as ReturnType<typeof vi.fn>).mockImplementationOnce(async (targets: string[]) => {
      const kept = await answer(targets);
      await late;
      return kept;
    });
    await show(fake.tests);
    const size = () => within(section("Plan")).getByRole("status", { name: "Test size" }).textContent;
    const start = () => within(section("Jobs")).getByRole("button", { name: "Start" }) as HTMLButtonElement;
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T1] })); }); // sends [T1, T2]
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T1] })); }); // then [T2]
    await settle();
    // The second waits for the first to be answered, so the server keeps them in click order.
    expect(fake.calls.filter((c) => c[0] === "tick")).toEqual([["tick", [T1, T2]]]);
    await act(async () => { release(); });
    await settle();
    expect(fake.calls.filter((c) => c[0] === "tick")).toEqual([["tick", [T1, T2]], ["tick", [T2]]]);
    expect(fake.ticked()).toEqual([T2]);
    expect([T1, T2, T3].map((t) => (screen.getByRole("checkbox", { name: NAMES[t] }) as HTMLInputElement).checked)).toEqual([false, true, false]);
    expect(size()).toMatch(/^1 target, 1 situation kept: up to 9 runs, about 8 min\./);
    expect(start().disabled).toBe(false);
    await act(async () => { fireEvent.click(start()); });
    await settle();
    expect(fake.calls).toContainEqual(["start", [T2]]);
  });

  it("while a tick's answer is held, says the ticks are being saved and holds Start disabled; then the new total and state", async () => {
    const fake = fakeTests({ jobs: { jobs: [], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" } });
    fake.lists[T1] = [situation("bbbbbbbbbbbbbbb1", T1, "kept", "Start a session in a new project.")];
    const answer = fake.tests.tick;
    const releases: (() => void)[] = [];
    const held = (targets: string[]) => answer(targets).then(async (kept) => {
      await new Promise<void>((resolve) => { releases.push(resolve); });
      return kept;
    });
    (fake.tests.tick as ReturnType<typeof vi.fn>).mockImplementationOnce(held).mockImplementationOnce(held);
    await show(fake.tests);
    const size = () => within(section("Plan")).getByRole("status", { name: "Test size" }).textContent;
    const start = () => within(section("Jobs")).getByRole("button", { name: "Start" }) as HTMLButtonElement;
    const reasons = () => within(section("Jobs")).queryByRole("list", { name: "Why Start is disabled" })?.textContent ?? "";
    expect(start().disabled).toBe(false);
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T1] })); }); // sends [T1, T2]
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T3] })); }); // then [T1, T2, T3]
    await settle();
    // Nothing answered yet: the size held is the first ticks', so the page says the ticks are being saved instead.
    expect(size()).toBe(SAVING_WORDS);
    expect(section("Plan").textContent).not.toMatch(/Fewer kept than/);
    expect(start().disabled).toBe(true);
    expect(reasons()).toBe(TICKS_SAVING);
    expect(section("Jobs").textContent).not.toMatch(/Start runs/);
    await act(async () => { releases[0](); }); // the first lands; the second is still held
    await settle();
    expect(size()).toBe(SAVING_WORDS);
    expect(start().disabled).toBe(true);
    expect(reasons()).toBe(TICKS_SAVING);
    await act(async () => { releases[1](); });
    await settle();
    expect(size()).toMatch(/^3 targets, 2 situations kept: up to 18 runs, about 16 min\./);
    expect(start().disabled).toBe(true);
    expect(reasons()).toBe(nothingKept(T3)); // the newest ticks' own reason
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T3] })); }); // answered at once
    await settle();
    expect(size()).toMatch(/^2 targets, 2 situations kept: up to 18 runs/);
    expect(start().disabled).toBe(false);
    expect(reasons()).toBe("");
  });

  it("after a refused tick, hides the size and holds Start disabled, saying to tick again, until a later tick is answered", async () => {
    const fake = fakeTests({ jobs: { jobs: [], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" } });
    await show(fake.tests);
    const size = () => within(section("Plan")).getByRole("status", { name: "Test size" }).textContent;
    const start = () => within(section("Jobs")).getByRole("button", { name: "Start" }) as HTMLButtonElement;
    const reasons = () => within(section("Jobs")).queryByRole("list", { name: "Why Start is disabled" })?.textContent ?? "";
    expect(start().disabled).toBe(false);
    fake.refuseNext("database is locked");
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T1] })); }); // [T1, T2], refused
    await settle();
    // The server kept the previous ticks and the page shows others: the size held is the server's, so it isn't shown.
    expect(fake.ticked()).toEqual([T2]);
    expect((screen.getByRole("checkbox", { name: NAMES[T1] }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("The ticks couldn't be kept: database is locked")).toBeTruthy();
    expect(size()).toBe(REFUSED_WORDS);
    expect(section("Plan").textContent).not.toMatch(/Fewer kept than/);
    expect(start().disabled).toBe(true);
    expect(reasons()).toBe(TICKS_REFUSED);
    expect(section("Jobs").textContent).not.toMatch(/Start runs/);
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T3] })); }); // [T1, T2, T3], answered
    await settle();
    expect(fake.ticked()).toEqual([T1, T2, T3]);
    expect(screen.queryByText(/The ticks couldn't be kept/)).toBeNull();
    expect(size()).toMatch(/^3 targets, 1 situation kept: up to 9 runs, about 8 min\./);
    expect(start().disabled).toBe(true);
    expect(reasons()).toBe([nothingKept(T1), nothingKept(T3)].join(""));
  });

  it("a tick answered with no job: the size and Start wait for the plan read again, and say so if it can't be read", async () => {
    const fake = fakeTests({ jobs: { jobs: [], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" } });
    fake.lists[T1] = [situation("bbbbbbbbbbbbbbb1", T1, "kept", "Start a session in a new project.")];
    await show(fake.tests);
    // The fake server's own answers: calling the mocks themselves would use up the answers queued below.
    const answer = (fake.tests.tick as ReturnType<typeof vi.fn>).getMockImplementation() as TestsApi["tick"];
    const read = (fake.tests.plan as ReturnType<typeof vi.fn>).getMockImplementation() as TestsApi["plan"];
    const unread = async (targets: string[]) => ({ ...(await answer(targets)), job: null }); // a server with no reading
    let release: () => void = () => {};
    const late = new Promise<void>((resolve) => { release = resolve; });
    (fake.tests.tick as ReturnType<typeof vi.fn>).mockImplementationOnce(unread).mockImplementationOnce(unread);
    (fake.tests.plan as ReturnType<typeof vi.fn>)
      .mockImplementationOnce(async () => { const plan = await read(); await late; return plan; })
      .mockImplementationOnce(async () => { throw new Error("the project couldn't be read"); });
    const size = () => within(section("Plan")).getByRole("status", { name: "Test size" }).textContent;
    const start = () => within(section("Jobs")).getByRole("button", { name: "Start" }) as HTMLButtonElement;
    const reasons = () => within(section("Jobs")).queryByRole("list", { name: "Why Start is disabled" })?.textContent ?? "";
    expect(start().disabled).toBe(false);
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T3] })); }); // [T2, T3]: no job
    await settle();
    // The tick is answered, but the plan read again for its size is held: the size held is the previous ticks'.
    expect(fake.calls.filter((c) => c[0] === "plan")).toHaveLength(2);
    expect(size()).toBe(SAVING_WORDS);
    expect(start().disabled).toBe(true);
    expect(reasons()).toBe(TICKS_SAVING);
    expect(section("Jobs").textContent).not.toMatch(/Start runs/);
    await act(async () => { release(); });
    await settle();
    expect(size()).toMatch(/^2 targets, 1 situation kept: up to 9 runs, about 8 min\./);
    expect(start().disabled).toBe(true);
    expect(reasons()).toBe(nothingKept(T3));
    // Again, and this time the plan can't be read: the size isn't known, so it isn't shown and Start says to tick again.
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T3] })); }); // [T2]: no job
    await settle();
    expect(fake.ticked()).toEqual([T2]);
    expect(screen.getByText("The plan couldn't be read again: the project couldn't be read")).toBeTruthy();
    expect(size()).toBe(UNREAD_WORDS);
    expect(start().disabled).toBe(true);
    expect(reasons()).toBe(SIZE_UNREAD);
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T1] })); }); // [T1, T2], sized
    await settle();
    expect(size()).toMatch(/^2 targets, 2 situations kept: up to 18 runs/);
    expect(start().disabled).toBe(false);
    expect(reasons()).toBe("");
  });

  it("a later tick answered with its size ends an earlier job-less tick's wait, and that plan read failing changes nothing", async () => {
    const fake = fakeTests({ jobs: { jobs: [], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" } });
    fake.lists[T1] = [situation("bbbbbbbbbbbbbbb1", T1, "kept", "Start a session in a new project.")];
    await show(fake.tests);
    const answer = (fake.tests.tick as ReturnType<typeof vi.fn>).getMockImplementation() as TestsApi["tick"];
    const read = (fake.tests.plan as ReturnType<typeof vi.fn>).getMockImplementation() as TestsApi["plan"];
    const unread = async (targets: string[]) => ({ ...(await answer(targets)), job: null }); // a server with no reading
    let fail: (error: Error) => void = () => {};
    const late = new Promise<never>((_, reject) => { fail = reject; });
    (fake.tests.tick as ReturnType<typeof vi.fn>).mockImplementationOnce(unread);
    (fake.tests.plan as ReturnType<typeof vi.fn>).mockImplementationOnce(async () => { await late; return read(); });
    const size = () => within(section("Plan")).getByRole("status", { name: "Test size" }).textContent;
    const start = () => within(section("Jobs")).getByRole("button", { name: "Start" }) as HTMLButtonElement;
    const reasons = () => within(section("Jobs")).queryByRole("list", { name: "Why Start is disabled" })?.textContent ?? "";
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T3] })); }); // [T2, T3]: no job
    await settle();
    expect(size()).toBe(SAVING_WORDS); // its plan read again is held
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T3] })); }); // [T2], sized
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T1] })); }); // [T1, T2], sized
    await settle();
    expect(fake.ticked()).toEqual([T1, T2]);
    expect(size()).toMatch(/^2 targets, 2 situations kept: up to 18 runs/);
    expect(start().disabled).toBe(false);
    expect(reasons()).toBe("");
    await act(async () => { fail(new Error("the project couldn't be read")); }); // the held read, for older ticks, fails
    await settle();
    expect(size()).toMatch(/^2 targets, 2 situations kept: up to 18 runs/);
    expect(size()).not.toBe(UNREAD_WORDS);
    expect(start().disabled).toBe(false);
    expect(reasons()).toBe("");
  });

  it("ignores an older tick's answer that comes after the newest was sent", async () => {
    const fake = fakeTests({ jobs: { jobs: [], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" } });
    const answer = fake.tests.tick;
    let release: () => void = () => {};
    const late = new Promise<void>((resolve) => { release = resolve; });
    // The first answer is the server's for [T1, T2] and comes once the newest ticks ([T2]) are on the page.
    (fake.tests.tick as ReturnType<typeof vi.fn>).mockImplementationOnce(async (targets: string[]) => {
      const kept = await answer(targets);
      await late;
      return kept;
    });
    await show(fake.tests);
    const size = () => within(section("Plan")).getByRole("status", { name: "Test size" }).textContent;
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T1] })); });
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T1] })); });
    // Before the newest is answered, the older answer arrives: it must not replace the total with two targets'.
    (fake.tests.tick as ReturnType<typeof vi.fn>).mockImplementationOnce(async () => new Promise(() => {}));
    await act(async () => { release(); });
    await settle();
    expect(size()).not.toMatch(/^2 targets/);
    expect(within(section("Jobs")).queryByRole("list", { name: "Why Start is disabled" })?.textContent ?? "").not.toMatch(NAMES[T1]);
  });

  it("says the whole product when every ticked target has enough kept", async () => {
    const fake = fakeTests();
    fake.lists[T2] = ["1", "2", "3"].map((n) => situation(`ccccccccccccccc${n}`, T2, "kept", `Situation ${n} for the rules.`));
    await show(fake.tests);
    expect(within(section("Plan")).getByRole("status", { name: "Test size" }).textContent).toMatch(/^1 target × 3 situations: up to 27 runs, about 24 min\./);
    expect(section("Plan").textContent).not.toMatch(/Fewer kept/);
  });

  it("keeps the ticks over a reload: they are the server's", async () => {
    const fake = fakeTests();
    await show(fake.tests);
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T3] })); });
    await settle();
    cleanup();
    await show(fake.tests);
    expect((screen.getByRole("checkbox", { name: NAMES[T3] }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: NAMES[T2] }) as HTMLInputElement).checked).toBe(true);
  });

  it("writes situations for the ticked targets that have none", async () => {
    await show(fakeTests().tests);
    expect((screen.getByRole("button", { name: "Write situations for 0 ticked targets" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T1] })); });
    expect(screen.getByRole("button", { name: "Write situations for 1 ticked target" })).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T3] })); });
    expect(screen.getByRole("button", { name: "Write situations for 2 ticked targets" })).toBeTruthy();
  });

  it("says why the plan couldn't be read", async () => {
    const { tests } = fakeTests();
    tests.plan = vi.fn(async () => { throw new Error("the agentlint database: locked"); });
    await show(tests);
    expect(screen.getByRole("alert").textContent).toBe("The plan couldn't be read: the agentlint database: locked");
  });
});

describe("situations", () => {
  it("shows each target folded, its situations in review's words; opened, each with what matters, in the order written", async () => {
    await show(fakeTests().tests);
    const situations = section("Situations");
    const folded = within(situations).getByRole("listitem", { name: "RULES.md § \"Style\"" });
    expect(folded.textContent).toMatch(/1 kept · 1 dropped/);
    expect(within(folded).queryAllByRole("listitem")).toEqual([]); // folded: review is the exception
    expect(situations.textContent).not.toMatch(/A model writes/); // the explanation is the heading's tooltip
    const target = openTarget("RULES.md § \"Style\"");
    expect(within(target).getByRole("button", { name: "RULES.md § \"Style\"" }).getAttribute("aria-expanded")).toBe("true");
    const items = within(target).getAllByRole("listitem");
    expect(items[0].textContent).toMatch(/^Tidy the imports in a file\./); // written first: no review state comes first
    expect(items[0].textContent).toMatch(/What matters: which tool, skill or agent the agent picks among those that could do the job: skill:notes · kept/);
    expect(items[1].textContent).toMatch(/Rename a function across the project\..*· dropped/);
    expect(situations.textContent).not.toMatch(/not reviewed|pending/);
  });

  it("keeps, drops and edits one situation, says when an edit names its target, and shows a refusal", async () => {
    const { tests, calls, refuseNext } = fakeTests();
    await show(tests);
    openTarget("RULES.md § \"Style\"");
    const droppedOne = () => screen.getByRole("listitem", { name: "Rename a function across the project." });
    await act(async () => { fireEvent.click(within(droppedOne()).getByRole("button", { name: "Keep" })); });
    expect(calls).toContainEqual(["review", "aaaaaaaaaaaaaaa2", "keep"]);
    expect(within(droppedOne()).getByRole("button", { name: "Keep" }).getAttribute("aria-pressed")).toBe("true");
    expect(section("Plan").textContent).toMatch(/2 kept/); // the plan's counts follow at once
    await settle();
    expect(calls).toContainEqual(["tick", [T2]]); // and the job is sized again, from what is kept now
    expect(within(section("Plan")).getByRole("status", { name: "Test size" }).textContent).toMatch(/1 target, 2 situations kept: up to 18 runs, about 16 min\./);
    await act(async () => { fireEvent.click(within(droppedOne()).getByRole("button", { name: "Drop" })); });
    expect(calls).toContainEqual(["review", "aaaaaaaaaaaaaaa2", "drop"]);
    fireEvent.click(within(droppedOne()).getByRole("button", { name: "Edit…" }));
    const field = screen.getByRole("textbox", { name: "Edit the situation" });
    fireEvent.change(field, { target: { value: "Follow RULES.md when renaming." } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save and keep" })); });
    expect(calls).toContainEqual(["review", "aaaaaaaaaaaaaaa2", "edit", "Follow RULES.md when renaming."]);
    const edited = screen.getByRole("listitem", { name: "Follow RULES.md when renaming." });
    expect(within(edited).getByRole("alert").textContent).toMatch(/This text names what it tests \(RULES\.md\)/);
    refuseNext("this text would be rejected if the generator wrote it: too short");
    fireEvent.click(within(edited).getByRole("button", { name: "Edit…" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Edit the situation" }), { target: { value: "x" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save and keep" })); });
    expect(screen.getByText("Not saved: this text would be rejected if the generator wrote it: too short")).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Edit the situation" })).toBeTruthy(); // still editing: nothing lost
  });

  it("writes situations in the background, asks every few seconds until done, then reads the plan again", async () => {
    vi.useFakeTimers();
    const fake = fakeTests();
    await show(fake.tests);
    expect(fake.calls.filter((c) => c[0] === "plan").length).toBe(1);
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T1] })); });
    await settle();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Write situations for 1 ticked target" })); });
    await settle();
    expect(fake.calls).toContainEqual(["generate", [T1], false]);
    expect(within(section("Situations")).getByText("Writing for 1 target…")).toBeTruthy();
    await act(async () => { vi.advanceTimersByTime(POLL_MS); });
    await settle();
    expect(screen.getByRole("listitem", { name: "hook: SessionStart · one" }).textContent).toMatch(/writing…/);
    fake.finishWriting(T1, [situation("bbbbbbbbbbbbbbb1", T1, "kept", "Start a session in a new project.")]);
    await act(async () => { vi.advanceTimersByTime(POLL_MS); });
    await settle();
    expect(screen.getByRole("listitem", { name: "hook: SessionStart · one" }).textContent).toMatch(/1 kept/);
    openTarget("hook: SessionStart · one");
    expect(screen.getByRole("listitem", { name: "Start a session in a new project." })).toBeTruthy();
    expect(screen.queryByText("Writing for 1 target…")).toBeNull();
    expect(fake.calls.filter((c) => c[0] === "plan").length).toBe(2);
  });

  it("writes situations for every ticked target in one request, more than fifty of them too", async () => {
    // Every target can be ticked (the server holds up to 500), and generate takes as many as can be ticked.
    const many = Array.from({ length: 57 }, (_, i) => `test-harness:instructions:/work/project/CLAUDE.md#section:/Rule ${i}`);
    const targets = many.map((id, i) => ({ id, name: `CLAUDE.md § "Rule ${i}"`, tokens: 40, load: "always" as const, situations: none, kept: 0 }));
    const fake = fakeTests({ plan: { ...PLAN, targets, ticked: many, job: { ...sized([], {}), targets: many, blocked: [] } } });
    await show(fake.tests);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Write situations for 57 ticked targets" })); });
    await settle();
    expect(fake.calls.filter((c) => c[0] === "generate")).toEqual([["generate", many, false]]);
    expect(within(section("Situations")).queryByRole("alert")).toBeNull();
    expect(within(section("Situations")).getByText("Writing for 57 targets…")).toBeTruthy();
  });

  it("writes one target again on request, and shows the server's refusal inside an agent session", async () => {
    const fake = fakeTests();
    await show(fake.tests);
    const target = screen.getByRole("listitem", { name: "RULES.md § \"Style\"" });
    await act(async () => { fireEvent.click(within(target).getByRole("button", { name: "Write again" })); });
    expect(fake.calls).toContainEqual(["generate", [T2], true]);
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: NAMES[T1] })); });
    await settle();
    fake.refuseNext(NESTED);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Write situations for 1 ticked target" })); });
    expect(within(section("Situations")).getByRole("alert").textContent).toBe(`Not started: ${NESTED}`);
  });

  it("says a ticked target has none yet, and what the generator said when it kept nothing", async () => {
    vi.useFakeTimers();
    const fake = fakeTests();
    await show(fake.tests);
    fireEvent.click(screen.getByRole("checkbox", { name: "skill: notes" }));
    const target = () => screen.getByRole("listitem", { name: "skill: notes" });
    expect(target().textContent).toMatch(/Not written yet/);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Write situations for 1 ticked target" })); });
    await settle();
    expect(fake.calls).toContainEqual(["generate", [T3], false]);
    fake.finishWriting(T3, []);
    await act(async () => { vi.advanceTimersByTime(POLL_MS); });
    await settle();
    expect(target().textContent).toMatch(/No situation was kept from the generator for this target/);
    expect(within(target()).getByRole("list", { name: "What the generator said" }).textContent).toBe("Generator empty: The generator kept nothing.");
  });
});

describe("jobs", () => {
  it("disables Start with the server's reason inside an agent session, and with a ticked target with nothing kept", async () => {
    const fake = fakeTests({ jobs: { jobs: [], accepted: true, blocked: [NESTED], keep_awake: KEEP_AWAKE, project: "/work/project" } });
    await show(fake.tests);
    const jobs = section("Jobs");
    expect((within(jobs).getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(jobs).getByRole("list", { name: "Why Start is disabled" }).textContent).toBe(NESTED);
    cleanup();
    await show(fakeTests({ ticked: [T1] }).tests);
    expect((within(section("Jobs")).getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(section("Jobs")).getByRole("list", { name: "Why Start is disabled" }).textContent).toBe(nothingKept(T1));
  });

  it("lists the tools removed from every run and those kept, each with why, once the onboarding is accepted", async () => {
    await show(fakeTests({ jobs: { jobs: [], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" } }).tests);
    const jobs = section("Jobs");
    expect(jobs.textContent).toMatch(/Tools removed from every run: 10 tools; 1 other tool stays/);
    const removed = within(jobs).getByRole("list", { name: "Removed from every run" });
    expect(removed.querySelectorAll("li")).toHaveLength(10);
    expect(removed.textContent).toMatch(/mcp__notes__post — acts: post/);
    expect(within(jobs).getByRole("list", { name: "Kept in every run" }).textContent).toBe("mcp__notes__read — reads only");
    cleanup();
    await show(fakeTests({ plan: { ...PLAN, tools: [] } }).tests);
    expect(section("Jobs").textContent).toMatch(/Tools removed from every run: none/);
    expect(section("Jobs").textContent).toMatch(/No tool was judged/);
  });

  it("says on Start what it runs of the ticked targets, and that the total is its ceiling", async () => {
    await show(fakeTests().tests);
    expect(section("Jobs").textContent).not.toMatch(/Start runs/); // the plan's size line says it: here only on hover
    fireEvent.mouseEnter(within(section("Jobs")).getByRole("button", { name: "Start" }).parentElement!);
    expect(screen.getByRole("tooltip").textContent).toBe("Start runs up to 9 runs, about 8 min: that total is the test's ceiling. The first time, Start says what a test runs and asks once.");
  });

  it("the first time, opens the onboarding with one question; No records nothing, Yes accepts and starts", async () => {
    const fake = fakeTests();
    await show(fake.tests);
    await act(async () => { fireEvent.click(within(section("Jobs")).getByRole("button", { name: "Start" })); });
    await settle();
    let dialog = screen.getByRole("dialog", { name: "Before your first test" });
    expect(dialog.textContent).toMatch(/Start runs the agent on the targets you ticked: 1 target, 1 situation kept: up to 9 runs, about 8 min\. \(53 s a run, from E1's 53 s run\)/);
    expect(dialog.textContent).toMatch(/Each situation: 3 runs on your configuration, counting the runs that do what it expects; where at least one does, 3 runs without the target, counted the same\./);
    expect(dialog.textContent).toMatch(/Fewer kept than 3: RULES\.md § "Style" \(1\)\./);
    expect(dialog.textContent).toMatch(/Every run asks for big-model \(the model your sessions use\)/);
    expect(dialog.textContent).toMatch(/10 tools are removed from every run; 1 other tool stays:/);
    expect(within(dialog).getByRole("list", { name: "Removed from every run" }).children.length).toBe(8);
    fireEvent.click(within(dialog).getByRole("button", { name: "2 more" }));
    expect(within(dialog).getByRole("list", { name: "Removed from every run" }).children.length).toBe(10);
    expect(dialog.textContent).toMatch(/Your hooks run in every test run/);
    expect(dialog.textContent).toMatch(/the harness's own telemetry may see the situations/);
    expect(dialog.textContent).toContain(KEEP_AWAKE);
    expect(within(dialog).getByRole("list", { name: "Test settings" }).textContent).toMatch(/Situations per target: 3 situations \(the default\).*Model: the model your sessions use \(the default\).*Tools removed: mcp__notes__post \(you set it\)/);
    expect(dialog.textContent).toMatch(/Run with these\?/);
    await act(async () => { fireEvent.click(within(dialog).getByRole("button", { name: "No" })); });
    expect(screen.queryByRole("dialog", { name: "Before your first test" })).toBeNull();
    expect(fake.calls.filter((c) => c[0] === "accept" || c[0] === "start")).toEqual([]);
    await act(async () => { fireEvent.click(within(section("Jobs")).getByRole("button", { name: "Start" })); });
    await settle();
    dialog = screen.getByRole("dialog", { name: "Before your first test" });
    await act(async () => { fireEvent.click(within(dialog).getByRole("button", { name: "Yes" })); });
    await settle();
    expect(fake.calls.filter((c) => c[0] === "accept" || c[0] === "start")).toEqual([["accept"], ["start", [T2]]]);
    expect(screen.queryByRole("dialog", { name: "Before your first test" })).toBeNull();
    expect(within(section("Jobs")).getByRole("list", { name: "Jobs" }).textContent).toMatch(/running/);
  });

  it("once accepted, Start starts at once and never shows the onboarding again", async () => {
    const fake = fakeTests({ jobs: { jobs: [], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" } });
    await show(fake.tests);
    await act(async () => { fireEvent.click(within(section("Jobs")).getByRole("button", { name: "Start" })); });
    await settle();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fake.calls.filter((c) => ["onboarding", "accept", "start"].includes(c[0] as string))).toEqual([["start", [T2]]]); // the ticked targets
  });

  it("shows a refusal from Start in place", async () => {
    const fake = fakeTests({ jobs: { jobs: [], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" } });
    await show(fake.tests);
    fake.refuseNext("a test is running; stop it, or wait for it, first");
    await act(async () => { fireEvent.click(within(section("Jobs")).getByRole("button", { name: "Start" })); });
    await settle();
    expect(within(section("Jobs")).getByRole("alert").textContent).toBe("Not started: a test is running; stop it, or wait for it, first");
  });

  it("shows a running job's progress, asked every few seconds, with the keep-awake line; Stop, then Resume and Salvage", async () => {
    vi.useFakeTimers();
    const fake = fakeTests({ jobs: { jobs: [job()], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" } });
    await show(fake.tests);
    const list = () => within(section("Jobs")).getByRole("list", { name: "Jobs" });
    expect(list().textContent).toMatch(/^Test of .* — running/);
    expect(list().textContent).toMatch(/1 of 3 runs recorded/);
    expect(list().textContent).toMatch(/1 situation with too few runs to compare/);
    expect(within(section("Jobs")).getByRole("status").textContent).toBe(KEEP_AWAKE);
    await act(async () => { fireEvent.click(within(list()).getByRole("button", { name: "Stop" })); });
    await settle();
    expect(fake.calls).toContainEqual(["stop", "20261005-0900-abcdef"]);
    expect(list().textContent).toMatch(/— stopping/);
    fake.setJobs({ jobs: [job({ standing: PAUSED, runner: { alive: false, pid: null, stopping: false }, runs: { recorded: 2, planned: 3 } })], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" });
    await act(async () => { vi.advanceTimersByTime(POLL_MS); });
    await settle();
    expect(list().textContent).toMatch(/paused: Resume goes on where it stopped/);
    expect(list().textContent).toMatch(/2 of 3 runs recorded/);
    expect(within(section("Jobs")).queryByRole("status")).toBeNull(); // nothing runs: no keep-awake line
    await act(async () => { fireEvent.click(within(list()).getByRole("button", { name: "Resume" })); });
    await settle();
    expect(fake.calls).toContainEqual(["resume", "20261005-0900-abcdef"]);
    expect(list().textContent).toMatch(/— running/);
    fake.setJobs({ jobs: [job({ standing: PAUSED, runner: { alive: false, pid: null, stopping: false } })], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" });
    await act(async () => { vi.advanceTimersByTime(POLL_MS); });
    await settle();
    fireEvent.click(within(list()).getByRole("button", { name: "Salvage…" }));
    expect(within(list()).getByRole("group", { name: "Salvage" }).textContent).toMatch(/Close this job for good, keeping what completed\?/);
    await act(async () => { fireEvent.click(within(list()).getByRole("button", { name: "Close it" })); });
    await settle();
    expect(fake.calls).toContainEqual(["salvage", "20261005-0900-abcdef"]);
    // Closed, it leaves Tests for Results: Tests shows only the open job.
    expect(within(section("Jobs")).queryByRole("list", { name: "Jobs" })).toBeNull();
    expect(within(section("Jobs")).getByRole("link", { name: "Past tests: Results" }).getAttribute("href")).toBe("#/results");
  });

  it("lists only the open job of the project in view: finished ones, and other projects', are Results'", async () => {
    const done = job({ job_id: "20261004-0900-aaaaaa", status: "complete", standing: "complete", runner: { alive: false, pid: null, stopping: false } });
    const elsewhere = job({ job_id: "20261005-1000-cccccc", project: "/work/other", standing: PAUSED, runner: { alive: false, pid: null, stopping: false } });
    const paused = job({ standing: PAUSED, runner: { alive: false, pid: null, stopping: false } });
    await show(fakeTests({ jobs: { jobs: [elsewhere, paused, done], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" } }).tests);
    const list = within(section("Jobs")).getByRole("list", { name: "Jobs" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(list.textContent).toMatch(/paused: Resume goes on where it stopped/);
    expect(within(section("Jobs")).getByRole("link", { name: "Past tests: Results" })).toBeTruthy();
  });

  it("opens a job's results", async () => {
    const fake = fakeTests({ jobs: { jobs: [job({ status: "complete", runner: { alive: false, pid: null, stopping: false } })], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" },
                             results: { job_id: "20261005-0900-abcdef", project: "/work/project", created: null, status: "complete", fingerprint: "f", situations: [] } });
    await show(fake.tests);
    await act(async () => { fireEvent.click(within(section("Jobs")).getByRole("link", { name: "Past tests: Results" })); });
    await settle();
    expect(window.location.hash).toBe("#/results");
    await act(async () => { fireEvent.click(screen.getByRole("link", { name: /^Oct|^\d/ })); }); // the test's start, its link
    await settle();
    expect(window.location.hash).toBe("#/results/20261005-0900-abcdef");
    expect(fake.calls).toContainEqual(["results", "20261005-0900-abcdef"]);
  });
});

/** `n` runs as the server lists them, each with one action. */
const ran = (n: number): RunResult[] => Array.from({ length: n }, (_, i) => ({
  run: i, outcome: "ok", seconds: 41.2, actions: [{ tool: "Edit", what: "./notes.md · old · new", uses: {} }], more_actions: 0,
}));

const result = (over: Partial<ResultsAnswer["situations"][number]>): ResultsAnswer["situations"][number] => ({
  comparison: "c0", situation: "aaaaaaaaaaaaaaa1", about: T2, name: NAMES[T2], text: "Tidy the imports in a file.",
  matters: "which_tools", matters_meaning: "which tool, skill or agent the agent picks among those that could do the job", matters_thing: null,
  planned: 3, recorded: { "": 3 }, progress: "complete", state: "agreed", runs: ran(3), unscreened: [], outcomes: [], modal_share: 1,
  branches: [{ runs: [0, 1, 2], count: 3, of: 3, used: { tools: ["Edit", "Read"] } }], differing: [], environments: [], ...over,
});

describe("results", () => {
  const RESULTS: ResultsAnswer = {
    job_id: "20261005-0900-abcdef", project: "/work/project", created: "2026-10-05T09:00:00+00:00", status: "complete", fingerprint: "f",
    situations: [
      result({}),
      result({ comparison: "c1", text: "Rename a function across the project.", state: "disagreed", modal_share: 2 / 3,
               branches: [{ runs: [0, 2], count: 2, of: 3, used: { tools: ["Grep", "Edit"], skills: ["refactor"] } },
                          { runs: [1], count: 1, of: 3, used: {} }] }),
      result({ comparison: "c2", text: "Start a session.", about: T1, name: NAMES[T1], state: "incomplete", runs: ran(2),
               unscreened: [{ run: 2, outcome: "timeout", seconds: 300 }], branches: [] }),
      result({ comparison: "c3", text: "Write notes.", about: "gone:target", name: "gone:target", state: "mixed environment", differing: ["harness_version"],
               environments: [{ harness_version: "2.0", models: ["big-model"] }, { harness_version: "2.1", models: [] }], branches: [] }),
      result({ comparison: "c4", text: "Explain the rules.", state: "no action", branches: [{ runs: [0, 1, 2], count: 3, of: 3, used: {} }] }),
    ],
  };
  /** A situation's row in the table: its cells' text, and the button that opens it. */
  const row = (text: string) => {
    const open = within(screen.getByRole("table", { name: "Situations" })).getByRole("button", { name: text });
    const cells = [...open.closest("tr")!.querySelectorAll("td")].map((td) => td.textContent);
    return { open, cells };
  };

  it("shows one test as a table: a row per situation grouped by target, its runs, result and what the runs used — never 'stable'", async () => {
    const fake = fakeTests({ results: RESULTS });
    const view = await show(fake.tests, "#/results/20261005-0900-abcdef");
    expect(screen.getByRole("heading", { name: /^Test of / })).toBeTruthy();
    expect(screen.getByLabelText("Summary").textContent).toBe("3 targets · 5 situations · 15 of 15 runs · 1 agreed · 1 disagreed · 1 no action · 1 incomplete · 1 refused");
    expect(screen.getAllByText(/^Agreed: every run used the same tools, skills and agents/)).toHaveLength(1); // the caveat, once
    const table = screen.getByRole("table", { name: "Situations" });
    expect([...table.querySelectorAll(".group-row th")].map((th) => th.textContent)).toEqual([NAMES[T2], NAMES[T1], "gone:target"]);
    expect(row("Tidy the imports in a file.").cells.slice(1)).toEqual(["3 of 3", "Agreed", "tools Edit, Read", "—"]);
    expect(row("Rename a function across the project.").cells.slice(1)).toEqual(["3 of 3", "Disagreed", "2× tools Grep, Edit · skills refactor; 1× nothing", "—"]);
    expect(row("Start a session.").cells.slice(1)).toEqual(["3 of 3", "Incomplete", "—", "—"]);
    expect(row("Write notes.").cells.slice(1)).toEqual(["3 of 3", "Mixed environments", "differing: harness_version", "—"]);
    expect(row("Explain the rules.").cells.slice(1)).toEqual(["3 of 3", "No action", "nothing", "—"]); // runs that did nothing never agree
    // Agreed and No action both mean the runs matched; each says what it means on hover, and the line above the table
    // says both once.
    fireEvent.mouseEnter(within(row("Explain the rules.").open.closest("tr")!).getByText("No action"));
    expect(screen.getByRole("tooltip").textContent).toBe("No run used any tool, skill or agent: each answered in words alone, so the runs say nothing about the target.");
    fireEvent.mouseLeave(within(row("Explain the rules.").open.closest("tr")!).getByText("No action"));
    fireEvent.mouseEnter(within(row("Tidy the imports in a file.").open.closest("tr")!).getByText("Agreed"));
    expect(screen.getByRole("tooltip").textContent).toMatch(/^Every run used the same tools, skills and agents\./);
    expect(screen.getByText(/^Agreed: every run used the same tools, skills and agents\. No action: no run used any — each answered in words alone\. Neither shows the target does anything\.$/)).toBeTruthy();
    expect(view.container.textContent).not.toMatch(/stable/i);
    expect(view.container.textContent).not.toMatch(/Runs that agree show no inconsistency/); // no sentence repeated per row
  });

  it("opens a row in place: the whole text, what matters, each run numbered from 1 with its actions, and runs it couldn't read", async () => {
    const fake = fakeTests({ results: RESULTS });
    await show(fake.tests, "#/results/20261005-0900-abcdef");
    const { open } = row("Tidy the imports in a file.");
    expect(open.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(open);
    expect(open.getAttribute("aria-expanded")).toBe("true");
    const detail = document.getElementById(open.getAttribute("aria-controls")!)!;
    expect(detail.textContent).toMatch(/What matters: which tool, skill or agent the agent picks among those that could do the job/);
    const runs = within(detail).getByRole("list", { name: "Runs" });
    expect(runs.textContent).toMatch(/^Run 1 · ok · 41 s/);
    expect(within(detail).getByRole("list", { name: "Run 1's actions" }).textContent).toBe("Edit ./notes.md · old · new");
    fireEvent.click(open);
    expect(open.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(row("Start a session.").open);
    expect(screen.getByText(/Run 3 left no readable trace \(timeout\): it isn't compared, and never counts as a run that did nothing\./)).toBeTruthy();
    fireEvent.click(row("Write notes.").open);
    expect(within(screen.getByRole("list", { name: "Environments" })).getAllByRole("listitem").map((li) => li.textContent)).toEqual(
      ["harness 2.0 · big-model", "harness 2.1 · model not recorded"]);
  });

  it("while a test runs, says how far each situation got — never a verdict read from some of its runs", async () => {
    // The user's second real test, 2026-10-06: the with/without runs of situation 2 under way (8 a side), the second
    // target's situations not started. The page said "9 of 18 runs", "Can't measure" and "Waiting", and looked stuck.
    const hook = { about: T1, name: NAMES[T1] }, other = { about: T3, name: NAMES[T3] };
    const effect = (over: Partial<NonNullable<ResultsAnswer["situations"][number]["effect"]>>) => ({
      comparison: "cmp-0005", planned: 10, recorded: { baseline: 0, without: 0 }, progress: "not-attempted", state: "measured" as const,
      measured: "incomplete" as const, reason: "too few runs on a side to compare", runs: { baseline: 0, without: 0 }, ...over });
    const RUNNING: ResultsAnswer = {
      job_id: "20261006-1155-abcdef", project: "/work/project", created: "2026-10-06T11:55:21+00:00", status: "open", fingerprint: "f",
      in_flight: "cmp-0005:baseline:8", runs: { recorded: 25, planned: 128 },
      situations: [
        result({ comparison: "cmp-0001", text: "Organise a new web app.", ...hook, state: "no action", branches: [{ runs: [0, 1, 2], count: 3, of: 3, used: {} }],
                 effect: effect({ comparison: "cmp-0004", state: "skipped" }) }),
        result({ comparison: "cmp-0002", text: "Set up testing.", ...hook, branches: [{ runs: [0, 1, 2], count: 3, of: 3, used: { tools: ["Bash"] } }],
                 effect: effect({ recorded: { baseline: 8, without: 8 }, progress: "partial", measured: "unmeasurable",
                                  reason: "neither a clear change nor the same actions", runs: { baseline: 8, without: 8 } }) }),
        result({ comparison: "cmp-0007", text: "Join two tables.", ...other, recorded: {}, runs: [], state: "incomplete", branches: [],
                 effect: effect({ comparison: "cmp-0010", state: "waiting" }) }),
      ],
      targets: [{ about: T1, name: NAMES[T1], effect: { measured: "unmeasurable", reason: null, situations: { unmeasurable: 1 } } },
                { about: T3, name: NAMES[T3], effect: { measured: "unmeasurable", reason: null, situations: { unmeasurable: 1 } } }],
    };
    const fake = fakeTests({ results: RUNNING });
    await show(fake.tests, "#/results/20261006-1155-abcdef");
    expect(screen.getByLabelText("Summary").textContent).toBe(
      "2 targets · 3 situations · 25 of up to 128 runs · 1 agreed · 1 no action · 1 to come · the test can still take runs: this updates as it goes");
    expect(row("Set up testing.").cells.slice(1)).toEqual(["3 of 3", "Agreed", "tools Bash", "Running · 8 with, 8 without"]);
    expect(row("Organise a new web app.").cells.slice(1)).toEqual(["3 of 3", "No action", "nothing", "Skipped"]);
    expect(row("Join two tables.").cells.slice(1)).toEqual(["0 of 3", "Not run yet", "—", "Queued"]);
    const verdicts = [...screen.getByRole("table", { name: "Situations" }).querySelectorAll(".group-row .target-verdict")];
    expect(verdicts.map((v) => v.textContent)).toEqual(["In progress", "In progress"]); // never "can't measure" mid-run
    expect(screen.getByRole("table", { name: "Situations" }).textContent).not.toMatch(/Can't measure/);
  });

  it("with runs at once, says each situation whose run is under way is running", async () => {
    const fake = fakeTests({ results: { ...RESULTS, status: "open", in_flight: "c0:baseline:0", running: ["c0:baseline:0", "c4:baseline:0"],
      situations: [result({ recorded: {}, runs: [], state: "incomplete", branches: [] }),
                   result({ comparison: "c4", text: "Explain the rules.", recorded: {}, runs: [], state: "incomplete", branches: [] }),
                   result({ comparison: "c9", text: "Join two tables.", recorded: {}, runs: [], state: "incomplete", branches: [] })] } });
    await show(fake.tests, "#/results/20261005-0900-abcdef");
    expect(row("Tidy the imports in a file.").cells[2]).toBe("Running");
    expect(row("Explain the rules.").cells[2]).toBe("Running"); // the second run out at once
    expect(row("Join two tables.").cells[2]).toBe("Not run yet");
  });

  it("says a situation of a test from before 7 Oct whose runs never did what it expects wasn't followed, and why others were skipped", async () => {
    const skipped = (comparison: string, why: string) => ({ comparison, planned: 3, recorded: { baseline: 0, without: 0 },
                                                            progress: "skipped", state: "skipped" as const, why });
    const fake = fakeTests({ results: { ...RESULTS, situations: [
      result({ matters: "tool_present", matters_meaning: "whether the agent does a particular thing at all", matters_thing: "skill:*",
               effect: skipped("c5", "not followed") }),
      result({ comparison: "c4", text: "Explain the rules.", state: "no action", branches: [{ runs: [0, 1, 2], count: 3, of: 3, used: {} }],
               effect: skipped("c6", "no action") }),
    ] } });
    await show(fake.tests, "#/results/20261005-0900-abcdef");
    expect(row("Tidy the imports in a file.").cells[4]).toBe("Not followed");
    fireEvent.mouseEnter(within(row("Tidy the imports in a file.").open.closest("tr")!).getByText("Not followed"));
    expect(screen.getByRole("tooltip").textContent).toMatch(/^None of this situation's runs did skill:\*: the target didn't make the agent do it/);
    fireEvent.mouseLeave(within(row("Tidy the imports in a file.").open.closest("tr")!).getByText("Not followed"));
    expect(row("Explain the rules.").cells[4]).toBe("Skipped");
    fireEvent.click(row("Tidy the imports in a file.").open);
    expect(screen.getByText("What matters: whether the agent does a particular thing at all: skill:*")).toBeTruthy();
  });

  it("says an agreed screen that isn't unanimous: 9 of 10, and the run that differed", async () => {
    const ten = result({
      comparison: "c9", text: "Read the config.", planned: 10, recorded: { "": 10 }, runs: ran(10), modal_share: 0.9,
      branches: [{ runs: [0, 1, 2, 3, 4, 5, 6, 8, 9], count: 9, of: 10, used: { tools: ["Read"] } },
                 { runs: [7], count: 1, of: 10, used: { tools: ["Bash", "Read"] } }],
    });
    const fake = fakeTests({ results: { ...RESULTS, situations: [ten] } });
    await show(fake.tests, "#/results/20261005-0900-abcdef");
    expect(row("Read the config.").cells.slice(1)).toEqual(["10 of 10", "Agreed, 9 of 10", "9× tools Read; 1× tools Bash, Read", "—"]);
    expect(screen.getByRole("table", { name: "Situations" }).textContent).not.toMatch(/all 10/);
  });

  it("asks again every few seconds while the test can take runs, and goes back to the list", async () => {
    vi.useFakeTimers();
    const fake = fakeTests({ results: { ...RESULTS, status: "open" } });
    await show(fake.tests, "#/results/20261005-0900-abcdef");
    expect(screen.getByLabelText("Summary").textContent).toMatch(/the test can still take runs: this updates as it goes$/);
    await act(async () => { vi.advanceTimersByTime(POLL_MS); });
    await settle();
    expect(fake.calls.filter((c) => c[0] === "results").length).toBe(2);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Results" })); });
    await settle();
    expect(window.location.hash).toBe("#/results");
  });

  it("opens a test from a link made before Results had its own page (#/tests/<job>)", async () => {
    const fake = fakeTests({ results: RESULTS });
    await show(fake.tests, "#/tests/20261005-0900-abcdef");
    expect(window.location.hash).toBe("#/results/20261005-0900-abcdef");
    expect(screen.getByRole("table", { name: "Situations" })).toBeTruthy();
  });

  it("lists every test of the project in view, newest first, as a table; a row opens it", async () => {
    const open = job({ standing: PAUSED, runner: { alive: false, pid: null, stopping: false } });
    const done = job({ job_id: "20261004-0900-aaaaaa", status: "complete", standing: "complete", created: "2026-10-04T09:00:00+00:00",
                       targets: [{ id: T1, name: NAMES[T1] }, { id: T2, name: NAMES[T2] }], situations: 6, runs: { recorded: 18, planned: 18 },
                       screens: { agreed: 4, disagreed: 1, "no action": 1, incomplete: 0, "mixed environment": 0 },
                       runner: { alive: false, pid: null, stopping: false } });
    const elsewhere = job({ job_id: "20261005-1000-cccccc", project: "/work/other" });
    const fake = fakeTests({ jobs: { jobs: [elsewhere, open, done], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" },
                             results: RESULTS });
    await show(fake.tests, "#/results");
    const table = screen.getByRole("table", { name: "Tests" });
    const rows = [...table.querySelectorAll("tbody tr")].map((tr) => [...tr.querySelectorAll("td")].map((td) => td.textContent));
    expect(rows).toHaveLength(2); // another project's test isn't this project's
    expect(rows[0].slice(1)).toEqual(["1 target", "1", "1 of 3", "1 incomplete", PAUSED]);
    expect(rows[1].slice(1)).toEqual(["2 targets", "6", "18 of 18", "4 agreed · 1 disagreed · 1 no action", "complete"]);
    await act(async () => { fireEvent.click(table.querySelectorAll("tbody tr")[1].querySelector("td:nth-child(3)")!); });
    await settle();
    expect(window.location.hash).toBe("#/results/20261004-0900-aaaaaa");
  });

  it("says when the project has no test yet", async () => {
    await show(fakeTests({ jobs: { jobs: [], accepted: true, blocked: [], keep_awake: KEEP_AWAKE, project: "/work/project" } }).tests, "#/results");
    expect(screen.getByText("No test has run in this project yet: start one in Tests.")).toBeTruthy();
  });
});

describe("what Python writes (src/fixtures/results.ts, from tests/test_ui_results.py)", () => {
  it("shows a test of both steps as Python writes it: what removing each target did, situation by situation", async () => {
    const fake = fakeTests({ results: effectResultsAnswer, jobs: jobsAnswer });
    await show(fake.tests, `#/results/${effectResultsAnswer.job_id}`);
    const table = screen.getByRole("table", { name: "Situations" });
    const cells = (text: string) => [...within(table).getByRole("button", { name: text }).closest("tr")!.querySelectorAll("td")].map((td) => td.textContent);
    expect(cells("Find the metrics.")[4]).toBe("Changes");
    expect(cells("Plan the pipeline.")[4]).toBe("Skipped"); // its runs disagreed: no with/without runs
    expect(cells("Start a session.")[4]).toBe("Seems to do nothing");
    expect([...table.querySelectorAll(".group-row .target-verdict")].map((v) => v.textContent)).toEqual([
      "Removing it changes what the agent does", "Removing it seems to change nothing"]);
    fireEvent.mouseEnter(within(within(table).getByRole("button", { name: "Find the metrics." }).closest("tr")!).getByText("Changes"));
    expect(screen.getByRole("tooltip").textContent).toBe("Removing the target changed what the agent did (5 with, 5 without): shell:acme — 5 of 5 with, 0 of 5 without.");
    fireEvent.click(within(table).getByRole("button", { name: "Find the metrics." }));
    const changed = screen.getByRole("list", { name: "What changed" });
    expect(within(changed).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "shell:acme — 5 of 5 with, 0 of 5 without", "shell:head — 5 of 5 with, 0 of 5 without"]);
    expect(screen.getByRole("list", { name: "Runs without the target" }).children).toHaveLength(5); // each with its actions
    expect(screen.getByRole("list", { name: "Runs with the target" }).textContent).toMatch(/acme data\.catalog fetch/);
    expect(screen.getByText("Without the target: changes")).toBeTruthy();
    expect(screen.getByRole("table", { name: "Situations" }).textContent).not.toMatch(/works/i); // a change is never "works"
  });

  it("shows a test of expectations as Python writes it: each situation's runs that did what it expects, with and without, and its verdict", async () => {
    const fake = fakeTests({ results: strengthResultsAnswer, jobs: jobsAnswer });
    await show(fake.tests, `#/results/${strengthResultsAnswer.job_id}`);
    const table = screen.getByRole("table", { name: "Situations" });
    expect([...table.querySelectorAll("thead th")].map((th) => th.textContent)).toEqual(["Situation", "Expects", "With it", "Without it", "Verdict"]);
    const cells = (text: string) => [...within(table).getByRole("button", { name: text }).closest("tr")!.querySelectorAll("td")].map((td) => td.textContent).slice(1);
    expect(cells("Find the metrics for the revenue table.")).toEqual(["program:acme", "3 of 3", "0 of 3", "Works"]);
    expect(cells("Write the parser, test first.")).toEqual(["skill:tdd", "0 of 3", "Not run", "Not followed"]); // never run without
    expect(cells("Start in a new repository.")).toEqual(["program:pwd", "2 of 3 · inconsistent", "1 of 3", "Partial · +0.33"]);
    expect(cells("Answer from the notes.")).toEqual(["tool:Read", "3 of 3", "3 of 3", "Adds nothing"]);
    expect([...table.querySelectorAll(".group-row .target-verdict")].map((v) => v.textContent)).toEqual([
      "Not followed · mean strength +0.50", "Adds nothing · mean strength +0.17"]); // the weakest leads
    fireEvent.mouseEnter(within(within(table).getByRole("button", { name: "Write the parser, test first." }).closest("tr")!).getByText("Not followed"));
    expect(screen.getByRole("tooltip").textContent).toBe("No run with the target did skill:tdd; they did shell:ls in 3 runs. A finding: the target didn't make the agent do what it is for.");
    fireEvent.click(within(table).getByRole("button", { name: "Write the parser, test first." }));
    expect(within(screen.getByRole("list", { name: "What the runs did instead" })).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["shell:ls — 3 runs"]);
    expect(screen.getByRole("list", { name: "Runs" }).textContent).toMatch(/didn't do what it expects/);
    fireEvent.click(within(table).getByRole("button", { name: "Find the metrics for the revenue table." }));
    expect(screen.getByRole("list", { name: "Runs without the target" }).children).toHaveLength(3);
  });

  it("shows a test's results as ui/testing.py answers them: a row of every screen state, never 'stable'", async () => {
    const fake = fakeTests({ results: resultsAnswer, jobs: jobsAnswer });
    const view = await show(fake.tests, `#/results/${resultsAnswer.job_id}`);
    const table = screen.getByRole("table", { name: "Situations" });
    for (const result of resultsAnswer.situations) expect(within(table).getByRole("button", { name: result.text })).toBeTruthy();
    const cells = (text: string) => [...within(table).getByRole("button", { name: text }).closest("tr")!.querySelectorAll("td")].map((td) => td.textContent);
    expect(cells("Explain the rules.")[2]).toBe("No action");
    expect(cells("Rename a function.")[2]).toBe("Disagreed");
    fireEvent.click(within(table).getByRole("button", { name: "Start a session." }));
    expect(view.container.textContent).toMatch(/Run 3 left no readable trace \(timeout\)/);
    expect(view.container.textContent).not.toMatch(/stable/i);
  });

  it("lists the tests ui/testing.py answers with: the open one on Tests, every one in Results", async () => {
    const fake = fakeTests({ jobs: { ...jobsAnswer, accepted: true }, results: resultsAnswer });
    await show(fake.tests);
    const jobs = section("Jobs");
    expect(within(within(jobs).getByRole("list", { name: "Jobs" })).getAllByRole("listitem")).toHaveLength(1); // the paused one
    expect(jobs.textContent).toMatch(/paused: Resume goes on where it stopped/);
    expect(jobsAnswer.jobs.map((j) => j.standing)).toEqual(["complete", "complete", "paused: Resume goes on where it stopped", "complete"]);
    cleanup();
    await show(fake.tests, "#/results");
    expect(screen.getByRole("table", { name: "Tests" }).querySelectorAll("tbody tr")).toHaveLength(jobsAnswer.jobs.length);
  });
});

describe("the live page", () => {
  it("asks the server for the project in view", async () => {
    const fake = fakeTests();
    const where: unknown[] = [];
    window.history.replaceState(null, "", "/?token=t&project=%2Fwork%2Fproject#/tests");
    const api: LiveApi = {
      report: vi.fn(async () => report), texts: async () => ({}), scope: async () => scopes,
      sessions: () => ({ list: async () => [], read: async () => { throw new Error("none"); } }),
      records: () => ({ snapshots: async () => [], diff: async () => { throw new Error("none"); }, diagnostics: async () => [] }),
      tests: (at) => { where.push(at); return fake.tests; },
    };
    render(<Live api={api} />);
    await settle();
    expect(where).toContainEqual({ harness: null, project: "/work/project" });
    expect(screen.getByRole("region", { name: "Plan" })).toBeTruthy();
  });
});
