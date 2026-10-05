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

  it("files messages people sent you under comms", () => {
    for (const type of ["tell", "page", "sm", "network_sm", "handset", "broadcast", "comms"]) {
      expect(categorize(type), type).toBe("comms");
    }
  });

  it("files LOOC and channels under ooc", () => {
    for (const type of ["looc", "ooc", "channel", "channel_ooc"]) {
      expect(categorize(type), type).toBe("ooc");
    }
  });

  it("reads a type as words, so a word that holds another is not it", () => {
    // "smell" holds "sm", "telling" holds "tell"; neither is a message.
    expect(categorize("smell")).toBe("system");
    expect(categorize("telling")).toBe("system");
    expect(categorize("pager")).toBe("system");
  });

  it("falls back to system", () => {
    expect(categorize("")).toBe("system");
    expect(categorize("whatever")).toBe("system");
    expect(categorize("help")).toBe("system");
  });

  it("answers the same for a type it has already seen", () => {
    expect(categorize("handset")).toBe(categorize("handset"));
    expect(categorize("Handset")).toBe("comms");
  });

  it("returns a category every filter chip can toggle", () => {
    // A category with no chip would be permanently unfilterable.
    const ids = new Set(CATS.map((c) => c.id));
    for (const type of ["say", "emote", "combat_hit", "page", "look", "looc", "handset", "?"]) {
      expect(ids.has(categorize(type))).toBe(true);
    }
  });

  it("has a chip for OOC, after comms", () => {
    const ids = CATS.map((c) => c.id);
    expect(ids).toContain("ooc");
    expect(ids.indexOf("ooc")).toBe(ids.indexOf("comms") + 1);
  });
});
