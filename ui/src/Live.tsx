// Served by `agentlint ui`: the report for the chosen harness and project is read when the page loads, and again on
// "read again". Reading never blanks the page: the frame stays, the last report stays (dimmed) until the next one
// lands, and an error is shown inside the frame. Every request names the harness; none named, the server reads its
// first. The server is reached through `api`, so tests drive this with fakes (Live.test.tsx).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { App } from "./App";
import { FirstRun } from "./FirstRun";
import { Home } from "./Home";
import type {
  Conversation, DiagnosticInfo, DirectivesAnswer, Dismissed, Forgotten, JobEntry, JobsAnswer, OnboardingAnswer, PlanAnswer, Report, ReadDetail, ReadEntry, ResultsAnswer, Salvaged, TickedAnswer,
  Scopes, SessionListing, SettingsAnswer, ShownAgain, Situation, SituationsAnswer, SnapshotDiff, SnapshotInfo, SubagentInfo, Suppressions,
} from "./contract";
import type { DismissApi } from "./Dismissed";
import { posted, served, type RecordSource, type Texts } from "./data";
import { withDismissal } from "./model";
import { href, PROJECTLESS, useRoute } from "./route";
import type { SessionSource } from "./SessionsView";
import type { ControlApi } from "./Settings";
import type { TestsApi } from "./testsApi";

/** A harness and a project; null for either is the server's default (its first harness, where it was started). */
export interface Where {
  harness: string | null;
  project: string | null;
}

/** What the live page asks the server. */
export interface LiveApi {
  report: (where: Where) => Promise<Report>;
  texts: (report: Report) => Promise<Texts>; // the texts of the scope that report is of
  scope: (harness: string | null) => Promise<Scopes>;
  sessions: (where: Where) => SessionSource;
  records: (where: Where) => RecordSource; // what agentlint stored about a project
  control?: ControlApi; // Settings, and the first-run panel
  dismiss?: DismissApi; // Dismiss and Show again, and the Dismissed screen
  tests?: (where: Where) => TestsApi; // Tests: the plan, situations and jobs of a project
}

/** The live server's endpoints, with the run's token. */
export function servedApi(token: string): LiveApi {
  const get = <T,>(path: string, params: Record<string, string | null>) => {
    const query = new URLSearchParams(Object.entries(params).filter((kv): kv is [string, string] => !!kv[1])).toString();
    return served<T>(query ? `${path}?${query}` : path, token);
  };
  const id = encodeURIComponent;
  return {
    report: (where) => get<Report>("/api/report", { ...where }),
    texts: (report) => get<Texts>("/api/texts", { harness: report.snapshot.harness, project: report.snapshot.cwd }),
    scope: (harness) => get<Scopes>("/api/scope", { harness }),
    sessions: ({ harness, project }) => ({
      list: () => get<SessionListing[]>("/api/sessions", { harness, project }),
      read: (session) => get<Conversation>(`/api/sessions/${id(session)}`, { harness }),
      subagents: (session, ref) => get<SubagentInfo[]>(`/api/sessions/${id(session)}/subagents`, { ref, harness }),
      subagent: (session, agent) => get<Conversation>(`/api/sessions/${id(session)}/subagents/${id(agent)}`, { harness }),
    }),
    records: ({ harness, project }) => ({
      snapshots: () => get<SnapshotInfo[]>("/api/snapshots", { harness, project }),
      diff: (from, to) => get<SnapshotDiff>("/api/diff", { harness, project, from, to }),
      diagnostics: () => get<DiagnosticInfo[]>("/api/diagnostics", { harness, project }),
    }),
    control: {
      settings: () => served<SettingsAnswer>("/api/settings", token),
      save: (key, value) => posted<SettingsAnswer>("/api/settings", token, { key, value }),
      reset: (key) => posted<SettingsAnswer>("/api/settings", token, { key, reset: true }),
      schedule: (on) => posted<SettingsAnswer>("/api/schedule", token, { on }),
      forget: (confirm) => posted<Forgotten>("/api/forget", token, { confirm }),
      welcomed: () => posted<unknown>("/api/welcomed", token, {}),
    },
    tests: ({ harness, project }) => {
      const scope = { ...(harness ? { harness } : {}), ...(project ? { project } : {}) };
      return {
        plan: () => get<PlanAnswer>("/api/plan", { harness, project }),
        tick: (targets) => posted<TickedAnswer>("/api/plan/ticked", token, { targets, ...scope }),
        situations: (target) => get<SituationsAnswer>("/api/situations", { target, harness, project }),
        generate: (targets, regenerate) => posted<{ generating: string[]; project: string }>("/api/situations/generate", token,
          { target_ids: targets, regenerate, ...scope }),
        review: (situation, decision, text) => posted<Situation>(`/api/situations/${id(situation)}`, token,
          decision === "edit" ? { decision, text } : { decision }),
        onboarding: () => get<OnboardingAnswer>("/api/onboarding", { harness, project }),
        accept: () => posted<unknown>("/api/onboarding", token, { accepted: true }),
        jobs: () => get<JobsAnswer>("/api/jobs", { harness, project }),
        start: (targets) => posted<{ job: JobEntry }>("/api/jobs", token, { targets, ...scope }),
        stop: (job) => posted<{ job: JobEntry }>(`/api/jobs/${id(job)}/stop`, token, {}),
        resume: (job) => posted<{ job: JobEntry }>(`/api/jobs/${id(job)}/resume`, token, {}),
        salvage: (job) => posted<Salvaged>(`/api/jobs/${id(job)}/salvage`, token, {}),
        results: (job) => get<ResultsAnswer>(`/api/jobs/${id(job)}/results`, {}),
        directives: () => get<DirectivesAnswer>("/api/directives", { harness, project }),
        readDirectives: (sources) => posted<{ project: string; read: ReadEntry | null }>("/api/directives/read", token,
          { source_ids: sources, ...scope }),
        reads: () => get<{ reads: ReadEntry[] }>("/api/reads", {}),
        readDetail: (read) => get<ReadDetail>(`/api/reads/${id(read)}`, {}),
        stopRead: (read) => posted<{ read: ReadEntry }>(`/api/reads/${id(read)}/stop`, token, {}),
        resumeRead: (read) => posted<{ read: ReadEntry }>(`/api/reads/${id(read)}/resume`, token, {}),
      };
    },
    dismiss: {
      suppress: (finding, project, harness, reason) =>
        posted<Dismissed>("/api/suppress", token, { finding_id: finding, project, reason, ...(harness ? { harness } : {}) }),
      unsuppress: (finding) => posted<ShownAgain>("/api/unsuppress", token, { finding_id: finding }),
      suppressions: () => served<Suppressions>("/api/suppressions", token),
    },
  };
}

/** The project the URL names, or null: the home. */
function fromLocation(): Where | null {
  const search = new URLSearchParams(window.location.search);
  const project = search.get("project");
  return project ? { harness: search.get("harness"), project } : null;
}

/** The URL for a scope, the token and anything else kept; `hash` is the view to land on. */
function urlOf(where: Where | null, hash: string, harness: string | null = where?.harness ?? null): string {
  const search = new URLSearchParams(window.location.search);
  for (const [name, value] of [["harness", harness], ["project", where?.project ?? null]] as const) {
    if (value) search.set(name, value);
    else search.delete(name);
  }
  return `?${search}${hash}`;
}

const keyOf = (where: Where) => `${where.harness ?? ""}\n${where.project ?? ""}`;

/** The live page: the home until a project is chosen (or named in the URL), then that project's report. Choosing
 * pushes a history entry, so Back returns home and a reload stays in the project. */
export function Live({ api }: { api: LiveApi }) {
  const [route, go] = useRoute();
  const [settings, setSettings] = useState<SettingsAnswer | null>(null);
  // Asked once as the page opens: something Settings can fix (a setting that can't be used, a harnesses setting that
  // names none agentlint reads) opens it there.
  useEffect(() => {
    let current = true;
    api.control?.settings().then((answer) => {
      if (!current) return;
      setSettings(answer);
      if (answer.attention.length) go(href("settings"));
    }, () => {}); // Settings says why when it is opened
    return () => { current = false; };
  }, [api, go]);
  // The first-run panel, while the server says nobody has seen it; closed once, it is gone for this page and, through
  // the server's flag, for good.
  const closed = useRef(false);
  const [welcomed, setWelcomed] = useState(false);
  const closeWelcome = useCallback(() => {
    if (closed.current) return;
    closed.current = true;
    setWelcomed(true);
    api.control?.welcomed().catch(() => {}); // not recorded: it shows again next time, which is the safe side
  }, [api]);
  const panel = settings?.first_run && !welcomed ? <FirstRun answer={settings} onClose={closeWelcome} go={go} /> : null;
  const [where, setWhere] = useState<Where | null>(fromLocation);
  const [homeHarness, setHomeHarness] = useState<string | null>(() => new URLSearchParams(window.location.search).get("harness"));
  const [left, setLeft] = useState<string | null>(null); // the project just left: the home's cursor starts there
  const [scopes, setScopes] = useState<Scopes | null>(null);
  const [scopeError, setScopeError] = useState<string | null>(null);
  const [asked, setAsked] = useState(0); // asking for the scopes again, after a failure
  const [held, setHeld] = useState<{ report: Report; of: string; where: Where } | null>(null);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(0); // the latest request: an answer to any earlier one is dropped
  const scopesNow = useRef<Scopes | null>(null);
  scopesNow.current = scopes;
  const whereNow = useRef(where);
  whereNow.current = where;
  // As the home appears, the default project's report is asked for at once; that answer is used only if that project is
  // the first one opened. Opening any other drops it, so a later visit to the default project reads it again.
  const early = useRef<{ harness: string | null; answer: Promise<Report> } | null>(null);
  const prefetched = useRef(false);
  useEffect(() => {
    if (where || prefetched.current) return;
    prefetched.current = true;
    const answer = api.report({ harness: homeHarness, project: null });
    answer.catch(() => {}); // shown only if that project is chosen
    early.current = { harness: homeHarness, answer };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- once, as the page opens

  const take = useCallback((wanted: Where) => {
    const kept = early.current;
    early.current = null; // the first project opened takes it or drops it
    const known = scopesNow.current;
    if (kept && known && wanted.project === known.default && wanted.harness === (kept.harness ?? known.harnesses[0]?.name)) {
      return kept.answer;
    }
    return api.report(wanted);
  }, [api]);

  const read = useCallback((wanted: Where, fresh: boolean) => {
    const id = ++latest.current;
    setReading(true);
    setError(null);
    (fresh ? api.report(wanted) : take(wanted)).then(
      (report) => {
        if (id !== latest.current) return;
        setHeld({ report, of: keyOf(wanted), where: wanted });
        setReading(false);
      },
      (failed: Error) => {
        if (id !== latest.current) return;
        setError(failed.message);
        setReading(false);
      },
    );
  }, [api, take]);
  const harness = where?.harness ?? null;
  const project = where?.project ?? null;
  useEffect(() => {
    if (project) read({ harness, project }, false);
  }, [read, harness, project]);
  const reread = useCallback(() => { // r: the report again and, if listing them failed, the scopes
    if (project) read({ harness, project }, true);
    if (scopeError) setAsked((n) => n + 1);
  }, [read, harness, project, scopeError]);

  // The scopes: every harness, and the projects of the one in view (the home's choice, or the project's).
  const listedHarness = where ? harness : homeHarness;
  useEffect(() => {
    let current = true;
    setScopeError(null);
    api.scope(listedHarness).then((answer) => { if (current) setScopes(answer); }, (failed: Error) => { if (current) setScopeError(failed.message); });
    return () => { current = false; };
  }, [api, listedHarness, asked]);

  useEffect(() => {
    const moved = () => { // Back or Forward
      const next = fromLocation();
      const was = whereNow.current;
      if (!next && was) { // back home: the cursor on the project just left, its harness listed
        setLeft(was.project);
        setHomeHarness(was.harness);
      }
      setWhere(next);
    };
    window.addEventListener("popstate", moved);
    return () => window.removeEventListener("popstate", moved);
  }, []);

  const enter = useCallback((next: Where, fromHome: boolean) => {
    window.history.pushState(null, "", urlOf(next, fromHome ? "#/overview" : window.location.hash));
    if (fromHome && held && held.of !== keyOf(next)) setHeld(null); // another project: its own skeleton, not the last one dimmed
    if (fromHome) setError(null);
    setWhere(next);
  }, [held]);
  const home = useCallback(() => { // 0, or the wordmark: a history entry, so Back returns to the project
    const was = whereNow.current;
    window.history.pushState(null, "", urlOf(null, "", was?.harness ?? null));
    setLeft(was?.project ?? null);
    setHomeHarness(was?.harness ?? null);
    setWhere(null);
  }, []);
  // The views' sessions are those of the report shown: a switch re-lists them when the new report lands.
  const shownWhere = held?.where ?? where;
  const sessions = useMemo(() => api.sessions({ harness: shownWhere?.harness ?? null, project: shownWhere?.project ?? null }),
    [api, shownWhere?.harness, shownWhere?.project]); // eslint-disable-line react-hooks/exhaustive-deps
  const records = useMemo(() => api.records({ harness: shownWhere?.harness ?? null, project: shownWhere?.project ?? null }),
    [api, shownWhere?.harness, shownWhere?.project]); // eslint-disable-line react-hooks/exhaustive-deps
  const tests = useMemo(() => api.tests?.({ harness: shownWhere?.harness ?? null, project: shownWhere?.project ?? null }),
    [api, shownWhere?.harness, shownWhere?.project]); // eslint-disable-line react-hooks/exhaustive-deps
  const listed = scopes && (!harness || scopes.harness === harness) ? scopes : null; // this harness's answer, never another's
  const scope = useMemo(() => project ? {
    harness: listed?.harnesses.find((h) => h.name === listed.harness) ?? null,
    project,
    projects: listed?.projects ?? null,
    homeDir: listed?.home_dir,
    started: listed?.default ?? null,
    error: scopeError,
    retry: () => setAsked((n) => n + 1),
    choose: (next: string) => enter({ harness: harness ?? listed?.harness ?? null, project: next }, false),
    home,
  } : undefined, [listed, harness, project, scopeError, enter, home]);

  // What the server just did to a finding, shown in the report held without reading the project again.
  // The home's early answer predates it, so it is dropped: the default project, opened next, is read as it is now.
  const dismissed = useCallback((id: string, isDismissed: boolean) => {
    early.current = null;
    setHeld((was) => was && { ...was, report: withDismissal(was.report, id, isDismissed) });
  }, []);

  if (!where && !PROJECTLESS.includes(route.view)) {
    return (
      <>
        <Home scopes={scopes} harness={homeHarness} error={scopeError} startAt={left}
          onHarness={(name) => { setHomeHarness(name); window.history.replaceState(null, "", urlOf(null, "", name)); }}
          onOpen={(h, p) => enter({ harness: h, project: p }, true)} onRetry={() => setAsked((n) => n + 1)}
          onSettings={api.control ? () => go(href("settings")) : undefined} />
        {panel}
      </>
    );
  }
  return (
    <>
      <App report={held?.report ?? null} reading={reading} error={error} loadTexts={api.texts} onReread={reread}
           sessions={sessions} records={records} scope={scope} control={api.control} settings={settings} onSettings={setSettings}
           dismiss={api.dismiss} onDismissed={dismissed} tests={tests} />
      {panel}
    </>
  );
}
