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

describe("the authenticated live feed", () => {
  beforeEach(() => {
    closeFeed();
    clearWatch();
    live.connected = false;
    vi.restoreAllMocks();
  });

  afterEach(() => {
    closeFeed();
    vi.unstubAllGlobals();
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
});
