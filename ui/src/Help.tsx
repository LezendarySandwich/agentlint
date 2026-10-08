// The keys, and what every colour and icon means — one keypress away, so nothing needs a label.
import { useEffect, useRef } from "react";
import type { Authority, Confidence, Load, Severity } from "./contract";
import { AUTHORITY_WORDS, CONFIDENCE_WORDS, GROUP_MEANING, GROUP_WORDS, LOAD_WORDS, SEVERITY_WORDS, VISIBILITIES, VISIBILITY_WORDS } from "./format";
import { authoritiesIn, GROUPS, LOADS, SEVERITIES } from "./model";
import { LIST_KEYS } from "./listNav";
import { PAGES, viewKey, VIEW_NAMES } from "./route";
import {
  AllClearIcon, ArchivedIcon, ConfidenceIcon, GroupSwatch, Glyph, Icon, Kbd, LapsedIcon, LoadIcon, LockIcon, MARK, NAV_ICON, ROLE_ICON,
  SeenIcon, SeverityIcon, SuppressedIcon, UnseenIcon, UntracedIcon, VisibilityIcon,
} from "./ui";

/** What each role's mark stands for; the entity's own kind, from the adapter, is in its tooltip. */
const ROLE_WORDS: [string, string][] = [
  ["skill", "A skill"], ["agent", "An agent, or a subagent a session started"], ["tool", "A tool, or a call to one in a session"],
  ["command", "A command"], ["path", "A path"], ["domain", "A domain"], ["other", "Anything else the agent is offered"],
];

/** The controls' marks, once each. */
const CONTROLS: [keyof typeof MARK, string][] = [
  ["disclose", "Open or fold a group, a listing, a call"], ["close", "Close, or clear a filter"], ["search", "Search"],
  ["copy", "Copy"],
];

/** The keys, a list's first. */
const KEYS: [string[], string][] = [
  ...LIST_KEYS, // a list's keys, from the handler that reads them (listNav.ts)
  [["→", "←"], "Open in place, close: an overview row (Space too), a listing's parts in Configuration"],
  [["j", "k"], "In a session: from prompt to prompt (↓ and ↑ still move the list)"],
  [["s"], "In a finding: dismiss it, with a reason"],
  [["c"], "In a finding: copy its id"],
  [["p"], "Switch to another project"],
  [["0"], "The home: choose a harness and a project"],
  [["Tab"], "On the home: the other list, harnesses or projects"],
  [["r"], "Read the configuration again"],
  [["?"], "This help"],
];

export function Help({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    // While open, the help takes every key: Escape or ? closes it, nothing reaches the views beneath.
    const pressed = (event: KeyboardEvent) => {
      event.stopImmediatePropagation();
      if (event.key === "Escape" || event.key === "?") {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", pressed, true);
    return () => window.removeEventListener("keydown", pressed, true);
  }, [onClose]);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    // Modal where the browser can (the page beneath is inert, the scrim is its ::backdrop); open in place where not.
    const shown = dialog.current;
    if (!shown || shown.open) return;
    if (typeof shown.showModal === "function") shown.showModal();
    else shown.setAttribute("open", "");
  }, []);
  const capital = (s: string) => s[0].toUpperCase() + s.slice(1);
  return (
    <dialog ref={dialog} className="help" aria-label="Shortcuts and icons"
            onCancel={(e) => { e.preventDefault(); onClose(); }}
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="help-body">
        <header className="detail-head">
          <h2>Shortcuts and icons</h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close"><Icon icon={MARK.close} size={16} /></button>
        </header>
        <div className="help-grid">
          <section>
            <h3>Views</h3>
            {PAGES.map((view) => (  // one line each: a key beside its view, never a row of keys against a list; none for one reached from another
              <div key={view} className="legend-row">
                <span className="keys">{viewKey(view) && <Kbd>{viewKey(view)}</Kbd>}</span> <Icon icon={NAV_ICON[view]} /> {VIEW_NAMES[view]}
              </div>
            ))}
            <h3>Keys</h3>
            {KEYS.map(([keys, what]) => (
              <div key={what} className="legend-row">
                <span className="keys">{keys.map((k) => <Kbd key={k}>{k}</Kbd>)}</span> {what}
              </div>
            ))}
          </section>
          <section>
            <h3>Severity</h3>
            {SEVERITIES.map((s: Severity) => <div key={s} className="legend-row"><SeverityIcon severity={s} /> {SEVERITY_WORDS[s]}</div>)}
            <h3>Confidence</h3>
            {(["certain", "measured", "suggestive"] as Confidence[]).map((c) => (
              <div key={c} className="legend-row"><ConfidenceIcon confidence={c} /> {CONFIDENCE_WORDS[c]}</div>
            ))}
            <h3>Lifecycle</h3>
            <div className="legend-row"><SuppressedIcon /> Suppressed — you dismissed it</div>
            <div className="legend-row"><LapsedIcon /> Dismissed before, but what it rests on changed</div>
          </section>
          <section>
            <h3>Who put it there</h3>
            {GROUPS.map((g) => (
              <div key={g}>
                <div className="legend-row"><GroupSwatch group={g} /> {GROUP_WORDS[g]} — {GROUP_MEANING[g]}</div>
                {authoritiesIn(g).map((a: Authority) => <div key={a} className="legend-row nested muted">{a} — {AUTHORITY_WORDS[a]}</div>)}
              </div>
            ))}
            <h3>When it loads</h3>
            {LOADS.map((l: Load) => <div key={l} className="legend-row"><LoadIcon load={l} /> {capital(LOAD_WORDS[l])}</div>)}
            <h3>What the agent is shown of a skill or agent</h3>
            {VISIBILITIES.map((v) => (
              <div key={v} className="legend-row"><VisibilityIcon visibility={v} /> {VISIBILITY_WORDS[v]}</div>
            ))}
            <div className="legend-row"><VisibilityIcon visibility={undefined} /> {VISIBILITY_WORDS.unknown}</div>
            <h3>Also</h3>
            <div className="legend-row"><LockIcon reason="for example, managed by your organisation" /> Can't be tested by removing it</div>
            <div className="legend-row"><SeenIcon seen={1} of={1} /> Its text was seen in recent sessions</div>
            <div className="legend-row"><UnseenIcon reason="no recent session showed it" /> Its text was not seen in recent sessions</div>
            <div className="legend-row"><UntracedIcon /> Text no configured source explains</div>
            <div className="legend-row"><Glyph icon={MARK.what} text="What this is" className="muted" /> What a text you didn't write is</div>
            <div className="legend-row"><AllClearIcon /> Nothing found</div>
            <div className="legend-row"><ArchivedIcon /> A session kept by agentlint's archive after the harness deleted it</div>
            <div className="legend-row"><Glyph icon={MARK.startedHere} text="Started here" className="muted" /> The project agentlint ui was started in</div>
            <h3>What a skill or agent is</h3>
            {ROLE_WORDS.map(([role, words]) => (
              <div key={role} className="legend-row"><Glyph icon={ROLE_ICON[role]} text={words} className="muted" /> {words}</div>
            ))}
            <h3>Controls</h3>
            {CONTROLS.map(([mark, words]) => (
              <div key={mark} className="legend-row"><Glyph icon={MARK[mark]} text={words} className="muted" /> {words}</div>
            ))}
          </section>
        </div>
      </div>
    </dialog>
  );
}
