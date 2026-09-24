// The shell's one voice for screen readers. `Announcer.svelte` renders these
// queues into visually hidden live regions; see announce.ts for why speech is
// not taken from the visible log.

import { BATCH_MS, composeBatch, speakable } from "./announce";

export interface Utterance {
  id: number;
  /** Rendered one element per line, so a reader pauses between them. */
  lines: string[];
}

//: Old utterances are pruned from the region. Removals are never spoken, and a
//: short tail keeps the region from growing for the whole session.
const KEEP = 4;
let nextId = 0;

class Announcer {
  polite = $state<Utterance[]>([]);
  assertive = $state<Utterance[]>([]);
  private pending: string[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;

  /** Queue a line of game output; a burst is spoken as one message. */
  say(text: string): void {
    if (!speakable(text)) return;
    this.pending.push(text);
    this.timer ??= setTimeout(() => this.flush(), BATCH_MS);
  }

  /** Speak now, interrupting: connection loss and other state the player must hear. */
  alert(text: string): void {
    const t = speakable(text);
    if (t) this.assertive = [...this.assertive, { id: nextId++, lines: [t] }].slice(-KEEP);
  }

  /** Speak one message now, politely: answers to a key the player pressed. */
  now(text: string): void {
    const t = speakable(text);
    if (t) this.polite = [...this.polite, { id: nextId++, lines: [t] }].slice(-KEEP);
  }

  private flush(): void {
    this.timer = null;
    const lines = composeBatch(this.pending);
    this.pending = [];
    if (lines.length) this.polite = [...this.polite, { id: nextId++, lines }].slice(-KEEP);
  }
}

export const announcer = new Announcer();
