import { render } from "@testing-library/svelte";
import { afterEach, describe, expect, it } from "vitest";

import { live, retainWatches } from "../lib/feed.svelte";
import WatchFeed from "./WatchFeed.svelte";

describe("WatchFeed", () => {
  afterEach(() => {
    live.watch.splice(0);
  });

  it("shows all active watches but only this operator's transcript", () => {
    live.watch.push(
      {
        watch_id: "watch-mine",
        sessid: 1,
        account: "one",
        at: 1,
        dir: "out",
        line: "mine to see",
      },
      {
        watch_id: "watch-old",
        sessid: 1,
        account: "one",
        at: 2,
        dir: "out",
        line: "not mine",
      },
    );

    const { container } = render(WatchFeed, {
      watches: [
        {
          watch_id: "watch-mine",
          sessid: 1,
          account: "one",
          watcher: "me",
          reason: "first report",
          seconds_left: 60,
          mine: true,
        },
        {
          watch_id: "watch-other",
          sessid: 1,
          account: "two",
          watcher: "other staff",
          reason: "second report",
          seconds_left: 30,
          mine: false,
        },
      ],
    });

    expect(container.textContent).toContain("me");
    expect(container.textContent).toContain("other staff");
    expect(container.textContent).toContain("mine to see");
    expect(container.textContent).not.toContain("not mine");
  });

  it("renders player-facing colour without traffic metadata", () => {
    live.watch.push({
      watch_id: "watch-mine",
      sessid: 1,
      account: "one",
      at: 1,
      dir: "out",
      kind: "output",
      line: "red warning",
      html: '<span class="color-009">red warning</span>',
      newline: true,
    });

    const { container } = render(WatchFeed, {
      watches: [
        {
          watch_id: "watch-mine",
          sessid: 1,
          account: "one",
          watcher: "me",
          reason: "a report",
          seconds_left: 60,
          mine: true,
        },
      ],
    });

    expect(container.querySelector(".shadow-terminal .color-009")?.textContent).toBe(
      "red warning",
    );
    expect(container.textContent).not.toContain("OUTPUT");
    expect(container.textContent).not.toContain("12:00");
  });

  it("keeps simultaneous watches in separate shadow terminals", () => {
    live.watch.push(
      {
        watch_id: "first",
        sessid: 1,
        account: "one",
        at: 1,
        dir: "out",
        kind: "output",
        line: "only first",
        html: "only first",
        newline: true,
      },
      {
        watch_id: "second",
        sessid: 2,
        account: "two",
        at: 2,
        dir: "out",
        kind: "output",
        line: "only second",
        html: "only second",
        newline: true,
      },
    );

    const { container } = render(WatchFeed, {
      watches: [
        {
          watch_id: "first",
          sessid: 1,
          account: "one",
          watcher: "me",
          reason: "first report",
          seconds_left: 60,
          mine: true,
        },
        {
          watch_id: "second",
          sessid: 2,
          account: "two",
          watcher: "me",
          reason: "second report",
          seconds_left: 60,
          mine: true,
        },
      ],
    });

    const terminals = Array.from(container.querySelectorAll(".shadow-terminal"));
    expect(terminals).toHaveLength(2);
    expect(terminals[0].textContent).toContain("only first");
    expect(terminals[0].textContent).not.toContain("only second");
    expect(terminals[1].textContent).toContain("only second");
  });

  it("forgets transcript lines when their watch lifetime ends", () => {
    live.watch.push(
      { watch_id: "live", sessid: 1, account: "one", at: 1, dir: "out", line: "keep" },
      { watch_id: "ended", sessid: 1, account: "one", at: 2, dir: "out", line: "drop" },
    );

    retainWatches(["live"]);

    expect(live.watch.map((frame) => frame.line)).toEqual(["keep"]);
  });
});
