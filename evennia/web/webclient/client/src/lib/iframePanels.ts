// Which shell panel a server-opened web page goes into.
//
// Each open names a base id ("wiki"). The page loads into the panel for that
// base that the player has not pinned, so `@wiki a` then `@wiki b` reads in one
// panel. A pinned panel keeps its page, and the next open makes a fresh panel
// beside it. Layouts saved before panels carried `base` read their id as base.

export interface PanelLike {
  id: string;
  params?: Record<string, unknown>;
}

export interface IframeTarget {
  /** The panel to load the page into, or null to make a new one. */
  reuse: string | null;
  /** The id a new panel takes: the base itself while it is free. */
  freshId: string;
}

export function iframeTarget(panels: PanelLike[], base: string): IframeTarget {
  const reuse = panels.find((p) => (p.params?.base ?? p.id) === base && !p.params?.pinned);
  const taken = new Set(panels.map((p) => p.id));
  let freshId = base;
  for (let n = 2; taken.has(freshId); n++) freshId = `${base}:${n}`;
  return { reuse: reuse?.id ?? null, freshId };
}

/**
 * How big a server-opened page floats. `wide` fills the workspace less a margin,
 * for full page apps like the grid. `side` floats at the right like help, so the
 * terminal stays in view.
 */
export type PageSize = "wide" | "side";

export interface Float {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function pageSize(value: unknown): PageSize {
  return value === "side" ? "side" : "wide";
}

/** The float for a new page panel in a `w` by `h` workspace. */
export function pageFloat(size: PageSize, w: number, h: number): Float {
  if (size === "side") {
    const width = Math.max(Math.min(w * 0.42, 640), Math.min(w, 340));
    const height = Math.max(h - 40, Math.min(h, 320));
    return { x: Math.max(0, w - width - 20), y: 20, width, height };
  }
  const width = Math.max(w - 40, Math.min(w, 480));
  const height = Math.max(h - 40, Math.min(h, 360));
  return { x: Math.max(0, (w - width) / 2), y: Math.max(0, (h - height) / 2), width, height };
}
