// The Reading page's data: the player's reading settings, asked for and changed
// through the game's typed requests (`reading:reading_get`, `reading:reading_set`).
// The game owns them, on the account, so a change made here is the one `@reading`
// shows and every other client reads. A game that does not answer leaves the
// page saying so; nothing else in the shell depends on this store.

import { connection } from "./evennia.svelte";
import type { ReadingAnswer, ReadingColour, ReadingSetting, ReadingValues } from "./reading";

/** One typed request to the game's `reading` namespace. */
export type Ask = (action: string, data?: unknown) => Promise<ReadingAnswer>;

/** Where the page stands: not asked, waiting, showing the settings, or no answer. */
export type ReadingState = "idle" | "loading" | "ready" | "unavailable";

export class ReadingStore {
  values = $state<ReadingValues | null>(null);
  colours = $state<ReadingColour[]>([]);
  state = $state<ReadingState>("idle");
  /** Why the last change was refused, for the page to show. Empty when it was not. */
  problem = $state("");
  /** A change is on its way to the game, and its answer has not come back. */
  busy = $state(false);

  private readonly ask: Ask;
  private inflight = 0;
  private sent = 0;

  constructor(ask?: Ask) {
    this.ask = ask ?? ((action, data) => connection.request("reading", action, data));
  }

  private take(answer: ReadingAnswer): void {
    this.values = answer.settings;
    this.colours = answer.colours;
    this.state = "ready";
  }

  /** Ask the game for the settings. Safe to call whenever the page opens. */
  async load(): Promise<void> {
    if (this.state === "loading") return;
    if (!this.values) this.state = "loading";
    try {
      this.take(await this.ask("reading_get"));
    } catch {
      // Offline or a game without the page: what was shown stays shown.
      this.state = this.values ? "ready" : "unavailable";
    }
  }

  /**
   * Change one setting, or put them all back with `reset`.
   *
   * The settings shown are the game's answer, not a guess at it, so a refused
   * value never leaves the page claiming a choice that was not made.
   *
   * @returns whether the game took the change.
   */
  async set(setting: ReadingSetting | "reset", value = ""): Promise<boolean> {
    this.problem = "";
    const mine = (this.sent += 1);
    this.inflight += 1;
    this.busy = true;
    try {
      const answer = await this.ask("reading_set", { setting, value });
      // Only the newest change's answer is the state shown: an older one may
      // reach us last, and it describes a game that has since moved on.
      if (mine === this.sent) this.take(answer);
      return true;
    } catch (e: unknown) {
      this.problem = (e as { message?: string } | null)?.message || "That change did not go through.";
      // What the game kept of the changes before this one is not known here.
      void this.load();
      return false;
    } finally {
      this.inflight -= 1;
      this.busy = this.inflight > 0;
    }
  }
}

export const reading = new ReadingStore();
