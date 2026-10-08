// Presentation helpers, pure: no DOM.
import { describe, expect, it } from "vitest";
import { report } from "./fixtures/report";
import { bytes, duration, packageName, signed } from "./format";

describe("packageName", () => {
  it("is the adapter's short name, read from the report Python writes", () => {
    expect(packageName(report.package_labels, "notes-pkg")).toBe("notes");
  });

  it("is the id when there is no label, and never cuts an id itself", () => {
    expect(packageName(undefined, "brain@acme-market")).toBe("brain@acme-market");
    expect(packageName({}, "brain@acme-market")).toBe("brain@acme-market");
    expect(packageName({ other: "other" }, "brain@acme-market")).toBe("brain@acme-market");
  });

  it("shows the ids of two packages that share a short name", () => {
    const labels = { "brain@one": "brain", "brain@two": "brain", "notes@one": "notes" };
    expect(packageName(labels, "brain@one")).toBe("brain@one");
    expect(packageName(labels, "brain@two")).toBe("brain@two");
    expect(packageName(labels, "notes@one")).toBe("notes");
  });
});

describe("bytes", () => {
  it("reads in the unit that fits", () => {
    expect(bytes(0)).toBe("0 B");
    expect(bytes(999)).toBe("999 B");
    expect(bytes(820_000)).toBe("820 KB");
    expect(bytes(4_230_000)).toBe("4.2 MB");
    expect(bytes(4_000_000)).toBe("4 MB");
    expect(bytes(312_400_000)).toBe("312 MB");
    expect(bytes(1_400_000_000)).toBe("1.4 GB");
  });
});

describe("signed", () => {
  it("signs a change with a minus sign, and says ? for one that isn't known, never 0", () => {
    expect(signed(1234)).toBe("+1,234");
    expect(signed(-56)).toBe("−56");
    expect(signed(0)).toBe("0");
    expect(signed(null)).toBe("?");
  });
});

describe("duration", () => {
  it("reads in seconds under a minute, then minutes, then hours and minutes", () => {
    expect(duration(53)).toBe("53 s");
    expect(duration(0)).toBe("0 s");
    expect(duration(954)).toBe("16 min");
    expect(duration(3600)).toBe("1 h");
    expect(duration(3180 * 1.1)).toBe("58 min");
    expect(duration(3900)).toBe("1 h 5 min");
  });
});
