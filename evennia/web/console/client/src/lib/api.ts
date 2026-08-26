/* The one way the console talks to the server.
 *
 * Every request goes through `call`, and every response is described by the
 * same five-outcome vocabulary the boundary uses. Nothing else in the client
 * calls `fetch`.
 */

const API = "/api/console/";
const HEADER = "X-Evennia-Console";

/** What the server said, in the boundary's own terms. */
export interface Result<T = Record<string, unknown>> {
  ok: boolean;
  status: number;
  /** One of the five outcomes, verbatim from `X-Console-Outcome`. */
  outcome: string;
  /** Whether repeating the operation is safe. Never inferred from the status. */
  retryable: boolean;
  payload: T;
}

export function cookie(name: string): string {
  const match = document.cookie.match(new RegExp(`(^|;\\s*)${name}=([^;]*)`));
  return match ? decodeURIComponent(match[2]) : "";
}

export interface CallOptions {
  body?: unknown;
  signal?: AbortSignal;
}

/**
 * Call one console endpoint.
 *
 * A body makes it a POST and attaches the CSRF token. Nothing else does.
 *
 * This never throws for a failed request. A thrown error would have to be
 * caught by every caller and turned back into a message, and the ones that
 * forgot would show the operator nothing at all.
 */
export async function call<T = Record<string, unknown>>(
  path: string,
  options: CallOptions = {},
): Promise<Result<T>> {
  const headers: Record<string, string> = { [HEADER]: "1", Accept: "application/json" };
  if (options.body) {
    headers["Content-Type"] = "application/json";
    headers["X-CSRFToken"] = cookie("csrftoken");
  }

  let response: Response;
  try {
    response = await fetch(API + path, {
      method: options.body ? "POST" : "GET",
      credentials: "same-origin",
      headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      return {
        ok: false,
        status: 0,
        outcome: "cancelled",
        retryable: false,
        payload: { detail: "A newer request replaced this one." } as T,
      };
    }
    // The request never reached the server, which is the one case where a
    // retry is unambiguously safe.
    return {
      ok: false,
      status: 0,
      outcome: "unavailable",
      retryable: true,
      payload: { detail: "THE CONSOLE CANNOT REACH THE SERVER." } as T,
    };
  }

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  const outcome = response.headers.get("X-Console-Outcome") || "";
  return {
    // HTTP 202 is transport success but console failure: the write may have
    // started and must not flow through a caller's success path.
    ok: response.ok && outcome !== "indeterminate",
    status: response.status,
    outcome,
    retryable: response.headers.get("X-Console-Retryable") === "true",
    payload: (payload || {}) as T,
  };
}

/**
 * Turn one failed result into the sentence an operator has to read.
 *
 * The five outcomes are reported, not collapsed. An operator must be able to
 * tell "the operation did not start" from "the outcome is unknown", because
 * the second one forbids a retry, and a single "something went wrong" makes
 * the two indistinguishable at exactly the moment the difference matters.
 */
export function failureLegend(result: Result<unknown>): string {
  if (result.outcome === "indeterminate") {
    return "OUTCOME UNKNOWN. DO NOT REPEAT THE OPERATION.";
  }
  return result.retryable
    ? "THE OPERATION DID NOT START. YOU CAN REPEAT IT."
    : "THE OPERATION FAILED.";
}

/** The kind of alarm one failure lights: an outage is not a fault. */
export function failureKind(result: Result<unknown>): "attn" | "fail" {
  return result.outcome === "unavailable" ? "attn" : "fail";
}

/** The server's own explanation, or a last-resort one naming the status. */
export function failureDetail(result: Result<Record<string, unknown>>): string {
  const detail = result.payload?.detail;
  if (typeof detail === "string" && detail) return detail;
  return `THE REQUEST FAILED. STATUS ${result.status}.`;
}
