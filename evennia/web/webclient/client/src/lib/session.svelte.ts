// Reactive game-session state: the scrollback log, the full record behind it
// (which keeps what a feed hides) and the current prompt.

import { connection } from "./evennia.svelte";
import { triggers } from "./triggers.svelte";
import { lore } from "./lore.svelte";
import { routing } from "./routing.svelte";
import { categorize, type LogCat } from "./logcats";
import { htmlToText } from "./text";

export interface LogLine {
  /**
   * Unique per page, and in arrival order: every list the log shows is sorted by
   * it, and the log finds a line by searching on it. A live line gets the next
   * number up from 0. A line brought back from the saved log gets one below
   * every id in use (-1, -2, ...), so it sorts before the lines of this visit.
   * The key routing uses to ask a feed if it holds this line.
   */
  id: number;
  html: string;
  text: string; // plain text for search, triggers and transcripts
  type: string;
  cat: LogCat; // lens category, resolved once at append (see below)
  ts: number; // epoch ms, for the timestamp gutter
  /** Where the saved log keeps this line, once it has been written there (lib/logstore.ts). */
  sk?: number;
}

/** A line read back from the saved log. */
export interface SavedRow {
  k: number;
  html: string;
  text: string;
  type: string;
  ts: number;
}

/** What the saved log hears from the session (lib/savedlog.svelte.ts). */
export interface SessionHooks {
  /** A line has been added to the record. */
  line(line: LogLine): void;
  /** The scrollback was emptied; `saved` is set when the saved copy is to go too. */
  cleared(opts: { saved: boolean }): void;
}

export const MAX_LINES = 5000;
//: Lines held in memory at most, however many older ones the reader has asked
//: for. The saved log itself is larger (lib/logstore.ts).
const MAX_HELD = 25_000;
//: Drop this many lines at once when the cap is hit. Splicing one line per
//: append re-indexes the whole reactive array on every single message; doing it
//: in blocks amortises that to roughly nothing.
const TRIM_BLOCK = 500;
let nextId = 0;

export class GameSession {
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
  private hooks: SessionHooks | null = null;
  /** The next id for a line that comes back from the saved log; counts down. */
  private lowId = -1;
  /**
   * Whether the reader has pulled older lines in from the saved log and is not
   * at the bottom. The record is then allowed to grow to MAX_HELD, or the next
   * arrivals would trim the very lines they scrolled up to read.
   */
  private held = false;
  /** Whether the reader is following the newest line (the log's `pinned`). */
  private following = true;

  /** Hear each line as it lands in the scrollback (after gags and routing). */
  onLine(fn: (line: LogLine) => void): void {
    this.lineListeners.push(fn);
  }

  /** Let the saved log hear what happens to the record. One listener, set once. */
  attach(hooks: SessionHooks | null): void {
    this.hooks = hooks;
  }

  /**
   * Append a pre-rendered (already HTML-safe) line to the scrollback.
   *
   * `ts` is when the line happened, if the server said; a line that arrives
   * late in a replay is shown at the time it was made, not the time it came.
   */
  append(html: string, type = "text", ts?: number): void {
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
      // The game's lore words are marked here, before routing, so a feed that copies
      // the line shows them too. `text` above is already plain and does not change.
      html = lore.wrap(html);
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
      ts: ts ?? Date.now(),
    };
    this.archive.push(line);
    this.hooks?.line(line);
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
    const cap = this.held && !this.following ? MAX_HELD : MAX_LINES;
    if (this.archive.length <= cap + TRIM_BLOCK) return;
    const cut = this.archive.length - cap;
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
  pruneMoved(fresh: Set<string> = new Set(), quiet = false): void {
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
    // History is not news: it fills a new feed without lighting its badge. Nor
    // is anything when `quiet` (lines brought back from the saved log).
    routing.backfill(history, false);
    routing.backfill(backfill, !quiet);
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

  /** A saved row as a log line, under the id it is given here. */
  private fromSaved(row: SavedRow, id: number): LogLine {
    return {
      id,
      html: row.html,
      text: row.text,
      type: row.type,
      cat: categorize(row.type),
      ts: row.ts,
      sk: row.k,
    };
  }

  /**
   * Put saved lines in front of everything held now: the log the player had
   * before this page loaded, or before the connection broke.
   *
   * They are not sent through gags, triggers, the announcer or the listeners
   * again: the player has read them. They get ids below every id in use so the
   * record stays in id order, which the log's searches rely on. Routing is
   * applied afterwards, so a feed that copies or takes a kind of line has its
   * old lines back as well.
   *
   * Args:
   *   rows: saved lines, oldest first.
   *   note: a line to put between them and what was already here, if any.
   *
   * Returns:
   *   The note line, so the caller can have it saved too; null if none was added.
   */
  restore(rows: readonly SavedRow[], note?: string): LogLine | null {
    if (!rows.length) return null;
    // The note is the newest of the new lines, so it takes the first id.
    const divider: LogLine | null = note
      ? {
          id: this.lowId--,
          html: note,
          text: htmlToText(note),
          type: "system",
          cat: categorize("system"),
          ts: Date.now(),
        }
      : null;
    const head = this.bring(rows);
    if (divider) head.push(divider);
    this.archive = head.concat(this.archive);
    this.lines = [...head, ...this.lines];
    this.reconcile();
    return divider;
  }

  /**
   * Put older saved lines in front of the record, because the reader scrolled
   * up to them. While they are not at the bottom the record keeps them: see
   * `follow`.
   */
  prepend(rows: readonly SavedRow[]): void {
    if (!rows.length) return;
    const head = this.bring(rows);
    this.archive = head.concat(this.archive);
    this.lines = [...head, ...this.lines];
    this.held = true;
    this.reconcile();
  }

  /** Saved rows as log lines with fresh low ids, oldest first. */
  private bring(rows: readonly SavedRow[]): LogLine[] {
    const made = new Array<LogLine>(rows.length);
    // Newest first, so the ids fall as the lines get older.
    for (let i = rows.length - 1; i >= 0; i--) made[i] = this.fromSaved(rows[i], this.lowId--);
    return made;
  }

  /** Re-apply the routes to everything held, copying into feeds that are empty of old lines. */
  private reconcile(): void {
    this.pruneMoved(new Set(routing.labels()), true);
  }

  /**
   * The saved-log key of the oldest saved line still held, or undefined when
   * none is. Older saved lines, if any, are the ones with a smaller key.
   */
  oldestSaved(): number | undefined {
    for (const line of this.archive) if (line.sk !== undefined) return line.sk;
    return undefined;
  }

  /**
   * Tell the record whether the reader is following the newest line. Back at
   * the bottom, the older lines they pulled in may go again, and the record
   * trims to its usual size at the next arrival.
   */
  follow(on: boolean): void {
    this.following = on;
    if (on) this.held = false;
  }

  /** The saved copy of the log is gone: no line held here is in it any more. */
  unsave(): void {
    for (const line of this.archive) line.sk = undefined;
  }

  /**
   * Wipe the scrollback, here and in the portal's replay window.
   *
   * The window is the half that makes it stick: a reloaded page asks to resume
   * from seq 0, so anything the portal still holds is replayed into the fresh
   * log, and a clear that skipped this step undid itself on the next refresh.
   *
   * The copy saved on this device stays unless `saved` is set. A sign-out, a
   * second account or a connection that could not be resumed clear the screen;
   * only a player who clears it, or quits, asks for the saved copy to go too.
   */
  clear(opts: { saved?: boolean } = {}): void {
    this.lines = [];
    this.archive = [];
    this.held = false;
    connection.dropReplayBuffer();
    this.hooks?.cleared({ saved: !!opts.saved });
  }

  /** Plain-text projection of the visible terminal buffer. */
  transcript(): string {
    return this.lines.map((l) => l.text).join("\n");
  }
}

export const session = new GameSession();
