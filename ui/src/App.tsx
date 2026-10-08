// The frame: a sidebar to move between views, the view, and the keys that work everywhere. Where you are
// lives in the URL's hash, so back and forward work and every view can link to another.
import { Component, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { Report, SettingsAnswer } from "./contract";
import type { RecordSource, Texts } from "./data";
import { ChangesView } from "./ChangesView";
import { ConfigView } from "./ConfigView";
import { DiagnosticsView } from "./DiagnosticsView";
import { ContextView } from "./ContextView";
import { DismissedView, type DismissApi } from "./Dismissed";
import { EntitiesView } from "./EntitiesView";
import { FindingsView } from "./FindingsView";
import { Help } from "./Help";
import { OverviewSkeleton, ReadingLine } from "./Loading";
import { Overview } from "./Overview";
import { InstructionsView } from "./Instructions";
import { JobsView } from "./JobsPage";
import { ResultsView } from "./Results";
import { SessionsView, type SessionSource } from "./SessionsView";
import { SettingsView, type ControlApi } from "./Settings";
import { TestsView } from "./Tests";
import type { TestsApi } from "./testsApi";
import type { LiveScope } from "./Sidebar";
import { href, PROJECTLESS, useKeys, useRoute, VIEWS } from "./route";
import { Sidebar } from "./Sidebar";
import { Kbd, TipProvider } from "./ui";

/** A view that throws shows why, in place, instead of blanking the page; moving anywhere tries again. */
class ViewBoundary extends Component<{ at: string; children: ReactNode }, { error: Error | null; at: string }> {
  state = { error: null as Error | null, at: this.props.at };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  static getDerivedStateFromProps(props: { at: string }, state: { at: string }) {
    return props.at === state.at ? null : { error: null, at: props.at };
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="reading-error" role="alert">
        <p>This view failed: {this.state.error.message}</p>
        <p className="muted">Go back, or press <Kbd>1</Kbd> for the overview</p>
      </div>
    );
  }
}

interface Props {
  report: Report | null; // null until the first reading lands
  reading?: boolean; // a reading is under way (the last report, if any, stays, dimmed)
  error?: string | null; // the last reading failed, in the server's words
  loadTexts: (report: Report) => Promise<Texts>; // the texts of that report's scope
  onReread?: () => void; // read the configuration again
  sessions?: SessionSource; // your transcripts, readable
  records?: RecordSource; // what agentlint stored about the project (What changed, Diagnostics)
  scope?: LiveScope; // which harness and project are in view, and how to move
  control?: ControlApi; // Settings reads and writes through it
  settings?: SettingsAnswer | null; // the page's last Settings answer
  onSettings?: (answer: SettingsAnswer) => void; // a newer one
  dismiss?: DismissApi; // findings are dismissed and shown again through it, and Dismissed lists them
  onDismissed?: (id: string, dismissed: boolean) => void; // the report in view, told what the server just did
  tests?: TestsApi; // the plan, situations, jobs and results of the project in view
}

/** The frame, with one tooltip at a time across it. */
export function App(props: Props) {
  return <TipProvider><Frame {...props} /></TipProvider>;
}

function Frame({ report, reading = false, error = null, loadTexts, onReread, sessions, records, scope, control, settings, onSettings,
                 dismiss, onDismissed, tests }: Props) {
  const [route, go] = useRoute();
  const [texts, setTexts] = useState<Texts | null>(null);
  const [textsMissing, setTextsMissing] = useState<string | null>(null); // why there are none, when asking failed
  const [help, setHelp] = useState(false);
  const [switching, setSwitching] = useState(false); // the breadcrumb's project list is open: it takes the keys
  // The texts are a reading's: asked for again when another reading lands, never when a finding is dismissed or shown
  // again (the report changes, its snapshot doesn't).
  const reportNow = useRef(report);
  reportNow.current = report;
  const snapshot = report?.snapshot;
  useEffect(() => {
    const report = reportNow.current;
    if (!report) return;
    let current = true; // an answer for a report no longer shown is dropped
    setTexts(null);
    setTextsMissing(null);
    loadTexts(report).then((loaded) => { if (current) setTexts(loaded); }, (error: Error) => {
      if (!current) return;
      setTexts({});
      setTextsMissing(onReread ? `${error.message}. Press r to read this project again.` : error.message);
    });
    return () => { current = false; };
  }, [loadTexts, snapshot, onReread]);
  const keys = useCallback((key: string) => {
    const view = VIEWS[Number(key) - 1];
    if (switching) return false;
    if (key === "?") setHelp(true);
    else if (key === "p" && scope) setSwitching(true);
    else if (key === "0" && scope) scope.home();
    else if (key === "r" && onReread) { if (!reading) onReread(); } // a reading under way is not started again
    else if (/^[1-9]$/.test(key) && view) go(href(view));
    else return false;
    return true;
  }, [go, onReread, reading, scope, switching]);
  useKeys(keys);
  const closeHelp = useCallback(() => setHelp(false), []);

  const projectless = PROJECTLESS.includes(route.view); // about agentlint itself: shown with or without a report
  return (
    <div className="app">
      <Sidebar report={report} view={route.view} go={go} onHelp={() => setHelp(true)} onReread={onReread} reading={reading}
               scope={scope} switching={switching} setSwitching={setSwitching} />
      <main className={`main${report && (reading || error) && !projectless ? " dim" : ""}`} aria-busy={reading}>
        {reading && <ReadingLine />}
        {error && !projectless && (
          <div className="reading-error" role="alert">
            <p>{error}</p>
            <p className="muted">Press <Kbd>r</Kbd> to try again</p>
          </div>
        )}
        <ViewBoundary at={`${route.view}/${route.id ?? ""}?${route.params}`}>
          {!report && !error && route.view === "overview" && <OverviewSkeleton />}
          {report && route.view === "overview" && <Overview report={report} go={go} />}
          {report && route.view === "findings" && <FindingsView report={report} texts={texts} route={route} go={go} dismiss={dismiss} onDismissed={onDismissed} />}
          {report && route.view === "config" && <ConfigView report={report} texts={texts} textsMissing={textsMissing} route={route} go={go} />}
          {report && route.view === "entities" && <EntitiesView report={report} route={route} go={go} />}
          {report && route.view === "context" && <ContextView report={report} go={go} />}
          {report && route.view === "sessions" && <SessionsView report={report} route={route} go={go} sessions={sessions} />}
          {report && route.view === "changes" && <ChangesView report={report} route={route} go={go} records={records} />}
          {report && route.view === "diagnostics" && <DiagnosticsView report={report} records={records} />}
          {report && route.view === "tests" && <TestsView tests={tests} route={route} go={go} />}
          {report && route.view === "results" && <ResultsView tests={tests} route={route} go={go} />}
          {report && route.view === "instructions" && <InstructionsView tests={tests} go={go} onReread={onReread} />}
          {route.view === "jobs" && <JobsView tests={tests} route={route} go={go} />}
          {route.view === "settings" && <SettingsView control={control} route={route} answer={settings} onAnswer={onSettings} />}
          {route.view === "dismissed" && <DismissedView dismiss={dismiss} onShownAgain={onDismissed && ((id) => onDismissed(id, false))} />}
        </ViewBoundary>
      </main>
      {help && <Help onClose={closeHelp} />}
    </div>
  );
}
