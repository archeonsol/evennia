// Reactive game-session state: the scrollback log and the current prompt.

import { connection } from "./evennia.svelte";
import { triggers } from "./triggers.svelte";
import { routing } from "./routing.svelte";
import { categorize, type LogCat } from "./logcats";
import { htmlToText } from "./text";

export interface LogLine {
  /** Unique per session; the key routing uses to ask a feed if it holds this line. */
  id: number;
  html: string;
  text: string; // plain text for search, triggers and transcripts
  type: string;
  cat: LogCat; // lens category, resolved once at append (see below)
  ts: number; // epoch ms, for the timestamp gutter
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
  private lineListeners: ((line: LogLine) => void)[] = [];

  /** Hear each line as it lands in the scrollback (after gags and routing). */
  onLine(fn: (line: LogLine) => void): void {
    this.lineListeners.push(fn);
  }

  /** Append a pre-rendered (already HTML-safe) line to the scrollback. */
  append(html: string, type = "text"): void {
    const text = htmlToText(html);
    const id = nextId++;
    // Client triggers: gag drops the line; highlights colour keywords. Media
    // lines are exempt (they carry embed markup, not prose).
    if (type !== "media") {
      if (triggers.shouldGag(text)) return;
      html = triggers.highlight(html);
      triggers.runActions(text);
      // A move-route takes the line out of the terminal entirely; it lives in
      // its feed instead. Copy-routes fall through and the line is appended
      // below as usual. The id lets the feed prove it holds the line, which
      // is what pruneMoved trusts instead of any filing memory of ours.
      if (routing.process(html, text, id)) return;
    }
    const line: LogLine = {
      id,
      html,
      text,
      type,
      // Resolved here, not in the log's filter: the filter re-runs over the
      // whole scrollback on every append, and categorize() is string work.
      cat: categorize(type),
      ts: Date.now(),
    };
    this.lines.push(line);
    for (const fn of this.lineListeners) fn(line);
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
   * retroactively. A claimed line is never dropped from the game: a line
   * leaves the log only once every claiming feed verifiably holds it (by id),
   * so a purged feed re-files it and a full feed that trims it back out keeps
   * the log copy. The array is only reassigned when something actually
   * moved, so an unrelated edit costs no re-render.
   */
  pruneMoved(): void {
    const claimed = new Map<LogLine, string[]>();
    const backfill: { label: string; html: string; ts: number; id: number }[] = [];
    for (const l of this.lines) {
      // Media lines are exempt for the same reason append() exempts them.
      const labels = l.type === "media" ? [] : routing.claims(l.text);
      if (!labels.length) continue;
      claimed.set(l, labels);
      for (const label of labels) {
        if (!routing.holds(label, l.id)) backfill.push({ label, html: l.html, ts: l.ts, id: l.id });
      }
    }
    if (!claimed.size) return;
    routing.backfill(backfill);
    const kept = this.lines.filter((l) => {
      const labels = claimed.get(l);
      return !labels || !labels.every((label) => routing.holds(label, l.id));
    });
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
