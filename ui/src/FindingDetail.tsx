// One finding, opened beside the list: its title, what to do after an arrow, the evidence (the lines it points at, or
// the members it sets side by side), why, and a short property list; s dismisses it with a reason (with no server
// behind the page to write through, it copies the suppress command), c copies its id, Esc closes (docs/tech_impl/11-ui.md, "Detail beside the list").
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import type { Finding, Member, Report } from "./contract";
import type { Texts } from "./data";
import type { DismissApi } from "./Dismissed";
import { checkName, CONFIDENCE_WORDS, count, counted, home, noun, packageName, shortPath, suppressCommand, VISIBILITY_SHORT } from "./format";
import { configHref, useKeys } from "./route";
import { Excerpt } from "./Text";
import { copyText, CopyButton, GroupSwatch, Icon, Kbd, MARK, More, SeverityIcon, Tip, VisibilityIcon } from "./ui";

interface Props {
  report: Report;
  finding: Finding;
  texts: Texts | null; // null while they unpack
  go: (to: string) => void;
  onClose: () => void;
  dismiss?: DismissApi; // Dismiss and Show again write through it
  onDismissed?: (id: string, dismissed: boolean) => void; // what the server did, for the list
}

const FIRST = 3; // what it rests on, before "N more"

const escaped = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** A figure is shown only when no line of the title or the text already says it: its label (whole, in any case) and
 * its number (whole: never the 10 in "10x" or in "1,106") on the same line. Not a bare substring: a package's
 * "106 tokens" is not said by another's "agent listing entries 106". */
const said = ({ label, value }: Finding["figures"][number], finding: Finding) => {
  const named = new RegExp(`(?<![\\w@:./-])${escaped(label)}(?![\\w@:/-]|\\.\\w)`, "i");
  const numbered = new RegExp(`(?<![\\w.,])(?:${[count(value), String(value)].map(escaped).join("|")})(?![\\w]|[.,]\\d)`);
  return [finding.title, ...finding.detail.split("\n")].some((line) => named.test(line) && numbered.test(line));
};

export function FindingDetail({ report, finding, texts, go, onClose, dismiss, onDismissed }: Props) {
  const { snapshot, labels } = report;
  const byId = useMemo(() => new Map(snapshot.sources.map((s) => [s.id, s])), [snapshot]);
  const [allWhere, setAllWhere] = useState(false);
  const [allSources, setAllSources] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [asking, setAsking] = useState(false); // the Dismiss form is open
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState<string | null>(null); // the server's refusal, in its words
  const [note, setNote] = useState<string | null>(null); // what the server did
  // A Dismiss or Show again answered after you moved to another finding is that finding's, not this one's: each request
  // takes a number, moving on takes the next, and an answer whose number isn't the latest changes nothing here (the
  // list is still told, for the finding it was about).
  const request = useRef(0);
  // Where focus goes once the control that held it is gone: the foot's action, Dismiss or Show again, whichever shows.
  const action = useRef<HTMLButtonElement | null>(null);
  const refocus = useRef(false);
  useEffect(() => {
    if (!refocus.current || !action.current) return;
    refocus.current = false;
    action.current.focus();
  });
  useEffect(() => {
    request.current += 1;
    refocus.current = false;
    setAllWhere(false);
    setAllSources(false);
    setCopied(null);
    setAsking(false);
    setReason("");
    setBusy(false);
    setRefused(null);
    setNote(null);
  }, [finding.id]);
  useEffect(() => {
    if (!copied) return;
    const done = setTimeout(() => setCopied(null), 1200);
    return () => clearTimeout(done);
  }, [copied]);

  const members = finding.members ?? [];
  const suppress = suppressCommand(finding.id, snapshot.cwd, report.home_dir);
  const link = (to: string) => ({ href: to, onClick: (e: MouseEvent) => { e.preventDefault(); go(to); } });
  const copy = useCallback((text: string, what: string) => { void copyText(text).then(() => setCopied(what)); }, []);
  useKeys(useCallback((key: string) => {
    if (key === "s" && !finding.suppressed) {
      if (dismiss) { setAsking(true); setRefused(null); } else copy(suppress, "Copied the suppress command");
    } else if (key === "c") copy(finding.id, "Copied the id");
    else return false;
    return true;
  }, [copy, suppress, finding.id, finding.suppressed, dismiss]));
  const cancel = () => { setAsking(false); setRefused(null); refocus.current = true; };
  const dismissIt = () => {
    if (!dismiss || !reason.trim() || busy) return;
    const id = finding.id;
    const mine = ++request.current;
    setBusy(true);
    setRefused(null);
    dismiss.suppress(id, snapshot.cwd, snapshot.harness, reason.trim()).then((answer) => {
      onDismissed?.(id, true);
      if (mine !== request.current) return; // you moved on: said nowhere but in the list
      setBusy(false);
      setAsking(false);
      setReason("");
      setNote(answer.note);
      refocus.current = true; // to Show again
    }, (failed: Error) => {
      if (mine !== request.current) return;
      setBusy(false);
      setRefused(failed.message);
    });
  };
  const showAgain = () => {
    if (!dismiss || busy) return;
    const id = finding.id;
    const mine = ++request.current;
    setBusy(true);
    setRefused(null);
    dismiss.unsuppress(id).then(() => {
      onDismissed?.(id, false);
      if (mine !== request.current) return;
      setBusy(false);
      setNote("Shown again, in every project.");
      refocus.current = true; // to Dismiss
    }, (failed: Error) => {
      if (mine !== request.current) return;
      setBusy(false);
      setRefused(failed.message);
    });
  };

  /** A location as the source it points into, by id, and where to open it: that source, at the line. */
  const sourceAt = (l: Finding["locations"][number]) => {
    const source = byId.get(l.source_id);
    return { source, to: source ? configHref({ kind: "source", id: source.id }, l.line ? { line: String(l.line) } : {}) : null };
  };
  const first = finding.locations[0];
  const pointed = first?.line ? sourceAt(first) : null;
  const content = pointed?.source?.content;
  const named = pointed?.source ? pointed.source.provenance.file?.split("/").pop() ?? labels[pointed.source.id] ?? pointed.source.id : "";
  const ref = content && "text_ref" in content ? content.text_ref : null;
  // the excerpt holds its place while the texts unpack; a text the report doesn't carry shows none
  const excerpt = ref && pointed?.to && (!texts || texts[ref] !== undefined);
  const figures = finding.figures.filter((f) => !said(f, finding));
  const rests = allSources ? finding.sources : finding.sources.slice(0, FIRST);
  /** Shown as its source's file and the place inside it; text no file holds, by its source's label. */
  const where = (l: Finding["locations"][number], i: number) => {
    const { source, to } = sourceAt(l);
    if (!source) return <li key={i}><span className="muted">{l.source_id} (no longer configured)</span></li>;
    const { file, anchor } = source.provenance;
    const at = `${anchor ? `#${anchor}` : ""}${l.line ? `:${l.line}` : ""}`;
    const text = file ? <code>{shortPath(file, report.home_dir)}{at}</code> : <>{labels[source.id] ?? source.id}{at}</>;
    return (
      <li key={i}>
        {file ? <Tip text={`${home(file, report.home_dir)}${at}`}><a {...link(to!)}>{text}</a></Tip> : <a {...link(to!)}>{text}</a>}
        {file && <CopyButton text={file} label="Copy the path" />}
      </li>
    );
  };

  return (
    <aside className="detail" aria-label="Finding">
      <header className="detail-meta">
        <SeverityIcon severity={finding.severity} />
        <span>{checkName(finding.check)}</span>
        {finding.suppressed && <span>· suppressed</span>}
        {finding.suppression_lapsed && <span>· dismissed before; what it rests on changed</span>}
        <button type="button" className="icon-button" onClick={onClose} aria-label="Close"><Icon icon={MARK.close} size={16} /></button>
      </header>
      <h2>{finding.title}</h2>
      {finding.suggested_action && <p className="act"><span className="arrow" aria-hidden="true">→</span> {finding.suggested_action}</p>}
      {excerpt && (
        <Excerpt text={texts?.[ref]} line={first!.line!} name={named} severity={finding.severity}
                 onOpen={() => go(pointed.to!)} />
      )}
      {members.length > 0 && <Members members={members} report={report} go={go} />}
      <p className="why">{finding.detail}</p>
      <dl className="props">
        <dt>Check</dt>
        <dd>{checkName(finding.check)} <code className="muted">{finding.check}</code></dd>
        {first && (
          <>
            <dt>Where</dt>
            <dd>
              <ul className="links">{(allWhere ? finding.locations : [first]).map(where)}</ul>
              {!allWhere && finding.locations.length > 1 && (
                <button type="button" className="text-button" onClick={() => setAllWhere(true)}
                        aria-label={counted(finding.locations.length - 1, "more location", "more locations")}>
                  +{count(finding.locations.length - 1)}
                </button>
              )}
            </dd>
          </>
        )}
        {finding.sources.length > 0 && (
          <>
            <dt>Rests on</dt>
            <dd>
              <ul className="links">
                {rests.map((id) => {
                  const source = byId.get(id);
                  return (
                    <li key={id}>
                      {source && <GroupSwatch authority={source.provenance.authority} />}
                      {source ? <a {...link(configHref({ kind: "source", id: id }))}>{labels[id] ?? id}</a> : <span className="muted">{id} (no longer configured)</span>}
                    </li>
                  );
                })}
              </ul>
              {!allSources && <More inline hidden={finding.sources.length - FIRST} onClick={() => setAllSources(true)} />}
            </dd>
          </>
        )}
        {finding.confidence !== "certain" && (
          <>
            <dt>Confidence</dt>
            <dd>{CONFIDENCE_WORDS[finding.confidence]}</dd>
          </>
        )}
        {figures.map((f) => (
          <FigureRow key={f.label} label={f.label} value={f.value} unit={noun(f.value, f.unit_one, f.unit)} plural={f.unit} />
        ))}
      </dl>
      {asking && !finding.suppressed && (
        <form className="dismiss-form" aria-label="Dismiss this finding" aria-busy={busy}
              onSubmit={(e) => { e.preventDefault(); dismissIt(); }}
              onKeyDown={(e) => { // the form keeps its keys: Escape closes it, never the finding
                if (e.key === "Escape") { e.preventDefault(); if (!busy) cancel(); }
                e.stopPropagation();
              }}>
          <input className="field" value={reason} autoFocus disabled={busy} maxLength={1000} spellCheck
                 aria-label="Why dismiss it" placeholder="Why dismiss it? Required" onChange={(e) => setReason(e.target.value)} />
          <button type="submit" className="toggle on" disabled={busy || !reason.trim()}>{busy ? "Dismissing…" : "Dismiss"}</button>
          <button type="button" className="toggle" onClick={cancel} disabled={busy}>Cancel</button>
          {refused
            ? <p className="refusal" role="alert">Not dismissed: {refused}</p>
            : <p className="muted">Hidden in every project until what it rests on changes; Dismissed lists it, to show it again.</p>}
        </form>
      )}
      <div className="keyhints">
        {dismiss && !finding.suppressed && !asking && (
          <button type="button" className="text-button" ref={action} onClick={() => { setAsking(true); setRefused(null); }}><Kbd>s</Kbd> Dismiss</button>
        )}
        {dismiss && finding.suppressed && (
          <button type="button" className="text-button" ref={action} onClick={showAgain} disabled={busy}>{busy ? "Showing again…" : "Show again"}</button>
        )}
        {!dismiss && !finding.suppressed && (
          <Tip text={suppress}>
            <button type="button" className="text-button" onClick={() => copy(suppress, "Copied the suppress command")}>
              <Kbd>s</Kbd> copy suppress
            </button>
          </Tip>
        )}
        <Tip text={finding.id}>
          <button type="button" className="text-button" onClick={() => copy(finding.id, "Copied the id")}><Kbd>c</Kbd> copy id</button>
        </Tip>
        <button type="button" className="text-button" onClick={onClose}><Kbd>Esc</Kbd> close</button>
        <span className="copied" role="status" aria-live="polite">{copied ?? note ?? ""}</span>
      </div>
      {refused && !asking && <p className="refusal" role="alert">Not shown again: {refused}</p>}
    </aside>
  );
}

/** "<label> <value> <unit>" as a property, the label as written (a package id or a skill name is never recased),
 * the unit in the form Python gives for the value ("1 run", "2 runs"), left out when it only repeats the label
 * (never "sessions 2 sessions"). */
function FigureRow({ label, value, unit, plural }: { label: string; value: number; unit: string; plural: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{count(value)}{plural === label ? "" : ` ${unit}`}</dd>
    </>
  );
}

/** The members a finding sets side by side (duplicate-name, trigger-overlap), one column each, two to a line: its
 * name, kind, package by its short name (else its scope), how the agent sees it, its description clamped, any copy
 * or symlink, and a link to its source, by the source's label. */
function Members({ members, report, go }: { members: Member[]; report: Report; go: (to: string) => void }) {
  const { snapshot } = report;
  const byId = new Map(snapshot.sources.map((s) => [s.id, s]));
  const place = (m: Member) => (m.package ? packageName(report.package_labels, m.package) : m.scope);
  const called = (m: Member) => (members.some((o) => o !== m && o.name === m.name) && place(m) ? `${m.name} (${place(m)})` : m.name);
  return (
    <ul className="members" aria-label="Side by side">
      {members.map((m) => {
        const source = byId.get(m.source);
        const twin = members.find((o) => o.entity === m.identical_to);
        return (
          <li key={m.entity}>
            <span className="name">{m.name}</span>
            <span className="muted">
              {m.kind}
              {m.package ? <> · <Tip text={m.package}>{packageName(report.package_labels, m.package)}</Tip></> : m.scope ? ` · ${m.scope}` : ""}
            </span>
            <span className="muted seen"><VisibilityIcon visibility={m.seen} /> {VISIBILITY_SHORT[m.seen ?? "unknown"]}</span>
            {m.description && <Tip text={m.description} className="desc">{m.description}</Tip>}
            {twin && <span className="muted">identical to {called(twin)}</span>}
            {m.symlink && <span className="muted">a symlink to {home(m.symlink, report.home_dir)}</span>}
            {source && (
              <a href={configHref({ kind: "source", id: source.id })} onClick={(e) => { e.preventDefault(); go(configHref({ kind: "source", id: source.id })); }}>
                {report.labels[source.id] ?? source.id}
              </a>
            )}
          </li>
        );
      })}
    </ul>
  );
}
