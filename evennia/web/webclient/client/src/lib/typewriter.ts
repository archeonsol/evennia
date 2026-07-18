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
 * Given each text node's full text and a total number of characters to reveal,
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
 * Reveal a log line's text one character at a time. Walks the element's text
 * nodes so ANSI colour spans survive; only fresh lines animate, and reduced
 * motion / screenreader / the typewriter toggle all render instantly.
 */
export function typewriter(node: HTMLElement, params: TWParams) {
  let cancelled = false;
  let raf = 0;

  const { id, durationMs, onstep } = params;
  const skip = revealed.has(id) || id <= baselineId || durationMs <= 0;
  revealed.add(id);

  if (!skip) {
    // Snapshot each text node's text, then blank it out.
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    const chunks: { node: Text; text: string }[] = [];
    let total = 0;
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const t = n as Text;
      chunks.push({ node: t, text: t.data });
      total += t.data.length;
      t.data = "";
    }

    if (total > 0) {
      // Reveal the whole line over durationMs: speed scales with length so
      // every line finishes together, no matter how long.
      let start = 0;
      const frame = (ts: number) => {
        if (cancelled) return;
        if (!start) start = ts;
        const progress = Math.min(1, (ts - start) / durationMs);
        const target = Math.ceil(progress * total);
        const slices = sliceChunks(chunks.map((c) => c.text), target);
        for (let i = 0; i < chunks.length; i++) {
          if (chunks[i].node.data !== slices[i]) chunks[i].node.data = slices[i];
        }
        onstep?.();
        if (progress < 1) raf = requestAnimationFrame(frame);
      };
      raf = requestAnimationFrame(frame);
    }
  }

  return {
    destroy() {
      cancelled = true;
      if (raf) cancelAnimationFrame(raf);
    },
  };
}
