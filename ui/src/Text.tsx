// Text as its format reads best — Markdown formatted, JSON as a tree, the rest as written — with a way back to the
// raw text, line-numbered, where a finding's line and the search are marked. Configuration comes from packages you
// didn't write, so the Markdown is rendered safely: raw HTML is dropped and images are named, never fetched.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Icon, MARK } from "./ui";

const MARKS = 500;

/** Text with line numbers; a pointed-at line is marked and scrolled to, and the search is highlighted. */
export function SourceText({ text, line, needle }: { text: string; line: number | null; needle: string }) {
  const lines = useMemo(() => text.split("\n"), [text]);
  useEffect(() => {
    if (line) document.getElementById(`L${line}`)?.scrollIntoView?.({ block: "center" });
  }, [line, text]);
  if (text.length > 400_000) return <pre className="text">{text}</pre>; // too long to number line by line
  let marks = 0;
  const highlight = (value: string) => {
    if (needle.length < 2 || marks >= MARKS) return value;
    const parts: ReactNode[] = [];
    const lower = value.toLowerCase();
    let from = 0;
    for (let at = lower.indexOf(needle); at >= 0 && marks < MARKS; at = lower.indexOf(needle, at + needle.length)) {
      parts.push(value.slice(from, at), <mark key={at}>{value.slice(at, at + needle.length)}</mark>);
      from = at + needle.length;
      marks += 1;
    }
    parts.push(value.slice(from));
    return parts;
  };
  return (
    <pre className="text numbered">
      {lines.map((value, i) => (
        <span key={i} id={`L${i + 1}`} className={`line${line === i + 1 ? " pointed" : ""}`} data-n={i + 1}>
          {highlight(value)}
        </span>
      ))}
    </pre>
  );
}

/** Five lines around the line a finding points at, the line marked in its severity's colour. Labelled like "SKILL.md
 * lines 40 to 44"; not a tab stop (the Where link beside it is); a click opens the source at the line. Before the
 * text arrives (`text` undefined), five empty lines hold its place, so nothing jumps. */
export function Excerpt({ text, line, name, severity, onOpen }: {
  text: string | undefined; line: number; name: string; severity: string; onOpen: () => void;
}) {
  const all = text?.split("\n");
  if (all && line > all.length) return null; // the file changed since the finding: nothing is guessed
  const from = Math.max(1, line - 2);
  const to = all ? Math.min(all.length, line + 2) : from + 4;
  const numbers = Array.from({ length: to - from + 1 }, (_, i) => from + i);
  return (
    <div className={`excerpt${all ? "" : " holding"}`} role="group" aria-label={`${name} lines ${from} to ${to}`} onClick={onOpen}>
      {numbers.map((n) => (
        <div key={n} className={n === line ? `pointed sev-${severity}` : undefined}>
          <span className="n">{n}</span>
          <span>{all ? all[n - 1] || " " : " "}</span>
        </div>
      ))}
    </div>
  );
}

type Way = "markdown" | "json";

/** How a text can be shown formatted, if at all: by its declared format, and only if it parses. */
export function formatter(format: string | undefined, text: string): Way | null {
  if (format === "json") {
    try {
      JSON.parse(text);
      return "json";
    } catch {
      return null; // not valid JSON after all: shown as written
    }
  }
  return format === "markdown" || format === "frontmatter_doc" || format === "plain" ? "markdown" : null;
}

/** A frontmatter document's two parts: the `---` block, and the Markdown body. */
export function splitFrontmatter(text: string): { front: string | null; body: string } {
  const match = text.match(/^---\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/);
  return match ? { front: match[1], body: text.slice(match[0].length) } : { front: null, body: text };
}

const SAFE: Components = {
  img: ({ alt }) => <span className="md-image">[image{alt ? `: ${alt}` : ""}]</span>, // never loads anything
  a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>,
};

/** Markdown, rendered: GitHub flavour (tables, task lists), no raw HTML, no images. */
export function MarkdownView({ text }: { text: string }) {
  return (
    <div className="markdown">
      <Markdown remarkPlugins={[remarkGfm]} components={SAFE} skipHtml>{text}</Markdown>
    </div>
  );
}

/** The frontmatter: simple `key: value` lines as a table; anything else as written. */
function Frontmatter({ text }: { text: string }) {
  const lines = text.split(/\r?\n/).filter((line) => line.trim());
  const pairs = lines.map((line) => line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/));
  if (pairs.every(Boolean)) {
    return (
      <dl className="facts frontmatter">
        {pairs.map((pair) => pair && [<dt key={`k${pair[1]}`}>{pair[1]}</dt>, <dd key={`v${pair[1]}`}>{pair[2].replace(/^["']|["']$/g, "")}</dd>])}
      </dl>
    );
  }
  return <pre className="text frontmatter">{text}</pre>;
}

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

function Leaf({ value, verbatim }: { value: Json; verbatim: boolean }) {
  if (typeof value === "string") {
    if (!(value.includes("\n") || value.length > 100)) return <span className="json-string">"{value}"</span>;
    return verbatim
      ? <div className="json-string block">{value}</div> // as recorded: a briefing shows exact text, never rendered
      : <div className="json-string block"><MarkdownView text={value} /></div>; // descriptions are Markdown: read as such
  }
  return <span className={`json-${value === null ? "null" : typeof value}`}>{String(value)}</span>;
}

/** One node of the tree: a value, or an object or array that folds; an object with a name shows it folded. */
function JsonNode({ name, value, depth, verbatim }: { name: string | null; value: Json; depth: number; verbatim: boolean }) {
  const branch = value !== null && typeof value === "object";
  const entries: [string, Json][] = !branch ? [] : Array.isArray(value)
    ? value.map((item, i) => [String(i), item]) : Object.entries(value);
  const [open, setOpen] = useState(depth === 0);
  if (!branch) {
    return (
      <div className="json-row">
        {name !== null && <span className="json-key">{name}</span>}
        <Leaf value={value} verbatim={verbatim} />
      </div>
    );
  }
  const named = !Array.isArray(value) && typeof value.name === "string" ? value.name : null;
  return (
    <div className="json-row">
      <button type="button" className="fold json-fold" onClick={() => setOpen(!open)} aria-expanded={open}>
        <Icon icon={MARK.disclose} className={`chevron${open ? " open" : ""}`} />
        {name !== null && <span className="json-key">{name}</span>}
        {named && <span className="json-name">{named}</span>}
        <span className="muted">{Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`}</span>
      </button>
      {open && <div className="json-children">{entries.map(([key, item]) => <JsonNode key={key} name={key} value={item} depth={depth + 1} verbatim={verbatim} />)}</div>}
    </div>
  );
}

export function JsonTree({ text, verbatim = false }: { text: string; verbatim?: boolean }) {
  const value = useMemo(() => JSON.parse(text) as Json, [text]);
  return <div className="json-tree"><JsonNode name={null} value={value} depth={0} verbatim={verbatim} /></div>;
}

/** A source's text: formatted when its format allows, raw (numbered, marked) on request — or when a finding
 * points at a line or a search is on, so those marks are in view. Plain text starts as written. */
export function FormattedText({ text, format, line, needle }: { text: string; format: string | undefined; line: number | null; needle: string }) {
  const way = formatter(format, text);
  const [raw, setRaw] = useState(!way || format === "plain" || line !== null || needle.length >= 2);
  let shown: ReactNode;
  if (raw || !way) {
    shown = <SourceText text={text} line={line} needle={needle} />;
  } else if (way === "json") {
    shown = <JsonTree text={text} />;
  } else {
    const { front, body } = format === "frontmatter_doc" ? splitFrontmatter(text) : { front: null, body: text };
    shown = <>{front !== null && <Frontmatter text={front} />}<MarkdownView text={body} /></>;
  }
  return (
    <div className="formatted-text">
      {way && (
        <div className="segmented" role="group" aria-label="Show the text">
          <button type="button" aria-pressed={!raw} onClick={() => setRaw(false)}>
            {way === "json" ? "Tree" : "Formatted"}
          </button>
          <button type="button" aria-pressed={raw} onClick={() => setRaw(true)}>
            Raw, numbered
          </button>
        </div>
      )}
      {shown}
    </div>
  );
}
