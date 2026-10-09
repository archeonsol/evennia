// Shared command dispatch + history. The input bar, command palette and macros
// all run commands through here so recents stay consistent. Recents persist,
// per account, so the next person at the browser cannot page back through
// them.

import { connection } from "./evennia.svelte";
import { triggers } from "./triggers.svelte";

export const HISTORY_KEY = "underspire.history.v2";
/** History from before it was kept per account: every login's commands, `connect` passwords included. */
const LEGACY_KEY = "underspire.history.v1";
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
  { cmd: "@stats", label: "Stats / sheet" },
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
  /** The signed-in account. Nothing is recorded without one, so a `connect` line is never kept. */
  private account: number | null = null;

  init(): void {
    try {
      localStorage.removeItem(LEGACY_KEY);
    } catch {
      /* ignore */
    }
  }

  /** Show the history of the account the shell is signed in as; null shows none. */
  useAccount(account: number | null): void {
    if (account === this.account) return;
    this.account = account;
    this.recent = [];
    if (account == null) return;
    try {
      const raw = localStorage.getItem(`${HISTORY_KEY}:${account}`);
      if (raw) this.recent = JSON.parse(raw);
    } catch {
      /* ignore */
    }
  }

  private runListeners: ((line: string) => void)[] = [];

  /** Hear each command the player sends (the local echo uses this). */
  onRun(fn: (line: string) => void): void {
    this.runListeners.push(fn);
  }

  run(line: string): void {
    // Client aliases expand the first word before it hits the server.
    const expanded = triggers.expand(line);
    for (const fn of this.runListeners) fn(line);
    connection.sendCommand(expanded);
    const t = line.trim();
    if (t && this.account != null) {
      this.recent = [t, ...this.recent.filter((x) => x !== t)].slice(0, MAX);
      this.save();
    }
  }

  private save(): void {
    try {
      localStorage.setItem(`${HISTORY_KEY}:${this.account}`, JSON.stringify(this.recent));
    } catch {
      /* ignore */
    }
  }
}

export const commands = new Commands();
