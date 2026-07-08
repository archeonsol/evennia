// Export / import every client-side preference (settings, triggers, macros,
// layouts, custom theme) as one JSON file, so a config can be backed up or
// shared. Everything lives under the "underspire." localStorage namespace.

export function exportConfig(): void {
  const data: Record<string, string> = {};
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith("underspire.")) {
        const v = localStorage.getItem(k);
        if (v != null) data[k] = v;
      }
    }
  } catch {
    /* ignore */
  }
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
        if (k.startsWith("underspire.") && typeof v === "string") {
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
