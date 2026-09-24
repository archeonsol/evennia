// Reactive game-session state: the scrollback log, the full record behind it
// (which keeps what a feed hides) and the current prompt.

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
  /**
   * Every line the session received, terminal or routed out of it, in arrival
   * order. Not `$state`: nothing renders from it, it is what the download
   * writes and what un-hiding a feed restores from, so taking a line out of
   * play never destroys it. Gagged lines are not kept: a gag is the player
   * muting the game, not filing it away.
   */
  archive: LogLine[] = [];
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
    let hidden = false;
    // Client triggers: gag drops the line; highlights colour keywords. Media
    // lines are exempt (they carry embed markup, not prose), and so is the
    // echo of the player's own command: a trigger or feed rule matching
    // "say whispers" must not fire on the typing, only on what the game says.
    if (type !== "media" && type !== "echo") {
      // Feeds are filed before gags: a gag hides a line from the terminal,
      // not from the feeds. It used to run first, so gagging chatter out of
      // the terminal also emptied the feed that was meant to collect it.
      const gagged = triggers.shouldGag(text);
      html = triggers.highlight(html);
      if (!gagged) triggers.runActions(text);
      // A move-route takes the line out of the terminal entirely; it lives in
      // its feed and in the record instead. Copy-routes fall through and the
      // line is appended below as usual. The id lets the feed prove it holds
      // the line, which is what pruneMoved trusts instead of any filing
      // memory of ours.
      const moved = routing.process(html, text, id);
      // A gag keeps the line out of the record too, so there is nothing for
      // a download to hand back.
      if (gagged) return;
      hidden = moved;
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
    this.archive.push(line);
    this.trim();
    if (hidden) return;
    this.lines.push(line);
    for (const fn of this.lineListeners) fn(line);
  }

  /**
   * Enforce the scrollback cap on the record and the terminal together.
   *
   * They are trimmed in the same step and by the same ids, so the terminal
   * always stays a subset of the record: a later pruneMoved rebuilds the
   * terminal from the archive, and a line the archive no longer holds must
   * not be left behind in it.
   */
  private trim(): void {
    if (this.archive.length <= MAX_LINES + TRIM_BLOCK) return;
    const cut = this.archive.length - MAX_LINES;
    const gone = new Set(this.archive.slice(0, cut).map((l) => l.id));
    this.archive.splice(0, cut);
    const kept = this.lines.filter((l) => !gone.has(l.id));
    if (kept.length !== this.lines.length) this.lines = kept;
  }

  /**
   * Reconcile the terminal with the current routes, in both directions.
   *
   * Routing gates lines as they arrive, so a rule switched to MOVE (or added
   * at all) left every earlier copy in the terminal, and the player read that
   * as routing doing nothing. Re-firing on each route edit makes the rule act
   * retroactively: a line a move-route now claims is filed into its feed and
   * taken out of the terminal, and a line no rule hides any more comes back
   * from the record, at its arrival position. A claimed line is never dropped
   * from the game: it leaves the terminal only once every claiming feed
   * verifiably holds it (by id), so a purged feed re-files it and a full feed
   * that trims it back out keeps the terminal copy. The array is only
   * reassigned when something actually moved, so an unrelated edit costs no
   * re-render.
   */
  pruneMoved(fresh: Set<string> = new Set()): void {
    const claimed = new Map<LogLine, string[]>();
    const backfill: { label: string; html: string; ts: number; id: number }[] = [];
    const history: { label: string; html: string; ts: number; id: number }[] = [];
    // What the terminal shows now: a claimed line whose feed does not hold it
    // (trimmed by its cap) stays where it already is rather than appearing or
    // vanishing on an unrelated edit.
    const shown = new Set(this.lines.map((l) => l.id));
    for (const l of this.archive) {
      if (l.type === "media") continue;
      // A new or changed copy rule takes a copy of what the record already
      // holds; the line stays where it is.
      if (fresh.size) {
        for (const label of routing.copies(l.text)) {
          if (fresh.has(label) && !routing.holds(label, l.id)) {
            history.push({ label, html: l.html, ts: l.ts, id: l.id });
          }
        }
      }
      // Media lines are exempt for the same reason append() exempts them.
      const labels = routing.claims(l.text);
      if (!labels.length) continue;
      claimed.set(l, labels);
      for (const label of labels) {
        if (!routing.holds(label, l.id)) backfill.push({ label, html: l.html, ts: l.ts, id: l.id });
      }
    }
    // History is not news: it fills a new feed without lighting its badge.
    routing.backfill(history, false);
    routing.backfill(backfill);
    // Rebuild the terminal from the record: a line every claiming feed holds
    // leaves it, a line no rule claims returns to it, and the rest keep the
    // side they were on.
    const kept = this.archive.filter((l) => {
      const labels = claimed.get(l);
      if (!labels) return true;
      if (labels.every((label) => routing.holds(label, l.id))) return false;
      return shown.has(l.id);
    });
    if (
      kept.length !== this.lines.length ||
      kept.some((l, i) => l.id !== this.lines[i].id)
    ) {
      this.lines = kept;
    }
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
    this.archive = [];
    connection.dropReplayBuffer();
  }

  /** Plain-text projection of the visible terminal buffer. */
  transcript(): string {
    return this.lines.map((l) => l.text).join("\n");
  }
}

export const session = new GameSession();
