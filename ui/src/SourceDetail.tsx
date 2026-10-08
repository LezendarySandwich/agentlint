// One source opened beside the list: who put it there, when it loads, what it costs, the findings that rest
// on it, what it provides, and its text — with the line a finding pointed at, and your search, marked.
import { useMemo, type MouseEvent, type ReactNode } from "react";
import type { Content, Finding, Load, Report, Source, Untraced } from "./contract";
import type { Texts } from "./data";
import { count, GROUP_WORDS, home, LISTING_OWN_PART, LOAD_WORDS, packageName, seenIn, short } from "./format";
import { GROUP_OF, isTestable, splitOf, tokensOf, type Row } from "./model";
import { configHref, href } from "./route";
import { FormattedText } from "./Text";
import { CopyButton, CostBar, GroupSwatch, Icon, LoadIcon, MARK, SeverityIcon, splitWords, Tip, UntracedIcon, VisibilityIcon } from "./ui";

function Head({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <header className="detail-head">
      {children}
      <button type="button" className="icon-button" onClick={onClose} aria-label="Close"><Icon icon={MARK.close} size={16} /></button>
    </header>
  );
}

/** A token figure as a property: "≈1,234" when estimated from text, "1,234 observed" (or derived) when measured. */
function tokenWords(content: Content): string {
  if (!("tokens" in content)) return "not known: its text was not seen";
  const { value, method } = content.tokens;
  return method === "estimated" ? `≈${count(value)}` : `${count(value)} ${method}`;
}

/** When it loads, as a property: its mark and the words, with the condition when it has one. */
function Loads({ load, condition }: { load: Load; condition?: string | null }) {
  return <><LoadIcon load={load} condition={condition} /> {LOAD_WORDS[load]}{condition ? <> · <code>{condition}</code></> : null}</>;
}

interface Props {
  report: Report;
  source: Source;
  texts: Texts | null;
  textsMissing?: string | null;
  line: number | null;
  needle: string;
  findings: Finding[];
  go: (to: string) => void;
  onClose: () => void;
}

export function SourceDetail({ report, source, texts, textsMissing = null, line, needle, findings, go, onClose }: Props) {
  const { snapshot, labels } = report;
  const seen = useMemo(() => new Map(snapshot.visibility), [snapshot]);
  const provided = snapshot.entities.filter((e) => e.provided_by === source.id);
  const content = source.content;
  const text = "text_ref" in content ? texts?.[content.text_ref] : undefined;
  const { authority, package: pkg, file, anchor } = source.provenance;
  const link = (to: string) => ({ href: to, onClick: (e: MouseEvent) => { e.preventDefault(); go(to); } });

  return (
    <aside className="detail" aria-label="Source">
      <Head onClose={onClose}>
        <GroupSwatch authority={authority} />
        <h2>{labels[source.id] ?? source.id}</h2>
      </Head>
      {source.provenance.point.description && <p className="detail-text">{source.provenance.point.description}</p>}
      <dl className="props">
        <dt>Who</dt>
        <dd>
          <GroupSwatch authority={authority} /> {GROUP_WORDS[GROUP_OF[authority]]} · {pkg
            ? <Tip text={pkg}>{packageName(report.package_labels, pkg)}</Tip> : authority}
        </dd>
        <dt>Loads</dt><dd><Loads load={source.activation.load} condition={source.activation.condition} /></dd>
        <dt>Tokens</dt><dd>{tokenWords(content)}</dd>
        {file && (<><dt>Where</dt>
          <dd><code>{home(file, report.home_dir)}{anchor && `#${anchor}`}</code> <CopyButton text={file} label="Copy the path" /></dd></>)}
        {content.type === "observed_text" && (<><dt>Seen</dt><dd>{seenIn(content.seen_in.length, content.sessions_examined)}</dd></>)}
        {content.type === "not_observed" && (<><dt>Seen</dt><dd>{content.reason}</dd></>)}
        <dt>Testable</dt>
        <dd>{isTestable(source) ? `by ${source.ablation.mechanism.replaceAll("_", " ")}` : `no — ${source.ablation.blocked_reason}`}</dd>
      </dl>
      {findings.length > 0 && (
        <section>
          <h3>Findings here <span className="count">{findings.length}</span></h3>
          <ul className="links">
            {findings.map((f) => (
              <li key={f.id}><SeverityIcon severity={f.severity} /> <a {...link(href("findings", f.id))}>{f.title}</a></li>
            ))}
          </ul>
        </section>
      )}
      {provided.length > 0 && (
        <section>
          <h3>Provides <span className="count">{provided.length}</span></h3>
          <ul className="entities">
            {provided.map((e) => {
              return (
                <li key={e.id}>
                  <VisibilityIcon visibility={seen.get(e.id)} />
                  <strong>{e.name}</strong> <span className="muted">{e.kind.label}</span>
                  {e.description && <div className="muted description">{e.description}</div>}
                </li>
              );
            })}
          </ul>
        </section>
      )}
      <section>
        <h3>Text</h3>
        {content.type === "not_observed" || content.type === "size_only" ? (
          <p className="muted">{content.reason}</p>
        ) : texts === null ? (
          <p className="muted">Unpacking…</p>
        ) : text === undefined ? (
          <p className="muted">{textsMissing ?? "Not included in this report."}</p>
        ) : (
          <FormattedText key={source.id} text={text} format={"format" in content ? content.format : undefined} line={line} needle={needle} />
        )}
      </section>
    </aside>
  );
}

/** A listing the harness composes from many packages: its real total, and each package's part. */
export function ListingDetail({ report, row, go, onClose }: {
  report: Report; row: Row & { kind: "listing" }; go: (to: string) => void; onClose: () => void;
}) {
  const { labels } = report;
  const max = Math.max(1, ...row.parts.map((p) => tokensOf(p.content) ?? 0)); // the largest part, drawn full
  return (
    <aside className="detail" aria-label="Listing">
      <Head onClose={onClose}><h2>{row.label}</h2></Head>
      <p className="detail-text">
        {row.parts[0]?.provenance.point.description || "The harness writes this listing itself, from what each package installs."}
      </p>
      <dl className="props">
        <dt>Loads</dt><dd><Loads load={row.parts[0].activation.load} /></dd>
        <dt>Tokens</dt><dd>{count(row.tokens)} in all{splitWords(row.split) ? ` · ${splitWords(row.split)}` : ""}</dd>
      </dl>
      <h3>Parts <span className="count">{count(row.parts.length)}</span></h3>
      <ul className="links parts">
        {row.parts.map((p) => {
          const n = tokensOf(p.content);
          return (
            <li key={p.id}>
              <CostBar value={n} max={max} split={splitOf([[p.provenance.authority, n ?? 0]])} />
              <span className="num">{n === null ? "—" : short(n)}</span>
              {p.provenance.package ? <GroupSwatch authority={p.provenance.authority} />
                : <GroupSwatch authority={p.provenance.authority} text={`${GROUP_WORDS[GROUP_OF[p.provenance.authority]]} · ${p.provenance.authority} — ${LISTING_OWN_PART}`} />}
              <a href={configHref({ kind: "source", id: p.id })} onClick={(e) => { e.preventDefault(); go(configHref({ kind: "source", id: p.id })); }}>{labels[p.id] ?? p.id}</a>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}

/** Text a session showed that nothing configured explains. */
export function UntracedDetail({ untraced, texts, textsMissing = null, onClose }: {
  untraced: Untraced; texts: Texts | null; textsMissing?: string | null; onClose: () => void;
}) {
  const content = untraced.content;
  const text = "text_ref" in content ? texts?.[content.text_ref] : undefined;
  return (
    <aside className="detail" aria-label="Untraced text">
      <Head onClose={onClose}><UntracedIcon /><h2>{untraced.label}</h2></Head>
      {untraced.point.description && <p className="detail-text">{untraced.point.description}</p>}
      <dl className="props">
        <dt>Who</dt><dd><GroupSwatch authority={null} /> No configured source explains it</dd>
        <dt>Loads</dt><dd><Loads load={untraced.load} /></dd>
        <dt>Tokens</dt><dd>{tokenWords(content)}</dd>
        {content.type === "observed_text" && (<><dt>Seen</dt><dd>{seenIn(content.seen_in.length, content.sessions_examined)}</dd></>)}
      </dl>
      <h3>Text</h3>
      {texts === null ? <p className="muted">Unpacking…</p>
        : text === undefined ? textsMissing && <p className="muted">{textsMissing}</p>
        : <FormattedText text={text} format={"format" in content ? content.format : undefined} line={null} needle="" />}
    </aside>
  );
}
