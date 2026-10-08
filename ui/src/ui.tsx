// The small pieces every view is built from: a colour or an icon for each property, with its words in a
// tooltip — severity, authority, when a source loads, confidence, lifecycle (docs/tech_impl/11-ui.md).
// Every icon the UI draws is named here, by what it means: one glyph per meaning, each in the `?` legend.
import {
  Activity, Archive, BellOff, BookOpen, Bot, Boxes, Check, ChevronRight, Circle, CircleDashed, CircleDot, CircleQuestionMark, CircleSlash, Copy,
  Eye, EyeOff, File, FlaskConical, Ghost, Globe, History, Layers, LayoutDashboard, ListChecks, ListX, Lock, MapPin, MessagesSquare, MonitorPlay, MousePointerClick, Power,
  Puzzle, ScrollText, Search, SlidersHorizontal, Split, RotateCcw, Stethoscope, ScanText, Table2, Terminal, Wrench, X, type LucideIcon,
} from "lucide-react";
import {
  createContext, Fragment, useContext, useEffect, useId, useRef, useState, useSyncExternalStore, type CSSProperties, type MouseEvent,
  type ReactNode, type RefObject,
} from "react";
import { createPortal } from "react-dom";
import type { Authority, Confidence, Load, Severity, Visibility } from "./contract";
import {
  AUTHORITY_WORDS, CONFIDENCE_WORDS, count, GROUP_MEANING, GROUP_WORDS, LOAD_WORDS, seenIn, SEVERITY_WORDS, severityCount, VISIBILITY_WORDS,
} from "./format";
import { authoritiesIn, GROUP_OF, GROUPS, SEVERITIES, type Group, type Mix, type Split as GroupSplit } from "./model";
import type { View } from "./route";

/** The marks: a state or property of a row, or a control. Each glyph means one thing and nothing else. */
export const MARK = {
  disclose: ChevronRight, close: X, search: Search, copy: Copy, allClear: Check,
  always: Power, perMessage: MessagesSquare, onInvoke: MousePointerClick, conditional: Split, notInContext: CircleSlash,
  certain: Circle, measured: CircleDot, suggestive: CircleDashed,
  untestable: Lock, seen: Eye, unseen: EyeOff, untraced: Ghost, suppressed: BellOff, lapsed: RotateCcw, what: CircleQuestionMark,
  archived: Archive, startedHere: MapPin,
} satisfies Record<string, LucideIcon>;

/** agentlint's name and mark: a logo, on the home only. */
export function Wordmark() {
  return (
    <div className="wordmark">
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M3 4.5h10M3 8h6.5M3 11.5h8" /></svg>
      agentlint
    </div>
  );
}

/** Each view beside its name in the sidebar, and nowhere else. */
export const NAV_ICON: Record<View, LucideIcon> = {
  overview: LayoutDashboard, findings: ListChecks, config: Layers, entities: Boxes, context: MonitorPlay, sessions: ScrollText,
  diagnostics: Stethoscope, tests: FlaskConical, changes: History, settings: SlidersHorizontal, dismissed: ListX,
  results: Table2, instructions: ScanText, jobs: Activity,
};

/** What kind of thing an entity is; the session reader marks tool calls and agents the same way. */
export const ROLE_ICON: Record<string, LucideIcon> = {
  skill: BookOpen, agent: Bot, tool: Wrench, command: Terminal, path: File, domain: Globe, other: Puzzle,
};

/** A Lucide icon at one stroke: 1.5 px whatever its size — 14 in rows, 16 in heads and for close. */
export function Icon({ icon: Drawn, size = 14, className }: { icon: LucideIcon; size?: 14 | 16; className?: string }) {
  return <Drawn size={size} strokeWidth={1.5} absoluteStrokeWidth className={className} aria-hidden="true" />;
}

/** Which tooltip is showing, one per tree: held by `TipProvider` (App and the home each hold one), never in a module
 * global. Each Tip subscribes to whether it is the one, so a hover re-renders only the Tip that starts or stops showing. */
interface TipStore {
  showing: () => string | null;
  show: (id: string) => void; // an outer tooltip gives way to the one inside it: the innermost hovered wins
  hide: (id: string) => void; // only its own: leaving a Tip that already gave way hides nothing
  subscribe: (listener: () => void) => () => void;
}

function tipStore(): TipStore {
  let showing: string | null = null;
  const listeners = new Set<() => void>();
  const set = (next: string | null) => {
    if (next === showing) return;
    showing = next;
    for (const listener of listeners) listener();
  };
  return {
    showing: () => showing,
    show: (id) => set(id),
    hide: (id) => { if (showing === id) set(null); },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
}

const TipContext = createContext<TipStore | null>(null);

export function TipProvider({ children }: { children: ReactNode }) {
  const [store] = useState(tipStore);
  return <TipContext.Provider value={store}>{children}</TipContext.Provider>;
}

const alone = () => () => {}; // a Tip outside any provider subscribes to nothing and shows on its own

/** Words on hover or focus, shown at once — the browser's own tooltip takes a second and can't be styled. */
export function Tip({ text, children, className, style }: { text: string; children: ReactNode; className?: string; style?: CSSProperties }) {
  const id = useId();
  const store = useContext(TipContext);
  const [own, setOwn] = useState(false);
  const chosen = useSyncExternalStore(store?.subscribe ?? alone, () => store?.showing() === id);
  const [box, setBox] = useState<DOMRect | null>(null);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => () => store?.hide(id), [store, id]); // unmounted while showing: nothing is left showing
  const show = () => {
    setBox(ref.current?.getBoundingClientRect() ?? null);
    if (store) store.show(id);
    else setOwn(true);
  };
  const hide = () => {
    if (store) store.hide(id);
    else setOwn(false);
  };
  const shown = (store ? chosen : own) && box !== null;
  const below = box !== null && box.top < 48;
  return (
    <span ref={ref} className={className} style={style} onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}>
      {children}
      {shown &&
        createPortal(
          <div
            className={`tooltip${below ? " below" : ""}`}
            role="tooltip"
            style={{
              left: Math.min(Math.max(box.left + box.width / 2, 140), window.innerWidth - 140),
              top: below ? box.bottom + 6 : box.top - 6,
            }}
          >
            {text}
          </div>,
          document.body,
        )}
    </span>
  );
}

/** An icon that stands for words: named for screen readers, explained on hover. */
export function Glyph({ icon, text, className, size = 14 }: { icon: LucideIcon; text: string; className?: string; size?: 14 | 16 }) {
  return (
    <Tip text={text} className={`glyph ${className ?? ""}`}>
      <span role="img" aria-label={text}>
        <Icon icon={icon} size={size} />
      </span>
    </Tip>
  );
}

/** Severity is drawn, not taken from Lucide: a filled octagon, a filled triangle, a circled i in outline. */
const SEVERITY_SHAPE: Record<Severity, ReactNode> = {
  error: <path fill="currentColor" d="M3.8 .8h4.4l3 3v4.4l-3 3H3.8l-3-3V3.8Z" />,
  warn: <path fill="currentColor" d="M6 .9 11.4 10.8H.6Z" />,
  info: (
    <>
      <circle cx="6" cy="6" r="4.9" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M6 5.3v3.2M6 3.4v.1" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </>
  ),
};

function SeverityShape({ severity, size }: { severity: Severity; size: 14 | 16 }) {
  return (
    <svg className={`severity sev-${severity}`} width={size} height={size} viewBox="0 0 12 12" aria-hidden="true">
      {SEVERITY_SHAPE[severity]}
    </svg>
  );
}

export function SeverityIcon({ severity, size = 14, bare }: { severity: Severity; size?: 14 | 16; bare?: boolean }) {
  if (bare) return <SeverityShape severity={severity} size={size} />; // inside something that already says what it is
  return (
    <Tip text={SEVERITY_WORDS[severity]} className="glyph">
      <span role="img" aria-label={SEVERITY_WORDS[severity]}>
        <SeverityShape severity={severity} size={size} />
      </span>
    </Tip>
  );
}

/** Findings by severity in one line — a glyph and an ink figure per severity, zeros left out — each a link to the
 * findings of that severity ("9 errors" on hover). `current` is the severity a list is filtered to: marked, and its
 * link (the caller's) clears the filter. */
export function CountLine({ mix, link, current }: {
  mix: Mix; link: (severity: Severity) => { href: string; onClick: (e: MouseEvent) => void }; current?: Severity | null;
}) {
  return (
    <span className="counts">
      {SEVERITIES.filter((s) => mix[s] > 0).map((severity) => (
        <Tip key={severity} text={current === severity ? `${severityCount(severity, mix[severity])}: showing only these` : severityCount(severity, mix[severity])}>
          <a {...link(severity)} aria-label={severityCount(severity, mix[severity])} className={current === severity ? "cur" : undefined}
             aria-current={current === severity ? "true" : undefined}>
            <SeverityShape severity={severity} size={14} />
            <span>{count(mix[severity])}</span>
          </a>
        </Tip>
      ))}
    </span>
  );
}

/** A mix in words: "1 error, 12 warnings". */
export function mixWords(mix: Mix): string {
  return SEVERITIES.filter((s) => mix[s] > 0).map((s) => severityCount(s, mix[s])).join(", ");
}

/** A severity mix packed into one cluster — error, warning, info, zeros left out — so a check with only errors leaves
 * no gaps; the cluster is right-aligned by whatever holds it. Each part says its count on hover; given `tip`, the
 * cluster is one image named by the mix in words, and `tip` is its one tooltip (a row's "3 findings rest on this"). */
export function SeverityMix({ mix, muted, tip }: { mix: Mix; muted?: boolean; tip?: string }) {
  const shown = SEVERITIES.filter((s) => mix[s] > 0);
  if (tip !== undefined) {
    return (
      <Tip text={tip} className="finding-count">
        <span className={`mix${muted ? " muted" : ""}`} role="img" aria-label={mixWords(mix)}>
          {shown.map((severity) => (
            <span key={severity} className="mix-part"><SeverityShape severity={severity} size={14} /><span>{count(mix[severity])}</span></span>
          ))}
        </span>
      </Tip>
    );
  }
  return (
    <span className={`mix${muted ? " muted" : ""}`}>
      {shown.map((severity, i) => (
        <Fragment key={severity}>
          {i > 0 && " "}
          <Tip text={severityCount(severity, mix[severity])}>
            <span className="mix-part" role="img" aria-label={severityCount(severity, mix[severity])}>
              <SeverityShape severity={severity} size={14} />
              <span>{count(mix[severity])}</span>
            </span>
          </Tip>
        </Fragment>
      ))}
    </span>
  );
}

/** Who put the text there, as one of the three groups' swatches; the authority inside the group, and what it means,
 * in the tooltip. `authority` null is untraced text, which counts as harness; `group` alone names a whole group. */
export function GroupSwatch({ authority, group, text }: { authority?: Authority | null; group?: Group; text?: string }) {
  const g = group ?? (authority ? GROUP_OF[authority] : "harness");
  const words = text ?? (authority ? `${GROUP_WORDS[g]} · ${authority} — ${AUTHORITY_WORDS[authority]}`
    : authority === null ? `${GROUP_WORDS[g]} · untraced — text no configured source explains`
    : `${GROUP_WORDS[g]} — ${GROUP_MEANING[g]}: ${authoritiesIn(g).join(", ")}`);
  return (
    <Tip text={words} className="glyph">
      <span role="img" aria-label={words} className={`swatch g-${g}`} />
    </Tip>
  );
}

export const LOAD_ICON: Record<Load, LucideIcon> = {
  always: MARK.always,
  per_message: MARK.perMessage,
  on_invoke: MARK.onInvoke,
  conditional: MARK.conditional,
  not_in_context: MARK.notInContext,
};

export function LoadIcon({ load, condition }: { load: Load; condition?: string | null }) {
  const words = `Loaded ${LOAD_WORDS[load]}${condition ? ` (${condition})` : ""}`;
  return <Glyph icon={LOAD_ICON[load]} text={words} className={`load-${load}`} />;
}

const CONFIDENCE_ICON: Record<Confidence, LucideIcon> = { certain: MARK.certain, measured: MARK.measured, suggestive: MARK.suggestive };

export function ConfidenceIcon({ confidence }: { confidence: Confidence }) {
  return <Glyph icon={CONFIDENCE_ICON[confidence]} text={CONFIDENCE_WORDS[confidence]} className={confidence === "suggestive" ? "muted" : undefined} />;
}

export const LapsedIcon = () => (
  <Glyph icon={MARK.lapsed} text="You dismissed this before; what it rests on has changed since" className="muted" />
);
export const LockIcon = ({ reason }: { reason: string | null }) => (
  <Glyph icon={MARK.untestable} text={`Can't be tested by removal: ${reason ?? "no reason given"}`} className="muted" />
);
export const SeenIcon = ({ seen, of }: { seen: number; of: number }) => (
  <Glyph icon={MARK.seen} text={`Its text was seen ${seenIn(seen, of)}`} className="muted" />
);
export const UnseenIcon = ({ reason }: { reason: string }) => (
  <Glyph icon={MARK.unseen} text={`Text not seen: ${reason}`} className="muted" />
);
export const UntracedIcon = () => (
  <Glyph icon={MARK.untraced} text="Untraced: a session showed this text, but no configured source explains it" className="muted" />
);
export const AllClearIcon = () => <Glyph icon={MARK.allClear} text="Nothing found" className="muted" />;
export const ArchivedIcon = () => (
  <Glyph icon={MARK.archived} text="The harness has deleted this transcript; agentlint's archive kept it" className="muted" />
);
export const SuppressedIcon = () => <Glyph icon={MARK.suppressed} text="Suppressed: you dismissed it" className="muted" />;

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}

/** Puts text on the clipboard: the browser's clipboard, else (where the browser withholds it) a hidden selection. */
export async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = Object.assign(document.createElement("textarea"), { value: text });
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
}

/** Copies on click; says so for a moment. */
export function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    await copyText(text);
    setDone(true);
    setTimeout(() => setDone(false), 1200);
  };
  return (
    <Tip text={done ? "Copied" : label}>
      <button type="button" className="icon-button" onClick={copy} aria-label={label}>
        <Icon icon={MARK.copy} />
      </button>
    </Tip>
  );
}

/** A proportional bar for a token figure, coloured by who put the text there. */
/** A cost as a bar, drawn split into the groups that hold its tokens (`splitOf`): stacked segments, each at least
 * 3 px, 2 px apart. `text` says on hover what the split is. */
export function CostBar({ value, max, split, text }: { value: number | null; max: number; split: GroupSplit; text?: string }) {
  const width = value && max ? Math.max(2, Math.round((value / max) * 100)) : 0;
  const total = GROUPS.reduce((n, g) => n + split[g], 0);
  const bar = (
    <span className="cost-bar" aria-hidden="true">
      <span className="fill" style={{ width: `${width}%` }}>
        {total > 0 && GROUPS.filter((g) => split[g] > 0).map((g) => (
          <span key={g} className={`seg g-${g}`} style={{ flexGrow: split[g] / total }} />
        ))}
      </span>
    </span>
  );
  return text ? <Tip text={text} className="cost-tip">{bar}</Tip> : bar;
}

/** A split in words, largest group first, e.g. "Installed 450 · Harness 50"; empty when one group holds it all. */
export function splitWords(split: GroupSplit): string {
  const held = GROUPS.filter((g) => split[g] > 0).sort((a, b) => split[b] - split[a]);
  return held.length > 1 ? held.map((g) => `${GROUP_WORDS[g]} ${count(split[g])}`).join(" · ") : "";
}

/** The search box: an icon, the field, and the key that focuses it. */
export function SearchBox({ value, onChange, placeholder, label, inputRef }: {
  value: string; onChange: (value: string) => void; placeholder: string; label?: string; inputRef: RefObject<HTMLInputElement | null>;
}) {
  return (
    <label className="search">
      <Icon icon={MARK.search} />
      <input ref={inputRef} type="search" value={value} placeholder={placeholder} aria-label={label ?? placeholder}
             onChange={(event) => onChange(event.target.value)} />
      <Kbd>/</Kbd>
    </label>
  );
}

/** A filter you switch on and off; what it does is in its tooltip. */
export function Toggle({ on, onClick, label, children }: { on: boolean; onClick: () => void; label: string; children: ReactNode }) {
  return (
    <Tip text={label}>
      <button type="button" className={`toggle${on ? " on" : ""}`} aria-pressed={on} aria-label={label} onClick={onClick}>
        {children}
      </button>
    </Tip>
  );
}

/** The one "N more" control: the end of a folded list (muted, under the rows' text), or the rest of a short list in
 * a detail (`inline`). A click shows what it hides, in place; nothing when nothing is hidden. */
export function More({ hidden, onClick, inline }: { hidden: number; onClick: () => void; inline?: boolean }) {
  if (hidden <= 0) return null;
  return (
    <button type="button" className={inline ? "text-button" : "more-row"} onClick={onClick}>
      {count(hidden)} more
    </button>
  );
}

/** What a source is, when the user didn't write it: one sentence, from the adapter. */
export function WhatIcon({ text }: { text: string }) {
  return <Glyph icon={MARK.what} text={text} className="muted what" />;
}

/** A collapsible group's header: chevron, icon, words, then its count — or `trail` in the count's place (a
 * severity mix, right-aligned). */
export function GroupHead({ open, onToggle, children, count, trail, tone }: {
  open: boolean; onToggle: () => void; children: ReactNode; count?: ReactNode; trail?: ReactNode; tone?: string;
}) {
  return (
    <button type="button" className={`group-head${tone ? ` ${tone}` : ""}`} aria-expanded={open} onClick={onToggle}>
      <Icon icon={MARK.disclose} className={`chevron${open ? " open" : ""}`} />
      {children}
      {trail !== undefined ? <span className="trail">{trail}</span> : <span className="count">{count}</span>}
    </button>
  );
}

/** How much of an entity the agent is shown: a full, half or dashed circle. */
export function VisibilityIcon({ visibility }: { visibility: Visibility | null | undefined }) {
  const key = visibility ?? "unknown";
  return (
    <Tip text={VISIBILITY_WORDS[key]} className="glyph">
      <span role="img" aria-label={VISIBILITY_WORDS[key]} className={`visibility ${key}`} />
    </Tip>
  );
}

export function RoleIcon({ role, label }: { role: string; label: string }) {
  return <Glyph icon={ROLE_ICON[role] ?? ROLE_ICON.other} text={label[0].toUpperCase() + label.slice(1)} className="muted" />;
}

/** Opens a `<dialog>` modally as it mounts, and as it unmounts closes it and puts focus back where it was — the button
 * that opened it — which the browser does only for a dialog that is closed, not one that is removed. */
export function useModal(dialog: RefObject<HTMLDialogElement | null>): void {
  // Where focus was as the dialog first renders: before an autoFocus inside it moves focus in.
  const [opener] = useState(() => (document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : null));
  useEffect(() => {
    const shown = dialog.current;
    if (!shown) return;
    if (!shown.open) {
      if (typeof shown.showModal === "function") shown.showModal();
      else shown.setAttribute("open", "");
    }
    return () => {
      if (shown.open) {
        if (typeof shown.close === "function") shown.close();
        else shown.removeAttribute("open");
      }
      if (opener?.isConnected) opener.focus();
    };
  }, [dialog, opener]);
}
