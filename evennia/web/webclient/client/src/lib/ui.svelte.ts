// Server-driven UI components. The game pushes declarative specs (card / form /
// menu / table / gauge) over the `ui_component` OOB; this store holds them by id
// and the UIHost renders them generically. Interactions run game commands.

import { connection } from "./evennia.svelte";

export interface UIComponent {
  id: string;
  type: "card" | "form" | "menu" | "table" | "gauge";
  title?: string;
  body?: string;
  buttons?: { label: string; cmd: string }[];
  fields?: { name: string; label?: string; type?: string; placeholder?: string; options?: string[] }[];
  cmd?: string;
  submit_label?: string;
  options?: { label: string; cmd: string }[];
  columns?: string[];
  rows?: string[][];
  value?: number;
  max?: number;
  color?: string;
  dismissible?: boolean;
  dock?: boolean;
}

class UI {
  components = $state<Record<string, UIComponent>>({});

  set(comp: UIComponent): void {
    if (!comp || !comp.id) return;
    this.components = { ...this.components, [comp.id]: comp };
  }
  remove(id: string): void {
    if (!id || !(id in this.components)) return;
    const c = { ...this.components };
    delete c[id];
    this.components = c;
  }

  /** Fill {field} placeholders in a command template and run it. */
  runForm(comp: UIComponent, values: Record<string, string>): void {
    let cmd = comp.cmd ?? "";
    for (const [k, v] of Object.entries(values)) {
      cmd = cmd.replaceAll(`{${k}}`, v);
    }
    if (cmd.trim()) connection.sendCommand(cmd);
    if (comp.dismissible !== false) this.remove(comp.id);
  }
  run(cmd: string, comp?: UIComponent): void {
    if (cmd && cmd.trim()) connection.sendCommand(cmd);
    if (comp && comp.dismissible !== false) this.remove(comp.id);
  }
}

export const ui = new UI();
