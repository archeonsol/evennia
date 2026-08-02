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
};

// The resume token identifies *one connection's* replay buffer, so it belongs to
// the tab, not the browser. In localStorage every tab presents the same token:
// they overwrite each other's stash on close and a reconnecting tab replays
// another tab's frames into its own log. sessionStorage is per-tab and survives
// a reload, which is exactly the lifetime resume covers.
function loadClientToken(): string {
  const newToken = () =>
    (crypto as any).randomUUID?.() ?? String(Date.now()) + Math.random().toString(36).slice(2);
  try {
    const existing = sessionStorage.getItem("underspire.client.token");
    if (existing) return existing;
    const t = newToken();
    sessionStorage.setItem("underspire.client.token", t);
    return t;
  } catch {
    return newToken();
  }
}

export type RpcErrorCode = "offline" | "timeout" | "error";
export class RpcError extends Error {
  constructor(public code: RpcErrorCode, message: string) {
    super(message);
    this.name = "RpcError";
  }
}

class AzabanConnection {
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
  // Resumable sessions: a stable token + the last server seq we've seen, sent in
  // `hello` so the portal can replay frames missed across a brief disconnect.
  private clientToken = loadClientToken();
  private lastSeq = 0;

  init(): void {
    this.manualClose = false;
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

  close(): void {
    this.manualClose = true;
    this.sendEnvelope({ t: "websocket_close" });
    this.ws?.close();
  }

  /** Server-initiated logout (@quit): stop auto-reconnect and raise the quit menu. */
  markLoggedOut(reason = ""): void {
    this.loggedOut = true;
    this.logoutReason = reason;
    this.manualClose = true; // suppress the auto-reconnect on the close that follows
    this.ws?.close();
  }

  /** Reconnect from the quit menu. */
  reconnect(): void {
    this.loggedOut = false;
    this.logoutReason = "";
    this.manualClose = false;
    this.reconnectAttempt = 0;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.open();
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
      this.everOpen = true;
      this.reconnectAttempt = 0;
      this.state = "open";
      // Announce our capabilities (replaces the CLIENT_NARRATIVE flag).
      this.sendEnvelope({
        t: "hello",
        client: "azaban-shell",
        caps: CLIENT_CAPS,
        resume: { token: this.clientToken, last_seq: this.lastSeq },
      });
      this.handlers.get("connection_open")?.({ t: "connection_open" });
    };
    ws.onclose = (ev: CloseEvent) => {
      this.state = "closed";
      if (!this.manualClose && this.everOpen) {
        console.warn("azaban: websocket closed", ev.code, ev.reason || "(no reason)");
      }
      // Fail any in-flight RPCs so callers don't hang across a disconnect.
      for (const [seq, p] of this.pending) {
        this.pending.delete(seq);
        p.reject(new RpcError("offline", "connection closed"));
      }
      if (!this.manualClose) this.scheduleReconnect();
    };
    ws.onerror = () => {
      this.state = "error";
    };
    ws.onmessage = (ev: MessageEvent) => {
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

  private dispatch(env: Record<string, any>): void {
    // The server's `hello` closes the handshake, after any replay, and re-bases
    // our cursor: assign, never max. The server restarts its counter whenever it
    // could not resume us, so a cursor that only ever climbs would sit above
    // everything the new connection will ever send and permanently ask to resume
    // from a seq that no longer exists.
    if (env.t === "hello") {
      this.lastSeq = typeof env.s === "number" ? env.s : 0;
      this.resumed = env.resumed === true;
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

  private scheduleReconnect(): void {
    if (this.reconnectTimer || this.manualClose) return;
    // Jitter matters exactly when things are already going wrong: without it,
    // every client reconnects on the same millisecond after a server restart.
    const backoff = Math.min(
      RECONNECT_BASE_MS * 2 ** this.reconnectAttempt,
      RECONNECT_MAX_MS,
    );
    const delay = Math.round(backoff * (0.75 + Math.random() * 0.5));
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.state !== "open") this.open();
    }, delay);
  }
}

export const connection = new AzabanConnection();
