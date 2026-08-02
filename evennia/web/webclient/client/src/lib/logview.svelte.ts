// Log lens + scrollback search state: which categories are on, and the current
// search query. GameLog reads this to filter + highlight. The category
// vocabulary itself lives in the rune-free `logcats.ts` and is re-exported here
// so existing importers keep working.

import { CATS, categorize, type LogCat } from "./logcats";

export { CATS, categorize };
export type { LogCat };

class LogView {
  filters = $state<Record<LogCat, boolean>>({
    speech: true,
    pose: true,
    combat: true,
    comms: true,
    look: true,
    system: true,
  });
  search = $state("");
  searchOpen = $state(false);
  timestamps = $state(false);

  toggle(cat: LogCat): void {
    this.filters[cat] = !this.filters[cat];
  }

  /** Solo a category (show only it) - click a chip while holding it, etc. */
  solo(cat: LogCat): void {
    for (const c of CATS) this.filters[c.id] = c.id === cat;
  }

  reset(): void {
    for (const c of CATS) this.filters[c.id] = true;
  }
}

export const logview = new LogView();
