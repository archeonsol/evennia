// Up and Down on the command line wiped whatever was being typed: Up replaced
// it with the newest command and Down past the newest set the line to "".
// These pin the walk that keeps it.

import { describe, expect, it } from "vitest";
import { HistoryWalk, shouldRecall, type RecallQuery } from "./history";

const HISTORY = ["say three", "say two", "say one"]; // newest first

describe("HistoryWalk", () => {
  it("keeps the typed line as the bottom slot", () => {
    const w = new HistoryWalk();
    expect(w.step(1, "half a pose", HISTORY)).toBe("say three");
    expect(w.step(1, "say three", HISTORY)).toBe("say two");
    expect(w.step(-1, "say two", HISTORY)).toBe("say three");
    expect(w.step(-1, "say three", HISTORY)).toBe("half a pose");
    expect(w.walking).toBe(false);
  });

  it("goes nowhere past either end", () => {
    const w = new HistoryWalk();
    expect(w.step(-1, "typed", HISTORY)).toBeNull();
    w.step(1, "typed", HISTORY);
    w.step(1, "say three", HISTORY);
    w.step(1, "say two", HISTORY);
    expect(w.index).toBe(2);
    expect(w.step(1, "say one", HISTORY)).toBeNull();
    expect(w.index).toBe(2);
  });

  it("does nothing on an empty history", () => {
    const w = new HistoryWalk();
    expect(w.step(1, "typed", [])).toBeNull();
    expect(w.walking).toBe(false);
  });

  it("keeps an edit to a recalled command while walking", () => {
    const w = new HistoryWalk();
    w.step(1, "", HISTORY);
    w.step(1, "say three", HISTORY);
    // Edited "say two", walked on, came back: the edit is still there.
    expect(w.step(1, "say two, edited", HISTORY)).toBe("say one");
    expect(w.step(-1, "say one", HISTORY)).toBe("say two, edited");
    expect(w.shown()).toBe("say two, edited");
  });

  it("keeps edits across a walk back to the typed line", () => {
    const w = new HistoryWalk();
    w.step(1, "typed", HISTORY);
    w.step(-1, "say three, edited", HISTORY);
    expect(w.step(1, "typed", HISTORY)).toBe("say three, edited");
  });

  it("forgets edits when a line is sent", () => {
    const w = new HistoryWalk();
    w.step(1, "", HISTORY);
    w.step(-1, "say three, edited", HISTORY);
    w.finish();
    expect(w.step(1, "", HISTORY)).toBe("say three");
  });

  it("passes over a slot that reads like the line", () => {
    const w = new HistoryWalk();
    w.step(1, "", HISTORY);
    w.step(1, "say three", HISTORY);
    // "say two" edited to read like its older neighbour.
    expect(w.step(1, "say one", HISTORY)).toBeNull();
    expect(w.step(-1, "say one", HISTORY)).toBe("say three");
  });

  it("passes over the typed line's twin both ways", () => {
    // "Keep command after sending" leaves the newest command on the line.
    const w = new HistoryWalk();
    expect(w.step(1, "say three", HISTORY)).toBe("say two");
    expect(w.step(-1, "say two", HISTORY)).toBe("say three");
    expect(w.walking).toBe(false);
  });

  it("never passes over the typed slot", () => {
    const w = new HistoryWalk();
    w.step(1, "say two", HISTORY); // typed line equal to an older entry
    expect(w.index).toBe(0);
    expect(w.step(-1, "say three", HISTORY)).toBe("say two");
    expect(w.walking).toBe(false);
  });

  it("goes nowhere when every older slot reads like the line", () => {
    const w = new HistoryWalk();
    expect(w.step(1, "say three", ["say three"])).toBeNull();
    expect(w.walking).toBe(false);
  });

  it("walks the history as it stood when the walk began", () => {
    // A macro or the hotbar can send a command in the middle of a walk.
    const w = new HistoryWalk();
    w.step(1, "typed", HISTORY);
    const later = ["hotbar look", ...HISTORY];
    expect(w.step(1, "say three", later)).toBe("say two");
    expect(w.step(-1, "say two", later)).toBe("say three");
    expect(w.step(-1, "say three", later)).toBe("typed");
    // The next walk sees the new history.
    expect(w.step(1, "typed", later)).toBe("hotbar look");
  });

  it("drops edits when the next walk starts on a changed history", () => {
    const w = new HistoryWalk();
    w.step(1, "", HISTORY);
    w.step(-1, "say three, edited", HISTORY);
    expect(w.step(1, "", ["new", ...HISTORY])).toBe("new");
    expect(w.step(1, "new", ["new", ...HISTORY])).toBe("say three");
  });

  describe("finish", () => {
    it("gives back the typed line when a recalled command was sent", () => {
      const w = new HistoryWalk();
      w.step(1, "half a pose", HISTORY);
      expect(w.finish()).toBe("half a pose");
      expect(w.walking).toBe(false);
    });

    it("gives back nothing when the typed line itself was sent", () => {
      const w = new HistoryWalk();
      w.step(1, "half a pose", HISTORY);
      w.step(-1, "say three", HISTORY);
      expect(w.finish()).toBe("");
    });

    it("gives back nothing for a blank typed line", () => {
      const w = new HistoryWalk();
      w.step(1, "   ", HISTORY);
      expect(w.finish()).toBe("");
    });

    it("gives back nothing without a walk", () => {
      expect(new HistoryWalk().finish()).toBe("");
    });
  });

  describe("jump", () => {
    it("keeps the typed line, as a step does", () => {
      const w = new HistoryWalk();
      expect(w.jump("say two", "half a pose", HISTORY)).toBe("say two");
      expect(w.index).toBe(1);
      expect(w.step(-1, "say two", HISTORY)).toBe("say three");
      expect(w.step(-1, "say three", HISTORY)).toBe("half a pose");
    });

    it("keeps an edit to the slot it leaves", () => {
      const w = new HistoryWalk();
      w.step(1, "", HISTORY);
      w.jump("say one", "say three, edited", HISTORY);
      expect(w.step(-1, "say one", HISTORY)).toBe("say two");
      expect(w.step(-1, "say two", HISTORY)).toBe("say three, edited");
    });

    it("finds a command sent since the walk began", () => {
      const w = new HistoryWalk();
      w.step(1, "half a pose", HISTORY);
      const later = ["hotbar look", ...HISTORY];
      expect(w.jump("hotbar look", "say three", later)).toBe("hotbar look");
      expect(w.index).toBe(0);
      expect(w.step(-1, "hotbar look", later)).toBe("half a pose");
    });
  });
});

describe("shouldRecall", () => {
  const base: RecallQuery = { walking: false, untouched: false, line: "", atEdge: true, keys: "edge" };

  it("recalls from the edge line of what is typed", () => {
    expect(shouldRecall({ ...base, line: "half a pose" })).toBe(true);
    expect(shouldRecall({ ...base, line: "half a pose", atEdge: false })).toBe(false);
  });

  it("keeps walking through an untouched recalled command from anywhere in it", () => {
    expect(shouldRecall({ ...base, walking: true, untouched: true, line: "a long pose", atEdge: false })).toBe(true);
  });

  it("treats an edited recalled command like typed text", () => {
    const q = { ...base, walking: true, untouched: false, line: "edited" };
    expect(shouldRecall({ ...q, atEdge: false })).toBe(false);
    expect(shouldRecall({ ...q, atEdge: true })).toBe(true);
  });

  describe('with "empty"', () => {
    const empty: RecallQuery = { ...base, keys: "empty" };

    it("leaves typed text out of the walk", () => {
      expect(shouldRecall({ ...empty, line: "half a pose" })).toBe(false);
    });

    it("recalls from an empty line", () => {
      expect(shouldRecall({ ...empty, line: "" })).toBe(true);
      expect(shouldRecall({ ...empty, line: "  " })).toBe(true);
    });

    it("keeps walking once a walk has begun", () => {
      expect(shouldRecall({ ...empty, walking: true, untouched: true, line: "say two" })).toBe(true);
      expect(shouldRecall({ ...empty, walking: true, untouched: false, line: "say two, edited" })).toBe(true);
    });
  });
});
