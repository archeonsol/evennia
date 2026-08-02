// The log's hot path is one append per message with the whole scrollback in a
// reactive array, so the cheap-looking bits (categorise on read, trim one line
// at a time) are the expensive ones. These pin the shape that keeps it cheap.

import { describe, expect, it } from "vitest";
import { CATS, categorize } from "./logcats";

describe("categorize", () => {
  it("maps speech kinds", () => {
    expect(categorize("say")).toBe("speech");
    expect(categorize("whisper")).toBe("speech");
  });

  it("maps pose kinds", () => {
    expect(categorize("emote")).toBe("pose");
  });

  it("matches comms kinds by substring", () => {
    expect(categorize("channel_ooc")).toBe("comms");
    expect(categorize("tell")).toBe("comms");
  });

  it("falls back to system", () => {
    expect(categorize("")).toBe("system");
    expect(categorize("whatever")).toBe("system");
  });

  it("returns a category every filter chip can toggle", () => {
    // A category with no chip would be permanently unfilterable.
    const ids = new Set(CATS.map((c) => c.id));
    for (const type of ["say", "emote", "combat_hit", "page", "look", "?"]) {
      expect(ids.has(categorize(type))).toBe(true);
    }
  });
});
