// Command-line history recall that never throws away what you typed.
//
// The line being typed is the bottom slot of the history. Up from it keeps it,
// and Down past the newest command brings it back, so reaching for an old
// command halfway through a pose costs nothing. A recalled command you edit
// keeps the edit while you walk, as readline does, until something is sent.
// Sending a recalled command puts the line you had been typing back.
//
// Rune-free so it can be unit tested. CommandInput owns the DOM half: which
// visual line the caret is on (textarea.ts) and when a key reaches this.

/** When Up and Down reach from the line being typed into the history. */
export type RecallKeys = "edge" | "empty";

export class HistoryWalk {
  /** The slot on the line: -1 is the line being typed, 0 the newest command sent. */
  index = -1;
  /** The history as it stood when the walk began, so a macro run mid-walk does not shift it. */
  private entries: readonly string[] = [];
  /** Lines as the player left them, by slot: the typed line at -1, edits elsewhere. */
  private kept = new Map<number, string>();

  /** True while the line shows a command from the history. */
  get walking(): boolean {
    return this.index >= 0;
  }

  /** What the current slot showed when the walk arrived at it. */
  shown(): string {
    return this.at(this.index);
  }

  /**
   * Step to an older or a newer command.
   *
   * Commands that read exactly like the line, or like the typed line, are
   * passed over, so a keypress never looks like it did nothing. With "Keep
   * command after sending" on, the typed line already holds the newest command.
   *
   * @param dir 1 for older (Up), -1 for newer (Down).
   * @param line The text on the line now. It is kept for the slot being left.
   * @param history Sent commands, newest first.
   * @returns The text to put on the line, or null when there is nowhere to go.
   */
  step(dir: 1 | -1, line: string, history: readonly string[]): string | null {
    if (!this.walking) this.begin(history);
    const typed = this.walking ? this.at(-1) : line;
    let i = this.index;
    do i += dir;
    while (i >= 0 && i < this.entries.length && (this.at(i) === line || this.at(i) === typed));
    if (i < -1 || i >= this.entries.length) return null;
    this.kept.set(this.index, line);
    this.index = i;
    return this.at(i);
  }

  /**
   * Go straight to a command found by the history search, keeping the line
   * being left exactly as a step does.
   *
   * @param entry The command the search matched.
   * @param line The text on the line now.
   * @param history Sent commands, newest first.
   * @returns The text to put on the line.
   */
  jump(entry: string, line: string, history: readonly string[]): string {
    if (!this.walking) this.begin(history);
    let i = this.entries.indexOf(entry);
    if (i < 0) {
      // Sent since this walk began. Walk the history as it is now; slots moved,
      // so only the typed line survives.
      const typed = this.walking ? (this.kept.get(-1) ?? "") : line;
      this.kept.clear();
      this.entries = history;
      this.index = -1;
      i = history.indexOf(entry);
      if (i < 0) return entry;
      this.kept.set(-1, typed);
    } else {
      this.kept.set(this.index, line);
    }
    this.index = i;
    return this.at(i);
  }

  /**
   * End the walk because a line was sent.
   *
   * @returns The line to put back: what was being typed when a recalled command
   *   went instead of it, otherwise "".
   */
  finish(): string {
    const typed = this.walking ? (this.kept.get(-1) ?? "") : "";
    this.index = -1;
    this.entries = [];
    this.kept.clear();
    return typed.trim() ? typed : "";
  }

  private begin(history: readonly string[]): void {
    // Edits are held by slot. They outlive a walk back down to the typed line,
    // but a changed history moves every slot, so they go with it.
    if (history !== this.entries) this.kept.clear();
    this.entries = history;
  }

  private at(i: number): string {
    return this.kept.get(i) ?? (i < 0 ? "" : (this.entries[i] ?? ""));
  }
}

export interface RecallQuery {
  /** The line shows a recalled command. */
  walking: boolean;
  /** ...exactly as recalled, not edited since. */
  untouched: boolean;
  /** The text on the line. */
  line: string;
  /** The caret is on the first visual line (for Up) or the last (for Down). */
  atEdge: boolean;
  /** The player's choice of when arrows recall. */
  keys: RecallKeys;
}

/**
 * Whether Up or Down walks the history rather than moving the caret.
 *
 * A recalled command left as it came keeps walking from anywhere in it, or a
 * long wrapped pose would take a keypress per line to get past. Anything
 * typed or edited walks only from its first line (Up) or last line (Down), so
 * the arrows still move through a long line. The "empty" choice keeps typed
 * text out of the history walk altogether.
 */
export function shouldRecall(q: RecallQuery): boolean {
  if (q.walking && q.untouched) return true;
  if (!q.walking && q.keys === "empty" && q.line.trim() !== "") return false;
  return q.atEdge;
}
