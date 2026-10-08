// The one-time first-run panel: what agentlint reads, what it keeps and for how long, and where to change that
// (docs/tech_impl/11-ui.md, "The first-run panel"). Shown while the database's first-run flag is unset — the CLI's
// notice sets it too, so you meet one of them once — and closing it, any way, sets the flag.
import { useEffect, useRef } from "react";
import type { SettingsAnswer } from "./contract";
import { counted, home } from "./format";
import { href } from "./route";
import { Icon, MARK, useModal } from "./ui";

export function FirstRun({ answer, onClose, go }: {
  answer: SettingsAnswer;
  onClose: () => void; // closed: the page tells the server, which sets the flag
  go: (to: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useModal(dialog); // closed, focus goes back where it was
  useEffect(() => {
    // While open, the panel takes every key: nothing reaches the page beneath; Escape closes it (the dialog's cancel).
    const pressed = (event: KeyboardEvent) => {
      event.stopImmediatePropagation();
      if (event.key === "Escape") { event.preventDefault(); onClose(); }
    };
    window.addEventListener("keydown", pressed, true);
    return () => window.removeEventListener("keydown", pressed, true);
  }, [onClose]);
  const keep = answer.settings.find((s) => s.key === "keep_days")?.value;
  const days = typeof keep === "number" ? counted(keep, "day", "days") : "the Keep for setting's days";
  const open = (at: string) => { onClose(); go(href("settings", null, { at })); };
  return (
    <dialog ref={dialog} className="sheet" aria-labelledby="first-run-title" onCancel={(e) => { e.preventDefault(); onClose(); }}>
      <div className="sheet-body">
        <header className="detail-head">
          <h2 id="first-run-title">Before you start</h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close"><Icon icon={MARK.close} size={16} /></button>
        </header>
        <p>agentlint reads your harness's configuration and its transcripts — your sessions and their subagents — on this machine.</p>
        <p>
          It keeps a copy of every transcript it reads, the usage derived from them, and each reading and its findings, in {home(answer.store, answer.home_dir)},
          readable by you only, for {days}. Nothing leaves this machine.
        </p>
        <ul className="sheet-list">
          <li><button type="button" className="text-button" onClick={() => open("privacy")}>Settings › Privacy</button>: what is kept, and Forget everything</li>
          <li><button type="button" className="text-button" onClick={() => open("sync")}>Settings › Daily sync</button>: keep the archive current even when agentlint isn't run</li>
        </ul>
        <div className="sheet-actions"><button type="button" className="toggle on" onClick={onClose} autoFocus>Got it</button></div>
      </div>
    </dialog>
  );
}
