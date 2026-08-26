/* How a panel gets its data.
 *
 * Every panel does the same three things: ask the server, report the outcome,
 * and hold what came back. Doing that in twenty-three places is how the vanilla
 * client ended up with twenty-three subtly different loading behaviours, one of
 * which rendered a stale table after a failed request.
 */

import { call } from "./api";
import { report } from "./report";
import { untrack } from "svelte";

/** One panel's data, and whether it has arrived. */
export class Loader<T> {
  value = $state<T | null>(null);
  /** True from the first request until the first answer, not on every reload. */
  loading = $state(true);
  /** True when the last request failed. The previous value is kept, not shown. */
  failed = $state(false);
  /** True while a refresh is in flight after the first answer. */
  refreshing = $state(false);
  #generation = 0;
  #controller: AbortController | null = null;

  /**
   * Fetch, report, and store.
   *
   * A failed request does not overwrite the value with null. An operator who
   * loses a connection mid-session should see the alarm and the last good
   * reading, not the alarm and an empty station.
   */
  async load(path: string, options: { body?: unknown } = {}): Promise<boolean> {
    const generation = ++this.#generation;
    this.#controller?.abort();
    this.#controller = new AbortController();
    // `load` is normally called from a panel effect. Reading `value` directly
    // here would make that effect depend on the value it is about to replace,
    // so every successful reply would schedule the same request again.
    const hasValue = untrack(() => this.value !== null);
    if (!hasValue) this.loading = true;
    else this.refreshing = true;
    const result = await call<Record<string, unknown>>(path, {
      ...options,
      signal: this.#controller.signal,
    });
    if (generation !== this.#generation) return false;
    this.loading = false;
    this.refreshing = false;
    if (result.outcome === "cancelled") return false;
    if (!report(result)) {
      this.failed = true;
      return false;
    }
    this.failed = false;
    this.value = result.payload as T;
    return true;
  }
}

/** GET one panel's listing. */
export function rowsPath(panel: string, params: Record<string, string> = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) query.set(key, value);
  }
  const text = query.toString();
  return `panels/${panel}/rows/${text ? "?" + text : ""}`;
}

/** POST one panel action, reporting the outcome. Returns the payload or null. */
export async function runAction<T = Record<string, unknown>>(
  panel: string,
  name: string,
  body: Record<string, unknown> = {},
): Promise<T | null> {
  const result = await call<Record<string, unknown>>(`panels/${panel}/actions/${name}/`, { body });
  if (!report(result)) return null;
  return (result.payload.result ?? result.payload) as T;
}

/** GET one record's detail. */
export async function loadDetail<T = Record<string, unknown>>(
  panel: string,
  pk: string | number,
): Promise<T | null> {
  const result = await call<Record<string, unknown>>(
    `panels/${panel}/detail/${encodeURIComponent(String(pk))}/`,
  );
  if (!report(result)) return null;
  return (result.payload.record ?? result.payload) as T;
}
