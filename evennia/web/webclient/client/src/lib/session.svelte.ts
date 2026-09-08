// Reactive game-session state: the scrollback log and the current prompt.

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
    // Client triggers: gag drops the line; highlights colour keywords. Media
    // lines are exempt (they carry embed markup, not prose).
    if (type !== "media") {
      if (triggers.shouldGag(text)) return;
      html = triggers.highlight(html);
      triggers.runActions(text);
      routing.process(html, text);
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
    });
    if (this.lines.length > MAX_LINES + TRIM_BLOCK) {
      this.lines.splice(0, this.lines.length - MAX_LINES);
    }
  }

  setPrompt(html: string): void {
    this.prompt = html;
  }

  /** Wipe the scrollback. */
  clear(): void {
    this.lines = [];
  }

  /** Plain-text transcript of the current buffer, for download. */
  transcript(): string {
    return this.lines.map((l) => l.text).join("\n");
  }
}

export const session = new GameSession();
