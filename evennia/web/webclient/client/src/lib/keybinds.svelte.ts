// Rebindable global shortcuts. Core actions (palette, search, settings, clear)
// read their combo from here so users can remap them. Macro keys stay in the
// macros store; these are the shell's built-in actions.

const KEY = "underspire.keybinds.v1";

export interface Binding {
  id: string;
  label: string;
  combo: string;
}

const DEFAULTS: Binding[] = [
  { id: "palette", label: "Command palette", combo: "Ctrl+K" },
  { id: "search", label: "Search log", combo: "Ctrl+F" },
  { id: "settings", label: "Open settings", combo: "Ctrl+," },
  { id: "clear", label: "Clear buffer", combo: "Ctrl+L" },
];

/** Build a combo string from an event, or null for plain typing keys. */
export function comboFromEvent(e: KeyboardEvent): string | null {
  const isFn = /^F\d{1,2}$/.test(e.key);
  const mod = e.ctrlKey || e.metaKey || e.altKey;
  if (!isFn && !mod) return null;
  let s = "";
  if (e.ctrlKey || e.metaKey) s += "Ctrl+";
  if (e.altKey) s += "Alt+";
  if (e.shiftKey && !isFn) s += "Shift+";
  s += isFn ? e.key : e.key.length === 1 ? e.key.toUpperCase() : e.key;
  return s;
}

class Keybinds {
  list = $state<Binding[]>([]);

  init(): void {
    try {
      const raw = localStorage.getItem(KEY);
      const saved: Binding[] = raw ? JSON.parse(raw) : [];
      // Merge defaults with saved (saved combos win; new defaults appear).
      this.list = DEFAULTS.map((d) => ({ ...d, combo: saved.find((s) => s.id === d.id)?.combo ?? d.combo }));
    } catch {
      this.list = structuredClone(DEFAULTS);
    }
  }

  combo(id: string): string {
    return this.list.find((b) => b.id === id)?.combo ?? "";
  }

  match(e: KeyboardEvent, id: string): boolean {
    const c = comboFromEvent(e);
    return !!c && c === this.combo(id);
  }

  set(id: string, combo: string): void {
    this.list = this.list.map((b) => (b.id === id ? { ...b, combo } : b));
    this.save();
  }

  private save(): void {
    try {
      localStorage.setItem(KEY, JSON.stringify(this.list));
    } catch {
      /* ignore */
    }
  }
}

export const keybinds = new Keybinds();
