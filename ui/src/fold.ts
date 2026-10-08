// How long lists open: every group starts folded, so the whole outline fits on a screen; an open group shows
// its first rows and "N more", so no group can push the others out of sight (docs/tech_impl/11-ui.md).
import { useState } from "react";

export const CAP = 10;

/** What you opened: the key of a group (or listing) holding it, and its place among that group's rows. */
export interface Reveal {
  key: string;
  at: number;
}

export function useFolding(filtering: boolean, reveal: Reveal[] = []) {
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const [whole, setWhole] = useState<Record<string, boolean>>({});
  // A filter opens what matches. Opening something opens what holds it, far enough to show it — once: it stays
  // yours to fold, and closing what you opened folds nothing back under the cursor.
  const wanted = reveal.map((r) => `${r.key}@${r.at}`).join("|");
  const [revealed, setRevealed] = useState("");
  if (wanted !== revealed) {
    // Adjusted during render (React re-runs it at once), so this render's rows and cursor already include it.
    setRevealed(wanted);
    setToggled((t) => ({ ...t, ...Object.fromEntries(reveal.map((r) => [r.key, true])) }));
    if (reveal.some((r) => r.at >= CAP)) {
      setWhole((w) => ({ ...w, ...Object.fromEntries(reveal.filter((r) => r.at >= CAP).map((r) => [r.key, true])) }));
    }
  }
  const isOpen = (key: string) => toggled[key] ?? filtering;
  const flip = (key: string) => setToggled((t) => ({ ...t, [key]: !(t[key] ?? filtering) }));
  const setOpen = (key: string, open: boolean) => setToggled((t) => ({ ...t, [key]: open }));
  const shown = <T,>(key: string, rows: T[]): T[] => (whole[key] ? rows : rows.slice(0, CAP));
  const hidden = (key: string, rows: unknown[]): number => (whole[key] ? 0 : Math.max(0, rows.length - CAP)); // for More
  const showAll = (key: string) => setWhole((w) => ({ ...w, [key]: true }));
  return { isOpen, flip, setOpen, shown, hidden, showAll };
}
