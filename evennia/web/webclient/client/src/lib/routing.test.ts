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

    expect(r.process("<i>a</i>", "someone whispers")).toBe(false);
    expect(r.buffers.chatter).toHaveLength(1);
  });

  it("claims a matching line when the route moves it", () => {
    const r = routed([{ pattern: "whispers", label: "chatter", move: true }]);

    expect(r.process("<i>a</i>", "someone whispers")).toBe(true);
    expect(r.buffers.chatter).toHaveLength(1);
  });

  it("claims the line if any matching route moves it", () => {
    const r = routed([
      { pattern: "whispers", label: "chatter" },
      { pattern: "someone", label: "watch", move: true },
    ]);

    expect(r.process("<i>a</i>", "someone whispers")).toBe(true);
    expect(r.buffers.chatter).toHaveLength(1);
    expect(r.buffers.watch).toHaveLength(1);
  });

  it("leaves a non-matching line alone", () => {
    const r = routed([{ pattern: "whispers", label: "chatter", move: true }]);

    expect(r.process("<i>a</i>", "someone shouts")).toBe(false);
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
    expect(r.process("<i>a</i>", "someone whispers")).toBe(false);
  });

  it("matches a pattern that is not valid regex as literal text", () => {
    const r = routed([{ pattern: "cost: 5 (+2", label: "prices" }]);

    expect(r.process("<i>a</i>", "cost: 5 (+2 upkeep")).toBe(false);
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

  it("clears a feed and its badge", () => {
    const r = routed([{ pattern: "whispers", label: "chatter", move: true }]);

    r.process("<i>a</i>", "someone whispers");
    r.clear("chatter");
    expect(r.buffers.chatter).toEqual([]);
    expect(r.unread.chatter).toBe(0);
  });
});
