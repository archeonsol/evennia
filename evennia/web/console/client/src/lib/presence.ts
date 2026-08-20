/* Proof that the operator is present, not merely signed in.
 *
 * A capability says who you are. This says you are here, and it is the only
 * thing between an unlocked laptop and a REPL. It is never carried in a link,
 * never cached beyond the server's own window, and cleared the moment a
 * confirmed action fails -- because the likeliest reason for that failure is
 * that the window closed.
 */

import { call } from "./api";
import { report } from "./report";
import { local } from "./state.svelte";

/**
 * Ask for the password, and record that it was accepted.
 *
 * Returns:
 *   Whether the caller may proceed. A refusal is reported to the operator, so
 *   callers only need the boolean.
 */
export async function confirmPresence(): Promise<boolean> {
  const password = prompt("Confirm your password to continue.");
  if (!password) return false;
  const result = await call<Record<string, unknown>>("confirm/", { body: { password } });
  if (!report(result)) return false;
  local.confirmed = true;
  return true;
}

/** Run one action that needs presence, asking for it first if necessary. */
export async function withPresence(run: () => Promise<boolean>): Promise<boolean> {
  if (!local.confirmed && !(await confirmPresence())) return false;
  const ok = await run();
  // A confirmed action that failed most likely failed because the window
  // closed. Keeping the flag would make the next attempt fail the same way
  // without ever telling the operator why.
  if (!ok) local.confirmed = false;
  return ok;
}
