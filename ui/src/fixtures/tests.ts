// Test settings as the live server sends them (GET /api/settings test_settings; onboarding's settings): fixtures for
// Settings.test.tsx and Tests.test.tsx.
import type { SettingEntry } from "../contract";

/** The test settings, as GET /api/settings gives them apart from Reading and keeping. */
export const TEST_SETTINGS: SettingEntry[] = [
  { key: "situations_per_target", group: "tests", value: 3, default: 3, set: false, meaning: "Situations written for each target.", problem: null, note: null, range: [1, 10] },
  { key: "runs_per_situation", group: "tests", value: 3, default: 3, set: false, meaning: "Runs per situation.", problem: null, note: null, range: [3, 20] },
  { key: "run_timeout_s", group: "tests", value: 300, default: 300, set: false, meaning: "One run's time limit.", problem: null, note: null, range: [30, 3600] },
  { key: "model", group: "tests", kind: "model", value: "", default: "", set: false, meaning: "The model every run asks for.", problem: null, note: null },
  { key: "keep_tools", group: "tests", kind: "tools", value: [], default: [], set: false, meaning: "Tools runs keep offering anyway.", problem: null, note: null },
  { key: "remove_tools", group: "tests", kind: "tools", value: ["mcp__notes__post"], default: [], set: true, meaning: "Tools removed besides.", problem: null, note: null },
];
