// What a reading looks like: never a blank page. A 2 px line runs along the top of the view while the server reads
// (a still, faint bar under reduced motion), the seconds appear once 10 have passed, and before the first report
// the overview's ledger is drawn as its own rows in grey blocks (docs/tech_impl/11-ui.md, "Loading").
import { useEffect, useState } from "react";

/** The line, and the seconds past 10. Mounted when a reading starts, so its count starts there. */
export function ReadingLine() {
  const [started] = useState(() => Date.now());
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const tick = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(tick);
  }, [started]);
  return (
    <>
      <div className="progress" role="progressbar" aria-label="Reading your configuration" />
      {seconds >= 10 && <div className="elapsed" aria-hidden="true">{seconds} s</div>}
    </>
  );
}

const Block = ({ w, h, end }: { w: number | string; h?: number; end?: boolean }) => (
  <span className={`sk${end ? " end" : ""}`} style={{ width: w, height: h }} />
);

/** The overview's ledger, empty: its three rows — a label, a bar or the counts, a figure, the line under them — as
 * static grey blocks in the same grid, so the report lands without anything moving. */
export function OverviewSkeleton() {
  return (
    <div className="overview skeleton" aria-hidden="true">
      <div className="ledger">
        <div className="lrow">
          <Block w={84} />
          <div className="body"><Block w="100%" h={8} /></div>
          <span className="fig"><Block w={48} end /></span>
          <span />
          <div className="sub"><Block w={260} h={8} /></div>
        </div>
        <div className="lrow">
          <Block w={62} />
          <div className="body counts"><Block w={34} /><Block w={40} /><Block w={46} /></div>
          <span className="fig" />
          <span />
          <div className="sub sk-lines">{[150, 130, 110, 160, 120, 100].map((w, i) => <Block key={i} w={w} h={8} />)}</div>
        </div>
        <div className="lrow">
          <Block w={110} />
          <div className="body"><Block w="100%" h={6} /></div>
          <span className="fig"><Block w={88} end /></span>
          <span />
          <div className="sub"><Block w={70} h={8} /></div>
        </div>
      </div>
    </div>
  );
}
