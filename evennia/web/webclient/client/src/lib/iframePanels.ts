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
