// The one picker for a filter with many values: a button showing the current choice, opening a popover with a filter
// box over a PickList — the same parts as the project switcher (`p`). Never a <select> (docs/tech_impl/11-ui.md).
// Enter or Space opens it, typing filters, ↓ enters the list, j/k and the arrows move, Enter or a click chooses, Esc
// closes and gives focus back to the button. Every key pressed inside stays inside: the view beneath never sees it.
import { useId, useMemo, useRef, useState, type ReactNode } from "react";
import { PickList } from "./PickList";
import { Icon, MARK } from "./ui";

export interface Choice {
  key: string;
  label: string; // what the button shows when chosen, and what typing matches
  mark?: ReactNode; // a swatch or glyph before the label
  hint?: string; // muted, after the label; typing matches it too
  nested?: boolean; // indented under the choice before it
}

interface Props {
  label: string; // what it filters, for screen readers: "Package"
  choices: Choice[];
  value: string; // the chosen key; the first choice is the default (no filter), the others mark the button as on
  onChoose: (key: string) => void;
}

export function FilterMenu({ label, choices, value, onChoose }: Props) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [current, setCurrent] = useState<string | null>(null);
  const wrap = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const chosen = choices.find((c) => c.key === value) ?? choices[0];
  const needle = filter.trim().toLowerCase();
  const shown = useMemo(() => (needle ? choices.filter((c) => `${c.label}\n${c.hint ?? ""}`.toLowerCase().includes(needle)) : choices),
    [choices, needle]);
  const cursor = shown.some((c) => c.key === current) ? current : shown[0]?.key ?? null;
  const box = open ? wrap.current?.getBoundingClientRect() : undefined;

  const show = () => {
    setFilter("");
    setCurrent(value);
    setOpen(true);
  };
  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) button.current?.focus();
  };
  const choose = (key: string) => {
    close(true);
    if (key !== value) onChoose(key);
  };

  return (
    <span className="menu" ref={wrap}>
      <button ref={button} type="button" className={`menu-button${value !== choices[0]?.key ? " on" : ""}`} aria-haspopup="listbox" aria-expanded={open}
              aria-label={`${label}: ${chosen?.label ?? ""}`} onClick={() => (open ? close(false) : show())}>
        {chosen?.mark && <span className="menu-mark" aria-hidden="true">{chosen.mark}</span>}
        <span className="menu-value">{chosen?.label}</span>
        <Icon icon={MARK.disclose} className="menu-open" />
      </button>
      {open && (
        <div className="menu-pop" role="dialog" aria-label={label} style={box ? { top: box.bottom + 4, left: box.left } : undefined}
             onKeyDown={(e) => {
               if (e.key === "Escape") { e.preventDefault(); close(true); }
               e.stopPropagation();
             }}
             onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) close(false); }}>
          <div className="home-filter">
            <Icon icon={MARK.search} />
            <input value={filter} placeholder="Type to filter" autoComplete="off" spellCheck={false} aria-label={`Filter: ${label}`} autoFocus
                   onChange={(e) => setFilter(e.target.value)}
                   onKeyDown={(e) => {
                     if (e.key === "ArrowDown") { e.preventDefault(); if (cursor !== null) setCurrent(cursor); list.current?.focus(); }
                     else if (e.key === "Enter") { e.preventDefault(); if (cursor !== null) choose(cursor); }
                   }} />
          </div>
          <PickList<Choice> id={`${id}-choice`} label={label} listRef={list} items={shown} keyOf={(c) => c.key} current={cursor}
            onMove={setCurrent} onOpen={choose} clickOpens
            render={(c) => (
              <>
                <span className="menu-mark" aria-hidden="true">{c.mark}</span>
                <span className={`nm${c.nested ? " nested" : ""}`}>{c.label}</span>
                <span className="r">{c.hint}</span>
              </>
            )}
            empty={<p className="home-note muted">Nothing matches</p>} />
        </div>
      )}
    </span>
  );
}
