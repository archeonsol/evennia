// Native WebSocket connection speaking the Azaban shell protocol (azaban.v1).
// Every frame is a typed JSON envelope { t, seq?, re?, ...payload } (see
// .agents/docs/engine-architecture/webclient-protocol.md). No jQuery, no legacy
// evennia.js. Browsers offer permessage-deflate automatically, so the transport
// is compressed for free.

export type ConnState = "connecting" | "open" | "closed" | "error";

type EnvHandler = (env: Record<string, any>) => void;

declare global {
  interface Window {
    csessid?: string | false;
    wsurl?: string;
    cuid?: string;
  }
}

const SUBPROTOCOL = "azaban.v1";
const RECONNECT_BASE_MS = 2000;
const RECONNECT_MAX_MS = 30000;

// A socket can die without the browser being told: a phone that changes network
// keeps the old one "open" until TCP gives up, minutes later, and commands typed
// into it go nowhere. The portal pings us, but a page never sees those pings, so
// the page asks for itself: after PROBE_QUIET_MS with nothing received it sends
// a `ping`, and no `pong` within PROBE_TIMEOUT_MS means the socket is dead. The
// reconnect that follows resumes the session, so being wrong costs little.
const PROBE_QUIET_MS = 25000;
const PROBE_TIMEOUT_MS = 10000;
const LIVENESS_TICK_MS = 5000;

// Close codes. 4000: this page gave up on a socket that stopped answering. It is
// not 1000/1001, which end the session: the portal holds the session for the
// reconnect. 4001: the portal handed this session to another connection (a
// copied tab took it over), so reconnecting on our own would take it back.
const CLOSE_ABANDONED = 4000;
const CLOSE_SUPERSEDED = 4001;

// Capabilities this shell announces to the server. As `render` / `patch` /
// `asset` land, flip these on to opt into structured delivery for this session.
const CLIENT_CAPS = {
  rendersNodes: true, // shell renders `render` node payloads (identity anchors)
  // Shell parses node bodies itself (lib/markup.ts, parity-tested against the
  // server's parse_html), so the server omits the duplicate parsed `html`.
  rendersMarkup: true,
  patches: true, // shell holds a reactive scene model fed by `patch` deltas
  batching: true, // shell unwraps `{t:"batch", frames:[...]}` bursts
  assets: false, // TODO: true once the asset channel exists
  images: true,
  theme: true,
  // Shows `help_view` in the help panel; the game then sends help there, not to the log.
  helpPanel: true,
};

// The resume token names *one connection's* session at the portal, which issues
// it in every `hello` reply and replaces it whenever a reconnect takes the
// session over. It belongs to the tab, not the browser: in localStorage every
// tab would present the same token and take over each other's session.
// sessionStorage is per-tab and survives a reload.
const TOKEN_KEY = "underspire.client.token";

function loadClientToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function saveClientToken(token: string): void {
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Storage refused (private mode): resume still works for this page's life.
  }
}

export type RpcErrorCode = "offline" | "timeout" | "error";
export class RpcError extends Error {
  constructor(public code: RpcErrorCode, message: string) {
    super(message);
    this.name = "RpcError";
  }
}

export class AzabanConnection {
  state = $state<ConnState>("connecting");
  loggedOut = $state(false); // server sent a `logout` (e.g. @quit): show the quit menu
  logoutReason = $state("");
  /** Whether the last handshake replayed a buffer rather than starting fresh. */
  resumed = $state(false);

  private ws: WebSocket | null = null;
  private handlers = new Map<string, EnvHandler>();
  private pending = new Map<number, { resolve: (v: any) => void; reject: (e: any) => void }>();
  private seq = 0;
  private everOpen = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempt = 0;
  private manualClose = false;
  // Resumable sessions: the portal's token for our session + the last server seq
  // we applied, sent in `hello` so a reconnect takes the session back and is
  // replayed what it missed.
  private clientToken: string | null = loadClientToken();
  private lastSeq = 0;
  // Liveness: when the last frame arrived, and the pending `pong` deadline.
  private lastRx = 0;
  private probeSeq = 0;
  private probeTimer: ReturnType<typeof setTimeout> | null = null;
  private livenessTimer: ReturnType<typeof setInterval> | null = null;
  private lifecycleBound = false;

  init(): void {
    this.manualClose = false;
    this.watchLifecycle();
    this.open();
  }

  /** Subscribe to a server message type ("text", "prompt", "render", "oob"…). */
  on(type: string, handler: EnvHandler): void {
    this.handlers.set(type, handler);
  }

  /** Send a command line. */
  sendCommand(line: string): void {
    this.sendEnvelope({ t: "cmd", line });
  }

  /** Fire-and-forget typed client action. */
  sendOob(action: string, data?: any): void {
    this.sendEnvelope({ t: "oob", action, data });
  }

  /** Raw OOB with args/kwargs, matching the legacy Evennia.msg(cmd, args, kwargs). */
  sendOobRaw(action: string, args: any[] = [], kwargs: Record<string, any> = {}): void {
    this.sendEnvelope({ t: "oob", action, args, kwargs });
  }

  /**
   * Legacy global-API shim. In-game MXP links render as
   * `<a onclick="Evennia.msg('text',['cmd'],{})">`, so we expose window.Evennia
   * to route those to the live socket. `text` is a command line; anything else
   * is a typed OOB call.
   */
  legacyMsg(cmdname: string, args: any[] = [], kwargs: Record<string, any> = {}): void {
    if (cmdname === "text") {
      const line = Array.isArray(args) ? args[0] : args;
      if (line != null) this.sendCommand(String(line));
    } else {
      this.sendOobRaw(cmdname, Array.isArray(args) ? args : [args], kwargs ?? {});
    }
  }

  /**
   * RPC: resolves with the matching `res` envelope's data, or rejects with a
   * typed RpcError. Rejects on timeout (default 10s) and if the socket is down,
   * so callers never hang on a lost reply.
   */
  request<T = any>(ns: string, action: string, data?: any, timeoutMs = 10000): Promise<T> {
    const seq = ++this.seq;
    return new Promise<T>((resolve, reject) => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        reject(new RpcError("offline", `not connected (${ns}.${action})`));
        return;
      }
      const timer = setTimeout(() => {
        if (this.pending.delete(seq)) {
          reject(new RpcError("timeout", `RPC timed out: ${ns}.${action}`));
        }
      }, timeoutMs);
      this.pending.set(seq, {
        resolve: (v) => {
          clearTimeout(timer);
          resolve(v);
        },
        reject: (e) => {
          clearTimeout(timer);
          reject(e instanceof RpcError ? e : new RpcError("error", String(e ?? "RPC failed")));
        },
      });
      this.sendEnvelope({ t: "req", seq, ns, action, data });
    });
  }

  /**
   * Tell the portal to drop its replay window for this connection.
   *
   * Frames the portal has already sent are replayed on the next handshake, and
   * a reloaded page presents no cursor - so without this a cleared scrollback
   * came straight back on refresh.
   */
  dropReplayBuffer(): void {
    this.sendEnvelope({ t: "resume_reset" });
  }

  close(): void {
    this.manualClose = true;
    this.stopLiveness();
    this.sendEnvelope({ t: "websocket_close" });
    this.ws?.close();
  }

  /** Server-initiated logout (@quit): stop auto-reconnect and raise the quit menu. */
  markLoggedOut(reason = ""): void {
    this.loggedOut = true;
    this.logoutReason = reason;
    this.manualClose = true; // suppress the auto-reconnect on the close that follows
    this.stopLiveness();
    this.ws?.close();
  }

  private sendEnvelope(obj: Record<string, any>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
    }
  }

  private wsUrl(): string {
    const base =
      window.wsurl ||
      `${location.protocol === "https:" ? "wss" : "ws"}://${location.hostname}:4002`;
    const csessid = window.csessid ? String(window.csessid) : "";
    const cuid = window.cuid ? String(window.cuid) : "";
    const browser = encodeURIComponent(navigator.userAgent);
    return `${base}?${csessid}&${cuid}&${browser}`;
  }

  private open(): void {
    this.state = "connecting";
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.wsUrl(), [SUBPROTOCOL]);
    } catch {
      this.state = "error";
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.everOpen = true;
      this.reconnectAttempt = 0;
      this.state = "open";
      this.lastRx = Date.now();
      // Announce our capabilities (replaces the CLIENT_NARRATIVE flag).
      this.sendEnvelope({
        t: "hello",
        client: "azaban-shell",
        caps: CLIENT_CAPS,
        resume: { token: this.clientToken, last_seq: this.lastSeq },
      });
      this.startLiveness();
      this.handlers.get("connection_open")?.({ t: "connection_open" });
    };
    ws.onclose = (ev: CloseEvent) => {
      if (this.ws !== ws) return; // a socket we already gave up on
      this.stopLiveness();
      this.state = "closed";
      if (!this.manualClose && this.everOpen) {
        console.warn("azaban: websocket closed", ev.code, ev.reason || "(no reason)");
      }
      this.failPending();
      if (ev.code === CLOSE_SUPERSEDED && !this.manualClose) {
        // Another window has this session now. Reconnecting on our own would
        // take it back, and that window would do the same.
        this.markLoggedOut("superseded");
        return;
      }
      if (!this.manualClose) this.scheduleReconnect();
    };
    ws.onerror = () => {
      if (this.ws === ws) this.state = "error";
    };
    ws.onmessage = (ev: MessageEvent) => {
      if (this.ws !== ws) return;
      // Anything at all from the portal proves the socket is alive.
      this.lastRx = Date.now();
      this.clearProbe();
      let env: any;
      try {
        env = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (!env || typeof env !== "object") return;
      this.dispatch(env);
    };
  }

  /** Fail any in-flight RPCs so callers don't hang across a disconnect. */
  private failPending(): void {
    for (const [seq, p] of this.pending) {
      this.pending.delete(seq);
      p.reject(new RpcError("offline", "connection closed"));
    }
  }

  private dispatch(env: Record<string, any>): void {
    // The heartbeat answer; its arrival already counted as proof of life.
    if (env.t === "pong") return;
    // The server's `hello` closes the handshake, after any replay, and re-bases
    // our cursor: assign, never max. The server restarts its counter whenever it
    // could not resume us, so a cursor that only ever climbs would sit above
    // everything the new connection will ever send and permanently ask to resume
    // from a seq that no longer exists.
    if (env.t === "hello") {
      this.lastSeq = typeof env.s === "number" ? env.s : 0;
      this.resumed = env.resumed === true;
      // The token for our next reconnect; the one we presented is spent.
      if (typeof env.token === "string" && env.token) {
        this.clientToken = env.token;
        saveClientToken(env.token);
      }
    } else if (typeof env.s === "number" && env.s > this.lastSeq) {
      // Track the highest server seq we've applied, for resume-on-reconnect.
      this.lastSeq = env.s;
    }
    // A batch is a burst coalesced into one frame; unwrap in order. The seq
    // belongs to the batch, so members are dispatched without one.
    if (env.t === "batch") {
      const frames = Array.isArray(env.frames) ? env.frames : [];
      for (const frame of frames) {
        if (frame && typeof frame === "object") this.dispatch(frame);
      }
      return;
    }
    // RPC responses resolve their pending promise.
    if (env.t === "res" && typeof env.re === "number") {
      const p = this.pending.get(env.re);
      if (p) {
        this.pending.delete(env.re);
        env.ok === false ? p.reject(env.error) : p.resolve(env.data);
      }
      return;
    }
    const handler = this.handlers.get(env.t);
    if (handler) {
      // A throwing handler must not kill the socket loop or drop later frames.
      try {
        handler(env);
      } catch (e) {
        console.error("azaban: handler for", env.t, "failed", e);
      }
    }
    // `hello` is handled above at the transport level; nothing else need subscribe.
    else if (import.meta.env?.DEV && env.t !== "hello") {
      // Ignored for forward compatibility, but silence hides a typo'd `t`
      // server-side, so make it visible while developing.
      console.warn("azaban: no handler for frame type", env.t, env);
    }
    // Unhandled types are ignored (forward-compatible).
  }

  private scheduleReconnect(soon = false): void {
    if (this.reconnectTimer || this.manualClose) return;
    // Jitter matters exactly when things are already going wrong: without it,
    // every client reconnects on the same millisecond after a server restart.
    const backoff = Math.min(
      RECONNECT_BASE_MS * 2 ** this.reconnectAttempt,
      RECONNECT_MAX_MS,
    );
    const delay = soon
      ? Math.round(Math.random() * 500)
      : Math.round(backoff * (0.75 + Math.random() * 0.5));
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.state !== "open") this.open();
    }, delay);
  }

  private startLiveness(): void {
    this.stopLiveness();
    this.livenessTimer = setInterval(() => this.probe(false), LIVENESS_TICK_MS);
  }

  private stopLiveness(): void {
    if (this.livenessTimer) {
      clearInterval(this.livenessTimer);
      this.livenessTimer = null;
    }
    this.clearProbe();
  }

  private clearProbe(): void {
    if (this.probeTimer) {
      clearTimeout(this.probeTimer);
      this.probeTimer = null;
    }
  }

  /**
   * Ask the portal whether this socket still works, if it has been quiet (or
   * `now`, when the page has just come back). No answer in time: give it up.
   */
  private probe(now: boolean): void {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN || this.probeTimer) return;
    if (!now && Date.now() - this.lastRx < PROBE_QUIET_MS) return;
    this.sendEnvelope({ t: "ping", n: ++this.probeSeq });
    this.probeTimer = setTimeout(() => {
      this.probeTimer = null;
      if (this.ws === ws) this.abandon(ws);
    }, PROBE_TIMEOUT_MS);
  }

  /**
   * Stop waiting on a socket that no longer answers and reconnect at once. Its
   * handlers are detached first, so whatever it does later changes nothing.
   */
  private abandon(ws: WebSocket): void {
    ws.onopen = ws.onclose = ws.onerror = ws.onmessage = null;
    try {
      ws.close(CLOSE_ABANDONED, "no answer");
    } catch {
      // already closing
    }
    this.ws = null;
    this.stopLiveness();
    this.state = "closed";
    this.failPending();
    this.reconnectAttempt = 0;
    this.scheduleReconnect(true);
  }

  /** Check the link when the page comes back, instead of waiting for a timer. */
  private watchLifecycle(): void {
    if (this.lifecycleBound || typeof window === "undefined") return;
    this.lifecycleBound = true;
    const wake = () => this.wake();
    window.addEventListener("online", wake);
    window.addEventListener("pageshow", (e) => {
      if ((e as PageTransitionEvent).persisted) wake();
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") wake();
    });
  }

  /**
   * The tab is visible again, the network is back, or the page came out of the
   * back-forward cache. A phone that slept may be holding a dead socket, or
   * sitting out a long backoff: probe the one, cut the other short.
   */
  private wake(): void {
    if (this.manualClose) return;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.probe(true);
    } else if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
      this.reconnectAttempt = 0;
      this.open();
    }
  }
}

export const connection = new AzabanConnection();
