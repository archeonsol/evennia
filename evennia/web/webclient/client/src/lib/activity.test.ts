import { describe, expect, it, vi } from "vitest";
import { ActivityFeed, type ActivityEvent, type Snapshot } from "./activity.svelte";

function event(seq: number, stream = "a"): ActivityEvent {
  return { id: `${stream}:${seq}`, seq, ts_ms: seq * 1000, kind: "looc", actor: { kind: "character", id: 1, name: "Mara" }, targets: [], npc_targets: [], target_count: 0, location: { kind: "location", id: 2, name: "Market" }, body: "hello", meta: {} };
}
function snapshot(events: ActivityEvent[] = [], stream = "a"): Snapshot {
  return { stream_id: stream, last_seq: events.at(-1)?.seq ?? 0, events, watches: { characters: [], locations: [] }, role: { allowed: true, can_puppet: true } };
}
function batch(events: ActivityEvent[], stream = "a") {
  return { stream_id: stream, first_seq: events[0].seq, last_seq: events.at(-1)!.seq, events };
}

describe("Activity stream", () => {
  it("coalesces role and mount requests and deduplicates replay", async () => {
    let resolve!: (value: Snapshot) => void;
    const request = vi.fn(() => new Promise<Snapshot>((done) => { resolve = done; }));
    const feed = new ActivityFeed(request);
    feed.setRole({ allowed: true, can_puppet: true });
    feed.open();
    feed.setRole({ allowed: true, can_puppet: true });
    expect(request).toHaveBeenCalledTimes(1);
    feed.batch(batch([event(2), event(3)]));
    resolve(snapshot([event(1), event(2)]));
    await feed.ensureSubscribed();
    expect(feed.events.map((e) => e.seq)).toEqual([1, 2, 3]);
    feed.batch(batch([event(2), event(3)]));
    expect(feed.events).toHaveLength(3);
    expect(feed.gap).toBe(false);
  });

  it("bounds history and detects sequence holes without false snapshot gaps", async () => {
    const feed = new ActivityFeed(async () => snapshot());
    feed.setRole({ allowed: true, can_puppet: false });
    feed.open();
    await feed.ensureSubscribed();
    for (let seq = 1; seq <= 10000; seq += 16) feed.batch(batch(Array.from({ length: Math.min(16, 10001 - seq) }, (_, i) => event(seq + i))));
    expect(feed.events).toHaveLength(1000);
    expect(feed.events[0].seq).toBe(9001);
    expect(feed.gap).toBe(false);
    feed.batch(batch([event(10003)]));
    expect(feed.gap).toBe(true);
  });

  it("refreshes a mounted panel after hub reset and rejects obsolete stream replay", async () => {
    let stream = "a";
    const request = vi.fn(async () => snapshot([event(1, stream)], stream));
    const feed = new ActivityFeed(request);
    feed.setRole({ allowed: true, can_puppet: true });
    feed.open();
    await feed.ensureSubscribed();
    stream = "b";
    feed.setRole({ allowed: true, can_puppet: true });
    await feed.ensureSubscribed();
    expect(feed.events.map((e) => e.id)).toEqual(["b:1"]);
    feed.batch(batch([event(9)], "a"));
    await feed.ensureSubscribed();
    expect(feed.events.map((e) => e.id)).toEqual(["b:1"]);
  });

  it("freezes a cutoff while paused and filters watched actor and location", async () => {
    const feed = new ActivityFeed(async () => snapshot([event(1)]));
    feed.setRole({ allowed: true, can_puppet: true });
    feed.open();
    await feed.ensureSubscribed();
    feed.togglePause();
    feed.batch(batch([event(2)]));
    expect(feed.filtered("all", false, "")).toHaveLength(1);
    feed.watches.characters = [{ id: 1, label: "Mara" }];
    expect(feed.filtered("all", true, "market")).toHaveLength(1);
    expect(feed.filtered("npc", true, "")).toHaveLength(0);
    feed.togglePause();
    expect(feed.filtered("all", false, "")).toHaveLength(2);
  });

  it("revocation and close erase data and ignore stale asynchronous replies", async () => {
    let resolve!: (value: Snapshot) => void;
    const feed = new ActivityFeed(() => new Promise((done) => { resolve = done as typeof resolve; }));
    feed.setRole({ allowed: true, can_puppet: true });
    feed.open();
    const pending = feed.ensureSubscribed();
    feed.setRole({ allowed: false, can_puppet: false });
    resolve(snapshot([event(1)]));
    await pending;
    expect(feed.events).toEqual([]);
    expect(feed.allowed).toBe(false);
    expect(feed.results).toEqual([]);
  });

  it("ignores stale search and mutation replies after a stream change", async () => {
    let stream = "a";
    let resolveSearch!: (value: any) => void;
    let resolveMutation!: (value: any) => void;
    const manifest = vi.fn();
    const request = vi.fn(async (_ns: string, action: string) => {
      if (action === "activity_subscribe") return snapshot([event(1, stream)], stream);
      if (action === "activity_search") return new Promise((done) => { resolveSearch = done; });
      if (action === "puppet_add") return new Promise((done) => { resolveMutation = done; });
    });
    const feed = new ActivityFeed();
    feed.connect(request, manifest);
    feed.setRole({ allowed: true, can_puppet: true });
    feed.open();
    await feed.ensureSubscribed();
    const searching = feed.search("old character");
    const mutating = feed.mutate("puppet_add", { npc_id: 9 });
    stream = "b";
    await feed.ensureSubscribed();
    resolveSearch({ results: [{ id: 9, kind: "npc", name: "Old" }] });
    resolveMutation({ puppets: [{ npc_id: 9 }] });
    await Promise.all([searching, mutating]);
    expect(feed.results).toEqual([]);
    expect(feed.query).toBe("");
    expect(manifest).not.toHaveBeenCalled();
    expect(feed.events[0].id).toBe("b:1");
    expect(feed.busy).toBe(false);
  });

  it("search keeps only the newest query reply", async () => {
    const resolves: ((value: any) => void)[] = [];
    const feed = new ActivityFeed(async (_ns, action) => {
      if (action === "activity_subscribe") return snapshot();
      return new Promise((done) => resolves.push(done));
    });
    feed.setRole({ allowed: true, can_puppet: true });
    feed.open();
    await feed.ensureSubscribed();
    const first = feed.search("first");
    const second = feed.search("second");
    resolves[1]({ results: [{ kind: "location", id: 8, name: "Second" }] });
    await second;
    resolves[0]({ results: [{ kind: "location", id: 7, name: "First" }] });
    await first;
    expect(feed.results.map((ref) => ref.id)).toEqual([8]);
  });

  it("close clears private state and reopen takes a fresh snapshot", async () => {
    const request = vi.fn(async (_ns, action) => action === "activity_subscribe" ? snapshot([event(1)]) : { ok: true });
    const feed = new ActivityFeed(request);
    feed.setRole({ allowed: true, can_puppet: true });
    feed.open();
    await feed.ensureSubscribed();
    feed.close();
    expect(feed.events).toEqual([]);
    expect(feed.watches.characters).toEqual([]);
    expect(request).toHaveBeenCalledWith("activity", "activity_unsubscribe");
    feed.open();
    await feed.ensureSubscribed();
    expect(feed.events).toHaveLength(1);
    feed.logout();
    expect(feed.allowed).toBe(false);
    expect(feed.events).toEqual([]);
  });

  it("filters NPC targets after the general target cap", async () => {
    const row = event(1);
    row.kind = "npc.action";
    row.npc_targets = [{ kind: "npc", id: 99, name: "Toma" }];
    const feed = new ActivityFeed(async () => snapshot([row]));
    feed.setRole({ allowed: true, can_puppet: true });
    feed.open();
    await feed.ensureSubscribed();
    feed.watches.characters = [{ id: 99, label: "Toma" }];
    expect(feed.filtered("npc", true, "Toma")).toHaveLength(1);
  });

  it("marks a reconnect gap when the snapshot byte budget fits no new event", async () => {
    let reply = snapshot([event(100)]);
    const feed = new ActivityFeed(async () => reply);
    feed.setRole({ allowed: true, can_puppet: true });
    feed.open();
    await feed.ensureSubscribed();
    reply = { ...snapshot(), last_seq: 200 };
    await feed.ensureSubscribed();
    expect(feed.gap).toBe(true);
    expect(feed.lastSeq).toBe(200);
  });

  it("a late unsubscribe failure cannot populate a reopened panel", async () => {
    let reject!: (error: Error) => void;
    const feed = new ActivityFeed(async (_ns, action) => {
      if (action === "activity_subscribe") return snapshot([event(1)]);
      return new Promise((_done, fail) => { reject = fail; });
    });
    feed.setRole({ allowed: true, can_puppet: true });
    feed.open();
    await feed.ensureSubscribed();
    feed.close();
    feed.open();
    await feed.ensureSubscribed();
    reject(new Error("Obsolete account failure"));
    await Promise.resolve();
    await Promise.resolve();
    expect(feed.error).toBe("");
  });
});
