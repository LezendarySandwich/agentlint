import { describe, expect, it } from "vitest";
import { targetKey, targetOf } from "./model";
import { configHref, href, parse } from "./route";

describe("the route", () => {
  it("parses each view's filters once, checked against what they can be", () => {
    expect(parse("#/findings?severity=warn&check=broken-reference&q=x")).toMatchObject({
      view: "findings", id: null, filters: { q: "x", severity: "warn", check: "broken-reference" } });
    expect(parse("#/findings?severity=loud")).toMatchObject({ filters: { severity: null } }); // no such severity: no filter
    expect(parse("#/config?load=always&group=installed&authority=user&line=12")).toMatchObject({
      filters: { load: "always", group: "installed", authority: null, line: 12 } }); // one of group and authority
    expect(parse("#/config?load=sometimes&group=mine&authority=user&line=-3")).toMatchObject({
      filters: { load: null, group: null, authority: "user", line: null } });
    expect(parse("#/entities?kind=skill&unused=1")).toMatchObject({ filters: { kind: "skill", unused: true, chosen: false } });
    expect(parse("#/nowhere")).toMatchObject({ view: "overview", id: null });
  });

  it("opens nothing for an id that doesn't decode, rather than throwing", () => {
    expect(parse("#/findings/%E0%A4%A").id).toBeNull();
    expect(parse(href("findings", "a/b c")).id).toBe("a/b c");
  });

  it("types what Configuration opens, each kind its own id space", () => {
    const source = { kind: "source" as const, id: "untraced:3/x" };
    expect(parse(configHref(source, { q: "a" }))).toMatchObject({ view: "config", target: source, id: "source/untraced:3/x" });
    const target = (hash: string) => { const route = parse(hash); return route.view === "config" ? route.target : undefined; };
    expect(target("#/config/untraced/abc123")).toEqual({ kind: "untraced", id: "abc123" });
    expect(target("#/config/listing/always%7Clisting")).toEqual({ kind: "listing", id: "always|listing" });
    expect(target("#/config/notes")).toEqual({ kind: "source", id: "notes" }); // a bare id: a source's
    expect(target("#/config")).toBeNull();
    expect(targetOf(targetKey(source))).toEqual(source);
    expect(targetOf("nothing")).toBeNull();
  });
});
