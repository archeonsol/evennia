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
interface Line {
  html: string;
  ts: number;
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

  init(): void {
    try {
      this.routes = JSON.parse(localStorage.getItem(KEY) || "[]");
    } catch {
      this.routes = [];
    }
    this.sync();
  }

  sync(): void {
    this.cRoutes = [];
    for (const r of this.routes) {
      const re = compile(r.pattern);
      const label = (r.label || "").trim();
      if (re && label) this.cRoutes.push({ re, label, move: r.move === true });
    }
    try {
      localStorage.setItem(KEY, JSON.stringify(this.routes));
    } catch {
      /* ignore */
    }
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

  labels(): string[] {
    return [...new Set(this.cRoutes.map((r) => r.label))];
  }

  /**
   * File a line into every buffer whose route matches.
   *
   * Returns true when at least one match was a move, meaning the caller must
   * not also append the line to the main log.
   */
  process(html: string, text: string): boolean {
    if (!this.cRoutes.length) return false;
    let moved = false;
    for (const r of this.cRoutes) {
      r.re.lastIndex = 0;
      if (!r.re.test(text)) continue;
      const buf = [...(this.buffers[r.label] ?? []), { html, ts: Date.now() }].slice(-MAX);
      this.buffers = { ...this.buffers, [r.label]: buf };
      this.unread = { ...this.unread, [r.label]: (this.unread[r.label] ?? 0) + 1 };
      if (r.move) moved = true;
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
