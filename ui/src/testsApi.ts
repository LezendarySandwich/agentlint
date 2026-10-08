// What the Tests screens ask the live server, and the small pieces they share (docs/tech_impl/11-ui.md, "Tests").
import { useEffect, useRef } from "react";
import type {
  DirectivesAnswer, JobEntry, ReadDetail, ReadEntry, JobsAnswer, OnboardingAnswer, PlanAnswer, PlanJob, ResultsAnswer, ReviewCounts, Salvaged, Situation,
  SituationsAnswer, TickedAnswer,
} from "./contract";
import { count, counted, duration } from "./format";

/** What the Tests screens ask the live server, for the project in view (Live.tsx's `servedApi`; fakes in
 * Tests.test.tsx). */
export interface TestsApi {
  plan: () => Promise<PlanAnswer>;
  tick: (targets: string[]) => Promise<TickedAnswer>; // keep the ticked targets, and size their job
  situations: (target: string) => Promise<SituationsAnswer>;
  generate: (targets: string[], regenerate: boolean) => Promise<{ generating: string[]; project: string }>;
  review: (situation: string, decision: "keep" | "drop" | "edit", text?: string) => Promise<Situation>;
  onboarding: () => Promise<OnboardingAnswer>;
  accept: () => Promise<unknown>;
  jobs: () => Promise<JobsAnswer>;
  start: (targets: string[]) => Promise<{ job: JobEntry }>; // a job of these targets (the ticked ones)
  stop: (job: string) => Promise<{ job: JobEntry }>;
  resume: (job: string) => Promise<{ job: JobEntry }>;
  salvage: (job: string) => Promise<Salvaged>;
  results: (job: string) => Promise<ResultsAnswer>;
  directives?: () => Promise<DirectivesAnswer>; // the sources that can be read for directives (Findings › Read instructions)
  readDirectives?: (sources: string[]) => Promise<{ project: string; read: ReadEntry | null }>; // null: all read already
  reads?: () => Promise<{ reads: ReadEntry[] }>; // every reading of instructions, as jobs
  readDetail?: (read: string) => Promise<ReadDetail>;
  stopRead?: (read: string) => Promise<{ read: ReadEntry }>;
  resumeRead?: (read: string) => Promise<{ read: ReadEntry }>;
}

/** How often a running job, or situations being written, are asked about again. */
export const POLL_MS = 3000;

/** Call `tick` every `ms` while `active`, and once at once when it becomes active. */
export function usePoll(tick: () => void, ms: number, active: boolean): void {
  const latest = useRef(tick);
  latest.current = tick;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => latest.current(), ms);
    return () => clearInterval(timer);
  }, [ms, active]);
}

/** A review count in words, the review words being the server's: "2 kept · 1 dropped"; "none written" when there are none. */
export function reviewWords(counts: ReviewCounts | null): string {
  if (!counts) return "none written";
  const parts = ([["kept", counts.kept], ["edited", counts.edited], ["dropped", counts.dropped]] as const)
    .filter(([, n]) => n > 0).map(([word, n]) => `${count(n)} ${word}`);
  return parts.length ? parts.join(" · ") : "none written";
}

/** A test's size in words, from the server's numbers — the most it can run, every gate open (D9): "2 targets × 3
 * situations: up to 138 runs, about 2 h"; with fewer kept than situations per target, what is kept: "2 targets, 5
 * situations kept: up to 115 runs, about 1 h 42 min"; when no ticked target can be removed, its consistency runs only:
 * "1 target × 3 situations × 3 runs = 9 runs, about 8 min". */
export function sizeWords(job: PlanJob): string {
  const n = job.targets.length;
  if (!n) return "No target is ticked: tick the targets to test.";
  const atOnce = (job.runs_at_once ?? 1) > 1 ? ` at ${count(job.runs_at_once ?? 1)} at once` : "";
  const about = job.runs ? `, about ${duration(job.seconds)}${atOnce}` : "";
  const targets = counted(n, "target", "targets");
  const prefix = job.short.length ? `${targets}, ${counted(job.situations, "situation", "situations")} kept`
    : `${targets} × ${counted(job.situations_per_target, "situation", "situations")}`;
  if (job.consistency_only.length === n) {
    return `${prefix} × ${counted(job.runs_per_situation, "run", "runs")} = ${counted(job.runs, "run", "runs")}${about}.`;
  }
  return `${prefix}: up to ${counted(job.runs, "run", "runs")}${about}.`;
}

/** What each situation of a test runs, when it has both steps (decided with the user 2026-10-07): "Each situation: 3
 * runs on your configuration, counting the runs that do what it expects; where at least one does, 3 runs without the
 * target, counted the same."; "" when no ticked target can be removed. */
export function stepsWords(job: PlanJob): string {
  if (!job.targets.length || job.consistency_only.length === job.targets.length) return "";
  const n = job.runs_per_situation;
  return `Each situation: ${counted(n, "run", "runs")} on your configuration, counting the runs that do what it expects; ` +
    `where at least one does, ${counted(n, "run", "runs")} without the target, counted the same.`;
}

/** The ticked targets no variant can remove yet, in words: "Consistency runs only for hook: one (why)."; "" for none. */
export function consistencyOnlyWords(job: PlanJob): string {
  if (!job.consistency_only.length) return "";
  return `Consistency runs only — it can't be removed yet: ${job.consistency_only.map((t) => `${t.name} (${t.why})`).join("; ")}.`;
}

/** The ticked targets with fewer kept than situations per target, in words: "Fewer kept than 3: hook: one (none), skill:
 * notes (2)."; "" when there are none. */
export function shortWords(job: PlanJob): string {
  if (!job.short.length) return "";
  return `Fewer kept than ${count(job.situations_per_target)}: ${job.short.map((t) => `${t.name} (${t.kept ? count(t.kept) : "none"})`).join(", ")}.`;
}
