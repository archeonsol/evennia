// Lines revealed at least once; remounts (filter/search toggles) skip replay.
const revealed = new Set<number>();
// Highest line id present when the log first mounted; backlog never animates.
let baselineId = Number.POSITIVE_INFINITY;

/** Freeze the backlog so only lines appended after mount type in. */
export function markBacklog(maxId: number): void {
  baselineId = maxId;
}

interface TWParams {
  id: number;
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
  const skip = revealed.has(id) || id <= baselineId || durationMs <= 0 || typeof Intl.Segmenter !== "function";
  revealed.add(id);

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
