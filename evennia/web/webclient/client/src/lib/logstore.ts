// The saved scrollback: the part of the log a player has already read, kept in
// the browser's IndexedDB so a reload, a closed tab or a restart does not take
// it away.
//
// This file is only the database. It knows nothing about Svelte, the socket or
// who is signed in; lib/savedlog.svelte.ts decides what to save and when.
//
// What is stored, and why it is shaped this way:
//
//   * `lines` holds one row per log line, under [account, k]. The account is part
//     of the key so one browser can keep several people's logs apart, and `k` is
//     a number this store hands out, in order, inside the same transaction that
//     writes the row. Lines are only ever removed from the old end, so the keys
//     of one account are one unbroken run, and "the last N lines" or "the N lines
//     before this one" is a single range read.
//   * `meta` holds one row per account: where the run starts and ends, how much
//     it holds, and the place the connection had reached when the lines were
//     written (`tab` and `seq`). That place is written in the same transaction as
//     the lines, so after a crash or a reload the two always agree, and the
//     reloaded page can ask the Portal for exactly what came after the last saved
//     line, no more and no less.
//
// Everything here can fail: a private window, a full disk, a browser that clears
// site data. Callers treat the store as a cache and carry on without it.

/** The IndexedDB name. */
export const DB_NAME = "underspire.scrollback";
export const DB_VERSION = 1;

/**
 * Bump when the markup of a saved line changes so that an old line can no longer
 * be shown. A log saved under another number is dropped, not shown wrongly.
 */
export const SCHEMA = 1;

export interface Limits {
  /** Lines kept for one account. */
  lines: number;
  /** The trim waits until the log is this far over, then cuts back in one go. */
  trimSlack: number;
  /** About this much line text kept for one account. */
  bytes: number;
  /** A log nobody has written to for this long is deleted without being read. */
  idleMs: number;
  /** Accounts kept in one browser. The least recently written go first. */
  accounts: number;
}

export const LIMITS: Readonly<Limits> = {
  lines: 20_000,
  trimSlack: 1_000,
  bytes: 24 * 1024 * 1024,
  idleMs: 7 * 24 * 60 * 60 * 1000,
  accounts: 3,
};

/** One saved line as the page gets it back. */
export interface SavedLine {
  /** Where the line sits in its account's saved run. */
  k: number;
  html: string;
  /** The same line as plain text, kept so search and the log lens need no parsing. */
  text: string;
  type: string;
  ts: number;
}

/** A line to save. */
export type NewLine = Omit<SavedLine, "k">;

/** What is known about one account's saved run. */
export interface SavedMeta {
  account: number;
  v: number;
  /** The key the next line gets. */
  nextK: number;
  /** The key of the oldest line kept. Equal to `nextK` when there are none. */
  firstK: number;
  count: number;
  /** An estimate of the size of the lines, for the size limit. */
  bytes: number;
  /** The tab that wrote the last lines. Only that tab may use `seq`. */
  tab: string;
  /** The last frame seq that connection had applied when it wrote them. */
  seq: number;
  /** When the run was last written, epoch ms. */
  savedAt: number;
}

/** The place a connection had reached, saved with the lines it had shown. */
export interface Cursor {
  tab: string;
  seq: number;
}

/** What a write did. */
export interface Written {
  /** The key each added line was given, in order. */
  keys: number[];
  firstK: number;
  count: number;
}

interface Row extends SavedLine {
  account: number;
}

/** The store cannot be used here, or no longer can. */
export class StoreUnavailable extends Error {
  constructor(why = "IndexedDB is not available") {
    super(why);
    this.name = "StoreUnavailable";
  }
}

/** Whether a failed write is the disk or the quota being full. */
export function isFull(e: unknown): boolean {
  const name = (e as { name?: string } | null)?.name;
  return name === "QuotaExceededError" || name === "NS_ERROR_DOM_QUOTA_REACHED";
}

function defaultFactory(): IDBFactory | undefined {
  try {
    // Reading the global itself can throw (cookies blocked, a sandboxed frame).
    return globalThis.indexedDB ?? undefined;
  } catch {
    return undefined;
  }
}

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("IndexedDB request failed"));
  });
}

function finished(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed"));
    tx.onabort = () => reject(tx.error ?? new Error("IndexedDB transaction aborted"));
  });
}

/** Every key of one account, or the part of its run between two keys. */
function span(account: number, from = 0, to = Number.MAX_SAFE_INTEGER): IDBKeyRange {
  return IDBKeyRange.bound([account, from], [account, to]);
}

/** A cheap size for the size limit. It does not have to be exact, only steady. */
export function sizeOf(line: { html: string; text: string }): number {
  return line.html.length + line.text.length + 96;
}

function isMeta(v: unknown): v is SavedMeta {
  const m = v as Partial<SavedMeta> | null | undefined;
  return (
    !!m &&
    typeof m.account === "number" &&
    typeof m.v === "number" &&
    Number.isFinite(m.nextK) &&
    Number.isFinite(m.firstK) &&
    Number.isFinite(m.count) &&
    Number.isFinite(m.bytes) &&
    Number.isFinite(m.seq) &&
    Number.isFinite(m.savedAt) &&
    typeof m.tab === "string"
  );
}

function isRow(v: unknown): v is Row {
  const r = v as Partial<Row> | null | undefined;
  return (
    !!r &&
    typeof r.k === "number" &&
    typeof r.html === "string" &&
    typeof r.text === "string" &&
    typeof r.type === "string" &&
    Number.isFinite(r.ts)
  );
}

function toSaved(r: Row): SavedLine {
  return { k: r.k, html: r.html, text: r.text, type: r.type, ts: r.ts };
}

export class LogStore {
  private db: IDBDatabase | null = null;
  private opening: Promise<boolean> | null = null;
  private limits: Limits;

  /**
   * Args:
   *   factory: the IndexedDB to use; the browser's own unless a test supplies one.
   *     Null stands for a browser that has none.
   *   clock: epoch milliseconds, for the idle limit.
   *   limits: any of `LIMITS` to change, for a test that does not want to write
   *     twenty thousand lines to see one trimmed.
   */
  constructor(
    private factory: IDBFactory | null = defaultFactory() ?? null,
    private clock: () => number = Date.now,
    limits: Partial<Limits> = {},
  ) {
    this.limits = { ...LIMITS, ...limits };
  }

  /** Whether the database is open right now. */
  get ready(): boolean {
    return this.db !== null;
  }

  /** Open the database. Resolves false, never rejects, when it cannot be had. */
  open(): Promise<boolean> {
    if (this.db) return Promise.resolve(true);
    this.opening ??= this.connect().finally(() => {
      this.opening = null;
    });
    return this.opening;
  }

  /** Close the connection. A later call opens it again. */
  close(): void {
    this.db?.close();
    this.db = null;
  }

  private connect(): Promise<boolean> {
    const factory = this.factory;
    if (!factory) return Promise.resolve(false);
    return new Promise((resolve) => {
      let req: IDBOpenDBRequest;
      try {
        req = factory.open(DB_NAME, DB_VERSION);
      } catch {
        resolve(false);
        return;
      }
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains("lines")) {
          db.createObjectStore("lines", { keyPath: ["account", "k"] });
        }
        if (!db.objectStoreNames.contains("meta")) {
          db.createObjectStore("meta", { keyPath: "account" });
        }
      };
      req.onsuccess = () => {
        const db = req.result;
        // A newer page wants to upgrade the database: step aside for it.
        db.onversionchange = () => {
          db.close();
          if (this.db === db) this.db = null;
        };
        db.onclose = () => {
          if (this.db === db) this.db = null;
        };
        this.db = db;
        resolve(true);
      };
      req.onerror = () => resolve(false);
      // Another tab holds an older version open. This page carries on without
      // saving rather than wait; the connection is still kept if it comes late.
      req.onblocked = () => resolve(false);
    });
  }

  /**
   * Run one transaction over both stores. The work may await only requests it
   * made on these stores: any other wait lets the transaction close under it.
   */
  private async run<T>(
    mode: IDBTransactionMode,
    work: (lines: IDBObjectStore, meta: IDBObjectStore) => Promise<T>,
  ): Promise<T> {
    if (!(await this.open()) || !this.db) throw new StoreUnavailable();
    const start = (db: IDBDatabase): IDBTransaction =>
      db.transaction(["lines", "meta"], mode, { durability: "relaxed" });
    let tx: IDBTransaction;
    try {
      tx = start(this.db);
    } catch {
      // The connection was closing (the browser dropped it). Try a fresh one once.
      this.db = null;
      if (!(await this.open()) || !this.db) throw new StoreUnavailable();
      tx = start(this.db);
    }
    const done = finished(tx);
    try {
      const result = await work(tx.objectStore("lines"), tx.objectStore("meta"));
      await done;
      return result;
    } catch (e) {
      try {
        tx.abort();
      } catch {
        // already finished
      }
      done.catch(() => {});
      throw e;
    }
  }

  /** An account's saved run, or null when it has none this build can show. */
  async meta(account: number): Promise<SavedMeta | null> {
    return this.run("readonly", async (_lines, meta) => {
      const found = await request(meta.get(account));
      return isMeta(found) && found.v === SCHEMA ? found : null;
    });
  }

  /** The newest `limit` lines of an account, oldest first. */
  async tail(account: number, limit: number): Promise<SavedLine[]> {
    return this.run("readonly", async (lines, metaStore) => {
      const meta = await request(metaStore.get(account));
      if (!isMeta(meta) || meta.v !== SCHEMA || meta.count <= 0) return [];
      const from = Math.max(meta.firstK, meta.nextK - limit);
      const rows = await request(lines.getAll(span(account, from, meta.nextK - 1)));
      return rows.filter(isRow).map(toSaved);
    });
  }

  /** Up to `limit` lines that come right before key `k`, oldest first. */
  async before(account: number, k: number, limit: number): Promise<SavedLine[]> {
    return this.run("readonly", async (lines, metaStore) => {
      const meta = await request(metaStore.get(account));
      if (!isMeta(meta) || meta.v !== SCHEMA || meta.count <= 0) return [];
      const to = Math.min(k - 1, meta.nextK - 1);
      const from = Math.max(meta.firstK, to - limit + 1);
      if (to < from) return [];
      const rows = await request(lines.getAll(span(account, from, to)));
      return rows.filter(isRow).map(toSaved);
    });
  }

  /**
   * Save lines after an account's last one, and the place the connection had
   * reached, in one transaction.
   *
   * Args:
   *   account: whose run this is.
   *   add: the lines, oldest first.
   *   cursor: the place the connection had reached when they were all shown;
   *     null leaves the saved place as it was.
   */
  async write(account: number, add: NewLine[], cursor: Cursor | null): Promise<Written> {
    return this.run("readwrite", async (lines, metaStore) => {
      const found = await request(metaStore.get(account));
      const now = this.clock();
      let meta: SavedMeta;
      if (isMeta(found) && found.v === SCHEMA) {
        meta = found;
      } else {
        // Nothing saved yet, or saved under markup this build cannot show.
        if (found) lines.delete(span(account));
        meta = {
          account,
          v: SCHEMA,
          nextK: 1,
          firstK: 1,
          count: 0,
          bytes: 0,
          tab: "",
          seq: 0,
          savedAt: now,
        };
      }
      const keys: number[] = [];
      for (const line of add) {
        const k = meta.nextK++;
        const row: Row = { account, k, html: line.html, text: line.text, type: line.type, ts: line.ts };
        lines.put(row);
        keys.push(k);
        meta.count += 1;
        meta.bytes += sizeOf(line);
      }
      if (cursor) {
        meta.tab = cursor.tab;
        meta.seq = cursor.seq;
      }
      meta.savedAt = now;
      await this.cut(lines, meta, this.limits.lines, Math.floor(this.limits.bytes * 0.8), true);
      metaStore.put(meta);
      return { keys, firstK: meta.firstK, count: meta.count };
    });
  }

  /**
   * Drop lines from the old end of a run until it is within `keepLines` and
   * `keepBytes`. With `onlyWhenOver` it does nothing until the run has passed
   * the limits by their slack, so the work is done in rare large steps.
   */
  private async cut(
    lines: IDBObjectStore,
    meta: SavedMeta,
    keepLines: number,
    keepBytes: number,
    onlyWhenOver: boolean,
  ): Promise<void> {
    if (
      onlyWhenOver &&
      meta.count <= this.limits.lines + this.limits.trimSlack &&
      meta.bytes <= this.limits.bytes
    ) {
      return;
    }
    if (meta.count <= 0) return;
    await new Promise<void>((resolve, reject) => {
      const req = lines.openCursor(span(meta.account, meta.firstK, meta.nextK - 1));
      req.onerror = () => reject(req.error ?? new Error("IndexedDB cursor failed"));
      req.onsuccess = () => {
        const at = req.result;
        if (!at || (meta.count <= keepLines && meta.bytes <= keepBytes)) {
          resolve();
          return;
        }
        const row = at.value as Row;
        meta.bytes -= sizeOf(row);
        meta.count -= 1;
        meta.firstK = row.k + 1;
        at.delete();
        at.continue();
      };
    });
    if (meta.count <= 0) {
      meta.count = 0;
      meta.bytes = 0;
      meta.firstK = meta.nextK;
    }
  }

  /** Make room after a write found the disk full: drop the older half of a run. */
  async shrink(account: number): Promise<void> {
    await this.run("readwrite", async (lines, metaStore) => {
      const found = await request(metaStore.get(account));
      if (!isMeta(found) || found.v !== SCHEMA) return;
      await this.cut(lines, found, Math.floor(found.count / 2), Math.floor(found.bytes / 2), false);
      metaStore.put(found);
    });
  }

  /** Delete one account's saved run. */
  async purge(account: number): Promise<void> {
    await this.run("readwrite", async (lines, meta) => {
      lines.delete(span(account));
      meta.delete(account);
    });
  }

  /** Delete every saved run. */
  async purgeAll(): Promise<void> {
    await this.run("readwrite", async (lines, meta) => {
      lines.clear();
      meta.clear();
    });
  }

  /**
   * Delete the runs nobody has written to for too long, the runs saved under
   * another markup, and the least recently written beyond the accounts kept.
   *
   * Returns:
   *   The accounts whose run was deleted.
   */
  async sweep(): Promise<number[]> {
    return this.run("readwrite", async (lines, metaStore) => {
      const all = (await request(metaStore.getAll())).filter(isMeta);
      const now = this.clock();
      const stale = all.filter((m) => m.v !== SCHEMA || now - m.savedAt > this.limits.idleMs);
      const fresh = all
        .filter((m) => !stale.includes(m))
        .sort((a, b) => b.savedAt - a.savedAt);
      const gone = [...stale, ...fresh.slice(this.limits.accounts)];
      for (const m of gone) {
        lines.delete(span(m.account));
        metaStore.delete(m.account);
      }
      return gone.map((m) => m.account);
    });
  }
}
