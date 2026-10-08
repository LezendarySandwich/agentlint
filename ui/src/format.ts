// Small presentation helpers shared by the views: the words behind every colour and icon.
import type { Authority, Confidence, Load, Severity, Visibility } from "./contract";
import type { Group } from "./model";

export const LOAD_WORDS: Record<Load, string> = {
  always: "every session",
  per_message: "every message",
  on_invoke: "when invoked",
  conditional: "when its condition holds",
  not_in_context: "not in context",
};

export const AUTHORITY_WORDS: Record<Authority, string> = {
  managed: "managed by your organisation",
  user: "yours, in your home directory",
  project: "this project's",
  package: "shipped by a package",
  runtime: "produced while running (hook output, listings)",
  harness: "the harness's own",
};

/** The three groups' names: generic words, never a harness's. Their order and members are model.ts's GROUPS. */
export const GROUP_WORDS: Record<Group, string> = { yours: "Yours", installed: "Installed", harness: "Harness" };

export const GROUP_MEANING: Record<Group, string> = {
  yours: "text you wrote and can change",
  installed: "text a package you installed brings",
  harness: "text the harness supplies, produces while running, or your organisation sets",
};

/** What a listing's own part holds, until the adapter can split it: it counts as harness. */
export const LISTING_OWN_PART =
  "the listing's own part: its header, the harness's own entries, and the lines for your own skills and agents, which can't be told apart yet";

/** What the agent is shown of an entity, each in its tooltip and the legend; "unknown" when no recent session showed
 * whether it sees it. */
export const VISIBILITIES: Visibility[] = ["described", "name_only", "not_shown"];
export const VISIBILITY_WORDS: Record<Visibility | "unknown", string> = {
  described: "The agent sees its name and description",
  name_only: "The agent sees only its name",
  not_shown: "The agent is not shown it",
  unknown: "No recent session showed whether the agent sees it",
};
/** The same, in a few words beside the mark. */
export const VISIBILITY_SHORT: Record<Visibility | "unknown", string> = {
  described: "described", name_only: "name only", not_shown: "not shown", unknown: "not known",
};

export const SEVERITY_WORDS: Record<Severity, string> = {
  error: "Error — something is broken",
  warn: "Warning — likely to cause the wrong behaviour",
  info: "Info — worth knowing",
};

/** A count of findings of one severity, in words: "1 error", "54 warnings", "109 info". */
export function severityCount(severity: Severity, n: number): string {
  if (severity === "info") return `${count(n)} info`;
  return severity === "error" ? counted(n, "error", "errors") : counted(n, "warning", "warnings");
}

/** A check's id made readable — "broken-reference" is "Broken reference" — with the id itself in tooltips and the
 * finding's detail (decided 2026-10-04: a per-check label in Python waits until a name reads badly). */
export function checkName(id: string): string {
  const words = id.replaceAll("-", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** A diagnostic's kind made readable — "generator_empty" is "Generator empty" — with the kind itself in its tooltip:
 * the kind is its producer's own name, free text, so nothing here lists them. */
export function diagnosticKind(kind: string): string {
  const words = kind.replaceAll(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export const CONFIDENCE_WORDS: Record<Confidence, string> = {
  certain: "Certain — mechanically true",
  measured: "Measured — observed by running the agent",
  suggestive: "Suggestive — a pattern worth a look, not a claim",
};

/** How the user knows a package: its adapter's short name, or the id itself when there is none or when another
 * package has the same short name (two "notes" can't be told apart, so neither is shortened). The id goes in the
 * tooltip wherever a short name is shown. */
export function packageName(labels: Record<string, string> | undefined, id: string): string {
  const label = labels?.[id];
  if (!label) return id;
  const shared = Object.entries(labels!).some(([other, theirs]) => other !== id && theirs === label);
  return shared ? id : label;
}

export function count(n: number): string {
  return n.toLocaleString("en-US");
}

/** A count and its noun, in the singular for 1: `counted(1, "prompt", "prompts")` is "1 prompt". The caller gives
 * both forms — never an English plural guessed from the singular. */
export function counted(n: number, one: string, many: string): string {
  return `${count(n)} ${noun(n, one, many)}`;
}

/** The noun alone, for a count drawn apart from it (`<strong>3</strong> sessions`): the caller gives both forms. */
export function noun(n: number, one: string, many: string): string {
  return n === 1 ? one : many;
}

/** Where observed text was seen: "in 1 of the 2 recent sessions read", "in 1 of the 1 recent session read". */
export function seenIn(seen: number, of: number): string {
  return `in ${count(seen)} of the ${counted(of, "recent session", "recent sessions")} read`;
}

/** A size in bytes, in the unit that reads best: "820 KB", "4.2 MB", "312 MB", "1.4 GB". */
export function bytes(n: number): string {
  const units: [number, string][] = [[1e9, "GB"], [1e6, "MB"], [1e3, "KB"]];
  for (const [size, unit] of units) {
    if (n >= size) return `${(n / size).toFixed(n < size * 10 ? 1 : 0).replace(/\.0$/, "")} ${unit}`;
  }
  return `${count(n)} B`;
}

/** A change in a figure, signed: "+1,234", "−56" (a minus sign, not a hyphen), "0"; "?" when it isn't known — never 0. */
export function signed(n: number | null): string {
  if (n === null) return "?";
  return n > 0 ? `+${count(n)}` : n < 0 ? `−${count(-n)}` : "0";
}

/** 12,345 → "12.3k": for bars and tiles, where the exact figure is one hover away. */
export function short(n: number): string {
  if (n < 1000) return String(n);
  if (n < 100_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  if (n < 1_000_000) return `${Math.round(n / 1000)}k`;
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}

/** A path in the `~` form when it is under the user's home directory — `homeDir`, the report's or the server's
 * `home_dir`, never guessed from the path (/Users/Shared/proj is no one's home) — otherwise whole. */
export function home(path: string, homeDir: string | undefined): string {
  const dir = homeDir?.replace(/\/+$/, "");
  if (!dir) return path;
  return path === dir ? "~" : path.startsWith(`${dir}/`) ? `~${path.slice(dir.length)}` : path;
}

/** A path as one shell word: in the `~` form when nothing in it needs quoting, otherwise single-quoted in full. The
 * shell expands `~` to its own $HOME, so only a path under the user's home directory is ever written with one. */
export function shellPath(path: string, homeDir: string | undefined): string {
  const shown = home(path, homeDir);
  return /^[\w@%+=:,./~-]+$/.test(shown) ? shown : `'${path.replaceAll("'", "'\\''")}'`;
}

/** The command that suppresses a finding, to paste in a terminal. `--cwd` names the project, so it works from any
 * terminal, before any `check` has recorded the finding (10-reporting.md, "Suppressions"). */
export function suppressCommand(id: string, cwd: string, homeDir: string | undefined): string {
  return `agentlint suppress ${id.slice(0, 12)} --cwd ${shellPath(cwd, homeDir)} --reason "…"`;
}

/** A file's path short enough for a property list: in the `~` form, its last two parts after "…/" when it is longer.
 * A place inside the file is the source's anchor, which the caller writes after it. */
export function shortPath(file: string, homeDir: string | undefined): string {
  const shown = home(file, homeDir);
  const parts = shown.split("/");
  return parts.length > 3 ? `…/${parts.slice(-2).join("/")}` : shown;
}

export function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** A day, without the time: "15 Sep" (in the reader's locale) — what "since <date>" says. */
export function day(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** How long ago, in one short figure: "5 min", "1 h", "12 d". */
export function age(iso: string, now: number = Date.now()): string {
  const minutes = Math.max(1, Math.floor((now - Date.parse(iso)) / 60000));
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)} h`;
  return `${Math.floor(minutes / (60 * 24))} d`;
}

/** A directory's own name, and where it sits (with `~` when that is under the user's home directory). */
export function placed(path: string, homeDir?: string): { name: string; parent: string } {
  const parts = path.split("/").filter(Boolean);
  if (!parts.length) return { name: "/", parent: "" };
  const parent = `/${parts.slice(0, -1).join("/")}`;
  return { name: parts[parts.length - 1], parent: home(parent, homeDir) };
}

/** A length of time in one or two short figures: "53 s", "16 min", "1 h", "1 h 5 min" — minutes rounded. */
export function duration(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `${count(hours)} h ${minutes % 60} min` : `${count(hours)} h`;
}
