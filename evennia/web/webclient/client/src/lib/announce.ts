// Screen-reader speech, kept rune-free so the batching can be unit tested.
//
// The visible log used to be the live region. That spoke nothing useful: the
// typewriter inserts each line empty and fills it a grapheme per frame, so a
// reader heard fragments or silence, a hidden Terminal tab spoke nothing at
// all, and a reconnect replay read out hundreds of lines. Speech now goes
// through an off-screen region fed with plain text, one batch per burst.

/** Lines arriving within this window are spoken as one message. */
export const BATCH_MS = 120;
/** A burst longer than this (a replay, a long help file) is cut to its tail. */
export const MAX_BATCH_LINES = 15;

/** Collapse whitespace and drop blank lines; speech gains nothing from either. */
export function speakable(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

/**
 * One burst of lines, cleaned, as the lines to speak. A burst over `max`
 * keeps its newest lines, since those are the ones still relevant, and says
 * how many it skipped so the player knows to read the log for the rest.
 */
export function composeBatch(lines: string[], max = MAX_BATCH_LINES): string[] {
  const clean = lines.map(speakable).filter(Boolean);
  if (clean.length <= max) return clean;
  const skipped = clean.length - max;
  const note = `${skipped} earlier line${skipped === 1 ? "" : "s"} skipped.`;
  return [note, ...clean.slice(-max)];
}
