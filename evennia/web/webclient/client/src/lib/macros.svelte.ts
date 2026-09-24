// User macros: labelled commands shown on the hotbar, each with an optional
// keybind. Persisted. A macro's key is a combo string like "F2" or "Ctrl+1".

import { commands } from "./commands.svelte";
import { comboFromEvent, isModifierOnly } from "./keybinds.svelte";

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
  { id: "score", label: "Stats", command: "@stats", key: "F5" },
];

/**
 * Build a combo string from a keyboard event, or null for a plain typing key or
 * a bare modifier. One builder for macros and keybinds: this used to be a copy
 * that turned a lone Ctrl press into "Ctrl+Control".
 */
export function comboOf(e: KeyboardEvent): string | null {
  return comboFromEvent(e);
}

export class Macros {
  list = $state<Macro[]>([]);

  init(): void {
    try {
      const raw = localStorage.getItem(KEY);
      this.list = raw ? JSON.parse(raw) : structuredClone(DEFAULTS);
      // Unbind keys saved by the old capture bug ("Ctrl+Control" and the like):
      // they matched every press of the modifier and fired the macro repeatedly.
      if (this.list.some((m) => isModifierOnly(m.key))) {
        this.list = this.list.map((m) => (isModifierOnly(m.key) ? { ...m, key: undefined } : m));
        this.save();
      }
    } catch {
      this.list = structuredClone(DEFAULTS);
    }
    this.migrate();
  }

  private migrate(): void {
    let changed = false;
    this.list = this.list.map((m) => {
      const stock =
        m.id === "score" &&
        m.label === "Score" &&
        (m.command === "score" || m.command === "@stats");
      if (!stock) return m;
      changed = true;
      return { ...m, label: "Stats", command: "@stats" };
    });
    if (changed) this.save();
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
    if (isModifierOnly(combo)) return false;
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
