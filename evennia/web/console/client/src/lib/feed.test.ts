import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearWatch, closeFeed, live, openFeed } from "./feed.svelte";

function liveResponse(...events: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(events.join("")));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function refusedResponse(status: number, body: string, type = "application/json"): Response {
  return new Response(body, { status, headers: { "Content-Type": type } });
}

describe("the authenticated live feed", () => {
  beforeEach(() => {
    closeFeed();
    clearWatch();
    live.connected = false;
    live.refusal = null;
    vi.restoreAllMocks();
  });

  afterEach(() => {
    closeFeed();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("sends the console header and delivers a watch frame", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      liveResponse(
        'id: 7\nevent: watch\ndata: {"t":"watch","s":7,"watch_id":"w1","sessid":4,"account":"Player","at":10,"dir":"in","line":"look"}\n\n',
      ),
    );
    vi.stubGlobal("fetch", fetcher);

    openFeed();

    await vi.waitFor(() => expect(live.watch).toHaveLength(1));
    expect(fetcher).toHaveBeenCalledWith(
      "/api/console/feed/",
      expect.objectContaining({
        credentials: "same-origin",
        headers: expect.objectContaining({
          "X-Evennia-Console": "1",
          Accept: "text/event-stream",
        }),
      }),
    );
    expect(live.watch[0]).toMatchObject({ watch_id: "w1", line: "look", dir: "in" });
  });

  it("stops asking once the server refuses the feed, and keeps the reason", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockImplementation(async () =>
      refusedResponse(
        403,
        JSON.stringify({ detail: "This console session sat idle.", reauthenticate: true }),
      ),
    );
    vi.stubGlobal("fetch", fetcher);

    openFeed();
    await vi.advanceTimersByTimeAsync(0);

    expect(live.refusal).toEqual({
      detail: "This console session sat idle.",
      reauthenticate: true,
    });
    expect(live.connected).toBe(false);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("reads a refusal that is plain text", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn()
      .mockImplementation(async () =>
        refusedResponse(403, "The console feed requires console access.", "text/plain"),
      );
    vi.stubGlobal("fetch", fetcher);

    openFeed();
    await vi.advanceTimersByTimeAsync(0);

    expect(live.refusal).toEqual({
      detail: "The console feed requires console access.",
      reauthenticate: false,
    });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("backs off after server errors instead of asking every second", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockImplementation(async () => new Response("", { status: 500 }));
    vi.stubGlobal("fetch", fetcher);

    openFeed();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetcher).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1999);
    expect(fetcher).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetcher).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(4000);
    expect(fetcher).toHaveBeenCalledTimes(4);
    expect(live.refusal).toBeNull();
  });

  it("never waits longer than the cap between attempts", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockImplementation(async () => new Response("", { status: 503 }));
    vi.stubGlobal("fetch", fetcher);

    openFeed();
    // 1 + 2 + 4 + 8 + 16 seconds of waiting, then every 30 seconds.
    await vi.advanceTimersByTimeAsync(31_000);
    const before = fetcher.mock.calls.length;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(fetcher.mock.calls.length).toBe(before + 1);
  });

  it("keeps the reason when the server closes an open feed, and does not reconnect", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn()
      .mockImplementation(async () =>
        liveResponse(
          'event: closed\ndata: {"t":"closed","reason":"This console session sat idle.","reauthenticate":true}\n\n',
        ),
      );
    vi.stubGlobal("fetch", fetcher);

    openFeed();
    await vi.advanceTimersByTimeAsync(0);

    expect(live.refusal).toEqual({
      detail: "This console session sat idle.",
      reauthenticate: true,
    });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
