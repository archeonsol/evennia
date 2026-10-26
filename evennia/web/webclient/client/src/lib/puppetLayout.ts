// How the Puppets panel lays its terminals out. Rune-free so it can be unit
// tested; the store that holds the choice is puppetLayout.svelte.ts.

/** How the shown terminals share the panel. */
export type PuppetArrange = "grid" | "columns" | "rows";

export const ARRANGES: readonly PuppetArrange[] = ["grid", "columns", "rows"];

/** A terminal narrower than this is not worth reading; extra columns give way. */
export const MIN_TILE_WIDTH = 300;

/**
 * Columns for `count` terminals.
 *
 * Grid is as square as it gets, columns puts them side by side, rows stacks
 * them. Whatever the choice, the panel never gets more columns than its width
 * can hold, so a narrow panel falls back to one column and scrolls.
 *
 * @param width The panel's width in px; 0 when it is not known yet.
 */
export function tileColumns(count: number, arrange: PuppetArrange, width = 0): number {
  if (count <= 1) return 1;
  let cols: number;
  if (arrange === "rows") cols = 1;
  else if (arrange === "columns") cols = count;
  else cols = Math.ceil(Math.sqrt(count));
  if (width > 0) cols = Math.min(cols, Math.max(1, Math.floor(width / MIN_TILE_WIDTH)));
  return Math.max(1, cols);
}

/**
 * The terminals to show: the player's choice kept in order and cut to puppets
 * that still exist, or every puppet when nothing has been chosen yet.
 */
export function shownIds(open: readonly number[] | null, known: readonly number[]): number[] {
  if (open === null) return [...known];
  const live = new Set(known);
  return open.filter((id) => live.has(id));
}

/** `open` with `id` added when absent and removed when present. */
export function toggled(open: readonly number[], id: number): number[] {
  return open.includes(id) ? open.filter((x) => x !== id) : [...open, id];
}

/** Read a saved choice; anything that is not the saved shape is no choice. */
export function parseSaved(raw: string | null): { open: number[] | null; arrange: PuppetArrange } {
  const fallback = { open: null, arrange: "grid" as PuppetArrange };
  if (!raw) return fallback;
  try {
    const data = JSON.parse(raw);
    const open = Array.isArray(data?.open)
      ? data.open.map(Number).filter((id: number) => Number.isInteger(id) && id > 0)
      : null;
    const arrange = ARRANGES.includes(data?.arrange) ? data.arrange : "grid";
    return { open, arrange };
  } catch {
    return fallback;
  }
}
