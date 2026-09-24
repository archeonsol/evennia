// Which scrollback lines still owe a typewriter reveal.
//
// The log is virtualized: a line is only mounted (and so only reachable by the
// typewriter action) while it is near the viewport. That breaks the old
// assumption that every line mounts once, in order, at append time - a line
// that arrives while the reader is scrolled up, or in a hidden panel, would
// otherwise type itself in whenever it is finally scrolled into view.
//
// Two rules keep that from happening:
//   * the backlog present at mount never animates (`markBacklog`), and
//   * a line appended while the reader is not following the bottom is marked
//     revealed at append time (the log calls `revealAll`), so the moment has
//     already passed by the time it mounts.
//
// The typewriter additionally refuses to animate a line older than its
// freshness window, which catches anything that slipped past both rules.

export class LogReveal {
  private revealed = new Set<number>();
  // Nothing is frozen until `markBacklog` says where the backlog ends.
  private baseline = Number.NEGATIVE_INFINITY;

  /** Freeze everything up to `maxId`: the backlog never types in. */
  markBacklog(maxId: number): void {
    this.baseline = maxId;
  }

  /** Whether this line's reveal is already spent (or never owed). */
  isRevealed(id: number): boolean {
    return id <= this.baseline || this.revealed.has(id);
  }

  /** Spend a line's reveal. */
  reveal(id: number): void {
    this.revealed.add(id);
  }

  /** Spend the reveal of every line in `ids` (a batch that landed off-screen). */
  revealAll(ids: Iterable<number>): void {
    for (const id of ids) this.revealed.add(id);
  }
}

export const logReveal = new LogReveal();
