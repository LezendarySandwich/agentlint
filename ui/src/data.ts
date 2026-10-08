// Where the report comes from: `agentlint ui`, on this machine — the report, the texts it refers to, and what
// agentlint recorded — each fetched with the run's token.
import type { DiagnosticInfo, SnapshotDiff, SnapshotInfo } from "./contract";

export type Texts = Record<string, string>; // text_ref -> text

/** What agentlint recorded about the project in view, read from its database by the live server (ui/records.py). */
export interface RecordSource {
  snapshots: () => Promise<SnapshotInfo[]>; // the most recent first
  diff: (from: string, to: string) => Promise<SnapshotDiff>; // fingerprints, the earlier first as a rule
  diagnostics: () => Promise<DiagnosticInfo[]>; // agentlint's findings about itself, the most recent first
}

/** One of the live server's answers; its error, in its own words, if it has one. */
export async function served<T>(path: string, token: string): Promise<T> {
  const response = await fetch(path, { headers: { "X-Agentlint-Token": token } });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.error ?? `${response.status} ${response.statusText}`);
  return body as T;
}

/** A write to the live server: JSON, with the run's token in its header (never in the address). Its error, in its own
 * words, if it refuses. */
export async function posted<T>(path: string, token: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "X-Agentlint-Token": token, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const answer = await response.json().catch(() => null);
  if (!response.ok) throw new Error(answer?.error ?? `${response.status} ${response.statusText}`);
  return answer as T;
}
