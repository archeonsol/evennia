// The review cursor: reading the scrollback back a line at a time, by ear.
//
// Alt+1 to Alt+9 read one of the last nine lines again. The cursor lets a
// player keep going from there, through the whole scrollback rather than the
// rows the log happens to have mounted: Alt+Up reads the line before, Alt+Down
// the line after, Alt+Shift+Up and Alt+Shift+Down jump to the oldest and the
// newest line (the keys are rebindable; lib/keybinds.svelte.ts).
//
// The cursor is a line id, never a row number, so it stays on its line while
// new output arrives underneath and while the scrollback cap trims the top;
// a line that has left the scrollback is simply stepped past. Sending a
// command puts it back below the newest line, so the first Alt+Up after
// acting reads what the game just said.
//
// Rune-free, so it is unit tested directly; App.svelte wires the keys and
// speaks the result through the announcer.

import { indexOfId } from "./logvirtual";

export interface ReviewLine {
  id: number;
  text: string;
}

/** Whether a line is worth reading out (the log's filters, no media, no blanks). */
export type Readable<L extends ReviewLine> = (line: L) => boolean;

/** The cursor's moves, one per review key. */
export type ReviewMove = "older" | "newer" | "oldest" | "newest";

export const REVIEW_EMPTY = "Nothing to read.";
export const REVIEW_TOP = "Top of the scrollback.";
export const REVIEW_BOTTOM = "Bottom of the scrollback.";

/** Where the first line with an id at or above `id` sits: the insertion point. */
function lowerBound(lines: readonly ReviewLine[], id: number): number {
  let lo = 0;
  let hi = lines.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].id < id) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export class ReviewCursor {
  /** The line under review, by id; null is below the newest line. */
  at: number | null = null;

  /** Back below the newest line: the next step up reads the newest. */
  reset(): void {
    this.at = null;
  }

  /** Read the nth newest readable line (1 is the newest) and move there. */
  recent<L extends ReviewLine>(lines: readonly L[], readable: Readable<L>, n: number): string {
    let seen = 0;
    for (let i = lines.length - 1; i >= 0; i--) {
      if (!readable(lines[i])) continue;
      if (++seen === n) return this.land(lines[i]);
    }
    return `No line ${n}.`;
  }

  /** Read the readable line before the cursor. */
  older<L extends ReviewLine>(lines: readonly L[], readable: Readable<L>): string {
    const from = this.at === null ? lines.length : lowerBound(lines, this.at);
    for (let i = from - 1; i >= 0; i--) {
      if (readable(lines[i])) return this.land(lines[i]);
    }
    return this.hasReadable(lines, readable) ? REVIEW_TOP : REVIEW_EMPTY;
  }

  /** Read the readable line after the cursor. */
  newer<L extends ReviewLine>(lines: readonly L[], readable: Readable<L>): string {
    if (this.at !== null) {
      const here = lowerBound(lines, this.at);
      // Past the line under the cursor if it is still there; a line that has
      // gone (trimmed, filtered) leaves the insertion point on its successor.
      const from = indexOfId(lines, this.at) === here ? here + 1 : here;
      for (let i = from; i < lines.length; i++) {
        if (readable(lines[i])) return this.land(lines[i]);
      }
    }
    return this.hasReadable(lines, readable) ? REVIEW_BOTTOM : REVIEW_EMPTY;
  }

  /** Read the oldest readable line in the scrollback. */
  oldest<L extends ReviewLine>(lines: readonly L[], readable: Readable<L>): string {
    const line = lines.find(readable);
    return line ? this.land(line) : REVIEW_EMPTY;
  }

  /** Read the newest readable line. */
  newest<L extends ReviewLine>(lines: readonly L[], readable: Readable<L>): string {
    const line = lines.findLast(readable);
    return line ? this.land(line) : REVIEW_EMPTY;
  }

  private land(line: ReviewLine): string {
    this.at = line.id;
    return line.text;
  }

  private hasReadable<L extends ReviewLine>(lines: readonly L[], readable: Readable<L>): boolean {
    return lines.some(readable);
  }
}

/** The one cursor the shell's review keys move. */
export const reviewCursor = new ReviewCursor();
