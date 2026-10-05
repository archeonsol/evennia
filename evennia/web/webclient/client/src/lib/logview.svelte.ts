// Log lens + scrollback search state: which categories are on, and the current
// search query. GameLog reads this to filter + highlight. The category
// vocabulary itself lives in the rune-free `logcats.ts` and is re-exported here
// so existing importers keep working.

import { CATS, categorize, type ChipCat, type LogCat } from "./logcats";

export { CATS, categorize };
export type { ChipCat, LogCat };

class LogView {
  filters = $state<Record<LogCat, boolean>>({
    speech: true,
    pose: true,
    combat: true,
    comms: true,
    ooc: true,
    look: true,
    system: true,
    // No chip turns this off, and solo and reset leave it alone: a notice (a
    // staff announcement) reaches the player whatever else is filtered.
    notice: true,
  });
  search = $state("");
  searchOpen = $state(false);
  timestamps = $state(false);

  toggle(cat: ChipCat): void {
    this.filters[cat] = !this.filters[cat];
  }

  /** Solo a category (show only it) - click a chip while holding it, etc. */
  solo(cat: ChipCat): void {
    for (const c of CATS) this.filters[c.id] = c.id === cat;
  }

  reset(): void {
    for (const c of CATS) this.filters[c.id] = true;
  }
}

export const logview = new LogView();
