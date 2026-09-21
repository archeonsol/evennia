import { describe, expect, it } from "vitest";

import { Routing } from "./routing.svelte";

function routed(routes: { pattern: string; label: string; move?: boolean }[]) {
  const r = new Routing();
  r.routes = routes;
  r.sync();
  return r;
}

describe("routing", () => {
  it("copies a matching line without claiming it", () => {
    const r = routed([{ pattern: "whispers", label: "chatter" }]);

    expect(r.process("<i>a</i>", "someone whispers", 1)).toBe(false);
    expect(r.buffers.chatter).toHaveLength(1);
  });

  it("claims a matching line when the route moves it", () => {
    const r = routed([{ pattern: "whispers", label: "chatter", move: true }]);

    expect(r.process("<i>a</i>", "someone whispers", 1)).toBe(true);
    expect(r.buffers.chatter).toHaveLength(1);
  });

  it("claims the line if any matching route moves it", () => {
    const r = routed([
      { pattern: "whispers", label: "chatter" },
      { pattern: "someone", label: "watch", move: true },
    ]);

    expect(r.process("<i>a</i>", "someone whispers", 1)).toBe(true);
    expect(r.buffers.chatter).toHaveLength(1);
    expect(r.buffers.watch).toHaveLength(1);
  });

  it("leaves a non-matching line alone", () => {
    const r = routed([{ pattern: "whispers", label: "chatter", move: true }]);

    expect(r.process("<i>a</i>", "someone shouts", 1)).toBe(false);
    expect(r.buffers.chatter).toBeUndefined();
  });

  it("counts a moved line as unread until the feed is read", () => {
    const r = routed([{ pattern: "whispers", label: "chatter", move: true }]);

    r.process("<i>a</i>", "someone whispers", 1);
    r.process("<i>b</i>", "someone whispers", 2);
    expect(r.unread.chatter).toBe(2);

    r.markRead("chatter");
    expect(r.unread.chatter).toBe(0);
  });

  it("ignores routes missing a pattern or a label", () => {
    const r = routed([
      { pattern: "", label: "chatter", move: true },
      { pattern: "whispers", label: "  ", move: true },
    ]);

    expect(r.labels()).toEqual([]);
    expect(r.process("<i>a</i>", "someone whispers", 1)).toBe(false);
  });

  it("matches a pattern that is not valid regex as literal text", () => {
    const r = routed([{ pattern: "cost: 5 (+2", label: "prices" }]);

    expect(r.process("<i>a</i>", "cost: 5 (+2 upkeep", 1)).toBe(false);
    expect(r.buffers.prices).toHaveLength(1);
  });

  it("re-tests each line from the start of the pattern", () => {
    // The compiled routes are /g, so a leftover lastIndex from the previous
    // line would make every other match fail.
    const r = routed([{ pattern: "whispers", label: "chatter" }]);

    r.process("<i>a</i>", "someone whispers", 1);
    r.process("<i>b</i>", "someone whispers", 2);
    expect(r.buffers.chatter).toHaveLength(2);
  });

  it("labels track the routes without waiting for a sync", () => {
    // The tabs are a $derived over labels(); reading the compiled cache
    // instead of the reactive routes would freeze the tab bar at mount.
    const r = routed([{ pattern: "whispers", label: "chatter" }]);

    r.routes = [...r.routes, { pattern: "shouts", label: "noise" }];
    expect(r.labels()).toEqual(["chatter", "noise"]);

    r.routes = r.routes.filter((x) => x.label !== "chatter");
    expect(r.labels()).toEqual(["noise"]);
  });

  it("claims returns only the move-route labels that match", () => {
    const r = routed([
      { pattern: "whispers", label: "chatter" },
      { pattern: "shouts", label: "noise", move: true },
      { pattern: "someone", label: "watch", move: true },
    ]);

    expect(r.claims("someone shouts")).toEqual(["noise", "watch"]);
    expect(r.claims("someone whispers")).toEqual(["watch"]);
    expect(r.claims("nobody murmurs")).toEqual([]);
  });

  it("claims leaves no trace in the buffers", () => {
    const r = routed([{ pattern: "shouts", label: "noise", move: true }]);

    r.claims("someone shouts");
    expect(r.buffers.noise).toBeUndefined();
    expect(r.unread.noise).toBeUndefined();
  });

  it("files once per label when several routes share one", () => {
    const r = routed([
      { pattern: "whispers", label: "chatter" },
      { pattern: "someone", label: "chatter", move: true },
    ]);

    expect(r.process("<i>a</i>", "someone whispers", 1)).toBe(true);
    expect(r.buffers.chatter).toHaveLength(1);
  });

  it("backfills claimed lines under their original timestamp", () => {
    const r = routed([{ pattern: "whispers", label: "chatter", move: true }]);

    r.backfill([{ label: "chatter", html: "<i>old</i>", ts: 1234, id: 9 }]);
    expect(r.buffers.chatter).toEqual([{ id: 9, html: "<i>old</i>", ts: 1234 }]);
    expect(r.unread.chatter).toBe(1);
  });

  it("merges backfilled lines into timestamp order", () => {
    const r = routed([{ pattern: "whispers", label: "chatter", move: true }]);

    r.backfill([
      { label: "chatter", html: "<i>10</i>", ts: 10, id: 1 },
      { label: "chatter", html: "<i>30</i>", ts: 30, id: 2 },
    ]);
    r.backfill([{ label: "chatter", html: "<i>20</i>", ts: 20, id: 3 }]);

    expect(r.buffers.chatter.map((l) => l.ts)).toEqual([10, 20, 30]);
  });

  it("trims a full buffer by timestamp, keeping the newest lines", () => {
    const r = routed([{ pattern: "whispers", label: "chatter", move: true }]);
    const fill = Array.from({ length: 300 }, (_, i) => ({
      label: "chatter",
      html: `<i>${i + 1}</i>`,
      ts: i + 1,
      id: i + 1,
    }));
    r.backfill(fill);
    expect(r.buffers.chatter).toHaveLength(300);

    // An older line cannot displace a newer one; a newer one evicts the oldest.
    r.backfill([{ label: "chatter", html: "<i>old</i>", ts: 0, id: 999 }]);
    expect(r.holds("chatter", 999)).toBe(false);
    r.backfill([{ label: "chatter", html: "<i>new</i>", ts: 500, id: 998 }]);
    expect(r.holds("chatter", 998)).toBe(true);
    expect(r.holds("chatter", 1)).toBe(false);
    expect(r.buffers.chatter[0].ts).toBe(2);
  });

  it("holds reports whether a feed has a line, by id", () => {
    const r = routed([{ pattern: "whispers", label: "chatter" }]);

    r.process("<i>a</i>", "someone whispers", 7);
    expect(r.holds("chatter", 7)).toBe(true);
    expect(r.holds("chatter", 8)).toBe(false);
    expect(r.holds("nowhere", 7)).toBe(false);

    r.clear("chatter");
    expect(r.holds("chatter", 7)).toBe(false);
  });

  it("notifies the app on sync, with the new rules already compiled", () => {
    const r = new Routing();
    const movedAtSync: boolean[] = [];
    r.setOnSync(() => movedAtSync.push(r.claims("someone shouts").length > 0));

    r.sync();
    r.routes = [{ pattern: "shouts", label: "noise", move: true }];
    r.sync();
    expect(movedAtSync).toEqual([false, true]);
  });

  it("drops a deleted route's buffer and badge", () => {
    const r = routed([{ pattern: "whispers", label: "chatter" }]);

    r.process("<i>a</i>", "someone whispers", 1);
    expect(r.buffers.chatter).toHaveLength(1);

    r.routes = [];
    r.sync();
    expect(r.buffers.chatter).toBeUndefined();
    expect(r.unread.chatter).toBeUndefined();
  });

  it("clears a feed and its badge", () => {
    const r = routed([{ pattern: "whispers", label: "chatter", move: true }]);

    r.process("<i>a</i>", "someone whispers", 1);
    r.clear("chatter");
    expect(r.buffers.chatter).toEqual([]);
    expect(r.unread.chatter).toBe(0);
  });
});
