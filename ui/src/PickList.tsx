// One list of choices, used for every list the UI chooses from: harnesses and projects on the home, projects in the
// breadcrumb's popover. Never a <select>, never disabled, not even with one row (user, 2026-10-03: the harness is a
// list like the project list). The keys are handled on the list itself — the page's keys ignore a focused filter,
// and the filter hands Down to the list — and stop there, so nothing beneath also moves.
import type { KeyboardEvent, ReactNode, Ref } from "react";

interface Props<T> {
  items: T[];
  keyOf: (item: T) => string;
  render: (item: T) => ReactNode;
  current: string | null; // the key of the row the cursor is on
  onMove: (key: string) => void; // the cursor moved to it
  onOpen: (key: string) => void; // Enter, or a click on the row already current
  id: string; // option ids are `${id}-${n}`, for aria-activedescendant
  label?: string;
  labelledBy?: string;
  onTab?: (shift: boolean) => boolean; // handled: true (the list moved focus itself)
  empty?: ReactNode; // shown when there are no rows
  className?: string;
  listRef?: Ref<HTMLUListElement>;
  clickOpens?: boolean; // a menu: one click on any row opens it (a list beside a detail moves first, opens second)
}

/** A listbox: `aria-activedescendant` names the current row, marked with the 2 px ink bar. */
export function PickList<T>(props: Props<T>) {
  const { items, keyOf, render, current, onMove, onOpen, id, label, labelledBy, onTab, empty, className, listRef, clickOpens } = props;
  const at = items.findIndex((item) => keyOf(item) === current);
  const move = (by: number) => {
    if (!items.length) return;
    const next = at < 0 ? 0 : Math.min(items.length - 1, Math.max(0, at + by));
    onMove(keyOf(items[next]));
  };
  const keyDown = (event: KeyboardEvent<HTMLUListElement>) => {
    if (event.altKey || event.metaKey || event.ctrlKey) return;
    let handled = true;
    if (event.key === "ArrowDown" || event.key === "j") move(1);
    else if (event.key === "ArrowUp" || event.key === "k") move(-1);
    else if (event.key === "Home") move(-items.length);
    else if (event.key === "End") move(items.length);
    else if (event.key === "Enter") { if (at >= 0) onOpen(keyOf(items[at])); }
    else if (event.key === "Tab") handled = !!onTab?.(event.shiftKey);
    else handled = false;
    if (handled) {
      event.preventDefault();
      event.stopPropagation();
    }
  };
  return (
    <>
      <ul ref={listRef} className={`plist${className ? ` ${className}` : ""}`} role="listbox" tabIndex={0} aria-label={label}
          aria-labelledby={labelledBy} aria-activedescendant={at >= 0 ? `${id}-${at}` : undefined} onKeyDown={keyDown}>
        {items.map((item, n) => {
          const key = keyOf(item);
          const on = key === current;
          return (
            <li key={key} id={`${id}-${n}`} role="option" aria-selected={on} className={on ? "cur" : undefined}
                onClick={(event) => {
                  (event.currentTarget.parentElement as HTMLElement).focus();
                  if (on || clickOpens) onOpen(key);
                  else onMove(key);
                }}>
              {render(item)}
            </li>
          );
        })}
      </ul>
      {!items.length && empty}
    </>
  );
}

