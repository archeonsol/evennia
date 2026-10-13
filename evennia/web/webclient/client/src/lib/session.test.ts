import { beforeEach, describe, expect, it, vi } from "vitest";

// append() projects HTML to text through the DOM, which this environment lacks.
vi.mock("./text", () => ({ htmlToText: (html: string) => html.replace(/<[^>]*>/g, "") }));

import { connection } from "./evennia.svelte";
import { routing, FEED_MAX } from "./routing.svelte";
import { MAX_LINES, session } from "./session.svelte";
import { triggers } from "./triggers.svelte";

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

describe("the saved log's side of the record", () => {
  const row = (k: number, text: string, ts = 1_000 + k) => ({ k, html: `<i>${text}</i>`, text, type: "text", ts });

  beforeEach(() => {
    session.lines = [];
    session.archive = [];
    session.attach(null);
    session.follow(true);
    routing.routes = [];
    routing.sync();
  });

  it("tells the saved log about every line in the record, including one a feed took away", () => {
    const heard: string[] = [];
    session.attach({ line: (l) => void heard.push(l.text), cleared: () => {} });
    routes([{ pattern: "whispers", label: "chatter", move: true }]);
    session.append("<i>someone whispers</i>");
    session.append("<i>plain</i>");
    expect(heard).toEqual(["someone whispers", "plain"]);
    expect(session.lines.map((l) => l.text)).toEqual(["plain"]);
  });

  it("does not tell it about a line a gag removed", () => {
    const heard: string[] = [];
    session.attach({ line: (l) => void heard.push(l.text), cleared: () => {} });
    vi.spyOn(triggers, "shouldGag").mockReturnValue(true);
    session.append("gagged");
    vi.restoreAllMocks();
    expect(heard).toEqual([]);
  });

  it("shows a line at the time the server gave", () => {
    session.append("late", "text", 1_234_000);
    expect(session.archive[0].ts).toBe(1_234_000);
    expect(session.lines[0].ts).toBe(1_234_000);
  });

  it("puts saved lines first, in order, under ids below every live one", () => {
    session.append("live one");
    session.append("live two");
    session.restore([row(4, "old a"), row(5, "old b"), row(6, "old c")]);
    expect(session.lines.map((l) => l.text)).toEqual(["old a", "old b", "old c", "live one", "live two"]);
    const ids = session.lines.map((l) => l.id);
    expect(ids.slice(0, 3).every((id) => id < 0)).toBe(true);
    expect(ids).toEqual([...ids].sort((a, b) => a - b));
    expect(new Set(ids).size).toBe(ids.length);
    expect(session.archive.map((l) => l.text)).toEqual(session.lines.map((l) => l.text));
  });

  it("keeps the time and the saved key of each line it brings back", () => {
    session.restore([row(4, "old a", 77)]);
    expect(session.lines[0]).toMatchObject({ text: "old a", ts: 77, sk: 4, type: "text", cat: expect.any(String) });
  });

  it("puts the note after the saved lines and before the live ones, and hands it back", () => {
    session.append("live");
    const note = session.restore([row(1, "old")], "<span>Back</span>");
    expect(session.lines.map((l) => l.text)).toEqual(["old", "Back", "live"]);
    expect(note).toBe(session.lines[1]);
    expect(note?.type).toBe("system");
    expect(note!.id).toBeGreaterThan(session.lines[0].id);
  });

  it("brings back nothing, and adds no note, when there is nothing saved", () => {
    session.append("live");
    expect(session.restore([], "<span>Back</span>")).toBeNull();
    expect(session.lines.map((l) => l.text)).toEqual(["live"]);
  });

  it("does not run the lines it brings back through the listeners", () => {
    const heard = vi.fn();
    session.onLine(heard);
    const hook = vi.fn();
    session.attach({ line: hook, cleared: () => {} });
    session.restore([row(1, "old")], "<span>Back</span>");
    expect(heard).not.toHaveBeenCalled();
    expect(hook).not.toHaveBeenCalled();
  });

  it("files the old lines into the feeds, and out of the terminal if a feed takes them", () => {
    routes([
      { pattern: "whisper", label: "chatter", move: true },
      { pattern: "tells you", label: "tells" },
    ]);
    session.restore([row(1, "Mira whispers"), row(2, "Bo tells you hello"), row(3, "plain")]);
    expect(session.lines.map((l) => l.text)).toEqual(["Bo tells you hello", "plain"]);
    expect(routing.buffers.chatter).toHaveLength(1);
    expect(routing.buffers.tells).toHaveLength(1);
  });

  it("does not light a feed's badge for lines that are only history", () => {
    routes([{ pattern: "whisper", label: "chatter", move: true }]);
    session.restore([row(1, "Mira whispers")]);
    expect(routing.unread.chatter ?? 0).toBe(0);
  });

  it("keeps trimming from the old end, saved lines included", () => {
    session.restore(Array.from({ length: MAX_LINES }, (_, i) => row(i + 1, `old ${i}`)));
    for (let i = 0; i < 700; i++) session.append(`new ${i}`);
    expect(session.archive.length).toBeLessThanOrEqual(MAX_LINES + 500);
    expect(session.archive[0].text).not.toBe("old 0");
    expect(session.archive.at(-1)?.text).toBe("new 699");
    expect(session.lines.length).toBe(session.archive.length);
  });

  it("names the oldest saved line it holds", () => {
    expect(session.oldestSaved()).toBeUndefined();
    session.append("unsaved");
    expect(session.oldestSaved()).toBeUndefined();
    session.restore([row(40, "a"), row(41, "b")]);
    expect(session.oldestSaved()).toBe(40);
    session.prepend([row(30, "older")]);
    expect(session.oldestSaved()).toBe(30);
  });

  it("puts older lines in front, under ids lower still", () => {
    session.restore([row(40, "a"), row(41, "b")]);
    session.prepend([row(38, "x"), row(39, "y")]);
    expect(session.lines.map((l) => l.text)).toEqual(["x", "y", "a", "b"]);
    const ids = session.lines.map((l) => l.id);
    expect(ids).toEqual([...ids].sort((a, b) => a - b));
    expect(new Set(ids).size).toBe(4);
  });

  it("keeps older lines while the reader is away from the bottom, and not once they are back", () => {
    session.restore(Array.from({ length: MAX_LINES }, (_, i) => row(i + 1_000, `old ${i}`)));
    session.follow(false);
    session.prepend(Array.from({ length: 500 }, (_, i) => row(i + 1, `older ${i}`)));
    for (let i = 0; i < 800; i++) session.append(`new ${i}`);
    expect(session.archive[0].text).toBe("older 0");
    session.follow(true);
    session.append("one more");
    expect(session.archive.length).toBeLessThan(MAX_LINES + 800);
    expect(session.archive.at(-1)?.text).toBe("one more");
  });

  it("forgets that any line is saved when told the saved copy is gone", () => {
    session.restore([row(1, "a")]);
    session.append("b");
    session.unsave();
    expect(session.archive.every((l) => l.sk === undefined)).toBe(true);
  });

  it("says, when it is cleared, whether the saved copy is to go too", () => {
    vi.spyOn(connection, "dropReplayBuffer").mockImplementation(() => {});
    const cleared = vi.fn();
    session.attach({ line: () => {}, cleared });
    session.append("a");
    session.clear();
    session.clear({ saved: true });
    expect(cleared.mock.calls).toEqual([[{ saved: false }], [{ saved: true }]]);
    expect(session.lines).toEqual([]);
    expect(session.archive).toEqual([]);
    vi.restoreAllMocks();
  });
});
