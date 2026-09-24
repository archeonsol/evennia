// Focus jumps between the shell's regions (Alt+I/O/C/R by default).
//
// A region is marked in markup with `data-focus-region`; this opens the view
// that holds it first, so a jump to channels works with the panel closed, on
// another tab, or in the screen-reader layout.

import { tick } from "svelte";
import { dock } from "./dock.svelte";

export type Region = "input" | "output" | "channels" | "scene";

const VIEW_OF: Record<Exclude<Region, "input">, string> = {
  output: "log",
  channels: "chat",
  scene: "scene",
};

export async function focusRegion(region: Region): Promise<boolean> {
  if (region !== "input") dock.openView(VIEW_OF[region]);
  // Opening may mount the panel: let Svelte flush and dockview lay it out.
  await tick();
  // A frame, or a beat when frames are paused (a background tab still gets keys).
  await new Promise((r) => {
    requestAnimationFrame(() => r(null));
    setTimeout(() => r(null), 50);
  });
  const el = document.querySelector<HTMLElement>(`[data-focus-region="${region}"]`);
  if (!el) return false;
  // Focus set after an await loses the browser's "came from the keyboard"
  // flag, so the ring would not show; ask for it explicitly.
  el.focus({ focusVisible: true } as FocusOptions);
  return document.activeElement === el;
}
