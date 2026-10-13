/**
 * The saved scrollback's database: what it keeps, in what order, how it stays
 * within its limits, and that it never throws at a page that cannot use it.
 */
import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  DB_NAME,
  DB_VERSION,
  LIMITS,
  LogStore,
  SCHEMA,
  StoreUnavailable,
  isFull,
  sizeOf,
  type Limits,
  type NewLine,
} from "./logstore";

let factory: IDBFactory;
let now: number;

function store(limits: Partial<Limits> = {}): LogStore {
  return new LogStore(factory, () => now, limits);
}

function line(n: number, text = `line ${n}`): NewLine {
  return { html: `<b>${text}</b>`, text, type: "text", ts: 1_000 + n };
}

function lines(from: number, to: number): NewLine[] {
  return Array.from({ length: to - from + 1 }, (_, i) => line(from + i));
}

/** Reach into the database the way an older or a broken build might have left it. */
async function raw(work: (db: IDBDatabase) => Promise<void> | void): Promise<void> {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const req = factory.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      req.result.createObjectStore("lines", { keyPath: ["account", "k"] });
      req.result.createObjectStore("meta", { keyPath: "account" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  await work(db);
  db.close();
}

function put(db: IDBDatabase, name: string, value: unknown): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(name, "readwrite");
    tx.objectStore(name).put(value);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

beforeEach(() => {
  factory = new IDBFactory();
  now = 1_700_000_000_000;
});

describe("opening", () => {
  it("opens, and says so", async () => {
    const s = store();
    expect(s.ready).toBe(false);
    expect(await s.open()).toBe(true);
    expect(s.ready).toBe(true);
  });

  it("opens once however many ask at the same time", async () => {
    const open = vi.spyOn(factory, "open");
    const s = store();
    expect(await Promise.all([s.open(), s.open(), s.open()])).toEqual([true, true, true]);
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("reports a browser without IndexedDB instead of throwing", async () => {
    const s = new LogStore(null);
    expect(await s.open()).toBe(false);
    await expect(s.tail(1, 10)).rejects.toBeInstanceOf(StoreUnavailable);
  });

  it("reports a browser whose IndexedDB throws on open", async () => {
    const broken = { open: () => { throw new Error("SecurityError"); } } as unknown as IDBFactory;
    expect(await new LogStore(broken).open()).toBe(false);
  });

  it("opens again after it was closed", async () => {
    const s = store();
    await s.write(1, [line(1)], null);
    s.close();
    expect(s.ready).toBe(false);
    expect((await s.tail(1, 10)).map((l) => l.text)).toEqual(["line 1"]);
  });
});

describe("writing and reading", () => {
  it("keeps lines in order under keys that count up from 1", async () => {
    const s = store();
    const done = await s.write(7, lines(1, 3), null);
    expect(done).toEqual({ keys: [1, 2, 3], firstK: 1, count: 3 });
    const more = await s.write(7, lines(4, 5), null);
    expect(more.keys).toEqual([4, 5]);
    const saved = await s.tail(7, 100);
    expect(saved.map((l) => l.text)).toEqual(["line 1", "line 2", "line 3", "line 4", "line 5"]);
    expect(saved.map((l) => l.k)).toEqual([1, 2, 3, 4, 5]);
  });

  it("gives a line back exactly as it went in", async () => {
    const s = store();
    await s.write(7, [{ html: '<span class="c1">a &amp; b</span>', text: "a & b", type: "say", ts: 42 }], null);
    expect(await s.tail(7, 10)).toEqual([
      { k: 1, html: '<span class="c1">a &amp; b</span>', text: "a & b", type: "say", ts: 42 },
    ]);
  });

  it("returns the newest lines when asked for fewer than it has", async () => {
    const s = store();
    await s.write(7, lines(1, 10), null);
    expect((await s.tail(7, 3)).map((l) => l.text)).toEqual(["line 8", "line 9", "line 10"]);
  });

  it("returns the lines right before a key", async () => {
    const s = store();
    await s.write(7, lines(1, 10), null);
    expect((await s.before(7, 6, 3)).map((l) => l.k)).toEqual([3, 4, 5]);
    expect((await s.before(7, 3, 10)).map((l) => l.k)).toEqual([1, 2]);
    expect(await s.before(7, 1, 10)).toEqual([]);
  });

  it("keeps each account's lines apart", async () => {
    const s = store();
    await s.write(1, [line(1, "mine")], null);
    await s.write(2, [line(1, "yours")], null);
    expect((await s.tail(1, 10)).map((l) => l.text)).toEqual(["mine"]);
    expect((await s.tail(2, 10)).map((l) => l.text)).toEqual(["yours"]);
  });

  it("reads nothing for an account that has nothing", async () => {
    const s = store();
    expect(await s.tail(9, 10)).toEqual([]);
    expect(await s.before(9, 5, 10)).toEqual([]);
    expect(await s.meta(9)).toBeNull();
  });

  it("writes the place the connection had reached together with the lines", async () => {
    const s = store();
    await s.write(7, lines(1, 2), { tab: "tab-a", seq: 41 });
    expect(await s.meta(7)).toMatchObject({ account: 7, tab: "tab-a", seq: 41, count: 2, nextK: 3, firstK: 1 });
  });

  it("leaves the saved place alone when a write brings none", async () => {
    const s = store();
    await s.write(7, [line(1)], { tab: "tab-a", seq: 41 });
    await s.write(7, [line(2)], null);
    expect(await s.meta(7)).toMatchObject({ tab: "tab-a", seq: 41, count: 2 });
  });

  it("moves the place on without a line when that is all there is", async () => {
    const s = store();
    await s.write(7, [line(1)], { tab: "tab-a", seq: 41 });
    const done = await s.write(7, [], { tab: "tab-a", seq: 90 });
    expect(done.keys).toEqual([]);
    expect(await s.meta(7)).toMatchObject({ seq: 90, count: 1 });
  });

  it("stamps the time of the last write", async () => {
    const s = store();
    await s.write(7, [line(1)], null);
    now += 5_000;
    await s.write(7, [line(2)], null);
    expect((await s.meta(7))?.savedAt).toBe(now);
  });

  it("counts the size of what it holds", async () => {
    const s = store();
    await s.write(7, lines(1, 3), null);
    expect((await s.meta(7))?.bytes).toBe(lines(1, 3).reduce((n, l) => n + sizeOf(l), 0));
  });

  it("rolls a failed write back whole", async () => {
    const s = store();
    await s.write(7, lines(1, 2), { tab: "tab-a", seq: 5 });
    // A row the database cannot store aborts the transaction; nothing of it stays.
    const poison = { ...line(5), html: (() => {}) as unknown as string };
    await expect(s.write(7, [line(3), poison], { tab: "tab-a", seq: 9 })).rejects.toBeTruthy();
    expect((await s.tail(7, 10)).map((l) => l.text)).toEqual(["line 1", "line 2"]);
    expect(await s.meta(7)).toMatchObject({ seq: 5, count: 2, nextK: 3 });
  });
});

describe("staying within its limits", () => {
  it("trims the old end once the log is a block over, and not before", async () => {
    const s = store({ lines: 10, trimSlack: 5 });
    await s.write(1, lines(1, 15), null);
    expect((await s.meta(1))?.count).toBe(15);
    const done = await s.write(1, lines(16, 16), null);
    expect(done.count).toBe(10);
    expect(done.firstK).toBe(7);
    expect((await s.tail(1, 100)).map((l) => l.k)).toEqual([7, 8, 9, 10, 11, 12, 13, 14, 15, 16]);
  });

  it("keeps the run unbroken after a trim, for the reads that rely on it", async () => {
    const s = store({ lines: 10, trimSlack: 5 });
    for (let i = 0; i < 6; i++) await s.write(1, lines(i * 5 + 1, i * 5 + 5), null);
    const meta = await s.meta(1);
    const kept = await s.tail(1, 1000);
    expect(kept).toHaveLength(meta!.count);
    expect(kept.map((l) => l.k)).toEqual(kept.map((_, i) => meta!.firstK + i));
    expect((await s.before(1, meta!.firstK + 4, 100)).map((l) => l.k)).toEqual([
      meta!.firstK,
      meta!.firstK + 1,
      meta!.firstK + 2,
      meta!.firstK + 3,
    ]);
  });

  it("trims by size as well as by count", async () => {
    const big = (n: number): NewLine => line(n, "x".repeat(1_000));
    const s = store({ bytes: 10_000, lines: 1_000 });
    await s.write(1, Array.from({ length: 30 }, (_, i) => big(i)), null);
    const meta = await s.meta(1);
    expect(meta!.bytes).toBeLessThanOrEqual(8_000);
    expect(meta!.count).toBeLessThan(30);
    expect(meta!.count).toBeGreaterThan(0);
  });

  it("does not trim one account because another is long", async () => {
    const s = store({ lines: 5, trimSlack: 0 });
    await s.write(1, lines(1, 12), null);
    await s.write(2, lines(1, 3), null);
    expect((await s.meta(2))?.count).toBe(3);
  });

  it("shrinks to half when the disk is full", async () => {
    const s = store();
    await s.write(1, lines(1, 10), null);
    await s.shrink(1);
    // About half goes, from the old end; the newest lines stay.
    const kept = (await s.tail(1, 100)).map((l) => l.k);
    expect(kept.length).toBeLessThanOrEqual(5);
    expect(kept.length).toBeGreaterThanOrEqual(3);
    expect(kept.at(-1)).toBe(10);
    expect((await s.meta(1))?.count).toBe(kept.length);
  });

  it("recognises a full disk by the error it gives", () => {
    expect(isFull({ name: "QuotaExceededError" })).toBe(true);
    expect(isFull({ name: "NS_ERROR_DOM_QUOTA_REACHED" })).toBe(true);
    expect(isFull(new Error("nope"))).toBe(false);
    expect(isFull(null)).toBe(false);
  });
});

describe("deleting", () => {
  it("purges one account and leaves the rest", async () => {
    const s = store();
    await s.write(1, lines(1, 3), null);
    await s.write(2, lines(1, 3), null);
    await s.purge(1);
    expect(await s.tail(1, 10)).toEqual([]);
    expect(await s.meta(1)).toBeNull();
    expect(await s.tail(2, 10)).toHaveLength(3);
  });

  it("starts the keys over after a purge", async () => {
    const s = store();
    await s.write(1, lines(1, 3), null);
    await s.purge(1);
    expect((await s.write(1, lines(1, 2), null)).keys).toEqual([1, 2]);
  });

  it("purges everything", async () => {
    const s = store();
    await s.write(1, lines(1, 3), null);
    await s.write(2, lines(1, 3), null);
    await s.purgeAll();
    expect(await s.tail(1, 10)).toEqual([]);
    expect(await s.tail(2, 10)).toEqual([]);
  });

  it("sweeps a log nobody has written to for too long", async () => {
    const s = store({ idleMs: 1_000 });
    await s.write(1, [line(1)], null);
    now += 500;
    await s.write(2, [line(1)], null);
    now += 700; // account 1 is 1,200 idle, account 2 is 700
    expect(await s.sweep()).toEqual([1]);
    expect(await s.meta(1)).toBeNull();
    expect(await s.tail(1, 10)).toEqual([]);
    expect(await s.meta(2)).not.toBeNull();
  });

  it("sweeps the least recently written beyond the accounts kept", async () => {
    const s = store({ accounts: 2 });
    for (const account of [1, 2, 3, 4]) {
      await s.write(account, [line(1)], null);
      now += 10;
    }
    expect((await s.sweep()).sort()).toEqual([1, 2]);
    expect(await s.meta(3)).not.toBeNull();
    expect(await s.meta(4)).not.toBeNull();
  });

  it("sweeps nothing when nothing is old", async () => {
    const s = store();
    await s.write(1, [line(1)], null);
    expect(await s.sweep()).toEqual([]);
  });
});

describe("what it cannot read", () => {
  it("shows nothing saved under another version of the markup, and starts that account over", async () => {
    const s = store();
    await raw(async (db) => {
      await put(db, "meta", { account: 1, v: SCHEMA + 1, nextK: 3, firstK: 1, count: 2, bytes: 10, tab: "t", seq: 1, savedAt: now });
      await put(db, "lines", { account: 1, k: 1, html: "old", text: "old", type: "text", ts: 1 });
      await put(db, "lines", { account: 1, k: 2, html: "old", text: "old", type: "text", ts: 2 });
    });
    expect(await s.meta(1)).toBeNull();
    expect(await s.tail(1, 10)).toEqual([]);
    const done = await s.write(1, [line(1)], null);
    expect(done).toEqual({ keys: [1], firstK: 1, count: 1 });
    expect((await s.tail(1, 10)).map((l) => l.text)).toEqual(["line 1"]);
  });

  it("skips a row that is not a line", async () => {
    const s = store();
    await s.write(1, lines(1, 3), null);
    await raw((db) => put(db, "lines", { account: 1, k: 2, html: 5, text: null }));
    expect((await s.tail(1, 10)).map((l) => l.k)).toEqual([1, 3]);
  });

  it("treats a damaged meta row as no log", async () => {
    const s = store();
    await raw((db) => put(db, "meta", { account: 1, v: SCHEMA, nextK: "x" }));
    expect(await s.meta(1)).toBeNull();
    expect(await s.tail(1, 10)).toEqual([]);
  });

  it("sweeps away what is saved under another version", async () => {
    const s = store();
    await raw((db) => put(db, "meta", { account: 1, v: SCHEMA + 1, nextK: 1, firstK: 1, count: 0, bytes: 0, tab: "", seq: 0, savedAt: now }));
    expect(await s.sweep()).toEqual([1]);
  });
});

describe("limits", () => {
  it("are the ones the settings page promises", () => {
    expect(LIMITS.idleMs).toBe(7 * 24 * 60 * 60 * 1000);
    expect(LIMITS.lines).toBe(20_000);
    expect(LIMITS.accounts).toBe(3);
  });
});
