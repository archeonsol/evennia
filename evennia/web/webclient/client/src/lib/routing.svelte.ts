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
   * File already-delivered lines into their buffers, keeping their arrival
   * time. Used when a route starts claiming lines the log still holds from
   * before the route existed; `labels` are pre-filtered by the caller, which
   * knows what each line was filed to on arrival.
   */
  backfill(entries: { label: string; html: string; ts: number }[]): void {
    let buffers = this.buffers;
    let unread = this.unread;
    for (const e of entries) {
      buffers = { ...buffers, [e.label]: [...(buffers[e.label] ?? []), { html: e.html, ts: e.ts }].slice(-MAX) };
      unread = { ...unread, [e.label]: (unread[e.label] ?? 0) + 1 };
    }
    if (buffers !== this.buffers) this.buffers = buffers;
    if (unread !== this.unread) this.unread = unread;
  }

  /**
   * File a line into every buffer whose route matches.
   *
   * Returns the labels filed to and whether any match was a move; on a move
   * the caller must not also append the line to the main log.
   */
  process(html: string, text: string): { labels: string[]; moved: boolean } {
    const labels: string[] = [];
    let moved = false;
    for (const r of this.cRoutes) {
      r.re.lastIndex = 0;
      if (!r.re.test(text)) continue;
      if (labels.includes(r.label)) {
        if (r.move) moved = true;
        continue;
      }
      labels.push(r.label);
      const buf = [...(this.buffers[r.label] ?? []), { html, ts: Date.now() }].slice(-MAX);
      this.buffers = { ...this.buffers, [r.label]: buf };
      this.unread = { ...this.unread, [r.label]: (this.unread[r.label] ?? 0) + 1 };
      if (r.move) moved = true;
    }
    return { labels, moved };
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
