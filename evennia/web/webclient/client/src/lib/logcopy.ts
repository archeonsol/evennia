// Selecting and copying the virtualized game log.
//
// Only the rows around the viewport exist as DOM (lib/logvirtual.ts), and a
// browser selection is made of DOM nodes. Two things follow, and this module
// handles both:
//
//   * Held rows. The rows a selection starts and ends on are kept mounted
//     (`heldLines`, fed to the virtualizer's `getPinned`), so the reader can
//     scroll anywhere with a selection open and it survives. The rows between
//     the ends mount and unmount as usual; whichever are mounted sit between
//     the ends in DOM order, so the browser paints them selected.
//   * Copy from the scrollback. A copy that spans lines is built from the
//     lines themselves (`spanToClipboard`), not from whichever rows happen to
//     be mounted, so the middle of a selection across three thousand lines is
//     all there. The two ends are cut out of their lines' own HTML at the
//     character the selection sits on.
//
// A selection inside one line is left to the browser, which already copies it
// exactly; so is one that reaches outside the log.

import type { LogLine } from "./session.svelte";
import { indexOfId } from "./logvirtual";
import { nodesToText } from "./text";
import { clipboardHtml } from "./transcript";

/** A selection boundary in log terms: which line, and how far into its text. */
export interface LogPoint {
  /** The line's id, as its row's `data-lid` carries it. */
  lid: number;
  /**
   * Characters of the line's text nodes before the point. `Infinity` is the
   * end of the line: a row can still be typing in, so its DOM does not know
   * how long the line is.
   */
  offset: number;
}

/** A selection across the log, start before end in document order. */
export interface LogSpan {
  start: LogPoint;
  end: LogPoint;
}

export interface LogClip {
  text: string;
  html: string;
}

const ROW = ".log-line";

function rowOf(logEl: HTMLElement, node: Node): HTMLElement | null {
  const el = node instanceof Element ? node : node.parentElement;
  const row = el?.closest<HTMLElement>(ROW) ?? null;
  return row && logEl.contains(row) ? row : null;
}

function lidOf(row: HTMLElement): number | null {
  const lid = Number(row.dataset.lid);
  return row.dataset.lid !== undefined && Number.isInteger(lid) ? lid : null;
}

/**
 * The ids of the lines a live selection starts and ends on, ascending: the
 * rows to hold mounted. Empty when nothing is selected or neither end is on a
 * line of this log.
 */
export function heldLines(logEl: HTMLElement, sel: Selection | null): number[] {
  if (!sel || sel.isCollapsed || !sel.rangeCount) return [];
  const ids = new Set<number>();
  for (const node of [sel.anchorNode, sel.focusNode]) {
    const row = node ? rowOf(logEl, node) : null;
    const lid = row ? lidOf(row) : null;
    if (lid !== null) ids.add(lid);
  }
  return Array.from(ids).sort((a, b) => a - b);
}

/** A boundary point as a log point, or null when it is not on this log's lines. */
function pointIn(logEl: HTMLElement, node: Node, offset: number, edge: "start" | "end"): LogPoint | null {
  if (!logEl.contains(node)) return null;
  const row = rowOf(logEl, node);
  if (row) {
    const lid = lidOf(row);
    const body = row.querySelector<HTMLElement>(".body");
    if (lid === null || !body) return null;
    const whole = document.createRange();
    whole.selectNodeContents(body);
    // Before the text (the timestamp gutter) or after it.
    const side = whole.comparePoint(node, offset);
    if (side < 0) return { lid, offset: 0 };
    if (side > 0) return { lid, offset: Infinity };
    const after = document.createRange();
    after.setStart(node, offset);
    after.setEnd(body, body.childNodes.length);
    // Nothing after the point is the end of the line, however much of it has
    // typed in so far.
    if (!after.toString()) return { lid, offset: Infinity };
    const before = document.createRange();
    before.setStart(body, 0);
    before.setEnd(node, offset);
    return { lid, offset: before.toString().length };
  }
  // Between rows (the spacer, the scroll box itself): the nearest line inward.
  const at = document.createRange();
  at.setStart(node, offset);
  const rows = Array.from(logEl.querySelectorAll<HTMLElement>(ROW));
  if (edge === "start") {
    const next = rows.find((r) => at.comparePoint(r, 0) >= 0);
    const lid = next ? lidOf(next) : null;
    return lid === null ? null : { lid, offset: 0 };
  }
  const prev = rows.findLast((r) => at.comparePoint(r, r.childNodes.length) <= 0);
  const lid = prev ? lidOf(prev) : null;
  return lid === null ? null : { lid, offset: Infinity };
}

/**
 * The live selection as a span of log lines, or null when there is none or
 * either end is outside this log's lines (the browser's own copy handles that).
 */
export function selectionSpan(logEl: HTMLElement, sel: Selection | null): LogSpan | null {
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const first = sel.getRangeAt(0);
  const last = sel.getRangeAt(sel.rangeCount - 1);
  const start = pointIn(logEl, first.startContainer, first.startOffset, "start");
  const end = pointIn(logEl, last.endContainer, last.endOffset, "end");
  return start && end ? { start, end } : null;
}

/** A character offset over `root`'s text nodes, as a boundary point. */
function locate(root: DocumentFragment, offset: number, edge: "start" | "end"): [Node, number] {
  if (offset <= 0) return [root, 0];
  if (offset === Infinity) return [root, root.childNodes.length];
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let seen = 0;
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const length = (n as Text).length;
    // On a boundary between two text nodes a start opens the next node and an
    // end closes the previous one: the same text, without an empty wrapper.
    if (edge === "start" ? offset < seen + length : offset <= seen + length) return [n, offset - seen];
    seen += length;
  }
  return [root, root.childNodes.length];
}

/**
 * The part of a line's HTML between two character offsets (counted over its
 * text nodes, as `LogPoint` counts them), as a fragment with its styling
 * intact: a cut through a coloured run keeps the run's element around the
 * part that was selected.
 */
export function sliceLine(html: string, from: number, to: number): DocumentFragment {
  const template = document.createElement("template");
  template.innerHTML = html;
  const root = template.content;
  const range = root.ownerDocument.createRange();
  const [startNode, startOffset] = locate(root, from, "start");
  const [endNode, endOffset] = locate(root, to, "end");
  range.setStart(startNode, startOffset);
  // An end before the start collapses the range: an empty cut, not an error.
  range.setEnd(endNode, endOffset);
  return range.cloneContents();
}

/**
 * The clipboard for a selection across several lines: plain text (one line
 * per log line, the same text search and the downloads use) and inline-styled
 * HTML. Null for a selection within one line, which the browser copies exactly
 * on its own, and for a line that has left the scrollback since.
 */
export function spanToClipboard(lines: readonly LogLine[], span: LogSpan): LogClip | null {
  const first = indexOfId(lines, span.start.lid);
  const last = indexOfId(lines, span.end.lid);
  if (first < 0 || last < 0 || first >= last) return null;
  const texts: string[] = [];
  const parts: (string | DocumentFragment)[] = [];
  for (let i = first; i <= last; i++) {
    const line = lines[i];
    const from = i === first ? span.start.offset : 0;
    const to = i === last ? span.end.offset : Infinity;
    if (from <= 0 && to === Infinity) {
      texts.push(line.text);
      parts.push(line.html);
      continue;
    }
    const cut = sliceLine(line.html, from, to);
    texts.push(nodesToText(cut.childNodes));
    parts.push(cut);
  }
  return { text: texts.join("\n"), html: clipboardHtml(parts) };
}

/**
 * Select from the start of one line's row to the end of another's. Both rows
 * must be mounted: hold them first. False when either is not in the DOM yet.
 */
export function selectRows(logEl: HTMLElement, firstLid: number, lastLid: number): boolean {
  const first = logEl.querySelector<HTMLElement>(`${ROW}[data-lid="${firstLid}"] .body`);
  const last = logEl.querySelector<HTMLElement>(`${ROW}[data-lid="${lastLid}"] .body`);
  const sel = document.getSelection();
  if (!first || !last || !sel) return false;
  sel.setBaseAndExtent(first, 0, last, last.childNodes.length);
  return true;
}
