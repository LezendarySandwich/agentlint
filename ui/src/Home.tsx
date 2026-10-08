// The home: which harness, then which project. Two lists built the same way (PickList), side by side — the wordmark
// and the harness list on the left, the project filter and list on the right (docs/tech_impl/11-ui.md, "The home").
import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import type { Scopes } from "./contract";
import { age, counted, home, placed, when } from "./format";
import { Help } from "./Help";
import { PickList } from "./PickList";
import { useKeys } from "./route";
import { Glyph, Icon, Kbd, MARK, Tip, TipProvider, Wordmark } from "./ui";

type Project = Scopes["projects"][number];
type Harness = Scopes["harnesses"][number];

/** Projects whose path matches what was typed, as written or with `~` (under `homeDir`, the user's home directory). */
export function matching(projects: Project[], typed: string, homeDir: string | undefined): Project[] {
  const needle = typed.trim().toLowerCase();
  if (!needle) return projects;
  return projects.filter((p) => p.path.toLowerCase().includes(needle) || home(p.path, homeDir).toLowerCase().includes(needle));
}

/** One project's row: the pin where agentlint ui was started, its name and where it sits, its sessions and age. */
export function ProjectRow({ project, started, homeDir }: { project: Project; started: boolean; homeDir: string | undefined }) {
  const { name, parent } = placed(project.path, homeDir);
  return (
    <>
      {started ? <Glyph icon={MARK.startedHere} text="Where agentlint ui was started" className="muted" /> : <span />}
      <span className="nm">{name}{parent && <span className="muted">{parent}</span>}</span>
      <span className="r">{project.sessions ? counted(project.sessions, "session", "sessions") : "No sessions"}</span>
      <span className="r">{project.sessions > 0 && project.last ? <Tip text={`The latest ${when(project.last)}`}>{age(project.last)}</Tip> : null}</span>
    </>
  );
}

/** The project filter over the project list: the home's right column, and the breadcrumb's popover. The cursor starts
 * on `startAt` when listed, else where agentlint ui was started; a filter hiding it moves it to the first match. */
export function ProjectPicker({ projects, homeDir, started, startAt, onOpen, id, listRef, filterRef, onTab, filterId, autoFocusFilter, children }: {
  projects: Project[] | null; // null while listing
  homeDir: string | undefined; // the user's home directory (Scopes.home_dir): paths under it read with `~`
  started: string | null;
  startAt: string | null;
  onOpen: (project: string) => void;
  id: string;
  listRef?: RefObject<HTMLUListElement | null>;
  filterRef?: RefObject<HTMLInputElement | null>;
  onTab?: (shift: boolean) => boolean;
  filterId?: string;
  autoFocusFilter?: boolean;
  children?: ReactNode; // between the filter and the list: an error instead of the list
}) {
  const [filter, setFilter] = useState("");
  const [current, setCurrent] = useState<string | null>(null);
  const ownList = useRef<HTMLUListElement>(null);
  const list = listRef ?? ownList;
  const shownProjects = useMemo(() => matching(projects ?? [], filter, homeDir), [projects, filter, homeDir]);
  useEffect(() => {
    if (projects) setCurrent(startAt && projects.some((p) => p.path === startAt) ? startAt : started);
  }, [projects, startAt, started]);
  const shown = shownProjects.some((p) => p.path === current) ? current : shownProjects[0]?.path ?? null;
  return (
    <>
      <div className="home-filter">
        <Icon icon={MARK.search} size={16} />
        <input id={filterId} ref={filterRef} value={filter} placeholder="Type to filter" autoComplete="off" spellCheck={false}
          aria-label={filterId ? undefined : "Filter projects"} autoFocus={autoFocusFilter}
          onChange={(e) => setFilter(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); if (shown) setCurrent(shown); list.current?.focus(); }
            else if (e.key === "Enter") { e.preventDefault(); if (shown) onOpen(shown); }
            else if (e.key === "Escape" && filter) { e.preventDefault(); e.stopPropagation(); setFilter(""); }
          }} />
      </div>
      {children ?? (
        <PickList<Project> id={id} label="Projects, most recent first" listRef={list}
          items={shownProjects} keyOf={(p) => p.path} current={shown}
          render={(p) => <ProjectRow project={p} started={p.path === started} homeDir={homeDir} />}
          onMove={setCurrent} onOpen={onOpen} onTab={onTab}
          empty={<p className="home-note muted">{!projects ? "Listing projects…" : filter ? "No project matches" : "No projects"}</p>} />
      )}
    </>
  );
}

interface Props {
  scopes: Scopes | null; // the last answer; null until the first
  harness: string | null; // the harness chosen; its projects are listed once `scopes` is of it
  error: string | null; // asking failed, in the server's words
  startAt: string | null; // the project the cursor starts on: the one just left, else where agentlint ui was started
  onHarness: (name: string) => void;
  onOpen: (harness: string, project: string) => void;
  onRetry: () => void;
  onSettings?: () => void; // live: open Settings
}

/** The home, with one tooltip at a time across it. */
export function Home(props: Props) {
  return <TipProvider><HomePage {...props} /></TipProvider>;
}

function HomePage({ scopes, harness, error, startAt, onHarness, onOpen, onRetry, onSettings }: Props) {
  const [help, setHelp] = useState(false);
  const harnessList = useRef<HTMLUListElement>(null);
  const projectList = useRef<HTMLUListElement>(null);
  const filterBox = useRef<HTMLInputElement>(null);
  const focused = useRef(false);
  const chosen = harness ?? scopes?.harness ?? null;
  const listed = scopes && scopes.harness === chosen ? scopes : null; // another harness's projects are never shown as these
  useEffect(() => {
    if (listed && !focused.current) { // focus starts in the project list, once
      focused.current = true;
      projectList.current?.focus();
    }
  }, [listed]);
  useKeys((key) => {
    if (key === "?") setHelp(true);
    else if (key === "r" && error) onRetry();
    else if (key === "/") filterBox.current?.focus();
    else return false;
    return true;
  });
  return (
    <div className="home-page">
      <div className="home-band">
        <div className="home">
          <div className="home-lede">
            <Wordmark />
            <div className="home-field">
              <span id="home-harness" className="home-label" onClick={() => harnessList.current?.focus()}>Harness</span>
              <PickList<Harness> id="harness" labelledBy="home-harness" className="hlist" listRef={harnessList}
                items={scopes?.harnesses ?? []} keyOf={(h) => h.name} current={chosen}
                render={(h) => <span className="nm">{h.label}{h.version && <span className="muted">{h.version}</span>}</span>}
                onMove={onHarness} onOpen={() => projectList.current?.focus()}
                onTab={() => { projectList.current?.focus(); return true; }} />
            </div>
          </div>
          <div className="home-pick">
            <div className="home-field">
              <label htmlFor="home-filter" className="home-label">Project</label>
              <ProjectPicker id="project" filterId="home-filter" projects={listed?.projects ?? null} homeDir={listed?.home_dir} started={listed?.default ?? null}
                startAt={startAt} listRef={projectList} filterRef={filterBox} onOpen={(project) => { if (chosen) onOpen(chosen, project); }}
                onTab={(shift) => { if (shift) return false; harnessList.current?.focus(); return true; }}>
                {error ? <div className="home-note" role="alert"><p>{error}</p><p className="muted">Press <Kbd>r</Kbd> to try again</p></div> : null}
              </ProjectPicker>
              <div className="home-keys">
                <span><Kbd>↑</Kbd><Kbd>↓</Kbd> or <Kbd>j</Kbd><Kbd>k</Kbd> move</span>
                <span><Kbd>Tab</Kbd> next list</span>
                <span><Kbd>/</Kbd> filter</span>
                <span><Kbd>↵</Kbd> open</span>
                <span><Kbd>?</Kbd> keys</span>
                {onSettings && <button type="button" className="text-button" onClick={onSettings}>Settings</button>}
              </div>
            </div>
          </div>
        </div>
      </div>
      {help && <Help onClose={() => setHelp(false)} />}
    </div>
  );
}
