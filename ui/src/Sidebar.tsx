// Where you are and where you can go: the breadcrumb "<harness> / <project>" at the top, the views with the key that
// takes you, and one muted line of keys at the foot. Live, the project part switches project in place (p).
import { useEffect, useRef, useState } from "react";
import type { Report, Scopes } from "./contract";
import { age, count, counted, placed, when } from "./format";
import { ProjectPicker } from "./Home";
import { href, viewKey, VIEW_NAMES, type View } from "./route";
import { Icon, Kbd, MARK, NAV_ICON, Tip } from "./ui";

/** Live: the harness and project in view, what else there is, and how to move. */
export interface LiveScope {
  harness: Scopes["harnesses"][number] | null; // null until /api/scope answers
  project: string; // the project in view (asked for; its report may still be reading)
  projects: Scopes["projects"] | null; // the harness's projects, to switch among; null until listed
  homeDir: string | undefined; // the user's home directory (Scopes.home_dir); undefined until listed
  started: string | null; // where agentlint ui was started
  error: string | null; // listing the projects failed, in the server's words
  retry: () => void; // ask for them again
  choose: (project: string) => void; // switch in place: a history entry, the view kept
  home: () => void;
}

interface Props {
  report: Report | null; // before the first reading lands: the views, without counts
  view: View;
  go: (to: string) => void;
  onHelp: () => void;
  onReread?: () => void;
  reading?: boolean; // a reading is under way: "read … r" is ignored, as r is
  scope?: LiveScope; // absent only where nothing can move (a test of the frame alone)
  switching: boolean; // the project list is open
  setSwitching: (open: boolean) => void;
}

/** Now, once a minute: for "read 3 min ago". */
function useMinute(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(tick);
  }, []);
  return now;
}

/** "<harness> / <project>": labels from data (the server's list, or the report's own label), never from the code. */
function Breadcrumb({ report, scope, switching, setSwitching }: Pick<Props, "report" | "scope" | "switching" | "setSwitching">) {
  const crumb = useRef<HTMLButtonElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const box = switching ? bar.current?.getBoundingClientRect() : undefined; // the sidebar scrolls, so the list is fixed below the crumb
  const snapshot = report?.snapshot;
  const label = scope?.harness?.label ?? report?.harness_label ?? snapshot?.harness ?? "";
  const version = scope?.harness?.version ?? snapshot?.harness_version ?? null;
  const path = scope?.project ?? snapshot?.cwd ?? "";
  const { name } = placed(path);
  const about = [
    `Harness · ${label}${version ? ` ${version}` : ""}`,
    ...(report && snapshot ? [snapshot.model ?? "model unknown", `${counted(snapshot.sessions.length, "recent session", "recent sessions")} read`,
                              `read ${when(report.generated_at)}`] : []),
  ].join(" · ");
  const close = (refocus: boolean) => {
    setSwitching(false);
    if (refocus) crumb.current?.focus();
  };
  return (
    <div className="crumb" ref={bar}>
      <Tip text={about} className="crumb-harness">{label}</Tip>
      <span className="sep" aria-hidden="true">/</span>
      {scope ? (
        <Tip text={`Project · ${path} · p to switch`} className="crumb-project">
          <button ref={crumb} type="button" className="crumb-button" aria-haspopup="listbox" aria-expanded={switching}
                  onClick={() => setSwitching(!switching)}>
            <span className="crumb-name">{name}</span>
            <Icon icon={MARK.disclose} className="crumb-open" />
          </button>
        </Tip>
      ) : (
        <Tip text={`Project · ${path}`} className="crumb-project crumb-name">{name}</Tip>
      )}
      {scope && switching && (
        <div className="crumb-pop" role="dialog" aria-label="Switch project" style={box ? { top: box.bottom, left: box.left } : undefined}
             onKeyDown={(e) => { // every key typed here stays here: the views beneath never see it
               if (e.key === "Escape") { e.preventDefault(); close(true); }
               e.stopPropagation();
             }}
             onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) close(false); }}>
          <ProjectPicker id="switch" projects={scope.projects} homeDir={scope.homeDir} started={scope.started} startAt={scope.project} autoFocusFilter
            onOpen={(project) => { close(true); if (project !== scope.project) scope.choose(project); }}>
            {scope.error ? ( // never "Listing projects…" for a listing that failed
              <div className="home-note" role="alert">
                <p>{scope.error}</p>
                <p><button type="button" className="text-button" onClick={scope.retry}>Try again</button></p>
              </div>
            ) : null}
          </ProjectPicker>
        </div>
      )}
    </div>
  );
}

export function Sidebar({ report, view, go, onHelp, onReread, reading = false, scope, switching, setSwitching }: Props) {
  const now = useMinute();
  const shown = report ? report.check.findings.filter((f) => !f.suppressed).length : undefined;
  const items: { view: View; count?: number; sub?: boolean }[] = [
    { view: "overview" },
    { view: "findings", count: shown }, // neutral: the count of findings, never coloured by the worst one
    { view: "dismissed", sub: true }, // every dismissal, across projects: under Findings, with no key of its own
    { view: "instructions", sub: true }, // Findings › Read instructions: what contradictions and repeats are found from
    { view: "config" },
    { view: "changes", sub: true }, // Configuration › What changed: under it, with no key of its own
    { view: "entities" },
    { view: "context" },
    { view: "sessions" },
    { view: "diagnostics" }, // agentlint's findings about itself: never counted with Findings
    { view: "tests" }, // the plan, situations and the open job: running the agent on your configuration
    { view: "results", sub: true }, // Tests › Results: every test of the project, with no key of its own
    { view: "jobs" }, // what runs, or ran: tests and readings of instructions, each opening to what is inside it
    { view: "settings" },
  ];
  return (
    <nav className="sidebar" aria-label="Views">
      {scope ? (
        <button type="button" className="brand" onClick={scope.home} aria-label="agentlint: the home (0)">agentlint</button>
      ) : (
        <div className="brand">agentlint</div>
      )}
      <Breadcrumb report={report} scope={scope} switching={switching} setSwitching={setSwitching} />
      <ul>
        {items.map((item) => (
          <li key={item.view}>
            <a
              href={href(item.view)}
              className={[view === item.view ? "on" : "", item.sub ? "sub" : ""].filter(Boolean).join(" ")}
              aria-current={view === item.view ? "page" : undefined}
              onClick={(event) => {
                event.preventDefault();
                go(href(item.view));
              }}
            >
              <Icon icon={NAV_ICON[item.view]} size={16} />
              <span className="label">{VIEW_NAMES[item.view]}</span>
              {item.count !== undefined && <span className="count">{count(item.count)}</span>}
              {viewKey(item.view) && <Kbd>{viewKey(item.view)}</Kbd>}
            </a>
          </li>
        ))}
      </ul>
      <div className="foot">
        <div className="foot-keys">
          <button type="button" className="foot-button" onClick={onHelp}><Kbd>?</Kbd> keys</button>
          {scope && <button type="button" className="foot-button" onClick={() => setSwitching(true)}><Kbd>p</Kbd> project</button>}
          {scope && onReread && report && (
            <Tip text={`Read ${when(report.generated_at)}: press r to read the configuration again`}>
              <button type="button" className="foot-button" onClick={() => { if (!reading) onReread(); }}>read {now - Date.parse(report.generated_at) < 60_000 ? "just now" : `${age(report.generated_at, now)} ago`} <Kbd>r</Kbd></button>
            </Tip>
          )}
        </div>
      </div>
    </nav>
  );
}
