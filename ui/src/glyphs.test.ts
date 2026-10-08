// One glyph per meaning, about 20 of them (docs/tech_impl/11-ui.md, "Design principles"): every icon is named in
// ui.tsx by what it means, and no other module draws one of its own.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { PAGES } from "./route";
import { MARK, NAV_ICON, ROLE_ICON } from "./ui";

const SOURCES = readdirSync(__dirname).filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f));
const importsOf = (file: string) =>
  [...readFileSync(join(__dirname, file), "utf8").matchAll(/import\s*\{([^}]*)\}\s*from\s*"lucide-react"/g)]
    .flatMap((m) => m[1].split(",")).map((name) => name.trim()).filter((name) => name && !name.startsWith("type "));

describe("glyphs", () => {
  it("come from one table: only ui.tsx imports Lucide", () => {
    for (const file of SOURCES) if (file !== "ui.tsx") expect(importsOf(file), file).toEqual([]);
  });

  it("mean one thing each: no glyph serves two meanings", () => {
    const tables = [Object.values(MARK), Object.values(NAV_ICON), Object.values(ROLE_ICON)].flat();
    expect(new Set(tables).size).toBe(tables.length);
    expect(importsOf("ui.tsx").length).toBe(tables.length); // nothing imported that the tables don't name
  });

  it("are about 20 marks — the three drawn severities with the rest — beside one per view and one per role", () => {
    expect(Object.keys(MARK).length + 3).toBeLessThanOrEqual(25); // 24, and the home's "started here" pin
    expect(Object.keys(NAV_ICON).sort()).toEqual([...PAGES].sort());
    expect(Object.keys(ROLE_ICON).length).toBeLessThanOrEqual(7);
  });

  it("retire the stock AI sparkle", () => {
    for (const file of SOURCES) expect(importsOf(file).filter((name) => /^Sparkles?$/.test(name)), file).toEqual([]);
  });
});
