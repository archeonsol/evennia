/* The live feed.
 *
 * One connection for the whole console. `EventSource` reconnects on its own and
 * replays what was missed through `Last-Event-ID`, so there is no retry loop to
 * write here and no gap to paper over.
 *
 * Nothing in this module touches the DOM. The vanilla client painted lamps by
 * walking `el.lamps` and setting `dataset.state`, which meant the strip only
 * updated if it happened to already be on the page; here the strip reads this
 * state and follows it, whichever panel is open.
 */

const API = "/api/console/";

/** How many log lines are kept. Past this, the oldest are dropped. */
export const LIVE_LOG_LIMIT = 300;

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

export const live = $state({
  connected: false,
  health: null as Health | null,
  metrics: null as Metrics | null,
  log: [] as LogEntry[],
});

let source: EventSource | null = null;

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
  if (source) return;
  source = new EventSource(API + "feed/", { withCredentials: true });

  source.addEventListener("health", (event) => {
    const payload = JSON.parse((event as MessageEvent).data) as Health;
    live.health = readHealth(payload);
    live.connected = true;
    onDegraded?.(Boolean(payload.degraded));
  });

  source.addEventListener("metrics", (event) => {
    live.metrics = JSON.parse((event as MessageEvent).data) as Metrics;
    live.connected = true;
  });

  source.addEventListener("log", (event) => {
    live.log.push(JSON.parse((event as MessageEvent).data) as LogEntry);
    if (live.log.length > LIVE_LOG_LIMIT) {
      live.log.splice(0, live.log.length - LIVE_LOG_LIMIT);
    }
    live.connected = true;
  });

  source.onerror = () => {
    // EventSource retries by itself. Show the state rather than intervene.
    live.connected = false;
  };
}

/** Close the feed. Used by tests; the console itself never closes it. */
export function closeFeed(): void {
  source?.close();
  source = null;
  live.connected = false;
}
