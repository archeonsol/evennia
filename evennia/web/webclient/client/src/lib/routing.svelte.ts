// Message routing (old "spawns"): file log lines matching a pattern into a named
// buffer, shown as tabs in the Feeds panel.
//
// A route either copies or moves. Copy leaves the line in the main log as well;
// move takes it out of the log entirely, which is the point of routing chatter
// away from the terminal you are actually playing in. A moved line is only ever
// in its feed, so each buffer carries an unread count - otherwise the whole
// point of moving something is that you never find out it arrived.

const KEY = "underspire.routing.v1";
const MAX = 300;

export interface Route {
  pattern: string;
  label: string;
  /** Take the line out of the main log rather than copying it. */
  move?: boolean;
}
export interface Line {
  /** Scrollback id of the line; lets callers ask a feed whether it holds it. */
  id: number;
  html: string;
  ts: number;
}

/** Union of a timestamp-ordered buffer and additions, keeping the newest MAX. */
function mergeLines(buf: Line[], adds: Line[]): Line[] {
  const out: Line[] = [];
  let i = 0;
  let j = 0;
  while (i < buf.length && j < adds.length) {
    out.push(buf[i].ts <= adds[j].ts ? buf[i++] : adds[j++]);
  }
  while (i < buf.length) out.push(buf[i++]);
  while (j < adds.length) out.push(adds[j++]);
  return out.length > MAX ? out.slice(-MAX) : out;
}

function compile(pattern: string): RegExp | null {
  const p = (pattern || "").trim();
  if (!p) return null;
  try {
    return new RegExp(p, "gi");
  } catch {
    return new RegExp(p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  }
}

export class Routing {
  routes = $state<Route[]>([]);
  buffers = $state<Record<string, Line[]>>({});
  /** Lines filed into each buffer since it was last read. */
  unread = $state<Record<string, number>>({});
  private cRoutes: { re: RegExp; label: string; move: boolean }[] = [];
  private onSync: (() => void) | null = null;

  init(): void {
    try {
      this.routes = JSON.parse(localStorage.getItem(KEY) || "[]");
    } catch {
      this.routes = [];
    }
    this.sync();
  }

  /** Run after routes change; the app prunes the scrollback of claimed lines. */
  setOnSync(fn: () => void): void {
    this.onSync = fn;
  }

  /**
   * Commit the current routes: compile, persist, purge and prune.
   *
   * Purging means a mid-typo rule state would destroy the buffer of its
   * orphaned label, so callers edit against a draft and sync only on commit.
   */
  sync(): void {
    this.cRoutes = this.compiled();
    // A deleted or renamed route's buffer would otherwise sit in memory for
    // the session and resurface verbatim if its label is ever reused.
    const live = new Set(this.cRoutes.map((r) => r.label));
    for (const key of Object.keys(this.buffers)) {
      if (!live.has(key)) {
        const rest = { ...this.buffers };
        delete rest[key];
        this.buffers = rest;
        const unread = { ...this.unread };
        delete unread[key];
        this.unread = unread;
      }
    }
    try {
      localStorage.setItem(KEY, JSON.stringify(this.routes));
    } catch {
      /* ignore */
    }
    this.onSync?.();
  }

  /** Routes with a usable pattern and a label, compiled in declaration order. */
  private compiled(): { re: RegExp; label: string; move: boolean }[] {
    const out: { re: RegExp; label: string; move: boolean }[] = [];
    for (const r of this.routes) {
      const re = compile(r.pattern);
      const label = (r.label || "").trim();
      if (re && label) out.push({ re, label, move: r.move === true });
    }
    return out;
  }

  add(): void {
    this.routes = [...this.routes, { pattern: "", label: "", move: false }];
    this.sync();
  }
  remove(i: number): void {
    this.routes = this.routes.filter((_, n) => n !== i);
    this.sync();
  }
  toggleMove(i: number): void {
    this.routes = this.routes.map((r, n) => (n === i ? { ...r, move: !r.move } : r));
    this.sync();
  }

  /**
   * Feed tab names, in declaration order.
   *
   * Derives from the `$state` routes, not the compiled cache: a `$derived`
   * reading only `cRoutes` (a plain field) computes once at mount and never
   * revalidates, so tabs for routes added later stayed invisible until reload.
   */
  labels(): string[] {
    return [...new Set(this.compiled().map((r) => r.label))];
  }

  /**
   * Labels of the move-routes that match this line, without filing anything.
   * A line carrying one of these labels belongs in that feed, not the log.
   */
  claims(text: string): string[] {
    const out: string[] = [];
    for (const r of this.cRoutes) {
      if (!r.move || out.includes(r.label)) continue;
      r.re.lastIndex = 0;
      if (r.re.test(text)) out.push(r.label);
    }
    return out;
  }

  /**
   * File already-delivered lines into their buffers under their arrival time,
   * in timestamp order. Used when a route starts claiming lines the log still
   * holds from before the route existed, or was purged after filing them.
   * A full buffer merges by timestamp and keeps the newest lines, so an older
   * backfill can be trimmed straight back out; `holds` reports the result.
   */
  backfill(entries: { label: string; html: string; ts: number; id: number }[]): void {
    if (!entries.length) return;
    const adds: Record<string, Line[]> = {};
    for (const e of entries) (adds[e.label] ??= []).push({ id: e.id, html: e.html, ts: e.ts });
    let buffers = this.buffers;
    let unread = this.unread;
    for (const [label, list] of Object.entries(adds)) {
      list.sort((a, b) => a.ts - b.ts);
      buffers = { ...buffers, [label]: mergeLines(buffers[label] ?? [], list) };
      unread = { ...unread, [label]: (unread[label] ?? 0) + list.length };
    }
    this.buffers = buffers;
    this.unread = unread;
  }

  /** True while this feed holds the line with that scrollback id. */
  holds(label: string, id: number): boolean {
    return this.buffers[label]?.some((l) => l.id === id) ?? false;
  }

  /**
   * File a line into every buffer whose route matches.
   *
   * Returns whether any match was a move; on a move the caller must not also
   * append the line to the main log. The id lets a later `pruneMoved` ask the
   * feed whether it still holds the line (cap eviction, clears).
   */
  process(html: string, text: string, id: number): boolean {
    let moved = false;
    const done = new Set<string>();
    for (const r of this.cRoutes) {
      r.re.lastIndex = 0;
      if (!r.re.test(text)) continue;
      if (r.move) moved = true;
      if (done.has(r.label)) continue;
      done.add(r.label);
      const buf = [...(this.buffers[r.label] ?? []), { id, html, ts: Date.now() }].slice(-MAX);
      this.buffers = { ...this.buffers, [r.label]: buf };
      this.unread = { ...this.unread, [r.label]: (this.unread[r.label] ?? 0) + 1 };
    }
    return moved;
  }

  /** Called when a feed's tab is on screen; drops its badge. */
  markRead(label: string): void {
    if (!this.unread[label]) return;
    this.unread = { ...this.unread, [label]: 0 };
  }

  clear(label: string): void {
    this.buffers = { ...this.buffers, [label]: [] };
    this.unread = { ...this.unread, [label]: 0 };
  }
}

export const routing = new Routing();
