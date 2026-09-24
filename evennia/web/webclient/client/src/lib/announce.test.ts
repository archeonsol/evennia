import { describe, expect, it } from "vitest";

import { composeBatch, speakable } from "./announce";

describe("speakable", () => {
  it("collapses whitespace and trims", () => {
    expect(speakable("  a\n\t b  ")).toBe("a b");
  });
});

describe("composeBatch", () => {
  it("joins a short burst in order and drops blank lines", () => {
    expect(composeBatch(["one", "   ", "two"])).toEqual(["one", "two"]);
  });

  it("keeps the newest lines of a long burst and says how many it skipped", () => {
    const lines = Array.from({ length: 20 }, (_, i) => `line ${i + 1}`);
    const out = composeBatch(lines, 5);
    expect(out[0]).toBe("15 earlier lines skipped.");
    expect(out.slice(1)).toEqual(["line 16", "line 17", "line 18", "line 19", "line 20"]);
  });

  it("uses the singular for one skipped line", () => {
    expect(composeBatch(["a", "b", "c"], 2)[0]).toBe("1 earlier line skipped.");
  });
});
