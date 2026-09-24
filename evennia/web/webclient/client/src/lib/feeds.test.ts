// Feed rules end to end: the routing store plus the scrollback refile.
import { beforeEach, describe, expect, it, vi } from "vitest";

// append() projects HTML to text through the DOM, which this environment lacks.
vi.mock("./text", () => ({ htmlToText: (html: string) => html.replace(/<[^>]*>/g, "") }));

import { Routing, routing } from "./routing.svelte";
import { session } from "./session.svelte";

describe("feed rules", () => {
  it("reads /regex/ and plain text as the settings field says", () => {
    const r = new Routing();
    r.routes = [
      { pattern: "/^\\[nous\\]/", label: "Nous", move: true },
      { pattern: "a.c", label: "dots" },
    ];
    r.sync();
    expect(r.process("x", "[Nous] ping", 1)).toBe(true);
    expect(r.process("x", "abc", 2)).toBe(false);
    expect(r.buffers.dots).toBeUndefined();
    r.process("x", "see a.c here", 3);
    expect(r.buffers.dots).toHaveLength(1);
  });

  it("files nothing for a broken or match-everything pattern", () => {
    const r = new Routing();
    r.routes = [
      { pattern: "/foo(/", label: "broken", move: true },
      { pattern: "/x*/", label: "everything", move: true },
    ];
    r.sync();
    expect(r.process("x", "foo( anything", 1)).toBe(false);
    expect(r.buffers.broken).toBeUndefined();
    expect(r.buffers.everything).toBeUndefined();
  });

  it("keeps a feed's lines when its rule is renamed", () => {
    const r = new Routing();
    r.routes = [{ pattern: "whispers", label: "nous", move: true }];
    r.sync();
    r.process("x", "someone whispers", 1);
    r.routes = [{ ...r.routes[0], label: "Nous" }];
    r.sync();
    expect(r.buffers.Nous).toHaveLength(1);
    expect(r.buffers.nous).toBeUndefined();
  });

  it("does not apply a switched-off rule, but keeps showing what it filed", () => {
    const r = new Routing();
    r.routes = [{ pattern: "whispers", label: "chatter", move: true }];
    r.sync();
    r.process("x", "someone whispers", 1);
    r.setEnabled(0, false);
    expect(r.process("x", "someone whispers", 2)).toBe(false);
    expect(r.buffers.chatter).toHaveLength(1);
    expect(r.labels()).toEqual(["chatter"]);
  });

  it("reorders rules", () => {
    const r = new Routing();
    r.routes = [
      { pattern: "a", label: "A" },
      { pattern: "b", label: "B" },
    ];
    r.sync();
    r.reorder(1, -1);
    expect(r.routes.map((x) => x.label)).toEqual(["B", "A"]);
  });

  it("files a replayed line once", () => {
    const r = new Routing();
    r.routes = [{ pattern: "whispers", label: "chatter" }];
    r.sync();
    r.process("x", "someone whispers", 7);
    r.process("x", "someone whispers", 7);
    expect(r.buffers.chatter).toHaveLength(1);
  });

  it("survives a pattern that throws on a line", () => {
    const r = new Routing();
    r.routes = [{ pattern: "whispers", label: "chatter" }];
    r.sync();
    expect(() => r.process("x", undefined as unknown as string, 1)).not.toThrow();
  });
});

describe("refiling the scrollback", () => {
  beforeEach(() => {
    session.lines = [];
    session.archive = [];
    routing.routes = [];
    routing.sync();
    for (const k of Object.keys(routing.buffers)) routing.clear(k);
  });

  it("gives a new copy rule the matching lines already in the terminal, without a badge", () => {
    session.append("<i>Kessa whispers hi</i>");
    session.append("<i>rain</i>");
    routing.routes = [{ pattern: "whispers", label: "chatter" }];
    routing.setOnSync((fresh) => session.pruneMoved(fresh));
    routing.sync();
    expect(routing.buffers.chatter).toHaveLength(1);
    expect(session.lines).toHaveLength(2); // copy: the terminal keeps it
    expect(routing.unread.chatter ?? 0).toBe(0);
  });

  it("does not refill a cleared feed when an unrelated rule changes", () => {
    session.append("<i>Kessa whispers hi</i>");
    routing.routes = [{ pattern: "whispers", label: "chatter" }];
    routing.setOnSync((fresh) => session.pruneMoved(fresh));
    routing.sync();
    routing.clear("chatter");
    routing.routes = [...routing.routes, { pattern: "shouts", label: "noise" }];
    routing.sync();
    expect(routing.buffers.chatter).toEqual([]);
  });

  it("files a line once when its feed has both a copy and a move rule", () => {
    session.append("<i>Kessa whispers hi</i>");
    routing.routes = [
      { pattern: "whispers", label: "chatter" },
      { pattern: "Kessa", label: "chatter", move: true },
    ];
    routing.setOnSync((fresh) => session.pruneMoved(fresh));
    routing.sync();
    expect(routing.buffers.chatter).toHaveLength(1);
    expect(session.lines).toHaveLength(0);
  });
});

describe("gags and feeds", () => {
  beforeEach(() => {
    session.lines = [];
    session.archive = [];
    routing.routes = [];
    routing.sync();
  });

  it("hide a line from the terminal but still file it into its feed", async () => {
    const { triggers } = await import("./triggers.svelte");
    triggers.gags = [{ pattern: "chatter" }];
    triggers.sync();
    routing.routes = [{ pattern: "chatter", label: "Chat" }];
    routing.sync();
    session.append("<i>some chatter</i>", "text");
    expect(session.lines).toHaveLength(0);
    expect(routing.buffers.Chat).toHaveLength(1);
    // A gag is the player muting chatter, so it is not kept in the record.
    expect(session.archive).toHaveLength(0);
    triggers.gags = [];
    triggers.sync();
  });
});
