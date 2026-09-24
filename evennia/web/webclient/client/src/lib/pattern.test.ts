import { describe, expect, it } from "vitest";

import { matches, migrateLegacyPattern, parsePattern } from "./pattern";

describe("parsePattern", () => {
  it("treats plain text literally and case-insensitively", () => {
    const { re, error, regex } = parsePattern("cost: 5 (+2");
    expect(error).toBe("");
    expect(regex).toBe(false);
    expect(matches(re!, "The COST: 5 (+2 today")).toBe(true);
    expect(matches(parsePattern("a.c").re!, "abc")).toBe(false);
  });

  it("reads /slashes/ as a regex, any case unless flags are written", () => {
    const any = parsePattern("/^\\[nous\\]/").re!;
    expect(matches(any, "[NOUS] ping")).toBe(true);
    expect(matches(any, "x [NOUS] ping")).toBe(false);
    const exact = parsePattern("/Nous/u").re!;
    expect(matches(exact, "nous")).toBe(false);
    expect(matches(exact, "Nous")).toBe(true);
  });

  it("reports a broken regex instead of silently using it as text", () => {
    const p = parsePattern("/foo(/");
    expect(p.re).toBeNull();
    expect(p.error).toMatch(/Not a valid regex/);
  });

  it("refuses a pattern that matches every line", () => {
    expect(parsePattern("/a*/").error).toMatch(/every line/);
    expect(parsePattern("/x|/").re).toBeNull();
  });

  it("refuses unknown flags", () => {
    expect(parsePattern("/x/q").error).toMatch(/Unknown flag/);
  });

  it("is safe to test repeatedly with the g flag", () => {
    const re = parsePattern("whispers").re!;
    expect(matches(re, "Kessa whispers")).toBe(true);
    expect(matches(re, "Kessa whispers")).toBe(true);
  });
});

describe("migrateLegacyPattern", () => {
  it("wraps a saved bare regex so it keeps its meaning", () => {
    expect(migrateLegacyPattern("^\\[Nous\\]")).toBe("/^\\[Nous\\]/i");
    expect(migrateLegacyPattern("whispers|shouts")).toBe("/whispers|shouts/i");
  });

  it("leaves plain text, slashed patterns and broken regexes alone", () => {
    expect(migrateLegacyPattern("whispers")).toBe("whispers");
    expect(migrateLegacyPattern("Mr. Smith?")).toBe("Mr. Smith?");
    expect(migrateLegacyPattern("/x/")).toBe("/x/");
    expect(migrateLegacyPattern("cost: 5 (+2")).toBe("cost: 5 (+2");
  });
});
