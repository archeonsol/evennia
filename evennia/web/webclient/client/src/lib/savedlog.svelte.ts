// The log a player keeps between visits, and when it comes back.
//
// The shell keeps the lines it has shown in the browser's IndexedDB
// (lib/logstore.ts). A reload, a closed tab, a phone that was put away, or a
// restart of the game then costs the player nothing: the log is there when the
// page is, and the lines that came in meanwhile are added to it.
//
// Four rules decide how:
//
//   1. One account's log is shown to that account only. Nothing is shown on
//      page load. The Portal says in its hello reply who the browser is signed
//      in as, and only then is the saved log of that account put back. A page
//      at the sign-in screen shows, and saves, nothing. This is the same rule
//      the command history follows (lib/commands.svelte.ts).
//   2. Lines and the place the connection had reached are saved together. A
//      reloaded page presents that place to the Portal, so it is sent exactly
//      what came after the last saved line: no line twice, none missing. Only
//      the tab that wrote the place may use it. Another tab of the same account
//      takes the session over and is sent nothing, since the saved lines are
//      the first tab's.
//   3. One tab saves at a time per account (a Web Lock). A second tab that is
//      open at the same time shows its own log as usual and starts saving when
//      the first closes.
//   4. Saving is a cache, never a duty. If the database cannot be opened, is
//      full, or stops working, the page carries on with the log in memory,
//      exactly as it did before there was a saved log.
//
// What is never saved: anything before sign-in, media embeds, and the echo of a
// command that carries a password. The staff Activity feed and the NPC
// terminals never reach the scrollback, so they are not here to be saved.

import { connection } from "./evennia.svelte";
import {
  LIMITS,
  LogStore,
  isFull,
  type Cursor,
  type NewLine,
  type SavedLine,
  type SavedMeta,
  type Written,
} from "./logstore";
import { GameSession, MAX_LINES, session, type LogLine } from "./session.svelte";
import { settings } from "./settings.svelte";

/** How long saving waits after a line lands, so a burst is one write. */
export const FLUSH_MS = 400;
/** The longest a page waits for the database before it connects without it. */
export const BOOT_WAIT_MS = 1500;
/** Lines read at a time when the reader scrolls up past what is held. */
export const OLDER_PAGE = 500;
/** Lines waiting for a write at which it is made at once, instead of after the pause. */
const BURST_LINES = 500;
/** Lines kept waiting for a write at most. Past it the oldest are not saved. */
const PENDING_MAX = 5000;
/** A line bigger than this (markup and text together) is shown but not saved. */
const LINE_MAX = 200_000;
/** Writes that may fail in a row before the page gives up on saving. */
const FAILURES_MAX = 3;

/** localStorage: the account whose log this browser saved last. */
export const POINTER_KEY = "underspire.log.last";
/** localStorage: this device has been told once that the log is kept on it. */
export const TOLD_KEY = "underspire.log.told";
/** sessionStorage: this tab's name. It outlives a reload and nothing else. */
const TAB_KEY = "underspire.client.tab";
const LOCK_PREFIX = "underspire.log.";

/**
 * The two lines the log says about itself. One place, so they can be read and
 * reworded together: plain, one fact each.
 */
export const NOTES = {
  /** The connection was lost and could not be resumed; this page kept its log. */
  away: "You were disconnected. Output from that time is missing.",
  /** The log above was brought back from this device. */
  returned: "Lines above are from your last visit.",
  /** Said once on a device, when it first starts keeping the log. */
  saving: "Your scrollback is now saved on this device. You can turn that off in Settings, under Backup.",
} as const;

/** A note as a log line's markup, in the style of the other connection notes. */
export function noteHtml(text: string): string {
  return `<span class="conn-note">${text}</span>`;
}

// A command echoed with a secret in it. The line is on screen as typed, but
// nothing that is written to disk should hold it.
const SECRET_ECHO = /^>\s*@?(password|passwd|pw|connect|create|login)\b/i;

/** Whether a line is worth keeping on this device. */
export function keepable(line: Pick<LogLine, "type" | "html" | "text">): boolean {
  if (line.type === "media") return false;
  if (line.html.length + line.text.length > LINE_MAX) return false;
  if (line.type === "echo" && SECRET_ECHO.test(line.text)) return false;
  return true;
}

/** The parts of the browser this module reaches for, so a test can supply its own. */
export interface SavedLogEnv {
  /** localStorage, or null where the browser refuses it. */
  local(): Storage | null;
  /** sessionStorage, or null where the browser refuses it. */
  tab(): Storage | null;
  /** The Web Locks manager, or null in a browser without one. */
  locks(): LockManager | null;
  now(): number;
}

function guard<T>(read: () => T): T | null {
  try {
    return read() ?? null;
  } catch {
    return null;
  }
}

const defaultEnv: SavedLogEnv = {
  local: () => guard(() => globalThis.localStorage),
  tab: () => guard(() => globalThis.sessionStorage),
  locks: () => guard(() => globalThis.navigator?.locks),
  now: () => Date.now(),
};

function makeId(): string {
  const c = globalThis.crypto;
  if (c?.randomUUID) return c.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** Wait for a promise, but not past `ms`: after that, `fallback` stands in for it. */
function within<T>(work: Promise<T>, ms: number, fallback: T): Promise<T> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(fallback), ms);
    work.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      () => {
        clearTimeout(timer);
        resolve(fallback);
      },
    );
  });
}

function toNew(line: LogLine): NewLine {
  return { html: line.html, text: line.text, type: line.type, ts: line.ts };
}

/** A saved run, ready to be put back in front of the log. */
interface Staged {
  account: number;
  meta: SavedMeta;
  lines: SavedLine[];
}

/** What the handshake told the shell, in the terms the saved log needs. */
export interface HelloInfo {
  /** The Portal resumed the session: the replay it sent is the gap and nothing else. */
  resumed: boolean;
  /** Who the browser is signed in as, or null at the sign-in screen. */
  account: number | null;
  /** This page kept its own log through the break, so there is nothing to bring back. */
  kept: boolean;
}

export class SavedLog {
  /** Saved lines exist that are older than the oldest one held in memory. */
  hasOlder = $state(false);
  /** Older lines are being read now. */
  loadingOlder = $state(false);

  private enabled = false;
  private broken = false;
  private failures = 0;
  /** The account the Portal or the game last said is signed in, whether or not saving is on. */
  private known: number | null = null;
  /** The account this log is bound to: the one its lines are saved under. */
  private account: number | null = null;
  private writer = false;
  private pending: LogLine[] = [];
  private staged: Staged | null = null;
  /** The key of the oldest line saved for this account, as of the last read or write. */
  private firstK = 0;
  private writtenSeq = -1;
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private queue: Promise<void> = Promise.resolve();
  private hold: (() => void) | null = null;
  private waiting: AbortController | null = null;
  private tabName: string | null = null;

  /** Saving is on, and the database is open. */
  get active(): boolean {
    return this.enabled;
  }

  /** This page is the one tab that saves the signed-in account's log right now. */
  get writing(): boolean {
    return this.writer && this.account !== null;
  }

  constructor(
    private store: LogStore = new LogStore(),
    private env: SavedLogEnv = defaultEnv,
    private sess: GameSession = session,
  ) {}

  // -- start-up ------------------------------------------------------------

  /** Listen to the session, and to the page being put away. Once, at load. */
  init(): void {
    this.sess.attach({
      line: (line) => this.enqueue(line),
      cleared: (opts) => this.cleared(opts),
    });
    connection.onLoggedOut(() => this.leave());
    if (typeof document !== "undefined" && typeof window !== "undefined") {
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "hidden") void this.flush(true);
      });
      window.addEventListener("pagehide", () => void this.flush(true));
    }
  }

  /**
   * Open the database and read the last account's saved log, before the socket
   * connects. The connection then asks the Portal for the gap in it and nothing
   * more. Never rejects, and never waits long: a page that cannot have its saved
   * log connects without it.
   */
  async boot(): Promise<void> {
    if (!settings.rememberLog) return;
    try {
      if (!(await within(this.store.open(), BOOT_WAIT_MS, false))) {
        // Too slow to wait for: the page connects without its saved log, and
        // starts saving if the database does come up.
        void this.store.open().then((ok) => {
          if (ok && settings.rememberLog) void this.setEnabled(true);
        });
        return;
      }
      this.enabled = true;
      void this.store.sweep().catch(() => {});
      const last = this.readPointer();
      if (last === null) return;
      const staged = await within(this.stage(last), BOOT_WAIT_MS, null);
      if (!staged) return;
      this.staged = staged;
      this.firstK = staged.meta.firstK;
      // Only the tab that wrote the saved place may use it. Any other tab holds
      // lines it was not sent the matching place for, and is sent none.
      connection.seedResume(
        staged.meta.tab === this.tab() ? { seq: staged.meta.seq } : { replay: false },
      );
    } catch {
      // The page carries on without a saved log.
    }
  }

  /** An account's saved run as it can be shown now, or null. */
  private async stage(account: number): Promise<Staged | null> {
    const meta = await this.store.meta(account);
    if (!meta || this.env.now() - meta.savedAt > LIMITS.idleMs) return null;
    const lines = await this.store.tail(account, MAX_LINES);
    return lines.length ? { account, meta, lines } : null;
  }

  // -- who is signed in ----------------------------------------------------

  /**
   * The handshake finished. This is the point at which the browser's account is
   * known, and so the point at which a saved log may be shown.
   */
  async hello({ resumed, account, kept }: HelloInfo): Promise<void> {
    if (account === null) {
      // Signed out: nothing is shown, and nothing from the sign-in screen is saved.
      this.staged = null;
      this.signedOut();
      return;
    }
    await this.safely(() => this.bind(account, resumed, kept));
  }

  /** Someone signed in after the handshake, at the connection screen. */
  async signedIn(account: number): Promise<void> {
    if (this.account === account) return;
    await this.safely(() => this.bind(account, false, false));
  }

  /** Nothing the saved log does may reach the page as an error. */
  private async safely(work: () => Promise<void>): Promise<void> {
    try {
      await work();
    } catch (e) {
      console.error("saved log:", e);
    }
  }

  /** The lines on screen so far belong to the sign-in screen: they are not kept. */
  discardUnbound(): void {
    if (this.account === null) this.pending = [];
  }

  private async bind(account: number, resumed: boolean, kept: boolean): Promise<void> {
    if (this.account !== null && this.account !== account) {
      this.unbind();
      this.sess.unsave();
    }
    this.known = account;
    if (!this.enabled) {
      this.staged = null;
      return;
    }
    this.writePointer(account);
    if (this.account === account) {
      this.staged = null;
      return;
    }
    this.account = account;
    let staged = this.staged;
    this.staged = null;
    if (!kept) {
      if (staged && staged.account !== account) staged = null;
      // A signed-in page that was not resumed has nothing replayed into it, so
      // the saved log cannot double a line. One that was resumed and has no
      // saved log read in time takes only what the Portal replays.
      if (!staged && !resumed) staged = await this.stage(account).catch(() => null);
      if (this.account !== account) return;
      if (staged) this.bringBack(staged, resumed);
    }
    this.claim(account);
    this.tell();
  }

  /** Say, once on this device, that the log is kept here and how to stop it. */
  private tell(): void {
    try {
      const local = this.env.local();
      if (!local || local.getItem(TOLD_KEY)) return;
      local.setItem(TOLD_KEY, "1");
    } catch {
      // Without storage it would be said every visit: better not at all.
      return;
    }
    this.sess.append(noteHtml(NOTES.saving), "system");
  }

  private bringBack(staged: Staged, resumed: boolean): void {
    this.firstK = staged.meta.firstK;
    // A page that was resumed is simply further on than it was: no break to mark.
    const note = this.sess.restore(staged.lines, resumed ? undefined : noteHtml(NOTES.returned));
    // Saved ahead of the lines that arrived while this was read.
    if (note) this.pending.unshift(note);
    this.updateOlder();
  }

  /**
   * The account is gone from the screen, whether it signed out or someone else
   * signed in: write what is waiting under it, and let go. The copy on this
   * device stays for the account to find when it is back.
   */
  signedOut(): void {
    this.known = null;
    this.unbind();
  }

  private unbind(): void {
    void this.flush(true);
    this.relinquish();
    this.account = null;
    this.pending = [];
    this.hasOlder = false;
    this.firstK = 0;
    this.writtenSeq = -1;
  }

  /** The shell was logged out, or lost its session to another window. */
  private leave(): void {
    this.signedOut();
  }

  // -- saving --------------------------------------------------------------

  private enqueue(line: LogLine): void {
    if (!this.enabled || this.broken || !keepable(line)) return;
    this.pending.push(line);
    if (this.pending.length > PENDING_MAX) this.pending.splice(0, this.pending.length - PENDING_MAX);
    // A flood is written as it comes, not after the pause, so it never grows
    // past what is worth holding in memory.
    if (this.pending.length >= BURST_LINES && this.account !== null && this.writer) {
      void this.flush(false);
      return;
    }
    this.schedule();
  }

  private schedule(): void {
    if (this.flushTimer || this.account === null || !this.writer || !this.pending.length) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      void this.flush(false);
    }, FLUSH_MS);
  }

  /**
   * Write what is waiting. `final` is for a page being put away: the place the
   * connection has reached is saved even when no line came with it.
   *
   * The lines and the place are taken in one step, here, before anything is
   * awaited, so the place saved is exactly how far the saved lines go.
   */
  flush(final = false): Promise<void> {
    if (this.flushTimer) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    const account = this.account;
    if (account === null || !this.writer || !this.enabled || this.broken || !connection.helloed) {
      return this.queue;
    }
    const batch = this.pending.splice(0);
    const seq = connection.cursor;
    if (!batch.length && (!final || seq === this.writtenSeq)) return this.queue;
    const cursor: Cursor = { tab: this.tab(), seq };
    this.queue = this.queue.then(() => this.commit(account, batch, cursor));
    return this.queue;
  }

  private async commit(account: number, batch: LogLine[], cursor: Cursor): Promise<void> {
    try {
      const rows = batch.map(toNew);
      let done: Written;
      try {
        done = await this.store.write(account, rows, cursor);
      } catch (e) {
        if (!isFull(e)) throw e;
        // The disk is full: give up the older half of this log and try again.
        await this.store.shrink(account);
        done = await this.store.write(account, rows, cursor);
      }
      batch.forEach((line, i) => {
        line.sk = done.keys[i];
      });
      this.failures = 0;
      this.writtenSeq = cursor.seq;
      if (this.account === account) {
        this.firstK = done.firstK;
        this.updateOlder();
      }
    } catch {
      this.failures += 1;
      if (this.failures >= FAILURES_MAX) {
        this.broken = true;
        this.pending = [];
        return;
      }
      // Try these again with the next write, ahead of the lines that came since.
      if (this.account === account) {
        this.pending = batch.concat(this.pending);
        this.schedule();
      }
    }
  }

  private updateOlder(): void {
    const oldest = this.sess.oldestSaved();
    this.hasOlder = oldest !== undefined && this.firstK > 0 && oldest > this.firstK;
  }

  /** Read older saved lines in front of the ones held, because the reader scrolled up. */
  async loadOlder(): Promise<void> {
    const account = this.account;
    const before = this.sess.oldestSaved();
    if (this.loadingOlder || !this.hasOlder || account === null || before === undefined) return;
    this.loadingOlder = true;
    try {
      const rows = await this.store.before(account, before, OLDER_PAGE);
      if (this.account !== account) return;
      this.sess.prepend(rows);
      this.updateOlder();
      // Nothing came back: whatever was thought to be older is gone.
      if (!rows.length) this.hasOlder = false;
    } catch {
      this.hasOlder = false;
    } finally {
      this.loadingOlder = false;
    }
  }

  // -- the lock ------------------------------------------------------------

  /**
   * Take the right to save this account's log. Where another tab already has
   * it, wait; when it is given up, start saving from then on. What this tab
   * showed in the meantime is saved only if the other tab did not get to it
   * (see dropSaved): the other tab saved the same output.
   */
  private claim(account: number): void {
    const locks = this.env.locks();
    if (!locks) {
      this.writer = true;
      this.schedule();
      return;
    }
    const name = LOCK_PREFIX + account;
    const held = () =>
      new Promise<void>((release) => {
        this.hold = release;
        this.writer = true;
        this.schedule();
      });
    locks
      .request(name, { ifAvailable: true }, (lock) => {
        if (lock) return held();
        const waiting = new AbortController();
        this.waiting = waiting;
        void locks
          .request(name, { signal: waiting.signal }, async () => {
            this.waiting = null;
            await this.dropSaved(account);
            return held();
          })
          .catch(() => {});
        return undefined;
      })
      .catch(() => {
        // No lock to be had: save anyway rather than not at all.
        this.writer = true;
        this.schedule();
      });
  }

  /**
   * The tab that held the lock saved what both tabs were shown. Of the lines
   * this one holds, keep only those newer than the last line saved: the rest
   * are a second copy of it, and the newer ones are lines the first tab never
   * got to write.
   */
  private async dropSaved(account: number): Promise<void> {
    try {
      const last = (await this.store.tail(account, 1))[0];
      if (last) this.pending = this.pending.filter((l) => l.ts > last.ts);
    } catch {
      // Keep them all: a line saved twice is better than a line not saved.
    }
  }

  /**
   * Stop being the tab that saves. The lock is let go only once the write on
   * its way has finished, so the next tab's lines cannot land in front of it.
   */
  private relinquish(): void {
    this.waiting?.abort();
    this.waiting = null;
    const hold = this.hold;
    this.hold = null;
    this.writer = false;
    if (hold) void this.queue.then(hold, hold);
  }

  // -- the player's choices ------------------------------------------------

  private cleared(opts: { saved: boolean }): void {
    this.pending = [];
    this.hasOlder = false;
    if (opts.saved) void this.erase();
  }

  /** Delete the saved copy of the log that is shown now. */
  erase(): Promise<void> {
    const account = this.account;
    this.pending = [];
    this.hasOlder = false;
    this.firstK = 0;
    // Nothing newer than this needs recording, or a last write on the way out
    // would put the place back into a log that was just deleted.
    this.writtenSeq = connection.cursor;
    this.sess.unsave();
    if (account === null || !this.enabled) return Promise.resolve();
    this.queue = this.queue.then(() => this.store.purge(account).catch(() => {}));
    return this.queue;
  }

  /** Delete every saved log on this device. Saving, if it is on, carries on from the next line. */
  async forgetAll(): Promise<void> {
    this.pending = [];
    this.hasOlder = false;
    this.firstK = 0;
    this.writtenSeq = -1;
    this.staged = null;
    this.sess.unsave();
    this.clearPointer();
    try {
      // A write already on its way finishes first, or it would put its lines
      // back after they were deleted.
      await this.queue;
      if (await this.store.open()) await this.store.purgeAll();
    } catch {
      // Nothing more to be done from here.
    }
    // Still saving, so the next page still knows whose log to look for.
    if (this.enabled && this.account !== null) this.writePointer(this.account);
  }

  /** The setting that turns saving on and off. */
  async setEnabled(on: boolean): Promise<void> {
    if (!on) {
      // Off first, so letting go of the account writes nothing more.
      this.enabled = false;
      this.unbind();
      await this.forgetAll();
      return;
    }
    if (this.enabled) return;
    try {
      if (!(await this.store.open())) return;
    } catch {
      return;
    }
    this.enabled = true;
    this.broken = false;
    this.failures = 0;
    if (this.known !== null && this.account === null) {
      // Someone is signed in: save what is on their screen now, then carry on.
      this.account = this.known;
      this.pending = this.sess.archive.filter((l) => l.sk === undefined && keepable(l));
      this.writePointer(this.account);
      this.claim(this.account);
    }
  }

  // -- small parts ---------------------------------------------------------

  /** This tab's name: the same through a reload, new for every other tab. */
  private tab(): string {
    if (this.tabName) return this.tabName;
    const store = this.env.tab();
    let name: string | null = null;
    try {
      name = store?.getItem(TAB_KEY) ?? null;
    } catch {
      name = null;
    }
    if (!name) {
      name = makeId();
      try {
        store?.setItem(TAB_KEY, name);
      } catch {
        // Storage refused: the name lasts as long as this page.
      }
    }
    this.tabName = name;
    return name;
  }

  private readPointer(): number | null {
    try {
      const n = Number(this.env.local()?.getItem(POINTER_KEY));
      return Number.isInteger(n) && n > 0 ? n : null;
    } catch {
      return null;
    }
  }

  private writePointer(account: number): void {
    try {
      this.env.local()?.setItem(POINTER_KEY, String(account));
    } catch {
      // Without the pointer the next page loads its log when the account is known.
    }
  }

  private clearPointer(): void {
    try {
      this.env.local()?.removeItem(POINTER_KEY);
    } catch {
      // ignore
    }
  }
}

export const savedLog = new SavedLog();
