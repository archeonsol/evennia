// The game log's virtualizer configuration, in one place so the component and
// its tests agree on every knob.
//
// The scrollback is capped in the thousands but every line used to be a live
// DOM node; virtualization keeps only the lines around the viewport mounted.
// TanStack Virtual does the math (see docs/webclient_revamp.md: do not
// hand-roll this). What this module fixes is the shape of the log:
//
//   * `anchorTo: "end"` - the log grows at the end and is trimmed at the
//     start. The virtualizer anchors on the item under the viewport, so a
//     reader scrolled up stays exactly where they were through both an append
//     and a trim, instead of being yanked by a re-indexed list.
//   * `followOnAppend` - while the reader is at the bottom, appends follow.
//   * `scrollEndThreshold` - "at the bottom" matches the log's own re-pin
//     distance (REPIN_PX), so the pin state and the virtualizer never disagree
//     about whether the reader is following.
//
// Everything is keyed by the line id (`getItemKey`), never by index: measured
// heights survive a trim, and re-filtering the log re-anchors on the same
// line rather than the same row number.
//
// Rows can also be held mounted outside the window (`getPinned`). A text
// selection lives in DOM nodes, so the rows where one starts and ends must
// survive the reader scrolling away from them, or the browser drops the
// selection with the node (see lib/logcopy.ts).

import {
  Virtualizer,
  defaultRangeExtractor,
  observeElementOffset,
  observeElementRect,
} from "@tanstack/virtual-core";
import type { Range, VirtualizerOptions } from "@tanstack/virtual-core";

import { REPIN_PX } from "./autoscroll";

/** How many rows past the viewport stay mounted on either side. */
export const LOG_OVERSCAN = 10;
/** Fallback line height when the element's computed style says nothing useful. */
export const DEFAULT_LINE_PX = 24;

type ScrollFn<T extends Element> = VirtualizerOptions<T, HTMLElement>["scrollToFn"];
type RectFn<T extends Element> = VirtualizerOptions<T, HTMLElement>["observeElementRect"];
type OffsetFn<T extends Element> = VirtualizerOptions<T, HTMLElement>["observeElementOffset"];

export interface LogVirtualizerOptions<T extends HTMLElement> {
  getScrollElement: () => T | null;
  getCount: () => number;
  /** Stable identity for a row: the scrollback line id. */
  getKey: (index: number) => number;
  estimateSize: (index: number) => number;
  /**
   * Fired when the rendered range or measurements change. The instance is
   * handed over so callbacks never have to read it back out of reactive state
   * (which would make an effect that owns the instance depend on it).
   */
  onChange: (instance: Virtualizer<T, HTMLElement>, sync: boolean) => void;
  /** The top our code just wrote, so the log can tell its own scroll from the user's. */
  onWrite?: (top: number) => void;
  /**
   * Row indexes to keep mounted wherever the viewport is. Read on every
   * `sync()`, so call `sync()` when the answer changes.
   */
  getPinned?: () => number[];
  // Test seams; production uses the real element observers and the scroll write
  // below.
  scrollToFn?: ScrollFn<T>;
  observeElementRect?: RectFn<T>;
  observeElementOffset?: OffsetFn<T>;
}

export interface LogVirtualizer<T extends HTMLElement> {
  instance: Virtualizer<T, HTMLElement>;
  /**
   * Re-state the current count, keys and pinned rows. `setOptions` replaces
   * the whole options object (it does not merge), so this must always hand
   * over every option; call it whenever the line list or the pinned rows
   * change.
   */
  sync: () => void;
}

/**
 * The window's indexes plus the pinned ones, ascending and unique.
 *
 * Ascending matters: the rows render in this order, and DOM order is what a
 * text selection spans, so a held row must sit before or after the window
 * exactly where its line is.
 */
export function withPinned(indexes: number[], pinned: number[], count: number): number[] {
  const extra = pinned.filter((i) => Number.isInteger(i) && i >= 0 && i < count);
  if (!extra.length) return indexes;
  return Array.from(new Set([...indexes, ...extra])).sort((a, b) => a - b);
}

/**
 * Where the line with this id sits in a scrollback list, or -1.
 *
 * Ids are handed out in arrival order and every list the log renders keeps
 * that order (appends at the end, trims and filters only remove), so a binary
 * search finds it without walking five thousand lines.
 */
export function indexOfId(lines: readonly { id: number }[], id: number): number {
  let lo = 0;
  let hi = lines.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const at = lines[mid].id;
    if (at === id) return mid;
    if (at < id) lo = mid + 1;
    else hi = mid - 1;
  }
  return -1;
}

export function createLogVirtualizer<T extends HTMLElement>(
  options: LogVirtualizerOptions<T>,
): LogVirtualizer<T> {
  // The virtualizer memoizes its rendered indexes on the extractor's identity,
  // so a new extractor is made only when the pinned set changes. Handing over
  // a fresh closure on every sync would recompute the window on every append.
  let pinnedKey = "";
  let extractor: (range: Range) => number[] = defaultRangeExtractor;
  const rangeExtractor = (): ((range: Range) => number[]) => {
    const pinned = options.getPinned?.() ?? [];
    const key = pinned.join(",");
    if (key !== pinnedKey) {
      pinnedKey = key;
      extractor = pinned.length
        ? (range) => withPinned(defaultRangeExtractor(range), pinned, range.count)
        : defaultRangeExtractor;
    }
    return extractor;
  };
  const build = (): VirtualizerOptions<T, HTMLElement> => ({
    count: options.getCount(),
    getScrollElement: options.getScrollElement,
    estimateSize: options.estimateSize,
    getItemKey: options.getKey,
    anchorTo: "end",
    followOnAppend: "auto",
    scrollEndThreshold: REPIN_PX,
    overscan: LOG_OVERSCAN,
    rangeExtractor: rangeExtractor(),
    onChange: options.onChange,
    scrollToFn:
      options.scrollToFn ??
      ((offset, opts) => {
        const el = options.getScrollElement();
        if (!el) return;
        const top = offset + (opts.adjustments ?? 0);
        options.onWrite?.(top);
        el.scrollTo({ top, behavior: opts.behavior });
      }),
    observeElementRect: options.observeElementRect ?? observeElementRect,
    observeElementOffset: options.observeElementOffset ?? observeElementOffset,
  });
  const instance = new Virtualizer<T, HTMLElement>(build());
  return { instance, sync: () => instance.setOptions(build()) };
}

/**
 * A line-height estimate for rows that have never been measured.
 *
 * `getComputedStyle().lineHeight` is normally the used pixel value ("22.5px").
 * When a stylesheet says `normal`, fall back to 1.5x the font size, then to a
 * plain default. An estimate only ever affects unmounted rows: the moment a row
 * mounts it is measured for real.
 */
export function estimateLinePx(
  style: Pick<CSSStyleDeclaration, "lineHeight" | "fontSize"> | null | undefined,
): number {
  if (!style) return DEFAULT_LINE_PX;
  const lineHeight = Number.parseFloat(style.lineHeight);
  if (Number.isFinite(lineHeight)) return lineHeight;
  const fontSize = Number.parseFloat(style.fontSize);
  if (Number.isFinite(fontSize)) return Math.round(fontSize * 1.5);
  return DEFAULT_LINE_PX;
}
