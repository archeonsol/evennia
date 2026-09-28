import { beforeEach, describe, expect, it } from "vitest";

import { REVIEW_BOTTOM, REVIEW_EMPTY, REVIEW_TOP, ReviewCursor } from "./review";

interface Line {
  id: number;
  text: string;
  hidden?: boolean;
}

function lines(...texts: string[]): Line[] {
  return texts.map((text, id) => ({ id, text }));
}

const readable = (l: Line) => !l.hidden && !!l.text.trim();

describe("ReviewCursor", () => {
  let cursor: ReviewCursor;
  beforeEach(() => {
    cursor = new ReviewCursor();
  });

  it("starts below the newest line: up reads the newest, down is the bottom", () => {
    const log = lines("one", "two", "three");
    expect(cursor.newer(log, readable)).toBe(REVIEW_BOTTOM);
    expect(cursor.older(log, readable)).toBe("three");
  });

  it("walks back one line at a time and stops at the top", () => {
    const log = lines("one", "two", "three");
    expect(cursor.older(log, readable)).toBe("three");
    expect(cursor.older(log, readable)).toBe("two");
    expect(cursor.older(log, readable)).toBe("one");
    expect(cursor.older(log, readable)).toBe(REVIEW_TOP);
    // The top does not move the cursor: down reads the next line, not a jump.
    expect(cursor.newer(log, readable)).toBe("two");
  });

  it("walks forward again and stops at the bottom", () => {
    const log = lines("one", "two", "three");
    cursor.oldest(log, readable);
    expect(cursor.newer(log, readable)).toBe("two");
    expect(cursor.newer(log, readable)).toBe("three");
    expect(cursor.newer(log, readable)).toBe(REVIEW_BOTTOM);
    expect(cursor.older(log, readable)).toBe("two");
  });

  it("skips lines that are not readable (filtered, blank)", () => {
    const log: Line[] = [
      { id: 0, text: "one" },
      { id: 1, text: "   " },
      { id: 2, text: "combat", hidden: true },
      { id: 3, text: "four" },
    ];
    expect(cursor.older(log, readable)).toBe("four");
    expect(cursor.older(log, readable)).toBe("one");
    expect(cursor.newer(log, readable)).toBe("four");
  });

  it("jumps to the oldest and the newest line", () => {
    const log = lines("one", "two", "three");
    expect(cursor.oldest(log, readable)).toBe("one");
    expect(cursor.older(log, readable)).toBe(REVIEW_TOP);
    expect(cursor.newest(log, readable)).toBe("three");
    expect(cursor.newer(log, readable)).toBe(REVIEW_BOTTOM);
  });

  it("reads the nth newest line and carries on from it", () => {
    const log = lines("one", "two", "three", "four");
    expect(cursor.recent(log, readable, 3)).toBe("two");
    expect(cursor.older(log, readable)).toBe("one");
    expect(cursor.recent(log, readable, 9)).toBe("No line 9.");
    // A miss does not move the cursor.
    expect(cursor.newer(log, readable)).toBe("two");
  });

  it("stays on its line while new output arrives", () => {
    const log = lines("one", "two", "three");
    cursor.older(log, readable); // three
    cursor.older(log, readable); // two
    log.push({ id: 3, text: "four" }, { id: 4, text: "five" });
    expect(cursor.older(log, readable)).toBe("one");
    expect(cursor.newer(log, readable)).toBe("two");
    expect(cursor.newer(log, readable)).toBe("three");
    expect(cursor.newer(log, readable)).toBe("four");
  });

  it("steps past a line the scrollback has trimmed away", () => {
    const log = lines("one", "two", "three", "four", "five");
    cursor.oldest(log, readable); // one (id 0)
    cursor.newer(log, readable); // two (id 1)
    const trimmed = log.slice(3); // four, five
    expect(cursor.older(trimmed, readable)).toBe(REVIEW_TOP);
    expect(cursor.newer(trimmed, readable)).toBe("four");
  });

  it("steps past its own line when a filter hides it", () => {
    const log = lines("one", "two", "three");
    cursor.recent(log, readable, 2); // two (id 1)
    log[1].hidden = true;
    expect(cursor.newer(log, readable)).toBe("three");
    cursor.recent(log, readable, 1); // three
    expect(cursor.older(log, readable)).toBe("one");
  });

  it("goes back below the newest line on reset", () => {
    const log = lines("one", "two", "three");
    cursor.oldest(log, readable);
    cursor.reset();
    expect(cursor.older(log, readable)).toBe("three");
  });

  it("says there is nothing to read in an empty or all-hidden log", () => {
    expect(cursor.older([], readable)).toBe(REVIEW_EMPTY);
    expect(cursor.newer([], readable)).toBe(REVIEW_EMPTY);
    expect(cursor.oldest([], readable)).toBe(REVIEW_EMPTY);
    expect(cursor.newest([], readable)).toBe(REVIEW_EMPTY);
    const hidden: Line[] = [{ id: 0, text: "x", hidden: true }];
    expect(cursor.older(hidden, readable)).toBe(REVIEW_EMPTY);
    expect(cursor.recent(hidden, readable, 1)).toBe("No line 1.");
  });
});
