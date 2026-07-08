// User macros: labelled commands shown on the hotbar, each with an optional
// keybind. Persisted. A macro's key is a combo string like "F2" or "Ctrl+1".

import { commands } from "./commands.svelte";

const KEY = "underspire.macros.v1";
let seq = 0;

export interface Macro {
  id: string;
  label: string;
  command: string;
  key?: string;
  icon?: string;
}

const DEFAULTS: Macro[] = [
  { id: "look", label: "Look", command: "look", key: "F2" },
  { id: "who", label: "Who", command: "who", key: "F3" },
  { id: "inv", label: "Inv", command: "inventory", key: "F4" },
  { id: "score", label: "Score", command: "score", key: "F5" },
];

/** Build a combo string from a keyboard event, or null if it's a plain typing key. */
export function comboOf(e: KeyboardEvent): string | null {
  const k = e.key;
  const isFn = /^F\d{1,2}$/.test(k);
  const mod = e.ctrlKey || e.metaKey || e.altKey;
  if (!isFn && !mod) return null; // plain character - leave for typing
  let s = "";
  if (e.ctrlKey || e.metaKey) s += "Ctrl+";
  if (e.altKey) s += "Alt+";
  if (e.shiftKey && !isFn) s += "Shift+";
  s += isFn ? k : k.length === 1 ? k.toUpperCase() : k;
  return s;
}

class Macros {
  list = $state<Macro[]>([]);

  init(): void {
    try {
      const raw = localStorage.getItem(KEY);
      this.list = raw ? JSON.parse(raw) : structuredClone(DEFAULTS);
    } catch {
      this.list = structuredClone(DEFAULTS);
    }
  }

  add(): Macro {
    const m: Macro = { id: `u${++seq}${Date.now()}`, label: "New", command: "" };
    this.list = [...this.list, m];
    this.save();
    return m;
  }

  update(id: string, patch: Partial<Macro>): void {
    this.list = this.list.map((m) => (m.id === id ? { ...m, ...patch } : m));
    this.save();
  }

  remove(id: string): void {
    this.list = this.list.filter((m) => m.id !== id);
    this.save();
  }

  /** Move the macro with id `from` to the index of id `to` (drag reorder). */
  move(fromId: string, toId: string): void {
    if (fromId === toId) return;
    const arr = [...this.list];
    const fi = arr.findIndex((m) => m.id === fromId);
    const ti = arr.findIndex((m) => m.id === toId);
    if (fi < 0 || ti < 0) return;
    const [m] = arr.splice(fi, 1);
    arr.splice(ti, 0, m);
    this.list = arr;
    this.save();
  }

  /** Run the macro bound to a key combo, if any. Returns true if handled. */
  handleKey(combo: string): boolean {
    const m = this.list.find((x) => x.key === combo);
    if (m && m.command.trim()) {
      commands.run(m.command);
      return true;
    }
    return false;
  }

  save(): void {
    try {
      localStorage.setItem(KEY, JSON.stringify(this.list));
    } catch {
      /* ignore */
    }
  }
}

export const macros = new Macros();
