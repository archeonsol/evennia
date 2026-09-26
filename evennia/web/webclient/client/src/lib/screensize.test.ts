import { describe, expect, it } from "vitest";
import { REPORT_DELAY_MS, ScreenReporter, gridFor, type ScreenGrid } from "./screensize";

describe("gridFor", () => {
  it("counts the whole character cells that fit", () => {
    // 900px of text at 9px a character, 22px lines in 400px.
    expect(gridFor({ width: 900, height: 400 }, { width: 9, height: 22 })).toEqual({ cols: 100, rows: 18 });
    // A partial cell does not count: text breaks before it.
    expect(gridFor({ width: 899, height: 399 }, { width: 9, height: 22 })).toEqual({ cols: 99, rows: 18 });
  });

  it("reports nothing for a box that is not laid out", () => {
    // A hidden dockview panel measures 0x0; reporting that would tell the
    // server the player's screen is a handful of columns wide.
    expect(gridFor({ width: 0, height: 0 }, { width: 9, height: 22 })).toBeNull();
    expect(gridFor({ width: 900, height: 0 }, { width: 9, height: 22 })).toBeNull();
    expect(gridFor({ width: 900, height: 400 }, { width: 0, height: 22 })).toBeNull();
    expect(gridFor({ width: Number.NaN, height: 400 }, { width: 9, height: 22 })).toBeNull();
  });

  it("keeps the report inside what the server can lay out", () => {
    expect(gridFor({ width: 60, height: 30 }, { width: 9, height: 22 })).toEqual({ cols: 20, rows: 5 });
    expect(gridFor({ width: 90000, height: 90000 }, { width: 9, height: 22 })).toEqual({ cols: 400, rows: 400 });
  });
});

function harness() {
  const sent: ScreenGrid[] = [];
  let pending: (() => void) | null = null;
  let delay = -1;
  const reporter = new ScreenReporter({
    setTimeout: (fn, ms) => {
      pending = fn;
      delay = ms;
      return 1;
    },
    clearTimeout: () => {
      pending = null;
    },
  });
  reporter.connect((grid) => sent.push(grid));
  return {
    reporter,
    sent,
    flush: () => {
      const fn = pending;
      pending = null;
      fn?.();
    },
    get pending() {
      return pending !== null;
    },
    get delay() {
      return delay;
    },
  };
}

describe("ScreenReporter", () => {
  it("reports once a resize settles, not on every frame of the drag", () => {
    const h = harness();
    h.reporter.update({ cols: 100, rows: 30 });
    h.reporter.update({ cols: 90, rows: 30 });
    h.reporter.update({ cols: 80, rows: 28 });
    expect(h.sent).toEqual([]);
    expect(h.delay).toBe(REPORT_DELAY_MS);
    h.flush();
    expect(h.sent).toEqual([{ cols: 80, rows: 28 }]);
  });

  it("does not repeat a size the server already has", () => {
    const h = harness();
    h.reporter.update({ cols: 80, rows: 28 });
    h.flush();
    h.reporter.update({ cols: 80, rows: 28 });
    expect(h.pending).toBe(false);
    h.reporter.update({ cols: 100, rows: 28 });
    h.reporter.update({ cols: 80, rows: 28 });
    h.flush();
    expect(h.sent).toEqual([{ cols: 80, rows: 28 }]);
  });

  it("ignores a box that is not laid out", () => {
    const h = harness();
    h.reporter.update({ cols: 80, rows: 28 });
    h.flush();
    h.reporter.update(null);
    expect(h.pending).toBe(false);
    expect(h.reporter.current).toEqual({ cols: 80, rows: 28 });
  });

  it("tells a new connection straight away", () => {
    // Screen size is a session flag, so every connection starts without one;
    // a reconnect must not wait for the next resize to be told.
    const h = harness();
    h.reporter.update({ cols: 80, rows: 28 });
    h.flush();
    h.reporter.resend();
    expect(h.sent).toEqual([
      { cols: 80, rows: 28 },
      { cols: 80, rows: 28 },
    ]);
  });

  it("sends a pending size on reconnect instead of the stale one", () => {
    const h = harness();
    h.reporter.update({ cols: 80, rows: 28 });
    h.flush();
    h.reporter.update({ cols: 120, rows: 40 });
    h.reporter.resend();
    expect(h.sent.at(-1)).toEqual({ cols: 120, rows: 40 });
    expect(h.pending).toBe(false);
  });

  it("has nothing to resend before the terminal has been measured", () => {
    const h = harness();
    h.reporter.resend();
    expect(h.sent).toEqual([]);
  });
});
