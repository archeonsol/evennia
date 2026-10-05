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

  it("files an announcement as a notice", () => {
    expect(categorize("announce")).toBe("notice");
    expect(categorize("Announce")).toBe("notice");
    // An announcement wins over the words it is also made of.
    expect(categorize("channel_announce")).toBe("notice");
  });

  it("does not take a word that holds announce for one", () => {
    expect(categorize("announcer")).toBe("system");
  });

  it("returns a category every filter chip can toggle, or a notice, which nothing filters", () => {
    // A category with no chip would be permanently unfilterable. A notice is
    // that on purpose, and the only one.
    const ids = new Set<string>(CATS.map((c) => c.id));
    for (const type of ["say", "emote", "combat_hit", "page", "look", "looc", "handset", "?"]) {
      expect(ids.has(categorize(type)), type).toBe(true);
    }
    expect(ids.has("notice")).toBe(false);
    expect(categorize("announce")).toBe("notice");
  });

  it("has a chip for OOC, after comms", () => {
    const ids = CATS.map((c) => c.id);
    expect(ids).toContain("ooc");
    expect(ids.indexOf("ooc")).toBe(ids.indexOf("comms") + 1);
  });
});
