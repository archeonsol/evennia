// Shared command dispatch + history. The input bar, command palette and macros
// all run commands through here so recents stay consistent. Recents persist.

import { connection } from "./evennia.svelte";
import { triggers } from "./triggers.svelte";

const KEY = "underspire.history.v1";
const MAX = 120;

export interface CmdItem {
  cmd: string;
  label: string;
}

// A curated starter set for the palette (free entry still runs anything).
export const CURATED: CmdItem[] = [
  { cmd: "look", label: "Look" },
  { cmd: "who", label: "Who is online" },
  { cmd: "inventory", label: "Inventory" },
  { cmd: "score", label: "Score / sheet" },
  { cmd: "say ", label: "Say…" },
  { cmd: "pose ", label: "Pose…" },
  { cmd: "emote ", label: "Emote…" },
  { cmd: "whisper ", label: "Whisper…" },
  { cmd: "get ", label: "Get…" },
  { cmd: "drop ", label: "Drop…" },
  { cmd: "give ", label: "Give…" },
  { cmd: "wear ", label: "Wear…" },
  { cmd: "remove ", label: "Remove…" },
  { cmd: "help", label: "Help" },
];

class Commands {
  recent = $state<string[]>([]);

  init(): void {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) this.recent = JSON.parse(raw);
    } catch {
      /* ignore */
    }
  }

  run(line: string): void {
    // Client aliases expand the first word before it hits the server.
    const expanded = triggers.expand(line);
    connection.sendCommand(expanded);
    const t = line.trim();
    if (t) {
      this.recent = [t, ...this.recent.filter((x) => x !== t)].slice(0, MAX);
      this.save();
    }
  }

  private save(): void {
    try {
      localStorage.setItem(KEY, JSON.stringify(this.recent));
    } catch {
      /* ignore */
    }
  }
}

export const commands = new Commands();
