// Message routing (old "spawns"): file log lines matching a pattern into a named
// buffer, shown as tabs in the Feeds panel.
//
// A route either copies or moves. Copy leaves the line in the main log as well;
// move takes it out of the log entirely, which is the point of routing chatter
// away from the terminal you are actually playing in. A moved line is only ever
// in its feed, so each buffer carries an unread count - otherwise the whole
// point of moving something is that you never find out it arrived.

import { migrateLegacyPattern, parsePattern } from "./pattern";

const KEY = "underspire.routing.v1";
export const FEED_MAX = 1000;
const MAX = FEED_MAX;

export interface Route {
  pattern: string;
  label: string;
  /** Take the line out of the main log rather than copying it. */
  move?: boolean;
  /** Off keeps the rule without applying it. Absent means on. */
  enabled?: boolean;
  /**
   * Stable identity, assigned on first sync. It is how a renamed feed keeps
   * its lines: the rule is the same rule, only its label changed.
   */
  id?: string;
}

let routeSeq = 0;
export function newRouteId(): string {
  return `r${Date.now().toString(36)}${(routeSeq++).toString(36)}`;
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

// Patterns are "text or /regex/"; see pattern.ts. An unusable one (broken
// regex, or one matching every line) routes nothing, and the settings field
// says why.
function compile(pattern: string): RegExp | null {
  return parsePattern(pattern).re;
}

// One bad rule must never take the log down with it: a line that makes a
// pattern throw simply does not match that rule.
function safeTest(re: RegExp, text: string): boolean {
  try {
    re.lastIndex = 0;
    return re.test(text);
  } catch {
    return false;
  } finally {
    re.lastIndex = 0;
  }
}

export class Routing {
  routes = $state<Route[]>([]);
  buffers = $state<Record<string, Line[]>>({});
  /** Lines filed into each buffer since it was last read. */
  unread = $state<Record<string, number>>({});
  private cRoutes: { re: RegExp; label: string; move: boolean }[] = [];
  private onSync: ((fresh: Set<string>) => void) | null = null;
  /** Each route's definition at the last sync, by id, to see which changed. */
  private sigById = new Map<string, string>();
  /** Each route's label at the last sync, by route id, to detect renames. */
  private labelById = new Map<string, string>();

  init(): void {
    try {
      const saved: Route[] = JSON.parse(localStorage.getItem(KEY) || "[]");
      // Saved under the old rules, where the raw field was the regex.
      this.routes = Array.isArray(saved)
        ? saved.map((r) => ({ ...r, pattern: migrateLegacyPattern(r.pattern) }))
        : [];
    } catch {
      this.routes = [];
    }
    this.sync();
  }

  /** Run after routes change; the app prunes the scrollback of claimed lines. */
  setOnSync(fn: (fresh: Set<string>) => void): void {
    this.onSync = fn;
  }

  /**
   * Commit the current routes: compile, persist, purge and prune.
   *
   * Purging means a mid-typo rule state would destroy the buffer of its
   * orphaned label, so callers edit against a draft and sync only on commit.
   */
  sync(): void {
    // Give every rule an id, then carry a renamed feed's lines to its new
    // name. Without this, renaming "nous" to "Nous" purged the old buffer
    // below and the feed came back empty.
    if (this.routes.some((r) => !r.id)) {
      this.routes = this.routes.map((r) => (r.id ? r : { ...r, id: newRouteId() }));
    }
    const stillUsed = new Set(this.routes.map((r) => (r.label || "").trim()));
    for (const r of this.routes) {
      const was = this.labelById.get(r.id!);
      const now = (r.label || "").trim();
      if (was && now && was !== now && !stillUsed.has(was) && this.buffers[was]?.length) {
        this.buffers = { ...this.buffers, [now]: mergeLines(this.buffers[now] ?? [], this.buffers[was]) };
        this.unread = { ...this.unread, [now]: (this.unread[now] ?? 0) + (this.unread[was] ?? 0) };
      }
    }
    this.labelById = new Map(this.routes.map((r) => [r.id!, (r.label || "").trim()]));

    // Feeds whose rule is new or was just changed. Their copy rules are
    // applied to lines already in the terminal, so a new feed starts with
    // its history instead of empty. Unchanged rules are left alone, or a
    // feed the player cleared would refill on every unrelated edit.
    const fresh = new Set<string>();
    const sigs = new Map<string, string>();
    for (const r of this.routes) {
      const sig = `${r.pattern}\u0000${(r.label || "").trim()}\u0000${r.enabled !== false}\u0000${!!r.move}`;
      sigs.set(r.id!, sig);
      if (r.enabled !== false && this.sigById.get(r.id!) !== sig) fresh.add((r.label || "").trim());
    }
    this.sigById = sigs;

    this.cRoutes = this.compiled();
    // A deleted or renamed route's buffer would otherwise sit in memory for
    // the session and resurface verbatim if its label is ever reused.
    // Any rule naming the feed keeps it, on or off: switching a rule off
    // must not throw away what it filed.
    const live = new Set(this.allLabels());
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
    this.onSync?.(fresh);
  }

  /** Routes with a usable pattern and a label, compiled in declaration order. */
  private compiled(): { re: RegExp; label: string; move: boolean }[] {
    const out: { re: RegExp; label: string; move: boolean }[] = [];
    for (const r of this.routes) {
      if (r.enabled === false) continue;
      const re = compile(r.pattern);
      const label = (r.label || "").trim();
      if (re && label) out.push({ re, label, move: r.move === true });
    }
    return out;
  }

  /** Every feed name a rule names, enabled or not, in declaration order. */
  allLabels(): string[] {
    return [...new Set(this.routes.map((r) => (r.label || "").trim()).filter(Boolean))];
  }

  /** Move rule i up (-1) or down (+1). Order decides which feed a line lists first. */
  reorder(i: number, dir: -1 | 1): void {
    const j = i + dir;
    if (i < 0 || j < 0 || i >= this.routes.length || j >= this.routes.length) return;
    const next = [...this.routes];
    [next[i], next[j]] = [next[j], next[i]];
    this.routes = next;
    this.sync();
  }

  setEnabled(i: number, on: boolean): void {
    this.routes = this.routes.map((r, n) => (n === i ? { ...r, enabled: on } : r));
    this.sync();
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
    // Feeds that still hold lines stay listed after their rule is switched
    // off, so turning a rule off never hides what it already filed.
    const live = this.compiled().map((r) => r.label);
    const kept = this.allLabels().filter((l) => this.buffers[l]?.length);
    return [...new Set([...live, ...kept])];
  }

  /**
   * Labels of the move-routes that match this line, without filing anything.
   * A line carrying one of these labels belongs in that feed, not the log.
   */
  claims(text: string): string[] {
    const out: string[] = [];
    for (const r of this.cRoutes) {
      if (!r.move || out.includes(r.label)) continue;
      if (safeTest(r.re, text)) out.push(r.label);
    }
    return out;
  }

  /** Labels of the copy-routes that match this line, without filing anything. */
  copies(text: string): string[] {
    const out: string[] = [];
    for (const r of this.cRoutes) {
      if (r.move || out.includes(r.label)) continue;
      if (safeTest(r.re, text)) out.push(r.label);
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
  backfill(entries: { label: string; html: string; ts: number; id: number }[], countUnread = true): void {
    if (!entries.length) return;
    const adds: Record<string, Line[]> = {};
    // One line once per feed: a feed with both a copy and a move rule would
    // otherwise be handed the same line twice.
    const seen = new Set<string>();
    for (const e of entries) {
      const k = `${e.label}\u0000${e.id}`;
      if (seen.has(k) || this.holds(e.label, e.id)) continue;
      seen.add(k);
      (adds[e.label] ??= []).push({ id: e.id, html: e.html, ts: e.ts });
    }
    let buffers = this.buffers;
    let unread = this.unread;
    for (const [label, list] of Object.entries(adds)) {
      list.sort((a, b) => a.ts - b.ts);
      buffers = { ...buffers, [label]: mergeLines(buffers[label] ?? [], list) };
      if (countUnread) unread = { ...unread, [label]: (unread[label] ?? 0) + list.length };
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
      if (!safeTest(r.re, text)) continue;
      if (r.move) moved = true;
      if (done.has(r.label)) continue;
      done.add(r.label);
      // A replayed line (reconnect resume) is filed once.
      if (this.holds(r.label, id)) continue;
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
