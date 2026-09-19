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

    expect(r.process("<i>a</i>", "someone whispers").moved).toBe(false);
    expect(r.buffers.chatter).toHaveLength(1);
  });

  it("claims a matching line when the route moves it", () => {
    const r = routed([{ pattern: "whispers", label: "chatter", move: true }]);

    expect(r.process("<i>a</i>", "someone whispers").moved).toBe(true);
    expect(r.buffers.chatter).toHaveLength(1);
  });

  it("claims the line if any matching route moves it", () => {
    const r = routed([
      { pattern: "whispers", label: "chatter" },
      { pattern: "someone", label: "watch", move: true },
    ]);

    expect(r.process("<i>a</i>", "someone whispers").moved).toBe(true);
    expect(r.buffers.chatter).toHaveLength(1);
    expect(r.buffers.watch).toHaveLength(1);
  });

  it("leaves a non-matching line alone", () => {
    const r = routed([{ pattern: "whispers", label: "chatter", move: true }]);

    expect(r.process("<i>a</i>", "someone shouts").moved).toBe(false);
    expect(r.buffers.chatter).toBeUndefined();
  });

  it("counts a moved line as unread until the feed is read", () => {
    const r = routed([{ pattern: "whispers", label: "chatter", move: true }]);

    r.process("<i>a</i>", "someone whispers");
    r.process("<i>b</i>", "someone whispers");
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
    expect(r.process("<i>a</i>", "someone whispers").moved).toBe(false);
  });

  it("matches a pattern that is not valid regex as literal text", () => {
    const r = routed([{ pattern: "cost: 5 (+2", label: "prices" }]);

    expect(r.process("<i>a</i>", "cost: 5 (+2 upkeep").moved).toBe(false);
    expect(r.buffers.prices).toHaveLength(1);
  });

  it("re-tests each line from the start of the pattern", () => {
    // The compiled routes are /g, so a leftover lastIndex from the previous
    // line would make every other match fail.
    const r = routed([{ pattern: "whispers", label: "chatter" }]);

    r.process("<i>a</i>", "someone whispers");
    r.process("<i>b</i>", "someone whispers");
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

    const out = r.process("<i>a</i>", "someone whispers");
    expect(out.labels).toEqual(["chatter"]);
    expect(out.moved).toBe(true);
    expect(r.buffers.chatter).toHaveLength(1);
  });

  it("backfills claimed lines under their original timestamp", () => {
    const r = routed([{ pattern: "whispers", label: "chatter", move: true }]);

    r.backfill([{ label: "chatter", html: "<i>old</i>", ts: 1234 }]);
    expect(r.buffers.chatter).toEqual([{ html: "<i>old</i>", ts: 1234 }]);
    expect(r.unread.chatter).toBe(1);
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

    r.process("<i>a</i>", "someone whispers");
    expect(r.buffers.chatter).toHaveLength(1);

    r.routes = [];
    r.sync();
    expect(r.buffers.chatter).toBeUndefined();
    expect(r.unread.chatter).toBeUndefined();
  });

  it("clears a feed and its badge", () => {
    const r = routed([{ pattern: "whispers", label: "chatter", move: true }]);

    r.process("<i>a</i>", "someone whispers");
    r.clear("chatter");
    expect(r.buffers.chatter).toEqual([]);
    expect(r.unread.chatter).toBe(0);
  });
});
