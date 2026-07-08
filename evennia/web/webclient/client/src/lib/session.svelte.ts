// Reactive game-session state: the scrollback log and the current prompt.

import { triggers } from "./triggers.svelte";
import { routing } from "./routing.svelte";

export interface LogLine {
  id: number;
  html: string;
  text: string; // tag-stripped, for search
  type: string;
  ts: number; // epoch ms, for the timestamp gutter
}

const MAX_LINES = 5000;
let nextId = 0;

function stripTags(html: string): string {
  return html.replace(/<[^>]+>/g, "");
}

class GameSession {
  lines = $state<LogLine[]>([]);
  prompt = $state<string>("");

  /** Append a pre-rendered (already HTML-safe) line to the scrollback. */
  append(html: string, type = "text"): void {
    const text = stripTags(html);
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
      ts: Date.now(),
    });
    if (this.lines.length > MAX_LINES) {
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
