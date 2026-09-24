import { beforeEach, describe, expect, it, vi } from "vitest";

// append() projects HTML to text through the DOM, which this environment lacks.
vi.mock("./text", () => ({ htmlToText: (html: string) => html.replace(/<[^>]*>/g, "") }));

import { routing, FEED_MAX } from "./routing.svelte";
import { session } from "./session.svelte";

function routes(routes: { pattern: string; label: string; move?: boolean }[]) {
  routing.routes = routes;
  routing.sync();
}

describe("pruneMoved", () => {
  beforeEach(() => {
    session.lines = [];
    session.archive = [];
    routing.routes = [];
    routing.sync();
  });

  it("moves a line a new route claims into its feed, keeping its timestamp", () => {
    session.append("<i>someone whispers</i>");
    const ts = session.archive[0].ts;

    routes([{ pattern: "whispers", label: "chatter", move: true }]);
    session.pruneMoved();

    expect(session.lines).toEqual([]);
    expect(routing.buffers.chatter).toHaveLength(1);
    expect(routing.buffers.chatter[0]).toMatchObject({ html: "<i>someone whispers</i>", ts });
    expect(routing.unread.chatter).toBe(1);
  });

  it("keeps a routed-out line in the record even though the terminal drops it", () => {
    routes([{ pattern: "whispers", label: "chatter", move: true }]);
    session.append("<i>someone whispers</i>");

    expect(session.lines).toEqual([]);
    expect(session.archive.map((l) => l.text)).toEqual(["someone whispers"]);
  });

  it("does not double-file a line already filed to the claiming feed", () => {
    routes([{ pattern: "whispers", label: "chatter" }]);
    session.append("<i>someone whispers</i>");

    routes([{ pattern: "whispers", label: "chatter", move: true }]);
    session.pruneMoved();

    expect(session.lines).toEqual([]);
    expect(routing.buffers.chatter).toHaveLength(1);
  });

  it("files a line into a claimed feed it was not filed to", () => {
    session.append("<i>someone whispers</i>");

    routes([{ pattern: "whispers", label: "chatter", move: true }]);
    session.pruneMoved();

    expect(session.lines).toEqual([]);
    expect(routing.buffers.chatter).toHaveLength(1);
  });

  it("leaves unclaimed lines and media lines alone", () => {
    session.append("<i>quiet text</i>");
    session.append("♪ x/y.mp3", "media");
    routes([{ pattern: "whispers", label: "chatter", move: true }]);

    session.pruneMoved();
    expect(session.lines).toHaveLength(2);

    // A media line whose URL matches a move route is still exempt, as in append.
    routes([{ pattern: "mp3", label: "media-feed", move: true }]);
    session.pruneMoved();
    expect(session.lines).toHaveLength(2);
    expect(routing.buffers["media-feed"]).toBeUndefined();
  });

  it("re-files a line whose buffer was purged by a rule edit", () => {
    // The purge-then-re-add cycle: a copy route files the line, the route is
    // deleted (purging the feed), then re-added. The record still holds the
    // line, so the re-filed feed must get it back rather than lose it.
    routes([{ pattern: "whispers", label: "chatter" }]);
    session.append("<i>someone whispers</i>");

    routes([]);
    expect(routing.buffers.chatter).toBeUndefined();

    routes([{ pattern: "whispers", label: "chatter", move: true }]);
    session.pruneMoved();

    expect(session.lines).toEqual([]);
    expect(routing.buffers.chatter).toHaveLength(1);
    expect(routing.buffers.chatter[0].html).toBe("<i>someone whispers</i>");
  });

  it("restores hidden lines to the terminal when the rule stops hiding them", () => {
    routes([{ pattern: "whisper", label: "chatter", move: true }]);
    session.append("<i>one whisper</i>");
    session.append("<i>plain</i>");
    session.append("<i>two whisper</i>");
    expect(session.lines.map((l) => l.text)).toEqual(["plain"]);

    routing.setEnabled(0, false);
    session.pruneMoved();

    // Back where they arrived, between the lines that never left.
    expect(session.lines.map((l) => l.text)).toEqual(["one whisper", "plain", "two whisper"]);
  });

  it("restores hidden lines when the rule is deleted", () => {
    routes([{ pattern: "whispers", label: "chatter", move: true }]);
    session.append("<i>someone whispers</i>");

    routes([]);
    session.pruneMoved();

    expect(session.lines.map((l) => l.text)).toEqual(["someone whispers"]);
  });

  it("does not put a hidden line back after its feed is cleared", () => {
    routes([{ pattern: "whispers", label: "chatter", move: true }]);
    session.append("<i>someone whispers</i>");
    routing.clear("chatter");

    session.pruneMoved();

    expect(session.lines).toEqual([]);
    expect(session.archive).toHaveLength(1);
  });

  it("keeps the scrollback copy when a full feed trims the backfilled line", () => {
    // A feed holds MAX lines; an older scrollback line it never filed is
    // backfilled, merged by ts and trimmed straight back out. The line must
    // stay in the terminal: pruneMoved may only drop what the feed holds.
    session.append("<i>someone whispers</i>");
    session.archive[0].ts = 1;
    routes([{ pattern: "whispers", label: "chatter", move: true }]);
    for (let i = 0; i < FEED_MAX; i++) {
      routing.process("<i>fresh</i>", "someone whispers", 100000 + i);
    }
    expect(routing.buffers.chatter).toHaveLength(FEED_MAX);

    session.pruneMoved();

    expect(session.lines).toHaveLength(1);
    expect(routing.buffers.chatter).toHaveLength(FEED_MAX);
    expect(routing.holds("chatter", session.lines[0].id)).toBe(false);
  });
});
