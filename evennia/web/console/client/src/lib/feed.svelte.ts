/* The live feed.
 *
 * One connection for the whole console. This uses a streaming `fetch` rather
 * than native `EventSource`: every console endpoint requires the scoped
 * `X-Evennia-Console` header, and `EventSource` has no API for sending one.
 * Reconnection and SSE parsing therefore live here as the small price of
 * applying the same authentication boundary to the long-lived request.
 *
 * Nothing in this module touches the DOM. The vanilla client painted lamps by
 * walking `el.lamps` and setting `dataset.state`, which meant the strip only
 * updated if it happened to already be on the page; here the strip reads this
 * state and follows it, whichever panel is open.
 */

const API = "/api/console/";
const HEADER = "X-Evennia-Console";
const RECONNECT_MS = 1000;

/** How many log lines are kept. Past this, the oldest are dropped. */
export const LIVE_LOG_LIMIT = 300;

/** How many captured watch lines are kept, across every watch. */
export const LIVE_WATCH_LIMIT = 500;

export interface Health {
  checks?: Record<string, unknown>;
  degraded?: boolean;
  /** Derived per check: "ok" or "fail", for a lamp to read directly. */
  database?: string;
  io_owner?: string;
}

export interface MetricSample {
  name: string;
  value: number | string;
}

export interface Metrics {
  available?: boolean;
  reason?: string;
  samples?: MetricSample[];
}

export interface LogEntry {
  source: string;
  line: string;
}

export interface WatchEntry {
  /** Unique watch lifetime, so an old transcript cannot reappear on rewatch. */
  watch_id: string;
  sessid: number;
  account: string;
  at: number;
  /** "out" for what the player was shown, "in" for what they typed. */
  dir: string;
  /** Terminal role after protocol noise has been removed. */
  kind?: "output" | "prompt" | "input";
  line: string;
  /** ANSI-rendered, server-sanitized HTML. Output only; input remains text. */
  html?: string;
  /** Whether this frame advances to the next terminal line. */
  newline?: boolean;
}

export const live = $state({
  connected: false,
  health: null as Health | null,
  metrics: null as Metrics | null,
  log: [] as LogEntry[],
  /* Captured session traffic, for whoever started the watch. Held here and
   * nowhere else: this is the only copy on the client, it is dropped when the
   * page closes, and it is never sent anywhere. */
  watch: [] as WatchEntry[],
});

let controller: AbortController | null = null;
let lastSequence = 0;

function readHealth(payload: Health): Health {
  const checks = payload.checks || {};
  const lamps: Record<string, string> = {};
  for (const [name, value] of Object.entries(checks)) {
    lamps[name] = value ? "ok" : "fail";
  }
  return { ...payload, ...lamps };
}

/**
 * Open the feed, once.
 *
 * Args:
 *   onDegraded: Called whenever the server's degraded state changes, so the
 *     rail can say so without this module importing the rail.
 */
export function openFeed(onDegraded?: (degraded: boolean) => void): void {
  if (controller) return;
  controller = new AbortController();
  void runFeed(controller, onDegraded);
}

/** Keep one authenticated stream open, reconnecting after a bounded pause. */
async function runFeed(
  run: AbortController,
  onDegraded?: (degraded: boolean) => void,
): Promise<void> {
  while (controller === run && !run.signal.aborted) {
    try {
      const suffix = lastSequence ? `?since=${lastSequence}` : "";
      const response = await fetch(API + "feed/" + suffix, {
        credentials: "same-origin",
        headers: { [HEADER]: "1", Accept: "text/event-stream" },
        signal: run.signal,
      });
      if (!response.ok || !response.body) {
        throw new Error(`console feed returned ${response.status}`);
      }
      live.connected = true;
      const reconnect = await consume(response.body, run.signal, onDegraded);
      if (!reconnect) {
        if (controller === run) controller = null;
        live.connected = false;
        return;
      }
    } catch {
      if (run.signal.aborted || controller !== run) return;
    }
    live.connected = false;
    await pause(RECONNECT_MS, run.signal);
  }
}

/** Parse one SSE response and return whether an ended connection may retry. */
async function consume(
  body: ReadableStream<Uint8Array>,
  signal: AbortSignal,
  onDegraded?: (degraded: boolean) => void,
): Promise<boolean> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  let topic = "message";
  let eventId = "";
  let data: string[] = [];

  const dispatch = (): boolean => {
    if (!data.length) return true;
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(data.join("\n")) as Record<string, unknown>;
    } catch {
      return true;
    }
    const sequence = Number(eventId || payload.s || 0);
    if (Number.isFinite(sequence)) lastSequence = Math.max(lastSequence, sequence);
    if (topic === "closed") return false;
    deliver(topic, payload, onDegraded);
    return true;
  };

  while (!signal.aborted) {
    const chunk = await reader.read();
    if (chunk.done) return true;
    buffered += decoder.decode(chunk.value, { stream: true });
    let newline = buffered.indexOf("\n");
    while (newline !== -1) {
      const line = buffered.slice(0, newline).replace(/\r$/, "");
      buffered = buffered.slice(newline + 1);
      if (!line) {
        if (!dispatch()) {
          await reader.cancel();
          return false;
        }
        topic = "message";
        eventId = "";
        data = [];
      } else if (!line.startsWith(":")) {
        const separator = line.indexOf(":");
        const field = separator === -1 ? line : line.slice(0, separator);
        const value = separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");
        if (field === "event") topic = value;
        else if (field === "id") eventId = value;
        else if (field === "data") data.push(value);
      }
      newline = buffered.indexOf("\n");
    }
  }
  return false;
}

/** Route one decoded payload into the reactive store. */
function deliver(
  topic: string,
  payload: Record<string, unknown>,
  onDegraded?: (degraded: boolean) => void,
): void {
  if (topic === "health") {
    const health = payload as Health;
    live.health = readHealth(health);
    onDegraded?.(Boolean(health.degraded));
  } else if (topic === "metrics") {
    live.metrics = payload as Metrics;
  } else if (topic === "log") {
    live.log.push(payload as unknown as LogEntry);
    if (live.log.length > LIVE_LOG_LIMIT) {
      live.log.splice(0, live.log.length - LIVE_LOG_LIMIT);
    }
  } else if (topic === "watch") {
    live.watch.push(payload as unknown as WatchEntry);
    if (live.watch.length > LIVE_WATCH_LIMIT) {
      live.watch.splice(0, live.watch.length - LIVE_WATCH_LIMIT);
    }
  }
  live.connected = true;
}

/** Wait before reconnecting, but wake immediately when the feed is closed. */
function pause(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const finish = () => {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timer = window.setTimeout(finish, milliseconds);
    signal.addEventListener("abort", finish, { once: true });
  });
}

/** Forget every captured line. Used when the last watch stops. */
export function clearWatch(): void {
  live.watch.length = 0;
}

/** Drop transcripts whose bounded watch lifetime has ended. */
export function retainWatches(watchIds: Iterable<string>): void {
  const keep = new Set(watchIds);
  for (let index = live.watch.length - 1; index >= 0; index -= 1) {
    if (!keep.has(live.watch[index].watch_id)) live.watch.splice(index, 1);
  }
}

/** Close the feed. Used by tests; the console itself never closes it. */
export function closeFeed(): void {
  controller?.abort();
  controller = null;
  lastSequence = 0;
  live.connected = false;
}
