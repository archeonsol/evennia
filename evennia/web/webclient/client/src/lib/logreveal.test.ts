import { describe, expect, it } from "vitest";

import { LogReveal } from "./logreveal";

describe("LogReveal", () => {
  it("owes a reveal for a fresh line", () => {
    const r = new LogReveal();
    expect(r.isRevealed(1)).toBe(false);
  });

  it("never owes the backlog at or below the baseline", () => {
    const r = new LogReveal();
    r.markBacklog(10);
    expect(r.isRevealed(10)).toBe(true);
    expect(r.isRevealed(3)).toBe(true);
    expect(r.isRevealed(11)).toBe(false);
  });

  it("spends a single line's reveal", () => {
    const r = new LogReveal();
    r.reveal(7);
    expect(r.isRevealed(7)).toBe(true);
    expect(r.isRevealed(8)).toBe(false);
  });

  it("spends a whole batch", () => {
    const r = new LogReveal();
    r.revealAll([4, 5, 6]);
    expect([4, 5, 6].map((id) => r.isRevealed(id))).toEqual([true, true, true]);
  });

  it("keeps a later line owed after a backlog freeze", () => {
    const r = new LogReveal();
    r.markBacklog(2);
    r.revealAll([5]);
    expect(r.isRevealed(3)).toBe(false);
    expect(r.isRevealed(5)).toBe(true);
  });
});
