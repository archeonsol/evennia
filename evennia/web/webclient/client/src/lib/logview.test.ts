// The log's filter chips decide what a player reads. These pin the one line that
// must never be filtered out: what the game says to everyone.

import { beforeEach, describe, expect, it } from "vitest";
import { CATS, categorize, logview } from "./logview.svelte";

/** Whether the log shows a line of this message type under the current chips. */
function shown(type: string): boolean {
  return !!logview.filters[categorize(type)];
}

describe("log filters", () => {
  beforeEach(() => logview.reset());

  it("shows every kind of line when every chip is on", () => {
    for (const type of ["say", "pose", "combat", "handset", "looc", "look", "text", "announce"]) {
      expect(shown(type), type).toBe(true);
    }
  });

  it("hides a kind of line when its chip is off", () => {
    logview.toggle("system");

    expect(shown("text")).toBe(false);
    expect(shown("say")).toBe(true);
  });

  it("keeps a text, an sm line and LOOC when only System is off", () => {
    logview.toggle("system");

    expect(shown("handset")).toBe(true);
    expect(shown("sm")).toBe(true);
    expect(shown("looc")).toBe(true);
  });

  it("shows an announcement whatever is filtered", () => {
    for (const chip of CATS) logview.filters[chip.id] = false;

    expect(shown("text")).toBe(false);
    expect(shown("announce")).toBe(true);
  });

  it("keeps showing an announcement when one chip is soloed", () => {
    logview.solo("speech");

    expect(shown("say")).toBe(true);
    expect(shown("handset")).toBe(false);
    expect(shown("announce")).toBe(true);
  });

  it("puts every chip back on with reset, and has no chip for a notice", () => {
    logview.solo("combat");

    logview.reset();

    expect(CATS.every((chip) => logview.filters[chip.id])).toBe(true);
    expect(CATS.map((chip) => chip.id as string)).not.toContain("notice");
  });
});
