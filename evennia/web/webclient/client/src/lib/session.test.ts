import { beforeEach, describe, expect, it } from "vitest";

import { routing, FEED_MAX } from "./routing.svelte";
import { session } from "./session.svelte";
import type { LogLine } from "./session.svelte";

let nextId = 0;
function line(text: string, over: Partial<LogLine> = {}): LogLine {
  return {
    id: nextId++,
    html: `<i>${text}</i>`,
    text,
    type: "text",
    cat: "system",
    ts: 1234,
    ...over,
  };
}

function routes(routes: { pattern: string; label: string; move?: boolean }[]) {
  routing.routes = routes;
  routing.sync();
}

describe("pruneMoved", () => {
  beforeEach(() => {
    session.lines = [];
    routing.routes = [];
    routing.sync();
  });

  it("moves a line a new route claims into its feed, keeping its timestamp", () => {
    session.lines.push(line("someone whispers"));

    routes([{ pattern: "whispers", label: "chatter", move: true }]);
    session.pruneMoved();

    expect(session.lines).toEqual([]);
    expect(routing.buffers.chatter).toHaveLength(1);
    expect(routing.buffers.chatter[0]).toMatchObject({ html: "<i>someone whispers</i>", ts: 1234 });
    expect(routing.unread.chatter).toBe(1);
  });

  it("does not double-file a line already filed to the claiming feed", () => {
    routes([{ pattern: "whispers", label: "chatter" }]);
    const l = line("someone whispers");
    routing.process(l.html, l.text, l.id);
    session.lines.push(l);

    routes([{ pattern: "whispers", label: "chatter", move: true }]);
    session.pruneMoved();

    expect(session.lines).toEqual([]);
    expect(routing.buffers.chatter).toHaveLength(1);
  });

  it("files a line into a claimed feed it was not filed to", () => {
    session.lines.push(line("someone whispers"));

    routes([{ pattern: "whispers", label: "chatter", move: true }]);
    session.pruneMoved();

    expect(session.lines).toEqual([]);
    expect(routing.buffers.chatter).toHaveLength(1);
  });

  it("leaves unclaimed lines and media lines alone", () => {
    session.lines.push(line("quiet text"), line("♪ http://x/y.mp3", { type: "media" }));
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
    // deleted (purging the feed), then re-added. The log still holds the line,
    // so the re-filed feed must get it back rather than lose it.
    routes([{ pattern: "whispers", label: "chatter" }]);
    const l = line("someone whispers");
    routing.process(l.html, l.text, l.id);
    session.lines.push(l);

    routes([]);
    expect(routing.buffers.chatter).toBeUndefined();

    routes([{ pattern: "whispers", label: "chatter", move: true }]);
    session.pruneMoved();

    expect(session.lines).toEqual([]);
    expect(routing.buffers.chatter).toHaveLength(1);
    expect(routing.buffers.chatter[0].html).toBe("<i>someone whispers</i>");
  });

  it("keeps the scrollback copy when a full feed trims the backfilled line", () => {
    // A feed holds MAX lines; an older scrollback line it never filed is
    // backfilled, merged by ts and trimmed straight back out. The line must
    // stay in the log: pruneMoved may only drop what the feed actually holds.
    routes([{ pattern: "whispers", label: "chatter", move: true }]);
    for (let i = 0; i < FEED_MAX; i++) {
      routing.process("<i>fresh</i>", "someone whispers", 100000 + i);
    }
    expect(routing.buffers.chatter).toHaveLength(FEED_MAX);

    const l = line("someone whispers", { ts: 1 });
    session.lines.push(l);
    session.pruneMoved();

    expect(session.lines).toHaveLength(1);
    expect(routing.buffers.chatter).toHaveLength(FEED_MAX);
    expect(routing.holds("chatter", l.id)).toBe(false);
  });
});
