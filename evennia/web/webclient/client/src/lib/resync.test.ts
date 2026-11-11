import { describe, expect, it, vi } from "vitest";

import { RESYNC_DELAYS, syncUntilAnswered } from "./resync";

/** An attempt that fails `misses` times and then answers. */
function flaky(misses: number) {
  let calls = 0;
  const attempt = vi.fn(async () => {
    calls++;
    if (calls <= misses) throw new Error("RPC timed out: channels.sync_channels");
    return {};
  });
  return attempt;
}

const noWait = () => vi.fn(async (_ms: number) => {});

describe("asking the game for the channel list on connect", () => {
  it("asks once and leaves everything else alone when the game answers at once", async () => {
    const attempt = flaky(0);
    const onRecovered = vi.fn();
    const wait = noWait();
    const answered = await syncUntilAnswered({ attempt, stillWanted: () => true, onRecovered, wait });
    expect(answered).toBe(true);
    expect(attempt).toHaveBeenCalledTimes(1);
    expect(onRecovered).not.toHaveBeenCalled();
    expect(wait).not.toHaveBeenCalled();
  });

  it("asks again after a miss, then has the page ask for everything else once", async () => {
    const attempt = flaky(3);
    const onRecovered = vi.fn();
    const wait = noWait();
    const answered = await syncUntilAnswered({ attempt, stillWanted: () => true, onRecovered, wait });
    expect(answered).toBe(true);
    expect(attempt).toHaveBeenCalledTimes(4);
    expect(onRecovered).toHaveBeenCalledTimes(1);
    expect(wait.mock.calls.map((c) => c[0])).toEqual(RESYNC_DELAYS.slice(0, 3));
  });

  it("gives up when the game never answers, after the whole schedule", async () => {
    const attempt = flaky(Infinity);
    const onRecovered = vi.fn();
    const wait = noWait();
    const answered = await syncUntilAnswered({ attempt, stillWanted: () => true, onRecovered, wait });
    expect(answered).toBe(false);
    expect(attempt).toHaveBeenCalledTimes(RESYNC_DELAYS.length + 1);
    expect(onRecovered).not.toHaveBeenCalled();
  });

  it("stops quietly when the socket it asked on is gone", async () => {
    const attempt = flaky(Infinity);
    const onRecovered = vi.fn();
    let wanted = true;
    const wait = vi.fn(async () => {
      wanted = false;
    });
    const answered = await syncUntilAnswered({ attempt, stillWanted: () => wanted, onRecovered, wait });
    expect(answered).toBe(true);
    expect(attempt).toHaveBeenCalledTimes(1);
    expect(onRecovered).not.toHaveBeenCalled();
  });

  it("does not ask for the rest on a socket that closed while the answer was on its way", async () => {
    const attempt = flaky(1);
    const onRecovered = vi.fn();
    let wanted = true;
    const wait = vi.fn(async () => {});
    attempt.mockImplementationOnce(async () => {
      throw new Error("timeout");
    });
    attempt.mockImplementationOnce(async () => {
      wanted = false;
      return {};
    });
    const answered = await syncUntilAnswered({ attempt, stillWanted: () => wanted, onRecovered, wait });
    expect(answered).toBe(true);
    expect(onRecovered).not.toHaveBeenCalled();
  });
});
