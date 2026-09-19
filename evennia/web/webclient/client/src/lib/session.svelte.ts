// Reactive game-session state: the scrollback log and the current prompt.

import { connection } from "./evennia.svelte";
import { triggers } from "./triggers.svelte";
import { routing } from "./routing.svelte";
import { categorize, type LogCat } from "./logcats";
import { htmlToText } from "./text";

export interface LogLine {
  id: number;
  html: string;
  text: string; // plain text for search, triggers and transcripts
  type: string;
  cat: LogCat; // lens category, resolved once at append (see below)
  ts: number; // epoch ms, for the timestamp gutter
  /** Feed labels this line was filed to on arrival (see routing). */
  filed: string[];
}

const MAX_LINES = 5000;
//: Drop this many lines at once when the cap is hit. Splicing one line per
//: append re-indexes the whole reactive array on every single message; doing it
//: in blocks amortises that to roughly nothing.
const TRIM_BLOCK = 500;
let nextId = 0;

class GameSession {
  lines = $state<LogLine[]>([]);
  prompt = $state<string>("");

  /** Append a pre-rendered (already HTML-safe) line to the scrollback. */
  append(html: string, type = "text"): void {
    const text = htmlToText(html);
    let filed: string[] = [];
    // Client triggers: gag drops the line; highlights colour keywords. Media
    // lines are exempt (they carry embed markup, not prose).
    if (type !== "media") {
      if (triggers.shouldGag(text)) return;
      html = triggers.highlight(html);
      triggers.runActions(text);
      // A move-route takes the line out of the terminal entirely; it lives in
      // its feed instead. Copy-routes fall through and the line is appended
      // below as usual. `filed` records where the line already went, so a
      // later move-route cannot double-file it in pruneMoved.
      const routed = routing.process(html, text);
      if (routed.moved) return;
      filed = routed.labels;
    }
    this.lines.push({
      id: nextId++,
      html,
      text,
      type,
      // Resolved here, not in the log's filter: the filter re-runs over the
      // whole scrollback on every append, and categorize() is string work.
      cat: categorize(type),
      ts: Date.now(),
      filed,
    });
    if (this.lines.length > MAX_LINES + TRIM_BLOCK) {
      this.lines.splice(0, this.lines.length - MAX_LINES);
    }
  }

  /**
   * Move scrollback lines a move-route now claims into their feed.
   *
   * Routing gates lines as they arrive, so a rule switched to MOVE (or added
   * at all) left every earlier copy in the terminal, and the player read that
   * as routing doing nothing. Re-firing on each route edit makes the rule act
   * retroactively. A claimed line is never dropped from the game: whatever
   * feed it was not already filed to gets it, so a wrong-buffer rule still
   * keeps the text. The array is only reassigned when something actually
   * moved, so an unrelated edit costs no re-render.
   */
  pruneMoved(): void {
    const backfill: { label: string; html: string; ts: number }[] = [];
    const kept: LogLine[] = [];
    for (const l of this.lines) {
      // Media lines are exempt for the same reason append() exempts them.
      const claimed = l.type === "media" ? [] : routing.claims(l.text);
      if (!claimed.length) {
        kept.push(l);
        continue;
      }
      for (const label of claimed) {
        if (!l.filed.includes(label)) backfill.push({ label, html: l.html, ts: l.ts });
      }
    }
    routing.backfill(backfill);
    if (kept.length !== this.lines.length) this.lines = kept;
  }

  setPrompt(html: string): void {
    this.prompt = html;
  }

  /**
   * Wipe the scrollback, here and in the portal's replay window.
   *
   * The window is the half that makes it stick: a reloaded page asks to resume
   * from seq 0, so anything the portal still holds is replayed into the fresh
   * log, and a clear that skipped this step undid itself on the next refresh.
   */
  clear(): void {
    this.lines = [];
    connection.dropReplayBuffer();
  }

  /** Plain-text transcript of the current buffer, for download. */
  transcript(): string {
    return this.lines.map((l) => l.text).join("\n");
  }
}

export const session = new GameSession();
