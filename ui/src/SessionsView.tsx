// Your sessions, readable (docs/tech_impl/11-ui.md, "Sessions"): a list, and a reader beside it — your prompts,
// the agent's replies, each tool call one line that opens to its input and output, and what the harness injected
// or the agent thought only when you ask; what the harness gave the model before your first prompt, after each
// compaction and where its prompt changed, as a folded block always in place. Read from `agentlint ui`, on this
// machine, by id.
import {
  createContext, useCallback, useContext, useDeferredValue, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject,
} from "react";
import type { Briefing, BriefingPart, BriefingReason, Clipped, Conversation, Item, Report, SessionListing, SubagentInfo } from "./contract";
import { count, counted, GROUP_WORDS, home, noun, short, when } from "./format";
import { chosenOf, GROUP_OF, GROUPS, splitOf } from "./model";
import { useListNavigation } from "./listNav";
import { configHref, href, type Route } from "./route";
import { JsonTree, MarkdownView } from "./Text";
import {
  ArchivedIcon, GroupSwatch, Icon, MARK, More, ROLE_ICON, SearchBox, SeverityIcon, Tip, Toggle, UntracedIcon, WhatIcon,
} from "./ui";

export interface SessionSource {
  list: () => Promise<SessionListing[]>;
  read: (id: string) => Promise<Conversation>;
  subagents?: (id: string, ref: string) => Promise<SubagentInfo[]>; // ref: an item's subagent_ref, handed back as is
  subagent?: (id: string, agent: string) => Promise<Conversation>;
}

interface Props {
  report: Report;
  route: Route;
  go: (to: string, replace?: boolean) => void;
  sessions?: SessionSource; // absent: nothing to list (a test of the frame alone)
}

const PAGE = 300;

function duration(info: SessionListing): string {
  const minutes = Math.round((Date.parse(info.ended_at) - Date.parse(info.header.started_at)) / 60000);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

export function SessionsView({ report, route, go, sessions }: Props) {
  const [list, setList] = useState<SessionListing[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => { // listed again for another project; an answer for the one left is dropped
    let current = true;
    setList(null);
    setFailed(null);
    sessions?.list().then((listed) => { if (current) setList(listed); }, (error: Error) => { if (current) setFailed(error.message); });
    return () => { current = false; };
  }, [sessions]);
  const byProject = useMemo(() => {
    const grouped = new Map<string, SessionListing[]>();
    for (const info of list ?? []) grouped.set(info.header.project ?? "", [...(grouped.get(info.header.project ?? "") ?? []), info]);
    return grouped;
  }, [list]);
  const rows = useMemo(() => [...byProject.values()].flat(), [byProject]);
  const open = route.id ? rows.find((s) => s.header.session_id === route.id) ?? null : null;
  const openId = open?.header.session_id ?? null;
  // the reader's search, for the session open now: another session starts with none
  const [search, setSearch] = useState({ session: "", text: "" });
  const query = search.session === openId ? search.text : "";
  const setQuery = useCallback((text: string) => setSearch({ session: openId ?? "", text }), [openId]);
  const searchRef = useRef<HTMLInputElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const nav = useListNavigation({
    items: rows, keyOf: (s) => s.header.session_id, openKey: openId, search: searchRef,
    open: (id, replace) => go(href("sessions", id), replace),
    close: () => (query ? setQuery("") : go(href("sessions"))), // Esc clears the reader's search first
    more: (key) => {
      const focused = document.activeElement;
      if (key === "Enter" && focused instanceof HTMLElement && focused.closest(".reader")) { // a call, a toggle: it opens, not the row
        focused.click();
        return true;
      }
      if (openId === null || (key !== "j" && key !== "k")) return false; // with a session open, j and k go from prompt to prompt
      toPrompt(body.current, key === "j" ? 1 : -1); // the arrows still move the list, and the reader follows
      return true;
    },
  });
  if (!sessions) {
    return (
      <p className="empty-state">
        No sessions to list.
      </p>
    );
  }
  if (failed) return <p className="empty-state">Couldn't list your sessions: {failed}</p>;
  if (!list) return <p className="empty-state">Listing your sessions…</p>;
  return (
    <div className={`split sessions${open ? " reading" : ""}`}>
      <div className="list-pane">
        <div className="strip">
          <span><strong>{count(list.length)}</strong> {noun(list.length, "session", "sessions")}</span>
        </div>
        <div className="list">
          {[...byProject].map(([project, infos]) => (
            <section key={project} className="group">
              <div className="group-head static"><span className="group-name">{home(project, report.home_dir) || "no project"}</span>
                <span className="count">{count(infos.length)}</span></div>
              {infos.map((info) => {
                const id = info.header.session_id;
                return (
                  <a key={id} href={href("sessions", id)} className={`row tall${nav.cursor === id ? " cursor" : ""}${openId === id ? " open" : ""}`}
                     {...nav.rowProps(id)}>
                    <span className="title">
                      {info.archived_only && (
                        <ArchivedIcon />
                      )}{" "}
                      {info.title ?? <span className="muted">Untitled session</span>}
                      <span className="sub muted">
                        {when(info.header.started_at)}{duration(info) ? ` · ${duration(info)}` : ""} · {counted(info.prompts, "prompt", "prompts")}
                        {info.header.model ? ` · ${info.header.model}` : ""}{info.header.interactive ? "" : " · headless"}
                      </span>
                    </span>
                  </a>
                );
              })}
            </section>
          ))}
        </div>
      </div>
      {open && <Reader key={open.header.session_id} report={report} info={open} sessions={sessions} go={go}
                       onClose={() => go(href("sessions"))} query={query} setQuery={setQuery} searchRef={searchRef} body={body} />}
    </div>
  );
}

/** Scrolls the reader to the next prompt below its top (`1`), or the one above (`-1`). */
function toPrompt(pane: HTMLElement | null, step: 1 | -1) {
  const prompts = [...(pane?.querySelectorAll<HTMLElement>(".msg.prompt") ?? [])];
  const top = pane?.getBoundingClientRect().top ?? 0;
  const next = step === 1 ? prompts.find((p) => p.getBoundingClientRect().top > top + 8)
    : [...prompts].reverse().find((p) => p.getBoundingClientRect().top < top - 8);
  next?.scrollIntoView?.({ block: "start" });
}

function Reader({ report, info, sessions, onClose, go, query, setQuery, searchRef, body }: {
  report: Report; info: SessionListing; sessions: SessionSource; onClose: () => void; go: (to: string) => void;
  query: string; setQuery: (text: string) => void; searchRef: RefObject<HTMLInputElement | null>; body: RefObject<HTMLDivElement | null>;
}) {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [shown, setShown] = useState(PAGE);
  const needle = useDeferredValue(query).trim().toLowerCase();
  const [show, setShow] = useState({ tools: true, thinking: false, injected: false });
  useEffect(() => {
    sessions.read(info.header.session_id).then(setConversation, (error: Error) => setFailed(error.message));
  }, [sessions, info.header.session_id]);
  // a used skill or agent links to its entry: the ones the agent chooses between, as Python decided (usage.summary)
  const entityIds = useMemo(() => chosenOf(report.usage).byReference, [report.usage]);
  const items = useMemo(() => (conversation?.items ?? []).map((item, index) => ({ item, index })).filter(({ item }) => {
    if (item.type === "tool_call" && !show.tools) return false;
    if (item.type === "thinking" && !show.thinking) return false;
    if (item.type === "injected" && !show.injected) return false; // a briefing is always in place: no toggle hides it
    if (!needle) return true;
    return searchable(item, conversation?.texts ?? {}).toLowerCase().includes(needle);
  }), [conversation, show, needle]);
  // what a briefing reads beside its parts: this conversation's texts, the home directory, the sources it may link to
  const world = useMemo<BriefingWorld>(() => ({
    texts: conversation?.texts ?? {}, homeDir: report.home_dir, sources: new Set(report.snapshot.sources.map((s) => s.id)), go,
  }), [conversation, report, go]);

  return (
    <article className="reader" aria-label="Session">
      <header className="reader-head">
        <div className="reader-title">
          <h2>{info.title ?? "Untitled session"}</h2>
          <span className="muted">
            {info.header.project ? home(info.header.project, report.home_dir) : "no project"} · {when(info.header.started_at)}{duration(info) ? ` · ${duration(info)}` : ""}
            {" · "}{counted(info.prompts, "prompt", "prompts")}{info.header.model ? ` · ${info.header.model}` : ""}{info.header.harness_version ? ` · ${info.header.harness_version}` : ""}
          </span>
        </div>
        <button type="button" className="icon-button" onClick={onClose} aria-label="Close"><Icon icon={MARK.close} size={16} /></button>
      </header>
      <div className="toolbar">
        <SearchBox value={query} onChange={setQuery} placeholder="Search this session" inputRef={searchRef} />
        <Toggle on={show.tools} onClick={() => setShow((s) => ({ ...s, tools: !s.tools }))} label="Show tool calls">
          tools
        </Toggle>
        <Toggle on={show.thinking} onClick={() => setShow((s) => ({ ...s, thinking: !s.thinking }))} label="Show the agent's thinking">
          thinking
        </Toggle>
        <Toggle on={show.injected} onClick={() => setShow((s) => ({ ...s, injected: !s.injected }))} label="Show what the harness and hooks injected">
          injected
        </Toggle>
      </div>
      <BriefingContext.Provider value={world}>
      <div className="reader-body" ref={body}>
        {failed && <p className="empty-state">Couldn't read it: {failed}</p>}
        {!conversation && !failed && <p className="empty-state">Reading the session…</p>}
        {conversation && items.length === 0 && <p className="empty-state">{needle ? "Nothing in this session matches." : "Nothing to show."}</p>}
        {items.slice(0, shown).map(({ item, index }) => ( // keyed by place in the session: opening one stays with it
          <Entry key={index} item={item} needle={needle} entityIds={entityIds} go={go} session={{ id: info.header.session_id, source: sessions }} />
        ))}
        <More hidden={Math.min(PAGE, items.length - shown)} onClick={() => setShown((n) => n + PAGE)} />
      </div>
      </BriefingContext.Provider>
    </article>
  );
}

/** What search looks in: an entry's words, whatever its kind — a briefing's note, and its parts' names and texts. */
function searchable(item: Item, texts: Record<string, string>): string {
  switch (item.type) {
    case "briefing":
      return [item.note ?? "", ...item.parts.flatMap((p) => [p.label, p.detail, texts[p.text_ref] ?? ""])].join("\n");
    case "tool_call":
      return [item.name, item.summary, item.result?.text ?? ""].join("\n");
    case "injected":
      return [item.by, item.text.text].join("\n");
    case "note":
      return item.text.text;
    default:
      return item.text;
  }
}

const isJson = (text: string): boolean => {
  try {
    JSON.parse(text);
    return true;
  } catch {
    return false;
  }
};

function Marked({ text, needle }: { text: string; needle: string }) {
  if (needle.length < 2) return <>{text}</>;
  const parts: ReactNode[] = [];
  const lower = text.toLowerCase();
  let from = 0;
  for (let at = lower.indexOf(needle); at >= 0 && parts.length < 400; at = lower.indexOf(needle, at + needle.length)) {
    parts.push(text.slice(from, at), <mark key={at}>{text.slice(at, at + needle.length)}</mark>);
    from = at + needle.length;
  }
  parts.push(text.slice(from));
  return <>{parts}</>;
}

/** Prose with its code: fenced blocks set apart, the rest as written. */
function Prose({ text, needle }: { text: string; needle: string }) {
  const pieces = text.split(/^```[^\n]*\n?/m);
  return (
    <div className="prose">
      {pieces.map((piece, i) => (i % 2 === 1
        ? <pre key={i} className="code"><Marked text={piece.replace(/\n$/, "")} needle={needle} /></pre>
        : piece.trim() && <p key={i}><Marked text={piece.trim()} needle={needle} /></p>))}
    </div>
  );
}

/** The subagents a call started, each opening to its own transcript, nested in the call. */
function Subagents({ subagentRef, session, entityIds, go }: {
  subagentRef: string; session: { id: string; source: SessionSource }; entityIds: Map<string, string>; go: (to: string) => void;
}) {
  const [list, setList] = useState<SubagentInfo[] | null>(null);
  const [open, setOpen] = useState<Record<string, Conversation | "loading">>({});
  const world = useContext(BriefingContext);
  useEffect(() => {
    session.source.subagents?.(session.id, subagentRef).then(setList, () => setList([]));
  }, [session, subagentRef]);
  if (!session.source.subagents) return null;
  if (list === null) return <p className="muted sub-note">Finding its subagents…</p>;
  if (list.length === 0) return <p className="muted sub-note">Its subagents' transcripts are gone, and the archive never had them.</p>;
  const toggle = (agent: string) => {
    if (open[agent]) return setOpen(({ [agent]: _, ...rest }) => rest);
    setOpen((o) => ({ ...o, [agent]: "loading" }));
    session.source.subagent?.(session.id, agent).then((c) => setOpen((o) => ({ ...o, [agent]: c })), () => setOpen(({ [agent]: _, ...rest }) => rest));
  };
  return (
    <div className="subagents">
      <div className="label muted">{list.length === 1 ? "the subagent it started" : `the ${counted(list.length, "subagent", "subagents")} it started`}</div>
      {list.map((agent) => {
        const conversation = open[agent.id];
        return (
          <div key={agent.id} className="subagent">
            <button type="button" className="fold" onClick={() => toggle(agent.id)} aria-expanded={!!conversation}>
              <Icon icon={MARK.disclose} className={`chevron${conversation ? " open" : ""}`} />
              <Icon icon={ROLE_ICON.agent} /> <span className="tool-summary">{agent.title ?? agent.id}</span>
              {agent.title && <span className="muted">{agent.id}</span>}
              <span className="muted">{agent.entries}</span>
            </button>
            {conversation === "loading" && <p className="muted sub-note">Reading it…</p>}
            {conversation && conversation !== "loading" && (
              <BriefingContext.Provider value={{ ...world, texts: conversation.texts ?? {} }}>
                <div className="nested">
                  {conversation.items.map((item, i) => (
                    <Entry key={i} item={item} needle="" entityIds={entityIds} go={go} voice="asked" />
                  ))}
                </div>
              </BriefingContext.Provider>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Entry({ item, needle, entityIds, go, session, voice = "you" }: {
  item: Item; needle: string; entityIds: Map<string, string>; go: (to: string) => void;
  session?: { id: string; source: SessionSource }; voice?: "you" | "asked";
}) {
  const [open, setOpen] = useState(false);
  const time = item.at ? new Date(item.at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "";
  const linked = (entities: string[]) => entities.filter((ref) => entityIds.has(ref)).map((ref) => (
    <a key={ref} className="entity-link" href={href("entities", entityIds.get(ref)!)}
       onClick={(e) => { e.preventDefault(); go(href("entities", entityIds.get(ref)!)); }}>{ref.split("/")[1]}</a>
  ));
  const omitted = (clipped: Clipped) => clipped.omitted > 0 && <span className="muted"> … {counted(clipped.omitted, "more character", "more characters")}</span>;
  switch (item.type) {
    case "briefing":
      return <BriefingBlock briefing={item} needle={needle} />;
    case "prompt":
      return (
        <div className="msg prompt">
          <div className="who">{voice === "you" ? "You" : "Asked of it"} <span className="muted">{time}</span></div>
          <div className="prompt-text"><Marked text={item.text} needle={needle} /></div>
        </div>
      );
    case "reply":
      return (
        <div className="msg reply">
          <div className="who">Agent <span className="muted">{time}</span></div>
          {needle.length >= 2 ? <Prose text={item.text} needle={needle} /> : <MarkdownView text={item.text} />}
        </div>
      );
    case "thinking":
      return (
        <div className="msg thinking">
          <button type="button" className="fold" onClick={() => setOpen(!open)} aria-expanded={open}>
            <Icon icon={MARK.disclose} className={`chevron${open ? " open" : ""}`} /> thinking
          </button>
          {open && <div className="thought"><Marked text={item.text} needle={needle} /></div>}
        </div>
      );
    case "tool_call": {
      const links = linked(item.entities);
      return (
        <div className={`tool${item.error ? " failed" : ""}`}>
          <button type="button" className="fold" onClick={() => setOpen(!open)} aria-expanded={open}>
            <Icon icon={MARK.disclose} className={`chevron${open ? " open" : ""}`} />
            <Icon icon={ROLE_ICON.tool} />
            <span className="tool-name">{item.name}</span>
            <span className="tool-summary"><Marked text={item.summary} needle={needle} /></span>
            {item.error && <span role="img" aria-label="failed"><SeverityIcon severity="error" bare /></span>}
            {item.result === null && <Tip text="No result was recorded"><span className="muted">…</span></Tip>}
          </button>
          {links.length > 0 && <span className="links-inline">{links}</span>}
          {open && (
            <div className="tool-body">
              <div className="label muted">input</div>
              {item.input && isJson(item.input) ? <JsonTree text={item.input} /> : <pre className="code">{item.input}</pre>}
              {item.result !== null && (
                <>
                  <div className="label muted">output</div>
                  <pre className="code"><Marked text={item.result.text} needle={needle} />{omitted(item.result)}</pre>
                </>
              )}
              {item.subagent_ref && session && <Subagents subagentRef={item.subagent_ref} session={session} entityIds={entityIds} go={go} />}
            </div>
          )}
        </div>
      );
    }
    case "injected":
      return (
        <div className="injected">
          <button type="button" className="fold" onClick={() => setOpen(!open)} aria-expanded={open}>
            <Icon icon={MARK.disclose} className={`chevron${open ? " open" : ""}`} />
            <span>{item.by}</span>
          </button>
          {open && <pre className="code"><Marked text={item.text.text} needle={needle} />{omitted(item.text)}</pre>}
        </div>
      );
    case "note":
      if (item.kind === "recap") {
        return (
          <div className="recap">
            <div className="who">Recap <span className="muted">{time}</span></div>
            <Marked text={item.text.text} needle={needle} />{omitted(item.text)}
          </div>
        );
      }
      if (item.kind === "compacted") return <div className="divider">compacted here — the conversation was summarised to free context</div>;
      return (
        <div className={`note${item.kind === "hook_error" ? " failed" : ""}`}>
          {item.kind === "hook_error" && <SeverityIcon severity="error" bare />}
          <span><Marked text={item.text.text} needle={needle} />{omitted(item.text)}</span>
          {linked(item.entities)}
        </div>
      );
  }
}


/** What a briefing reads beside its own fields: its conversation's texts (a subagent's are its own), the home
 * directory for `~` paths, the report's source ids it may link to, and how to go there. */
interface BriefingWorld {
  texts: Record<string, string>;
  homeDir: string | undefined;
  sources: Set<string>;
  go: (to: string) => void;
}
const BriefingContext = createContext<BriefingWorld>({ texts: {}, homeDir: undefined, sources: new Set(), go: () => {} });

/** A briefing's title, by why the harness gave the model its prompt there. */
const BRIEFING_TITLE: Record<BriefingReason, string> = {
  start: "Before your first prompt",
  compacted: "After compacting",
  changed: "The harness changed its prompt",
};

const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);

/** A part's name on the summary line, by how it reaches the agent (its point's mechanism, never its name): every
 * instruction file and the policy are "instructions", every hook's output "hook output", the rest its own label — a
 * label that is itself a list (the session's context) by its point's label, without its parenthesis. */
function summaryName(part: BriefingPart): string {
  const { mechanism, label } = part.point;
  if (mechanism === "instruction_file" || mechanism === "config_file") return "instructions";
  if (mechanism === "hook_output") return "hook output";
  const own = lowerFirst(part.label);
  return own.includes(" · ") ? lowerFirst(label.replace(/\s*\(.*\)$/, "")) : own;
}

/** The summary line's parts: each name once, in the order first given, its tokens together. */
function summaryOf(parts: BriefingPart[]): string {
  const sums = new Map<string, number>();
  for (const part of parts) sums.set(summaryName(part), (sums.get(summaryName(part)) ?? 0) + part.tokens.value);
  return [...sums].map(([name, n]) => `${name} ${short(n)}`).join(" · ");
}

/** What the harness gave the model at one point: folded to its title, its total and each part's tokens, always in
 * place (no toggle hides it); open, one row per part; a part open, its exact recorded text. */
function BriefingBlock({ briefing, needle }: { briefing: Briefing; needle: string }) {
  const [open, setOpen] = useState(false);
  const total = briefing.tokens.value;
  return (
    <div className={`briefing ${briefing.reason}`}>
      <button type="button" className="fold" onClick={() => setOpen(!open)} aria-expanded={open}>
        <Icon icon={MARK.disclose} className={`chevron${open ? " open" : ""}`} />
        <span className="briefing-title">{BRIEFING_TITLE[briefing.reason]}</span>
        <span className="briefing-summary">
          {briefing.parts.length === 0 ? "nothing recorded"
            : `≈${short(total)} ${noun(total, "token", "tokens")} — ${summaryOf(briefing.parts)}`}
        </span>
      </button>
      {briefing.note && <p className="note briefing-note">{briefing.note}</p>}
      {open && (
        <div className="briefing-parts">
          {briefing.parts.length === 0 && <p className="muted sub-note">The transcript recorded nothing the harness gave the model here.</p>}
          {briefing.parts.map((part, i) => <BriefingRow key={i} part={part} needle={needle} />)}
        </div>
      )}
    </div>
  );
}

/** Whose a part is, as the Context view groups it: its group; "mixed" when it spans groups, the split in the
 * tooltip; untraced counts as harness, named apart. */
function PartGroup({ part }: { part: BriefingPart }) {
  if (part.authority) {
    return <span className="part-group"><GroupSwatch authority={part.authority} />{GROUP_WORDS[GROUP_OF[part.authority]]}</span>;
  }
  if (part.shares.length > 0) {
    const split = splitOf(part.shares.map((s) => [s.authority, s.tokens.value]));
    const words = `Mixed — ${part.shares.map((s) => `${s.label}: ${GROUP_WORDS[GROUP_OF[s.authority]]} ≈${count(s.tokens.value)}`).join(" · ")}`;
    return (
      <Tip text={words} className="part-group">
        <span role="img" aria-label={words} className="swatches">
          {GROUPS.filter((g) => split[g] > 0).map((g) => <span key={g} className={`swatch g-${g}`} />)}
        </span>
        mixed
      </Tip>
    );
  }
  return <span className="part-group"><GroupSwatch authority={null} />{GROUP_WORDS.harness}<UntracedIcon /></span>;
}

/** One part: its name, what tells it apart, how many entries, whose, and its tokens; open, its source in
 * Configuration when the report has it, its split when it spans groups, and its exact recorded text. */
function BriefingRow({ part, needle }: { part: BriefingPart; needle: string }) {
  const { texts, homeDir, sources, go } = useContext(BriefingContext);
  const [open, setOpen] = useState(false);
  const [tree, setTree] = useState(false); // JSON (the tool definitions) may be read as a tree; exact text first
  const text = texts[part.text_ref];
  const json = useMemo(() => text !== undefined && /^\s*[[{]/.test(text) && isJson(text), [text]); // the tool definitions
  const mechanism = part.point.mechanism;
  const detail = mechanism === "instruction_file" || mechanism === "config_file" ? home(part.detail, homeDir) : part.detail;
  const link = (id: string | null, words: string) => {
    if (id === null || !sources.has(id)) return null;
    const to = configHref({ kind: "source", id });
    return <a className="entity-link" href={to} onClick={(e) => { e.preventDefault(); go(to); }}>{words}</a>;
  };
  return (
    <div className="briefing-part">
      <button type="button" className="fold" onClick={() => setOpen(!open)} aria-expanded={open}>
        <Icon icon={MARK.disclose} className={`chevron${open ? " open" : ""}`} />
        <span className="tool-name">{part.label}{part.count !== null ? ` (${count(part.count)})` : ""}</span>
        {detail && <span className="tool-summary">{detail}</span>}
        {part.point.description && <WhatIcon text={part.point.description} />}
        <PartGroup part={part} />
        <span className="num">{short(part.tokens.value)}</span>
      </button>
      {open && (
        <div className="tool-body">
          {link(part.source_id, "Its source in Configuration")}
          {part.shares.length > 0 && (
            <>
              <div className="label muted">split across groups, estimated</div>
              {part.shares.map((share, i) => (
                <div key={i} className="share">
                  <GroupSwatch authority={share.authority} />
                  <span>{share.label}</span>
                  <span className="muted">≈{counted(share.tokens.value, "token", "tokens")}</span>
                  {link(share.source_id, "in Configuration")}
                </div>
              ))}
            </>
          )}
          <div className="label muted">what was recorded, exactly</div>
          {json && (
            <div className="segmented" role="group" aria-label="Show the text">
              <button type="button" aria-pressed={!tree} onClick={() => setTree(false)}>Exact text</button>
              <button type="button" aria-pressed={tree} onClick={() => setTree(true)}>Tree</button>
            </div>
          )}
          {text === undefined ? <p className="muted sub-note">Its text didn't come with the session.</p>
            : json && tree ? <JsonTree text={text} verbatim />
            : <pre className="code"><Marked text={text} needle={needle} /></pre>}
        </div>
      )}
    </div>
  );
}
