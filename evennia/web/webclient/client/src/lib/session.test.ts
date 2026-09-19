import { beforeEach, describe, expect, it } from "vitest";

import { routing } from "./routing.svelte";
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
    filed: [],
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
    expect(routing.buffers.chatter).toEqual([{ html: "<i>someone whispers</i>", ts: 1234 }]);
    expect(routing.unread.chatter).toBe(1);
  });

  it("does not double-file a line already filed to the claiming feed", () => {
    routes([{ pattern: "whispers", label: "chatter" }]);
    const filed = routing.process("<i>someone whispers</i>", "someone whispers");
    session.lines.push(line("someone whispers", { filed: filed.labels }));

    routes([{ pattern: "whispers", label: "chatter", move: true }]);
    session.pruneMoved();

    expect(session.lines).toEqual([]);
    expect(routing.buffers.chatter).toHaveLength(1);
  });

  it("files a line into a claimed feed it was not filed to", () => {
    session.lines.push(line("someone whispers", { filed: ["other"] }));

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
});
