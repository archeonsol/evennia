/* Turning one result into what the operator sees.
 *
 * Separate from `api.ts` on purpose: the API layer describes what the server
 * said, and this decides what to do about it. Keeping the two apart is what
 * lets the API layer be tested without a DOM.
 */

import { failureDetail, failureKind, failureLegend, type Result } from "./api";
import { clearNotice, showNotice } from "./state.svelte";

/**
 * Report one result, and say whether it succeeded.
 *
 * Returns:
 *   Whether the caller may use the payload. Every call site is written as
 *   `if (!report(result)) return;` so a failure cannot be read past.
 */
export function report(result: Result<Record<string, unknown>>): boolean {
  if (result.ok) {
    clearNotice();
    return true;
  }
  showNotice(failureKind(result), failureLegend(result), failureDetail(result));
  return false;
}
