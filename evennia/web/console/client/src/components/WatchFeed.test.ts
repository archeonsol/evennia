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

  it("forgets transcript lines when their watch lifetime ends", () => {
    live.watch.push(
      { watch_id: "live", sessid: 1, account: "one", at: 1, dir: "out", line: "keep" },
      { watch_id: "ended", sessid: 1, account: "one", at: 2, dir: "out", line: "drop" },
    );

    retainWatches(["live"]);

    expect(live.watch.map((frame) => frame.line)).toEqual(["keep"]);
  });
});
