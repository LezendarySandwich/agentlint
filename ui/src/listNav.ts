// The keys of a list beside a detail, written once for every such view (Findings, Configuration, Skills and agents, Sessions):
// a cursor held by key, j/k and the arrows to move it, Enter to open what it is on, an open detail following it, Esc
// to close the detail or else clear the search, / to search. The `?` help lists LIST_KEYS, the same keys.
import { useCallback, useEffect, useRef, useState, type MouseEvent, type RefObject } from "react";
import { useKeys } from "./route";

/** Moves the cursor: down or up one row. */
const MOVE: Record<string, 1 | -1> = { j: 1, ArrowDown: 1, k: -1, ArrowUp: -1 };

/** What the list keys do, for the help; the handler below is the only reader of these keys. */
export const LIST_KEYS: [string[], string][] = [
  [["j", "k"], "Move down and up a list (↓ and ↑ too)"],
  [["Enter"], "Open what the cursor is on; an open detail follows the cursor"],
  [["Esc"], "Close it, or clear the search"],
  [["/"], "Search"],
];

interface Options<T> {
  items: T[]; // the rows the keys move through, in the order shown
  keyOf: (item: T) => string;
  openKey: string | null; // what is open beside the list
  open: (key: string, replace: boolean) => void; // open it; `replace` when the detail follows the cursor
  close: () => void; // Esc with something open
  search?: RefObject<HTMLInputElement | null>; // / focuses it; Esc leaves it
  clear?: () => void; // Esc with nothing open: clear the search
  more?: (key: string, cursor: string | null, setCursor: (key: string) => void) => boolean; // a view's own keys
}

export function useListNavigation<T>({ items, keyOf, openKey, open, close, search, clear, more }: Options<T>) {
  const [held, setHeld] = useState<string | null>(openKey);
  useEffect(() => {
    if (openKey !== null) setHeld(openKey); // follow navigation from elsewhere
  }, [openKey]);
  const keys = items.map(keyOf);
  const found = held === null ? -1 : keys.indexOf(held);
  const at = found >= 0 ? found : 0; // a cursor whose row went (folded, filtered) starts again at the top
  const cursor = keys.length ? keys[at] : null;

  const row = useRef<HTMLElement | null>(null);
  useEffect(() => {
    row.current?.scrollIntoView?.({ block: "nearest" });
  }, [cursor]);

  const handle = useCallback((key: string) => {
    if (key === "/" && search) {
      search.current?.focus();
      return true;
    }
    if (key === "Escape") {
      if (openKey !== null) close();
      else clear?.();
      search?.current?.blur();
      return true;
    }
    if (more?.(key, cursor, setHeld)) return true;
    if (!keys.length) return false;
    if (key in MOVE) {
      const next = keys[Math.min(Math.max(at + MOVE[key], 0), keys.length - 1)];
      setHeld(next);
      if (openKey !== null) open(next, true);
      return true;
    }
    if (key === "Enter") {
      open(keys[at], false);
      return true;
    }
    return false;
  }, [keys, at, cursor, openKey, open, close, search, clear, more]);
  useKeys(handle);

  /** The cursor row's mark, its scroll target, and a click that opens it. */
  const rowProps = (key: string) => ({
    ref: key === cursor ? (el: HTMLElement | null) => { row.current = el; } : undefined,
    onClick: (event: MouseEvent) => {
      event.preventDefault();
      setHeld(key);
      open(key, false);
    },
  });
  return { cursor, setCursor: setHeld, rowProps };
}
