/**
 * The saved log between visits: what it keeps, when it shows it again, and
 * that it shows it to nobody but the account it belongs to.
 *
 * The database is a real IndexedDB implementation in memory; the session, the
 * connection and the browser's storages are the real stores or small fakes, so
 * each test runs the whole path from a line landing to the same line coming
 * back on a page that was never told about it.
 */
import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// append() projects HTML to text through the DOM, which this environment lacks.
vi.mock("./text", () => ({ htmlToText: (html: string) => html.replace(/<[^>]*>/g, "") }));

import { connection } from "./evennia.svelte";
import { LogStore, type Limits } from "./logstore";
import { routing } from "./routing.svelte";
import { FLUSH_MS, NOTES, OLDER_PAGE, POINTER_KEY, SavedLog, TOLD_KEY, keepable, noteHtml, type SavedLogEnv } from "./savedlog.svelte";
import { GameSession, MAX_LINES, session } from "./session.svelte";
import { settings } from "./settings.svelte";

class FakeStorage {
  map = new Map<string, string>();
  getItem(k: string): string | null {
    return this.map.get(k) ?? null;
  }
  setItem(k: string, v: string): void {
    this.map.set(k, v);
  }
  removeItem(k: string): void {
    this.map.delete(k);
  }
}

/** Web Locks, as far as the saved log uses them: exclusive, queued, and `ifAvailable`. */
class FakeLocks {
  private held = new Set<string>();
  private queue = new Map<string, (() => void)[]>();

  request(name: string, a: unknown, b?: unknown): Promise<unknown> {
    const opts = (typeof a === "function" ? {} : a) as { ifAvailable?: boolean; signal?: AbortSignal };
    const cb = (typeof a === "function" ? a : b) as (lock: unknown) => unknown;
    return new Promise((resolve, reject) => {
      const grant = async () => {
        this.held.add(name);
        try {
          resolve(await cb({ name }));
        } catch (e) {
          reject(e);
        } finally {
          this.held.delete(name);
          this.queue.get(name)?.shift()?.();
        }
      };
      if (!this.held.has(name)) {
        void grant();
      } else if (opts.ifAvailable) {
        resolve(cb(null));
      } else {
        const waiting = this.queue.get(name) ?? [];
        this.queue.set(name, waiting);
        waiting.push(grant);
        opts.signal?.addEventListener("abort", () => {
          const at = waiting.indexOf(grant);
          if (at >= 0) waiting.splice(at, 1);
          reject(new DOMException("aborted", "AbortError"));
        });
      }
    });
  }
}

let factory: IDBFactory;
let clock: number;
let local: FakeStorage;
let tabStorage: FakeStorage;
let locks: FakeLocks | null;
let place: { seq: number };
const made: SavedLog[] = [];

function env(): SavedLogEnv {
  return {
    local: () => local as unknown as Storage,
    tab: () => tabStorage as unknown as Storage,
    locks: () => locks as unknown as LockManager | null,
    now: () => clock,
  };
}

function store(limits: Partial<Limits> = {}): LogStore {
  return new LogStore(factory, () => clock, limits);
}

/** A page: a saved log over the shared database, listening to the session. */
function page(s: LogStore = store(), e: SavedLogEnv = env(), sess: GameSession = session): SavedLog {
  const log = new SavedLog(s, e, sess);
  log.init();
  made.push(log);
  return log;
}

/** A second browser tab: its own session and its own sessionStorage, the same device. */
function tab(): { log: SavedLog; sess: GameSession } {
  const sess = new GameSession();
  const mine = new FakeStorage();
  const e: SavedLogEnv = { ...env(), tab: () => mine as unknown as Storage };
  return { log: page(store(), e, sess), sess };
}

/** What a new page finds: an empty session and no memory of the last one. */
function reload(): void {
  session.lines = [];
  session.archive = [];
  session.attach(null);
}

const texts = () => session.lines.map((l) => l.text);

function say(...lines: string[]): void {
  for (const line of lines) session.append(line, "text");
}

async function signedIn(log: SavedLog, account = 7, opts: { resumed?: boolean; kept?: boolean } = {}): Promise<void> {
  await log.hello({ resumed: opts.resumed ?? false, account, kept: opts.kept ?? false });
}

/** The saved lines of an account, read by a store of its own. */
async function onDisk(account = 7): Promise<string[]> {
  return (await store().tail(account, 100_000)).map((l) => l.text);
}

beforeEach(() => {
  factory = new IDBFactory();
  clock = 1_700_000_000_000;
  local = new FakeStorage();
  // The one-time notice has its own tests; the rest do not want it in the log.
  local.setItem(TOLD_KEY, "1");
  tabStorage = new FakeStorage();
  locks = new FakeLocks();
  place = { seq: 0 };
  settings.rememberLog = true;
  reload();
  routing.routes = [];
  routing.sync();
  vi.spyOn(connection, "helloed", "get").mockReturnValue(true);
  vi.spyOn(connection, "cursor", "get").mockImplementation(() => place.seq);
  vi.spyOn(connection, "seedResume").mockImplementation(() => {});
  vi.spyOn(connection, "dropReplayBuffer").mockImplementation(() => {});
});

afterEach(async () => {
  for (const log of made.splice(0)) {
    log.signedOut();
    await log.flush(true);
  }
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("start-up", () => {
  it("does nothing when saving is off", async () => {
    settings.rememberLog = false;
    const s = store();
    const open = vi.spyOn(s, "open");
    const log = page(s);
    await log.boot();
    expect(open).not.toHaveBeenCalled();
    say("not kept");
    await signedIn(log);
    await log.flush(true);
    expect(await onDisk()).toEqual([]);
  });

  it("carries on when the browser has no database", async () => {
    const log = page(new LogStore(null));
    await expect(log.boot()).resolves.toBeUndefined();
    say("shown, not kept");
    await signedIn(log);
    await log.flush(true);
    expect(texts()).toEqual(["shown, not kept"]);
    expect(connection.seedResume).not.toHaveBeenCalled();
  });

  it("does not hold the connection up for a database that does not answer", async () => {
    vi.useFakeTimers();
    const s = store();
    vi.spyOn(s, "open").mockReturnValue(new Promise(() => {}));
    const log = page(s);
    let done = false;
    void log.boot().then(() => (done = true));
    await vi.advanceTimersByTimeAsync(1_499);
    expect(done).toBe(false);
    await vi.advanceTimersByTimeAsync(2);
    expect(done).toBe(true);
  });

  it("starts saving if a database that was too slow to wait for does come up", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const s = store();
    vi.spyOn(s, "open").mockReturnValueOnce(new Promise(() => {}));
    const log = page(s);
    const booted = log.boot();
    await vi.advanceTimersByTimeAsync(2_000);
    await booted;
    // The database opens in real time, which the faked timers do not move.
    await vi.waitFor(() => expect(log.active).toBe(true), { timeout: 5_000, interval: 5 });
    await signedIn(log);
    say("kept, late");
    await log.flush(true);
    expect(await onDisk()).toEqual(["kept, late"]);
  });

  it("asks the Portal for the gap only, when this tab wrote the saved place", async () => {
    const first = page();
    await first.boot();
    place.seq = 5;
    await signedIn(first);
    say("one", "two");
    place.seq = 9;
    await first.flush();

    reload();
    const second = page();
    await second.boot();
    expect(connection.seedResume).toHaveBeenCalledWith({ seq: 9 });
  });

  it("asks the Portal for nothing, when another tab wrote the saved place", async () => {
    const first = page();
    await first.boot();
    await signedIn(first);
    say("one");
    await first.flush();

    reload();
    tabStorage = new FakeStorage(); // a different tab
    const second = page();
    await second.boot();
    expect(connection.seedResume).toHaveBeenCalledWith({ replay: false });
  });

  it("does not seed the connection when there is no saved log", async () => {
    const log = page();
    await log.boot();
    expect(connection.seedResume).not.toHaveBeenCalled();
  });

  it("ignores a log nobody has written to for a week", async () => {
    const first = page();
    await first.boot();
    await signedIn(first);
    say("old news");
    await first.flush();

    reload();
    clock += 8 * 24 * 60 * 60 * 1000;
    // The sweep would remove it in the background; this is about not showing it
    // in the meantime.
    vi.spyOn(LogStore.prototype, "sweep").mockResolvedValue([]);
    const second = page();
    await second.boot();
    await signedIn(second);
    expect(texts()).toEqual([]);
    expect(connection.seedResume).not.toHaveBeenCalled();
  });

  it("removes logs that have gone stale, once the page is up", async () => {
    const first = page();
    await first.boot();
    await signedIn(first, 4);
    say("stale");
    await first.flush();
    first.signedOut();

    reload();
    clock += 8 * 24 * 60 * 60 * 1000;
    const second = page();
    await second.boot();
    await vi.waitFor(async () => expect(await store().meta(4)).toBeNull(), { timeout: 5_000, interval: 5 });
  });
});

describe("showing the log", () => {
  async function saved(...lines: string[]): Promise<void> {
    const first = page();
    await first.boot();
    place.seq = 3;
    await signedIn(first);
    say(...lines);
    place.seq = 8;
    await first.flush();
    first.signedOut();
    reload();
  }

  it("shows nothing until the Portal says who the browser is", async () => {
    await saved("a secret scene");
    const log = page();
    await log.boot();
    expect(texts()).toEqual([]);
  });

  it("puts the log back in front of the lines of this visit, in order", async () => {
    await saved("one", "two", "three");
    const log = page();
    await log.boot();
    say("replayed while the hello was on its way");
    await signedIn(log, 7, { resumed: true });
    expect(texts()).toEqual(["one", "two", "three", "replayed while the hello was on its way"]);
  });

  it("gives the old lines ids below every live one, so the list stays in order", async () => {
    await saved("one", "two");
    const log = page();
    await log.boot();
    say("live");
    await signedIn(log, 7, { resumed: true });
    const ids = session.lines.map((l) => l.id);
    expect(ids.slice(0, 2).every((id) => id < 0)).toBe(true);
    expect(ids).toEqual([...ids].sort((a, b) => a - b));
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("keeps the time each line was shown", async () => {
    const first = page();
    await first.boot();
    await signedIn(first);
    session.append("then", "text", 1_234_000);
    await first.flush();
    first.signedOut();
    reload();
    const second = page();
    await second.boot();
    await signedIn(second, 7, { resumed: true });
    expect(session.lines[0].ts).toBe(1_234_000);
  });

  it("does not run the old lines through the listeners again", async () => {
    await saved("one", "two");
    const heard = vi.fn();
    session.onLine(heard);
    const log = page();
    await log.boot();
    await signedIn(log, 7, { resumed: true });
    expect(texts()).toEqual(["one", "two"]);
    expect(heard).not.toHaveBeenCalled();
  });

  it("says nothing when the session was resumed, because nothing broke", async () => {
    await saved("one");
    const log = page();
    await log.boot();
    await signedIn(log, 7, { resumed: true });
    expect(texts()).toEqual(["one"]);
  });

  it("marks where the last visit ends when the session was not resumed", async () => {
    await saved("one");
    const log = page();
    await log.boot();
    await signedIn(log, 7, { resumed: false });
    expect(texts()).toEqual(["one", NOTES.returned]);
    expect(session.lines[1].type).toBe("system");
    expect(session.lines[1].html).toBe(noteHtml(NOTES.returned));
  });

  it("keeps the mark in the saved log too, ahead of what comes after it", async () => {
    await saved("one");
    const log = page();
    await log.boot();
    await signedIn(log, 7, { resumed: false });
    say("welcome back");
    await log.flush(true);
    expect(await onDisk()).toEqual(["one", NOTES.returned, "welcome back"]);
  });

  it("shows another account none of it", async () => {
    await saved("one");
    const log = page();
    await log.boot();
    await signedIn(log, 9);
    expect(texts()).toEqual([]);
  });

  it("leaves the first account's log for the first account", async () => {
    await saved("one");
    const log = page();
    await log.boot();
    await signedIn(log, 9);
    say("nine's line");
    await log.flush(true);
    log.signedOut();
    expect(await onDisk(7)).toEqual(["one"]);
    expect(await onDisk(9)).toEqual(["nine's line"]);
  });

  it("shows nothing at the sign-in screen", async () => {
    await saved("one");
    const log = page();
    await log.boot();
    await log.hello({ resumed: false, account: null, kept: false });
    expect(texts()).toEqual([]);
    // Whose log this device saved last is still known, for when they come back.
    expect(local.getItem(POINTER_KEY)).toBe("7");
  });

  it("brings the log back when the account signs in at the sign-in screen", async () => {
    await saved("one", "two");
    const log = page();
    await log.boot();
    await log.hello({ resumed: false, account: null, kept: false });
    say("Welcome to the game");
    log.discardUnbound();
    await log.signedIn(7);
    expect(texts()).toEqual(["one", "two", NOTES.returned, "Welcome to the game"]);
  });

  it("does not bring a log back over a page that kept its own", async () => {
    await saved("one");
    const log = page();
    await log.boot();
    say("my own lines");
    await signedIn(log, 7, { kept: true });
    expect(texts()).toEqual(["my own lines"]);
  });

  it("takes only what the Portal replays when the saved log was too slow to read", async () => {
    await saved("one");
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    vi.spyOn(LogStore.prototype, "tail").mockReturnValue(new Promise(() => {}));
    const log = page();
    const booted = log.boot();
    await vi.advanceTimersByTimeAsync(2_000);
    await booted;
    say("replayed");
    await signedIn(log, 7, { resumed: true });
    // Reading it now would double any line the replay carries.
    expect(texts()).toEqual(["replayed"]);
  });

  it("applies the routes to the old lines, so a feed has its lines back", async () => {
    await saved("a whisper from Mira", "a plain line");
    routing.routes = [{ pattern: "whisper", label: "chatter", move: true }];
    routing.sync();
    const log = page();
    await log.boot();
    await signedIn(log, 7, { resumed: true });
    expect(texts()).toEqual(["a plain line"]);
    expect(routing.buffers.chatter).toHaveLength(1);
  });
});

describe("saving", () => {
  async function ready(account = 7): Promise<SavedLog> {
    const log = page();
    await log.boot();
    await signedIn(log, account);
    return log;
  }

  it("waits a moment so a burst is one write", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const log = await ready();
    const write = vi.spyOn(LogStore.prototype, "write");
    say("a", "b", "c");
    await vi.advanceTimersByTimeAsync(FLUSH_MS - 1);
    expect(write).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2);
    await log.flush();
    expect(write).toHaveBeenCalledTimes(1);
    expect(await onDisk()).toEqual(["a", "b", "c"]);
  });

  it("writes the lines and the place the connection had reached in one step", async () => {
    const log = await ready();
    place.seq = 40;
    say("one");
    const pending = log.flush();
    // More arrives before the write finishes: it belongs to the next write.
    place.seq = 41;
    say("two");
    await pending;
    expect(await store().meta(7)).toMatchObject({ seq: 40, count: 1 });
    await log.flush();
    expect(await store().meta(7)).toMatchObject({ seq: 41, count: 2 });
  });

  it("saves nothing before sign-in", async () => {
    const log = page();
    await log.boot();
    say("the sign-in screen");
    await log.flush(true);
    expect(await onDisk()).toEqual([]);
  });

  it("does not save the sign-in screen once someone signs in", async () => {
    const log = page();
    await log.boot();
    await log.hello({ resumed: false, account: null, kept: false });
    say("a banner", "a prompt");
    log.discardUnbound();
    await log.signedIn(7);
    say("Welcome");
    await log.flush(true);
    expect(await onDisk()).toEqual(["Welcome"]);
  });

  it("saves a line a feed took out of the terminal", async () => {
    routing.routes = [{ pattern: "whisper", label: "chatter", move: true }];
    routing.sync();
    const log = await ready();
    say("a whisper from Mira");
    await log.flush(true);
    expect(session.lines).toEqual([]);
    expect(await onDisk()).toEqual(["a whisper from Mira"]);
  });

  it("does not save a line a gag removed", async () => {
    const log = await ready();
    say("one");
    await log.flush(true);
    expect(await onDisk()).toEqual(["one"]);
  });

  it("gives each saved line the key it was stored under", async () => {
    const log = await ready();
    say("one", "two");
    await log.flush(true);
    expect(session.archive.map((l) => l.sk)).toEqual([1, 2]);
  });

  it("writes the place alone when the page is put away with nothing new", async () => {
    const log = await ready();
    say("one");
    place.seq = 3;
    await log.flush(true);
    place.seq = 50;
    await log.flush(true);
    expect(await store().meta(7)).toMatchObject({ seq: 50, count: 1 });
  });

  it("writes nothing when nothing changed", async () => {
    const log = await ready();
    say("one");
    place.seq = 3;
    await log.flush(true);
    const write = vi.spyOn(LogStore.prototype, "write");
    await log.flush(true);
    await log.flush(false);
    expect(write).not.toHaveBeenCalled();
  });

  it("does not write before the handshake has answered", async () => {
    vi.spyOn(connection, "helloed", "get").mockReturnValue(false);
    const log = await ready();
    say("one");
    await log.flush(true);
    expect(await onDisk()).toEqual([]);
  });

  it("makes room and tries again when the disk is full", async () => {
    const log = await ready();
    say("one", "two");
    await log.flush(true);
    const real = LogStore.prototype.write;
    let failed = false;
    vi.spyOn(LogStore.prototype, "write").mockImplementation(async function (this: LogStore, ...args) {
      if (!failed) {
        failed = true;
        throw Object.assign(new Error("full"), { name: "QuotaExceededError" });
      }
      return real.apply(this, args);
    });
    const shrink = vi.spyOn(LogStore.prototype, "shrink");
    say("three");
    await log.flush(true);
    expect(shrink).toHaveBeenCalledWith(7);
    expect((await onDisk()).at(-1)).toBe("three");
  });

  it("tries a failed write again with the next one, in order", async () => {
    const log = await ready();
    const real = LogStore.prototype.write;
    let calls = 0;
    vi.spyOn(LogStore.prototype, "write").mockImplementation(async function (this: LogStore, ...args) {
      if (++calls === 1) throw new Error("transient");
      return real.apply(this, args);
    });
    say("one");
    await log.flush(true);
    say("two");
    await log.flush(true);
    expect(await onDisk()).toEqual(["one", "two"]);
  });

  it("gives up on saving after repeated failures, and the page carries on", async () => {
    const log = await ready();
    vi.spyOn(LogStore.prototype, "write").mockRejectedValue(new Error("broken"));
    for (let i = 0; i < 4; i++) {
      say(`line ${i}`);
      await log.flush(true);
    }
    expect(texts()).toEqual(["line 0", "line 1", "line 2", "line 3"]);
    const write = vi.mocked(LogStore.prototype.write);
    write.mockClear();
    say("after");
    await log.flush(true);
    expect(write).not.toHaveBeenCalled();
  });

  it("writes what is waiting when the account goes away", async () => {
    const log = await ready();
    say("last words");
    log.signedOut();
    await log.flush(true);
    expect(await onDisk()).toEqual(["last words"]);
  });

  it("writes a flood as it comes, so it is not held in memory", async () => {
    const log = await ready();
    const write = vi.spyOn(LogStore.prototype, "write");
    for (let i = 0; i < 6_000; i++) say(`line ${i}`);
    await log.flush(true);
    expect(write.mock.calls.length).toBeGreaterThan(5);
    const saved = await onDisk();
    expect(saved).toHaveLength(6_000);
    expect(saved[0]).toBe("line 0");
    expect(saved.at(-1)).toBe("line 5999");
  });

  it("never holds more than a page's worth when nothing can be written", async () => {
    vi.spyOn(connection, "helloed", "get").mockReturnValue(false);
    const log = await ready();
    for (let i = 0; i < 6_000; i++) say(`line ${i}`);
    vi.spyOn(connection, "helloed", "get").mockReturnValue(true);
    const write = vi.spyOn(LogStore.prototype, "write");
    await log.flush(true);
    expect(write.mock.calls[0][1]).toHaveLength(5_000);
    expect((await onDisk())[0]).toBe("line 1000");
  });
});

describe("telling the player", () => {
  it("says once, on a device that has not been told, that the log is kept there", async () => {
    local.removeItem(TOLD_KEY);
    const log = page();
    await log.boot();
    await signedIn(log);
    expect(texts()).toEqual([NOTES.saving]);
    expect(session.lines[0].type).toBe("system");
    expect(local.getItem(TOLD_KEY)).toBe("1");
  });

  it("does not say it again on the next visit, or for another account", async () => {
    local.removeItem(TOLD_KEY);
    const first = page();
    await first.boot();
    await signedIn(first, 7);
    first.signedOut();
    reload();
    const second = page();
    await second.boot();
    await signedIn(second, 9);
    expect(texts()).toEqual([]);
  });

  it("keeps the notice in the log it describes", async () => {
    local.removeItem(TOLD_KEY);
    const log = page();
    await log.boot();
    await signedIn(log);
    await log.flush(true);
    expect(await onDisk()).toEqual([NOTES.saving]);
  });

  it("does not say it when saving is off", async () => {
    local.removeItem(TOLD_KEY);
    settings.rememberLog = false;
    const log = page();
    await log.boot();
    await signedIn(log);
    expect(texts()).toEqual([]);
    expect(local.getItem(TOLD_KEY)).toBeNull();
  });

  it("does not say it at the sign-in screen", async () => {
    local.removeItem(TOLD_KEY);
    const log = page();
    await log.boot();
    await log.hello({ resumed: false, account: null, kept: false });
    expect(texts()).toEqual([]);
  });
});

describe("what is never kept", () => {
  const line = (type: string, text: string, html = text) => ({ type, text, html });

  it("keeps ordinary lines", () => {
    expect(keepable(line("text", "hello"))).toBe(true);
    expect(keepable(line("echo", "> look"))).toBe(true);
  });

  it("drops media embeds", () => {
    expect(keepable(line("media", "♪ song"))).toBe(false);
  });

  it("drops the echo of a command with a secret in it", () => {
    for (const cmd of ["connect mira hunter2", "create mira hunter2", "@password old = new", "password a b", "login mira x", "pw a b"]) {
      expect(keepable(line("echo", `> ${cmd}`))).toBe(false);
    }
  });

  it("keeps a word like password when it is only said", () => {
    expect(keepable(line("echo", "> say the password is swordfish"))).toBe(true);
    expect(keepable(line("text", "connect to the dock"))).toBe(true);
  });

  it("drops a single line too big to be worth the disk", () => {
    expect(keepable(line("text", "x", "y".repeat(250_000)))).toBe(false);
  });
});

describe("two tabs of one account", () => {
  async function open(): Promise<{ log: SavedLog; sess: GameSession }> {
    const t = tab();
    await t.log.boot();
    await signedIn(t.log);
    return t;
  }

  it("lets one save at a time, and the second starts when the first lets go", async () => {
    const one = await open();
    const two = await open();

    one.sess.append("said while both were open", "text");
    two.sess.append("said while both were open", "text");
    await one.log.flush(true);
    await two.log.flush(true);
    expect(await onDisk()).toEqual(["said while both were open"]);

    one.log.signedOut();
    await vi.waitFor(() => expect(two.log.writing).toBe(true), { timeout: 5_000, interval: 5 });
    two.sess.append("said after the first left", "text");
    await two.log.flush(true);
    expect(await onDisk()).toEqual(["said while both were open", "said after the first left"]);
  });

  it("does not save a second copy of what both tabs were shown", async () => {
    const one = await open();
    const two = await open();
    one.sess.append("both tabs showed this", "text", 5_000);
    two.sess.append("both tabs showed this", "text", 5_000);
    await one.log.flush(true);
    one.log.signedOut();
    await vi.waitFor(() => expect(two.log.writing).toBe(true), { timeout: 5_000, interval: 5 });
    await two.log.flush(true);
    expect(await onDisk()).toEqual(["both tabs showed this"]);
  });

  it("does save what only the second tab got before it took over", async () => {
    const one = await open();
    const two = await open();
    one.sess.append("both tabs showed this", "text", 5_000);
    two.sess.append("both tabs showed this", "text", 5_000);
    await one.log.flush(true);
    // The first tab closed before it wrote this one.
    two.sess.append("only the second tab got this", "text", 6_000);
    one.log.signedOut();
    await vi.waitFor(() => expect(two.log.writing).toBe(true), { timeout: 5_000, interval: 5 });
    await two.log.flush(true);
    expect(await onDisk()).toEqual(["both tabs showed this", "only the second tab got this"]);
  });

  it("lets the first tab finish its last write before the second starts", async () => {
    const one = await open();
    const two = await open();
    one.sess.append("the first tab's last words", "text", 5_000);
    two.sess.append("the second tab's first words", "text", 6_000);
    // The first tab goes away with that line still unwritten.
    one.log.signedOut();
    await vi.waitFor(() => expect(two.log.writing).toBe(true), { timeout: 5_000, interval: 5 });
    await two.log.flush(true);
    expect(await onDisk()).toEqual(["the first tab's last words", "the second tab's first words"]);
  });

  it("keeps the first tab's lines first even when its writes are slow", async () => {
    const one = await open();
    const two = await open();
    const real = LogStore.prototype.write;
    let slowed = false;
    vi.spyOn(LogStore.prototype, "write").mockImplementation(async function (this: LogStore, ...args) {
      // Only the first tab's first write is slow.
      if (!slowed) {
        slowed = true;
        await new Promise((r) => setTimeout(r, 60));
      }
      return real.apply(this, args);
    });
    one.sess.append("first tab, early", "text", 5_000);
    void one.log.flush();
    one.sess.append("first tab, last words", "text", 5_100);
    two.sess.append("second tab, new", "text", 9_000);
    one.log.signedOut();
    await vi.waitFor(() => expect(two.log.writing).toBe(true), { timeout: 5_000, interval: 5 });
    await two.log.flush(true);
    expect(await onDisk()).toEqual(["first tab, early", "first tab, last words", "second tab, new"]);
  });

  it("saves in every tab when the browser has no locks", async () => {
    locks = null;
    const one = await open();
    one.sess.append("kept", "text");
    await one.log.flush(true);
    expect(await onDisk()).toEqual(["kept"]);
  });

  it("lets two different accounts save at once", async () => {
    const one = tab();
    await one.log.boot();
    await signedIn(one.log, 1);
    const other = tab();
    await other.log.boot();
    await signedIn(other.log, 2);
    one.sess.append("one's line", "text");
    other.sess.append("two's line", "text");
    await one.log.flush(true);
    await other.log.flush(true);
    expect(await onDisk(1)).toEqual(["one's line"]);
    expect(await onDisk(2)).toEqual(["two's line"]);
  });

  it("gives a tab that did not write the saved place no replay", async () => {
    const one = await open();
    one.sess.append("one", "text");
    await one.log.flush(true);
    one.log.signedOut();

    // Another tab opens on the same device and reads what the first saved.
    const two = tab();
    await two.log.boot();
    expect(connection.seedResume).toHaveBeenCalledWith({ replay: false });
  });
});

describe("older lines", () => {
  async function longLog(count: number): Promise<void> {
    const s = store();
    for (let from = 1; from <= count; from += 1000) {
      await s.write(
        7,
        Array.from({ length: Math.min(1000, count - from + 1) }, (_, i) => ({
          html: `line ${from + i}`,
          text: `line ${from + i}`,
          type: "text",
          ts: from + i,
        })),
        from === 1 ? { tab: "elsewhere", seq: 1 } : null,
      );
    }
    local.setItem(POINTER_KEY, "7");
  }

  it("brings back one screenful and offers the rest", async () => {
    await longLog(MAX_LINES + 1200);
    const log = page();
    await log.boot();
    await signedIn(log, 7, { resumed: true });
    expect(session.lines).toHaveLength(MAX_LINES);
    expect(session.lines.at(-1)?.text).toBe(`line ${MAX_LINES + 1200}`);
    expect(log.hasOlder).toBe(true);
  });

  it("does not offer more when the whole log is held", async () => {
    await longLog(300);
    const log = page();
    await log.boot();
    await signedIn(log, 7, { resumed: true });
    expect(session.lines).toHaveLength(300);
    expect(log.hasOlder).toBe(false);
  });

  it("reads the page before the oldest line held, in front of it", async () => {
    await longLog(MAX_LINES + 1200);
    const log = page();
    await log.boot();
    await signedIn(log, 7, { resumed: true });
    const oldest = session.lines[0].text;
    await log.loadOlder();
    expect(session.lines).toHaveLength(MAX_LINES + OLDER_PAGE);
    expect(session.lines[OLDER_PAGE].text).toBe(oldest);
    const ids = session.lines.map((l) => l.id);
    expect(ids).toEqual([...ids].sort((a, b) => a - b));
    expect(new Set(ids).size).toBe(ids.length);
    expect(log.hasOlder).toBe(true);
  });

  it("reads on until the start of the log, then stops offering", async () => {
    await longLog(MAX_LINES + 700);
    const log = page();
    await log.boot();
    await signedIn(log, 7, { resumed: true });
    await log.loadOlder();
    expect(log.hasOlder).toBe(true);
    await log.loadOlder();
    expect(session.lines).toHaveLength(MAX_LINES + 700);
    expect(session.lines[0].text).toBe("line 1");
    expect(log.hasOlder).toBe(false);
  });

  it("keeps the older lines while the reader is away from the bottom", async () => {
    await longLog(MAX_LINES + 1200);
    const log = page();
    await log.boot();
    await signedIn(log, 7, { resumed: true });
    session.follow(false);
    await log.loadOlder();
    const oldest = session.lines[0].text;
    for (let i = 0; i < 700; i++) say(`new ${i}`);
    expect(session.lines[0].text).toBe(oldest);
    expect(session.lines.length).toBe(MAX_LINES + OLDER_PAGE + 700);
  });

  it("lets them go again once the reader is back at the bottom", async () => {
    await longLog(MAX_LINES + 1200);
    const log = page();
    await log.boot();
    await signedIn(log, 7, { resumed: true });
    session.follow(false);
    await log.loadOlder();
    session.follow(true);
    for (let i = 0; i < 700; i++) say(`new ${i}`);
    expect(session.lines.length).toBeLessThanOrEqual(MAX_LINES + 500);
    expect(session.lines.at(-1)?.text).toBe("new 699");
  });

  it("trims as usual if the reader never left the bottom", async () => {
    await longLog(MAX_LINES + 1200);
    const log = page();
    await log.boot();
    await signedIn(log, 7, { resumed: true });
    await log.loadOlder();
    for (let i = 0; i < 700; i++) say(`new ${i}`);
    expect(session.lines.length).toBeLessThanOrEqual(MAX_LINES + 500);
  });

  it("asks for one page at a time", async () => {
    await longLog(MAX_LINES + 1200);
    const log = page();
    await log.boot();
    await signedIn(log, 7, { resumed: true });
    const before = vi.spyOn(LogStore.prototype, "before");
    await Promise.all([log.loadOlder(), log.loadOlder(), log.loadOlder()]);
    expect(before).toHaveBeenCalledTimes(1);
  });

  it("does nothing when there is nothing older", async () => {
    const log = page();
    await log.boot();
    await signedIn(log);
    const before = vi.spyOn(LogStore.prototype, "before");
    await log.loadOlder();
    expect(before).not.toHaveBeenCalled();
  });
});

describe("the player's choices", () => {
  async function ready(): Promise<SavedLog> {
    const log = page();
    await log.boot();
    await signedIn(log);
    say("one", "two");
    await log.flush(true);
    return log;
  }

  it("clearing the screen leaves the saved copy, as a sign-out does", async () => {
    const log = await ready();
    session.clear();
    expect(session.lines).toEqual([]);
    await log.flush(true);
    expect(await onDisk()).toEqual(["one", "two"]);
  });

  it("clearing it as the player asks deletes the saved copy too", async () => {
    const log = await ready();
    session.clear({ saved: true });
    await log.flush(true);
    expect(await onDisk()).toEqual([]);
    expect(await store().meta(7)).toBeNull();
  });

  it("keeps saving after the player clears, from the next line", async () => {
    const log = await ready();
    session.clear({ saved: true });
    say("fresh");
    await log.flush(true);
    expect(await onDisk()).toEqual(["fresh"]);
  });

  it("forgets that any held line is saved once the saved copy is gone", async () => {
    await ready();
    expect(session.archive.every((l) => l.sk !== undefined)).toBe(true);
    session.clear({ saved: true });
    say("fresh");
    expect(session.archive.every((l) => l.sk === undefined)).toBe(true);
  });

  it("erasing does not write the place back into the log it just deleted", async () => {
    const log = await ready();
    place.seq = 77;
    await log.erase();
    log.signedOut();
    await log.flush(true);
    expect(await store().meta(7)).toBeNull();
  });

  it("deletes every saved log on the device", async () => {
    const log = await ready();
    const s2 = store();
    await s2.write(9, [{ html: "x", text: "x", type: "text", ts: 1 }], null);
    await log.forgetAll();
    expect(await onDisk(7)).toEqual([]);
    expect(await onDisk(9)).toEqual([]);
  });

  it("still knows whose log to look for after deleting, if it keeps saving", async () => {
    const log = await ready();
    await log.forgetAll();
    expect(local.getItem(POINTER_KEY)).toBe("7");
    say("after");
    await log.flush(true);
    expect(await onDisk()).toEqual(["after"]);
  });

  it("turning saving off deletes everything and stops", async () => {
    const log = await ready();
    await log.setEnabled(false);
    expect(await onDisk()).toEqual([]);
    expect(local.getItem(POINTER_KEY)).toBeNull();
    say("not kept");
    await log.flush(true);
    expect(await onDisk()).toEqual([]);
    expect(texts()).toEqual(["one", "two", "not kept"]);
  });

  it("turning it back on saves what is on the screen, then carries on", async () => {
    const log = await ready();
    await log.setEnabled(false);
    await log.setEnabled(true);
    say("three");
    await log.flush(true);
    expect(await onDisk()).toEqual(["one", "two", "three"]);
  });

  it("starts saving when it is turned on in a session that began with it off", async () => {
    settings.rememberLog = false;
    const log = page();
    await log.boot();
    await signedIn(log);
    say("shown before the switch");
    settings.rememberLog = true;
    await log.setEnabled(true);
    say("shown after the switch");
    await log.flush(true);
    expect(await onDisk()).toEqual(["shown before the switch", "shown after the switch"]);
    expect(local.getItem(POINTER_KEY)).toBe("7");
  });

  it("does not save anyone's screen if no one is signed in when it is turned on", async () => {
    settings.rememberLog = false;
    const log = page();
    await log.boot();
    say("the sign-in screen");
    await log.setEnabled(true);
    await log.flush(true);
    expect(await onDisk()).toEqual([]);
  });

  it("never lets a failure inside it reach the page", async () => {
    const first = page();
    await first.boot();
    await signedIn(first);
    say("one");
    await first.flush(true);
    first.signedOut();
    reload();
    vi.spyOn(console, "error").mockImplementation(() => {});
    const second = page();
    await second.boot();
    vi.spyOn(session, "restore").mockImplementation(() => {
      throw new Error("restore failed");
    });
    await expect(signedIn(second)).resolves.toBeUndefined();
    expect(console.error).toHaveBeenCalled();
  });

  it("does not remember who was signed in while saving is off", async () => {
    settings.rememberLog = false;
    const log = page();
    await log.boot();
    await signedIn(log);
    expect(local.getItem(POINTER_KEY)).toBeNull();
  });
});
