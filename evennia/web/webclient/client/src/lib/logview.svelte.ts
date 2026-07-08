// Log lens + scrollback search state. The game log carries a msgType per line;
// here we categorise it, toggle categories on/off, and hold the search query.
// GameLog reads this to filter + highlight.

export type LogCat = "speech" | "pose" | "combat" | "comms" | "look" | "system";

export const CATS: { id: LogCat; label: string }[] = [
  { id: "speech", label: "Speech" },
  { id: "pose", label: "Pose" },
  { id: "combat", label: "Combat" },
  { id: "comms", label: "Comms" },
  { id: "look", label: "Look" },
  { id: "system", label: "System" },
];

export function categorize(type: string): LogCat {
  const t = (type || "").toLowerCase();
  if (t === "say" || t === "whisper" || t === "speech") return "speech";
  if (t === "pose" || t === "emote") return "pose";
  if (t.includes("combat") || t.includes("damage")) return "combat";
  if (["channel", "comms", "page", "tell", "network", "sm"].some((x) => t.includes(x)))
    return "comms";
  if (t === "look" || t === "room") return "look";
  return "system";
}

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
