// The game log is virtualized, so a line mounts when it scrolls into view, not
// once at append time. Three gates decide whether a mount still types in:
// the backlog at mount never does, a line the log marked revealed (it landed
// off-screen) never does, and a line older than FRESH_MS never does - the
// moment it was news has passed.

import { logReveal } from "./logreveal";

/** How long after a line lands it may still type in. */
export const FRESH_MS = 1500;

/** Freeze the backlog so only lines appended after mount type in. */
export function markBacklog(maxId: number): void {
  logReveal.markBacklog(maxId);
}

interface TWParams {
  id: number;
  /**
   * Epoch ms the line landed. A line older than FRESH_MS renders instantly:
   * virtualization can mount it late (scrolled back to, panel restored), and
   * replaying the reveal then reads as the log being broken, not alive.
   */
  ts?: number;
  /**
   * Milliseconds to reveal this line in full, regardless of its length, so a
   * help file and a one-liner finish together. 0 (reduce-motion / screenreader
   * / slider at off) renders instantly.
   */
  durationMs: number;
  /** Called each frame so the caller can keep the log scrolled to bottom. */
  onstep?: () => void;
}

/**
 * Given each text node's full text and a UTF-16 cutoff to reveal,
 * return the visible prefix of every node. Characters fill in document order,
 * so colour spans light up left to right.
 */
export function sliceChunks(texts: string[], target: number): string[] {
  const out: string[] = [];
  let acc = 0;
  for (const t of texts) {
    const take = Math.max(0, Math.min(t.length, target - acc));
    out.push(t.slice(0, take));
    acc += t.length;
  }
  return out;
}

/**
 * Reveal a log line's text one grapheme at a time. Walks the element's text
 * nodes so ANSI colour spans survive; only fresh lines animate, and reduced
 * motion / screenreader / the typewriter toggle all render instantly.
 */
export function typewriter(node: HTMLElement, params: TWParams) {
  let finished = false;
  let raf = 0;
  const chunks: { node: Text; text: string }[] = [];
  const { id, durationMs, onstep } = params;
  const fresh = params.ts === undefined || Date.now() - params.ts <= FRESH_MS;
  // A background tab gets no animation frames, so a line typed in there would
  // sit blank until the player looked; it has nobody to animate for anyway.
  const skip =
    !fresh ||
    logReveal.isRevealed(id) ||
    durationMs <= 0 ||
    typeof Intl.Segmenter !== "function" ||
    (typeof document !== "undefined" && document.hidden);
  logReveal.reveal(id);

  const finish = () => {
    if (finished) return;
    finished = true;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    for (const chunk of chunks) chunk.node.data = chunk.text;
    if (chunks.length) onstep?.();
  };

  if (!skip) {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const textNode = n as Text;
      chunks.push({ node: textNode, text: textNode.data });
    }
    const texts = chunks.map((chunk) => chunk.text);
    // Segment the whole line so a style boundary cannot split a grapheme.
    const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });
    const ends = Array.from(segmenter.segment(texts.join("")), ({ index, segment }) => index + segment.length);
    if (ends.length) {
      for (const chunk of chunks) chunk.node.data = "";
      let start: number | undefined;
      const frame = (ts: number) => {
        raf = 0;
        if (finished) return;
        start ??= ts;
        const progress = Math.min(1, (ts - start) / durationMs);
        if (progress >= 1) {
          finish();
          return;
        }
        const count = Math.ceil(progress * ends.length);
        const slices = sliceChunks(texts, count ? ends[count - 1] : 0);
        for (let i = 0; i < chunks.length; i++) {
          if (chunks[i].node.data !== slices[i]) chunks[i].node.data = slices[i];
        }
        onstep?.();
        if (!finished) raf = requestAnimationFrame(frame);
      };
      raf = requestAnimationFrame(frame);
    }
  }

  return {
    update(next: TWParams) {
      if (next.durationMs <= 0) finish();
    },
    destroy() {
      finished = true;
      if (raf) cancelAnimationFrame(raf);
    },
  };
}
