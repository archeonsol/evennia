import { describe, expect, it, vi, beforeEach } from "vitest";

import { call, failureDetail, failureKind, failureLegend } from "./api";

/* The five outcomes are the whole point of this layer. An operator has to be
 * able to tell "the operation did not start" from "the outcome is unknown",
 * because the second one forbids a retry. */

function answer(body: unknown, init: { status?: number; headers?: Record<string, string> } = {}) {
  const headers = new Headers(init.headers || {});
  return {
    ok: (init.status ?? 200) < 400,
    status: init.status ?? 200,
    headers,
    json: async () => body,
  } as unknown as Response;
}

describe("call", () => {
  beforeEach(() => {
    document.cookie = "csrftoken=abc123";
  });

  it("reads a listing without a token or a body", async () => {
    const fetcher = vi.fn().mockResolvedValue(answer({ rows: [] }));
    vi.stubGlobal("fetch", fetcher);

    await call("panels/records/rows/");

    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe("/api/console/panels/records/rows/");
    expect(options.method).toBe("GET");
    expect(options.body).toBeUndefined();
    expect(options.headers["X-CSRFToken"]).toBeUndefined();
  });

  it("sends the token only when there is something to write", async () => {
    const fetcher = vi.fn().mockResolvedValue(answer({}));
    vi.stubGlobal("fetch", fetcher);

    await call("panels/records/actions/save/", { body: { id: 1 } });

    const [, options] = fetcher.mock.calls[0];
    expect(options.method).toBe("POST");
    expect(options.headers["X-CSRFToken"]).toBe("abc123");
  });

  it("carries the outcome and the retry flag through verbatim", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        answer(
          { detail: "the game server is not reachable" },
          {
            status: 503,
            headers: { "X-Console-Outcome": "unavailable", "X-Console-Retryable": "true" },
          },
        ),
      ),
    );

    const result = await call("panels/objects/rows/");
    expect(result.ok).toBe(false);
    expect(result.outcome).toBe("unavailable");
    expect(result.retryable).toBe(true);
  });

  it("does not invent a retry flag the server did not send", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer({}, { status: 500 })));
    const result = await call("panels/objects/rows/");
    expect(result.retryable).toBe(false);
  });

  it("survives an answer that is not JSON", async () => {
    const broken = {
      ok: false,
      status: 502,
      headers: new Headers(),
      json: async () => {
        throw new SyntaxError("not json");
      },
    } as unknown as Response;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(broken));

    const result = await call("");
    expect(result.ok).toBe(false);
    expect(result.payload).toEqual({});
  });

  it("reports a request that never left as retryable", async () => {
    // The one case where a retry is unambiguously safe: the server never saw it.
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));
    const result = await call("");
    expect(result.retryable).toBe(true);
    expect(result.outcome).toBe("unavailable");
  });
});

describe("the failure vocabulary", () => {
  const base = { ok: false, status: 500, outcome: "", retryable: false, payload: {} };

  it("forbids a retry when the outcome is unknown", () => {
    const legend = failureLegend({ ...base, outcome: "indeterminate", retryable: true });
    expect(legend).toContain("DO NOT REPEAT");
  });

  it("permits a retry when the operation did not start", () => {
    expect(failureLegend({ ...base, retryable: true })).toContain("YOU CAN REPEAT IT");
  });

  it("does not promise a retry it was not told about", () => {
    expect(failureLegend(base)).toBe("THE OPERATION FAILED.");
  });

  it("treats an outage as attention, not as a fault", () => {
    expect(failureKind({ ...base, outcome: "unavailable" })).toBe("attn");
    expect(failureKind(base)).toBe("fail");
  });

  it("prefers the server's own words", () => {
    expect(failureDetail({ ...base, payload: { detail: "no such model" } })).toBe("no such model");
  });

  it("names the status when the server said nothing", () => {
    expect(failureDetail(base)).toContain("500");
  });
});
