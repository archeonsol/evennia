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
  // Focus jumps. A screen reader user otherwise tabs or arrows through every
  // panel to get from the command line to what the game just said.
  { id: "focusInput", label: "Go to command line", combo: "Alt+I" },
  { id: "focusOutput", label: "Go to game output", combo: "Alt+O" },
  { id: "focusChannels", label: "Go to channels", combo: "Alt+C" },
  { id: "focusScene", label: "Go to scene", combo: "Alt+R" },
];

/**
 * The key a combo names. With Alt held, macOS Option turns a letter into a
 * symbol (Option+I is a dead key, Option+O is "ø"), so an Alt combo reads the
 * physical key instead, and "Alt+O" means the same key on every platform.
 */
function keyName(e: KeyboardEvent): string {
  if (e.altKey) {
    const m = /^(?:Key([A-Z])|Digit(\d))$/.exec(e.code ?? "");
    if (m) return m[1] ?? m[2];
  }
  return e.key.length === 1 ? e.key.toUpperCase() : e.key;
}

/** Build a combo string from an event, or null for plain typing keys. */
export function comboFromEvent(e: KeyboardEvent): string | null {
  const isFn = /^F\d{1,2}$/.test(e.key);
  const mod = e.ctrlKey || e.metaKey || e.altKey;
  if (!isFn && !mod) return null;
  let s = "";
  if (e.ctrlKey || e.metaKey) s += "Ctrl+";
  if (e.altKey) s += "Alt+";
  if (e.shiftKey && !isFn) s += "Shift+";
  s += isFn ? e.key : keyName(e);
  return s;
}

/**
 * Alt+1 to Alt+9: which recent line to read back (1 is the newest), or null.
 * Fixed rather than rebindable: it is nine keys, and the digit is the argument.
 */
export function reviewIndex(e: KeyboardEvent): number | null {
  if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return null;
  const m = /^Digit([1-9])$/.exec(e.code ?? "");
  return m ? Number(m[1]) : null;
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
