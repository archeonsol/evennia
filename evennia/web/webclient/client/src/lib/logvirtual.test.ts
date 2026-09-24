import { describe, expect, it } from "vitest";

import type { Virtualizer } from "@tanstack/virtual-core";

import { createLogVirtualizer, estimateLinePx } from "./logvirtual";

const VIEWPORT = 300;
const ESTIMATE = 24;
const WIDTH = 600;

/**
 * A window good enough for the virtualizer's rAF-scheduled scroll reconcile.
 * Frames are run by hand so the tests stay deterministic.
 */
class FakeWindow {
  private queue = new Map<number, FrameRequestCallback>();
  private nextId = 1;
  private time = 0;
  performance = { now: () => this.time };

  requestAnimationFrame(cb: FrameRequestCallback): number {
    const id = this.nextId++;
    this.queue.set(id, cb);
    return id;
  }

  cancelAnimationFrame(id: number): void {
    this.queue.delete(id);
  }

  /** Run the callbacks queued for the next frame. */
  frame(): void {
    this.time += 16;
    const due = [...this.queue.values()];
    this.queue.clear();
    for (const cb of due) cb(this.time);
  }
}

/** A scroll element: scrollHeight/clientHeight and a clamping scrollTo. */
class FakeScroller {
  scrollTop = 0;
  contentHeight = 0;
  readonly ownerDocument: { defaultView: FakeWindow };

  constructor(
    public clientHeight: number,
    win: FakeWindow,
  ) {
    this.ownerDocument = { defaultView: win };
  }

  get scrollHeight(): number {
    return this.contentHeight;
  }

  scrollTo(opts: { top?: number }): void {
    const max = Math.max(0, this.contentHeight - this.clientHeight);
    this.scrollTop = Math.max(0, Math.min(opts.top ?? 0, max));
  }
}

interface Harness {
  scroller: FakeScroller;
  v: Virtualizer<HTMLElement, HTMLElement>;
  keys: number[];
  /** Swap the list, as the log does when lines append or trim. */
  setKeys(next: number[]): void;
  /** Simulate a frame boundary: run rAF work, then the spacer/layout and scroll event. */
  settle(frames?: number): void;
  /** First measurement of a row (mount). */
  measure(index: number, height: number): void;
  /** A row changing size after it was measured (ResizeObserver). */
  resize(index: number, height: number): void;
  topVisible(): { index: number; key: number; delta: number };
}

function range(from: number, to: number): number[] {
  return Array.from({ length: to - from }, (_, i) => from + i);
}

/**
 * Mirrors GameLog's wiring: options are re-set on every list change, the
 * spacer's height follows the measurements synchronously (the component writes
 * it before `_willUpdate`, and in `onChange`), and scroll events are delivered
 * after each frame.
 */
function makeHarness(init: { keys?: number[]; viewport?: number; estimate?: number } = {}): Harness {
  const win = new FakeWindow();
  const scroller = new FakeScroller(init.viewport ?? VIEWPORT, win);
  let keys = init.keys ?? range(0, 5000);
  let offsetCb: ((offset: number, isScrolling: boolean) => void) | null = null;
  let v!: Virtualizer<HTMLElement, HTMLElement>;

  const sync = () => {
    scroller.contentHeight = v.getTotalSize();
  };
  const flushScroll = () => offsetCb?.(scroller.scrollTop, false);
  const settle = (frames = 6) => {
    for (let i = 0; i < frames; i++) {
      win.frame();
      sync();
      flushScroll();
    }
  };

  const handle = createLogVirtualizer({
    getScrollElement: () => scroller as unknown as HTMLElement,
    getCount: () => keys.length,
    getKey: (i) => keys[i],
    estimateSize: () => init.estimate ?? ESTIMATE,
    onChange: sync,
    observeElementRect: (_instance, cb) => {
      cb({ width: WIDTH, height: scroller.clientHeight });
    },
    observeElementOffset: (_instance, cb) => {
      offsetCb = cb;
    },
  });
  v = handle.instance;
  v._didMount();
  v._willUpdate();
  sync();

  return {
    scroller,
    v,
    get keys() {
      return keys;
    },
    setKeys(next) {
      keys = [...next];
      handle.sync();
      sync();
      v._willUpdate();
    },
    settle,
    measure(index, height) {
      v.measureElement({
        getAttribute: (name: string) => (name === "data-index" ? String(index) : null),
        isConnected: true,
        offsetHeight: height,
      } as unknown as HTMLElement);
    },
    resize(index, height) {
      v.resizeItem(index, height);
    },
    topVisible() {
      const offset = v.scrollOffset ?? 0;
      const item = v.getVirtualItemForOffset(offset);
      if (!item) throw new Error("no item under the viewport");
      return { index: item.index, key: item.key as number, delta: offset - item.start };
    },
  };
}

describe("createLogVirtualizer", () => {
  it("renders only a window around the viewport", () => {
    const h = makeHarness();
    h.settle();
    const items = h.v.getVirtualItems();
    expect(items.length).toBeGreaterThan(0);
    // A viewport of 300px over 24px rows is ~13 rows; overscan is 10 a side.
    expect(items.length).toBeLessThan(60);
  });

  it("starts at the end after scrollToEnd", () => {
    const h = makeHarness();
    h.v.scrollToEnd();
    h.settle();
    expect(h.v.isAtEnd(1)).toBe(true);
    expect(h.v.getVirtualItems().some((i) => i.index === h.keys.length - 1)).toBe(true);
  });

  it("follows an append while at the end", () => {
    const h = makeHarness();
    h.v.scrollToEnd();
    h.settle();
    const before = h.scroller.scrollTop;
    h.setKeys([...h.keys, 5000]);
    h.settle();
    expect(h.v.isAtEnd(1)).toBe(true);
    expect(h.scroller.scrollTop).toBeGreaterThan(before);
    expect(h.v.getVirtualItems().some((i) => i.index === 5000)).toBe(true);
  });

  it("keeps a scrolled-up reader anchored through an append", () => {
    const h = makeHarness();
    h.v.scrollToOffset(30000);
    h.settle();
    const before = h.topVisible();
    h.setKeys([...h.keys, 5000]);
    h.settle();
    const after = h.topVisible();
    expect(after.key).toBe(before.key);
    expect(Math.abs(after.delta - before.delta)).toBeLessThan(1);
    expect(h.v.isAtEnd()).toBe(false);
  });

  it("keeps a scrolled-up reader anchored through a front trim", () => {
    const h = makeHarness();
    h.v.scrollToOffset(30000);
    h.settle();
    const before = h.topVisible();
    h.setKeys(h.keys.slice(500));
    h.settle();
    const after = h.topVisible();
    expect(after.key).toBe(before.key);
    expect(Math.abs(after.delta - before.delta)).toBeLessThan(1);
  });

  it("stays at the end through a front trim while following", () => {
    const h = makeHarness();
    h.v.scrollToEnd();
    h.settle();
    h.setKeys(h.keys.slice(500));
    h.settle();
    expect(h.v.isAtEnd(1)).toBe(true);
  });

  it("keeps measured heights across a trim", () => {
    const h = makeHarness();
    h.v.scrollToEnd();
    h.settle();
    const last = h.keys.length - 1;
    h.measure(last, 60);
    h.setKeys(h.keys.slice(500));
    h.settle();
    const kept = h.v.getVirtualItems().find((i) => i.key === 4999);
    expect(kept?.size).toBe(60);
  });

  it("compensates scroll when a row above the viewport is measured for the first time", () => {
    const h = makeHarness();
    h.v.scrollToOffset(30000);
    h.settle();
    const before = h.topVisible();
    const top = h.scroller.scrollTop;
    // An unmeasured row above the fold turns out to be 100px, not 24.
    h.measure(before.index - 1, 100);
    h.settle();
    expect(h.scroller.scrollTop).toBe(top + 76);
    expect(h.topVisible().key).toBe(before.key);
  });

  it("compensates scroll when a measured row above the viewport grows", () => {
    const h = makeHarness();
    h.v.scrollToOffset(30000);
    h.settle();
    const before = h.topVisible();
    h.measure(before.index - 1, ESTIMATE); // mount it, so the next change is a re-measure
    h.settle();
    const top = h.scroller.scrollTop;
    h.resize(before.index - 1, ESTIMATE + 30);
    h.settle();
    expect(h.scroller.scrollTop).toBe(top + 30);
    expect(h.topVisible().key).toBe(before.key);
  });

  it("keeps the bottom pinned when the last row grows (typewriter)", () => {
    const h = makeHarness();
    h.v.scrollToEnd();
    h.settle();
    const last = h.keys.length - 1;
    const top = h.scroller.scrollTop;
    h.resize(last, ESTIMATE + 40);
    h.settle();
    expect(h.v.isAtEnd(1)).toBe(true);
    expect(h.scroller.scrollTop).toBe(top + 40);
  });

  it("does not drag the viewport when a row spanning the fold grows", () => {
    const h = makeHarness();
    h.v.scrollToOffset(30000);
    h.settle();
    const anchor = h.topVisible();
    const top = h.scroller.scrollTop;
    // The anchor row spans the fold: growing it must not move the viewport.
    h.measure(anchor.index, ESTIMATE);
    h.settle();
    const top2 = h.scroller.scrollTop;
    h.resize(anchor.index, ESTIMATE + 30);
    h.settle();
    expect(h.scroller.scrollTop).toBe(top2);
  });

  it("centres a searched row in the viewport", () => {
    const h = makeHarness();
    h.v.scrollToEnd();
    h.settle();
    h.v.scrollToIndex(2500, { align: "center" });
    h.settle();
    const visible = h.v.getVirtualItems();
    expect(visible.some((i) => i.index === 2500)).toBe(true);
    const item = visible.find((i) => i.index === 2500);
    const centre = h.scroller.scrollTop + h.scroller.clientHeight / 2;
    expect(centre).toBeGreaterThan(item!.start);
    expect(centre).toBeLessThan(item!.end);
  });

  it("re-anchors on the same line when a filter re-indexes the list", () => {
    const h = makeHarness();
    h.v.scrollToOffset(30000);
    h.settle();
    const before = h.topVisible();
    // Drop every odd key (the anchor itself is even): indices shift, keys do not.
    const filtered = h.keys.filter((k) => k % 2 === 0);
    h.setKeys(filtered);
    h.settle();
    const after = h.topVisible();
    expect(after.key).toBe(before.key);
    expect(Math.abs(after.delta - before.delta)).toBeLessThan(1);
  });
});

describe("estimateLinePx", () => {
  const style = (lineHeight: string, fontSize: string) =>
    ({ lineHeight, fontSize }) as CSSStyleDeclaration;

  it("uses the computed pixel line-height", () => {
    expect(estimateLinePx(style("22.5px", "15px"))).toBe(22.5);
  });

  it("falls back to 1.5x the font size when line-height is normal", () => {
    expect(estimateLinePx(style("normal", "16px"))).toBe(24);
  });

  it("falls back to the default when there is no style", () => {
    expect(estimateLinePx(null)).toBe(24);
    expect(estimateLinePx(undefined)).toBe(24);
  });

  it("falls back to the default when nothing parses", () => {
    expect(estimateLinePx(style("", "auto"))).toBe(24);
  });
});
