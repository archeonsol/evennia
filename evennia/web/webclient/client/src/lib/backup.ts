// Export / import every client-side preference (settings, triggers, macros,
// layouts, custom theme) as one JSON file, so a config can be backed up or
// shared. Everything lives under the "underspire." localStorage namespace.

import { SEEN_KEY, QUEUE_SEEN_KEY } from "./chat.svelte";
import { HISTORY_KEY } from "./commands.svelte";
import { DRAFT_KEY } from "./compose.svelte";

/** Per-account records of what a player typed or read. A shared config file must not carry them. */
const ACCOUNT_RECORDS = [HISTORY_KEY, DRAFT_KEY, SEEN_KEY, QUEUE_SEEN_KEY].map((base) => `${base}:`);

function isConfigKey(k: string): boolean {
  return k.startsWith("underspire.") && !ACCOUNT_RECORDS.some((prefix) => k.startsWith(prefix));
}

/** The stored preferences an export writes, by key. */
export function configEntries(): Record<string, string> {
  const data: Record<string, string> = {};
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && isConfigKey(k)) {
        const v = localStorage.getItem(k);
        if (v != null) data[k] = v;
      }
    }
  } catch {
    /* ignore */
  }
  return data;
}

export function exportConfig(): void {
  const data = configEntries();
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "underspire-config.json";
  a.click();
  URL.revokeObjectURL(url);
}

export function importConfig(file: File): void {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const data = JSON.parse(String(reader.result));
      if (!data || typeof data !== "object") return;
      for (const [k, v] of Object.entries(data)) {
        if (isConfigKey(k) && typeof v === "string") {
          localStorage.setItem(k, v);
        }
      }
      location.reload(); // simplest way to re-init every store cleanly
    } catch {
      /* ignore */
    }
  };
  reader.readAsText(file);
}
