// Asking the game for what a page needs on connect, when the game may still be away.
//
// The Portal accepts a socket while the Server is restarting or reloading, and it drops every
// request that arrives before the game is back: nothing is queued and nothing answers. A page that
// asked once, as the socket opened, never got its channel list, its role (which the history and the
// feeds are keyed on) or its feed subscription, and stayed that way until it was refreshed.

/** Waits between attempts, in milliseconds: about three minutes in all, then the page gives up. */
export const RESYNC_DELAYS = [3000, 5000, 8000, 13000, 20000, 30000, 30000, 30000, 30000];

export interface ResyncOptions {
  /** Ask the game for the channel list and roles; rejects when no answer came. */
  attempt: () => Promise<unknown>;
  /** Whether this socket is still the one that asked. False ends the retries quietly. */
  stillWanted: () => boolean;
  /** Runs once the game answered after at least one miss, to ask for everything else again. */
  onRecovered: () => void;
  /** Resolves after `ms`. Injected so tests need no clock. */
  wait?: (ms: number) => Promise<void>;
  delays?: number[];
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Ask until the game answers.
 *
 * Returns true when it did, and false when every retry went unanswered. A socket that closed or was
 * replaced meanwhile ends the loop with true: there is nothing left to complain about.
 */
export async function syncUntilAnswered(opts: ResyncOptions): Promise<boolean> {
  const wait = opts.wait ?? sleep;
  const delays = opts.delays ?? RESYNC_DELAYS;
  let missed = false;
  for (let tries = 0; ; tries++) {
    try {
      await opts.attempt();
      if (missed && opts.stillWanted()) opts.onRecovered();
      return true;
    } catch {
      missed = true;
    }
    if (tries >= delays.length) return false;
    await wait(delays[tries]);
    if (!opts.stillWanted()) return true;
  }
}
