// The Quiet ledger's rules, read straight from styles.css (no DOM): contrast that must hold in light and dark,
// and the scales every rule keeps to (docs/tech_impl/11-ui.md, "Design principles").
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const CSS = readFileSync(join(__dirname, "styles.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

type Rule = { selector: string; media: string | null; decls: [string, string][] };

/** Top-level rules and rules one @media or @keyframes deep — all styles.css has. */
function parse(css: string): Rule[] {
  const rules: Rule[] = [];
  let i = 0;
  const block = (media: string | null) => {
    while (i < css.length) {
      const open = css.indexOf("{", i);
      const close = css.indexOf("}", i);
      if (close !== -1 && (open === -1 || close < open)) { i = close + 1; return; }
      if (open === -1) return;
      const head = css.slice(i, open).trim();
      i = open + 1;
      if (head.startsWith("@media") || head.startsWith("@keyframes")) { block(head); continue; }
      const end = css.indexOf("}", i);
      const decls = css.slice(i, end).split(";").map((d) => d.trim()).filter(Boolean).map((d) => {
        const colon = d.indexOf(":");
        return [d.slice(0, colon).trim(), d.slice(colon + 1).trim()] as [string, string];
      });
      rules.push({ selector: head, media, decls });
      i = end + 1;
    }
  };
  block(null);
  return rules;
}

const RULES = parse(CSS);
const DARK = "@media (prefers-color-scheme: dark)";
const MOTION = "@media (prefers-reduced-motion: reduce)";
const isTokens = (r: Rule) => r.selector === ":root" && (r.media === null || r.media === DARK);
const tokensOf = (media: string | null) =>
  Object.fromEntries(RULES.filter((r) => r.selector === ":root" && r.media === media)
    .flatMap((r) => r.decls).filter(([p]) => p.startsWith("--")));
const LIGHT_TOKENS: Record<string, string> = tokensOf(null);
const DARK_TOKENS: Record<string, string> = { ...LIGHT_TOKENS, ...tokensOf(DARK) };
const MODES = { light: LIGHT_TOKENS, dark: DARK_TOKENS };
const STYLE_RULES = RULES.filter((r) => !isTokens(r));
const declsOf = (prop: (p: string) => boolean) =>
  STYLE_RULES.flatMap((r) => r.decls.filter(([p]) => prop(p)).map(([p, v]) => ({ selector: r.selector, prop: p, value: v })));

function luminance(hex: string): number {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? [...h].map((c) => c + c).join("") : h;
  const [r, g, b] = [0, 2, 4].map((k) => parseInt(full.slice(k, k + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
const token = (tokens: Record<string, string>, name: string) => {
  const value = tokens[name];
  expect(value, name).toMatch(/^#[0-9a-f]{3,6}$/i);
  return value;
};
/** `var(--x)` to `--x`, else null. */
const varName = (value: string) => /^var\((--[\w-]+)\)$/.exec(value)?.[1] ?? null;

describe("contrast, light and dark", () => {
  for (const [mode, tokens] of Object.entries(MODES)) {
    it(`${mode}: ink, secondary and muted text read at 4.5:1 on page, panel and sidebar`, () => {
      for (const text of ["--ink", "--sec", "--mut"])
        for (const surface of ["--page", "--panel", "--side"])
          expect(contrast(token(tokens, text), token(tokens, surface)), `${text} on ${surface}`).toBeGreaterThanOrEqual(4.5);
    });
    it(`${mode}: error and warning glyphs read at 3:1 on panel and sidebar`, () => {
      for (const hue of ["--err", "--warn"])
        for (const surface of ["--panel", "--side"])
          expect(contrast(token(tokens, hue), token(tokens, surface)), `${hue} on ${surface}`).toBeGreaterThanOrEqual(3);
    });
    it(`${mode}: tooltip text reads at 4.5:1 on the tooltip`, () => {
      expect(contrast(token(tokens, "--tip-fg"), token(tokens, "--tip-bg"))).toBeGreaterThanOrEqual(4.5);
    });
    it(`${mode}: JSON text reads at 4.5:1 on panel and page`, () => {
      const colours = declsOf((p) => p === "color").filter((d) => d.selector.includes(".json-"));
      expect(colours.length).toBeGreaterThan(3);
      for (const { selector, value } of colours) {
        const name = varName(value);
        expect(name, `${selector} colour is a token`).not.toBeNull();
        for (const surface of ["--panel", "--page"])
          expect(contrast(token(tokens, name!), token(tokens, surface)), `${selector} on ${surface}`).toBeGreaterThanOrEqual(4.5);
      }
    });
  }
  it("the groups' ramp: lightness steps one way in each mode, at least 0.06 apart, and its faint end reads at 2:1", () => {
    const ramp = ["--b1", "--b2", "--b3"];
    const light = ramp.map((t) => luminance(token(LIGHT_TOKENS, t)));
    const dark = ramp.map((t) => luminance(token(DARK_TOKENS, t)));
    for (let i = 1; i < 3; i++) {
      expect(light[i] - light[i - 1], `light ${ramp[i]}`).toBeGreaterThanOrEqual(0.06); // darker = more yours
      expect(dark[i - 1] - dark[i], `dark ${ramp[i]}`).toBeGreaterThanOrEqual(0.06); // lighter = more yours
    }
    for (const [mode, tokens] of Object.entries(MODES))
      expect(contrast(token(tokens, "--b3"), token(tokens, "--panel")), mode).toBeGreaterThanOrEqual(2);
    expect(CSS).not.toMatch(/--auth-|\.auth-/); // the six authority colours are gone
  });
  it("the tooltip uses its tokens, and in dark mode has an edge", () => {
    const tip = STYLE_RULES.find((r) => r.selector === ".tooltip")!;
    expect(Object.fromEntries(tip.decls)).toMatchObject({ background: "var(--tip-bg)", color: "var(--tip-fg)", border: "1px solid var(--tip-line)" });
    expect(DARK_TOKENS["--tip-line"]).toMatch(/^#[0-9a-f]{6}$/i);
  });
});

describe("the scales", () => {
  /** A size, weight or radius: a literal, or a token resolved in both modes. */
  const resolve = (value: string) => {
    const name = varName(value);
    if (!name) return [value];
    expect(LIGHT_TOKENS[name], `${name} is defined`).toBeDefined();
    return [LIGHT_TOKENS[name], DARK_TOKENS[name]];
  };

  it("every font size is 12, 13, 15 or 20 px — the wordmark aside, a logo at 30 px on the home", () => {
    const sizes = [
      ...declsOf((p) => p === "font-size").map((d) => ({ ...d, size: d.value })),
      ...declsOf((p) => p === "font").filter((d) => d.value !== "inherit")
        .map((d) => ({ ...d, size: d.value.split(/\s+/).find((part) => /px|var\(--t-/.test(part))!.split("/")[0] })),
      { selector: ":root", prop: "font", value: "", size: LIGHT_TOKENS["--t-body"] },
    ];
    expect(sizes.length).toBeGreaterThan(40);
    expect(sizes.filter((d) => d.size === "30px").map((d) => d.selector)).toEqual([".wordmark"]);
    for (const { selector, size } of sizes) {
      if (size === "inherit" || (selector === ".wordmark" && size === "30px")) continue;
      for (const px of resolve(size)) expect(["12px", "13px", "15px", "20px"], `${selector}: ${size}`).toContain(px);
    }
  });

  it("every weight is 400, 500 or 600", () => {
    const weights = [
      ...declsOf((p) => p === "font-weight").map((d) => ({ ...d, weight: d.value })),
      ...declsOf((p) => p === "font").filter((d) => /^\d/.test(d.value)).map((d) => ({ ...d, weight: d.value.split(/\s+/)[0] })),
    ];
    expect(weights.length).toBeGreaterThan(10);
    for (const { selector, weight } of weights) expect(["400", "500", "600"], selector).toContain(weight);
  });

  it("every radius is 2, 4 or 6 px, or a circle", () => {
    for (const { selector, value } of declsOf((p) => p === "border-radius"))
      for (const part of value.split(/\s+/))
        for (const r of resolve(part)) expect(["0", "2px", "4px", "6px", "50%"], `${selector}: ${value}`).toContain(r);
  });

  it("spacing keeps to the 4 px scale (2 px only for hairlines and gaps)", () => {
    for (const step of ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7"])
      expect(["4px", "8px", "12px", "16px", "24px", "32px", "48px"]).toContain(LIGHT_TOKENS[step]);
    const spacing = declsOf((p) => /^(padding|margin|gap|row-gap|column-gap)(-|$)/.test(p));
    expect(spacing.length).toBeGreaterThan(100);
    for (const { selector, prop, value } of spacing)
      for (const part of value.split(/\s+/)) {
        if (part === "auto" || part === "0") continue;
        for (const v of resolve(part)) {
          const px = /^-?(\d+)px$/.exec(v);
          expect(px, `${selector} ${prop}: ${value}`).not.toBeNull();
          const n = Number(px![1]);
          expect(n % 4 === 0 || n === 2, `${selector} ${prop}: ${value}`).toBe(true);
        }
      }
  });

  it("no caps, no Inter, no indigo, and no colour outside the token blocks", () => {
    expect(CSS).not.toMatch(/uppercase/i);
    expect(CSS).not.toMatch(/\bInter\b/);
    expect(CSS).not.toMatch(/#5e6ad2/i);
    for (const rule of STYLE_RULES)
      for (const [prop, value] of rule.decls)
        expect(value, `${rule.selector} ${prop}`).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(/i);
  });

  it("digits are tabular only in columns", () => {
    const COLUMNS = [".num", ".group-head .count", ".ledger .fig", ".ledger .line .right", ".mix", ".elapsed", ".plist .r"];
    const tabular = declsOf((p) => p === "font-variant-numeric").map((d) => d.selector);
    expect(tabular.length).toBeGreaterThan(0);
    for (const selector of tabular) expect(COLUMNS, selector).toContain(selector);
  });

  it("a results table never widens with what its cells hold: fixed layout, so a long line is cut, never scrolled", () => {
    // Opening a row of Tests › Results once widened the whole page: its runs' one-line actions set the table's width.
    const fixed = STYLE_RULES.filter((r) => r.selector === ".results-table").flatMap((r) => r.decls);
    expect(fixed).toContainEqual(["table-layout", "fixed"]);
    expect(declsOf((p) => p === "table-layout").filter((d) => d.value !== "fixed")).toEqual([]);
  });

  it("reduced motion turns off every transition and animation, with a static form for each animation", () => {
    const motion = RULES.filter((r) => r.media === MOTION);
    const all = motion.find((r) => r.selector.split(",").map((s) => s.trim()).includes("*"));
    expect(all && Object.fromEntries(all.decls)).toMatchObject({ transition: "none !important", animation: "none !important" });
    for (const rule of STYLE_RULES.filter((r) => r.media !== MOTION && r.decls.some(([p]) => p === "animation")))
      expect(motion.map((r) => r.selector), `${rule.selector} has a static form`).toContain(rule.selector);
  });
});
