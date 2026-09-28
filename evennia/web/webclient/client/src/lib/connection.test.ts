/**
 * The connection store's side of session continuity: it presents the resume
 * token the portal issued, notices a socket that died without a close event,
 * reconnects at once when the page comes back, and does not fight another
 * window for a session the portal handed over.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AzabanConnection } from "./evennia.svelte";

class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 3;
  static made: FakeSocket[] = [];

  readyState = FakeSocket.CONNECTING;
  sent: Record<string, any>[] = [];
  closedWith: number | undefined | null = null;
  onopen: (() => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;

  constructor(public url: string) {
    FakeSocket.made.push(this);
  }

  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }

  close(code?: number): void {
    this.closedWith = code;
    this.readyState = FakeSocket.CLOSED;
  }

  // -- test drivers
  accept(): void {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }

  receive(env: Record<string, any>): void {
    this.onmessage?.({ data: JSON.stringify(env) });
  }

  drop(code = 1006): void {
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.({ code, reason: "" });
  }
}

const latest = () => FakeSocket.made[FakeSocket.made.length - 1];
const listeners: Record<string, (e?: any) => void> = {};
let storage: Map<string, string>;
let visibility = "visible";

beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.made = [];
  storage = new Map();
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("sessionStorage", {
    getItem: (k: string) => storage.get(k) ?? null,
    setItem: (k: string, v: string) => void storage.set(k, v),
  });
  vi.stubGlobal("location", { protocol: "https:", hostname: "example.test" });
  vi.stubGlobal("navigator", { userAgent: "vitest" });
  vi.stubGlobal("window", {
    wsurl: "wss://example.test/ws",
    csessid: "csess",
    addEventListener: (name: string, fn: (e?: any) => void) => (listeners[name] = fn),
  });
  vi.stubGlobal("document", {
    get visibilityState() {
      return visibility;
    },
    addEventListener: (name: string, fn: (e?: any) => void) => (listeners[name] = fn),
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** A connection whose first socket is open and has had the portal's hello. */
function connected(token = "T1") {
  const conn = new AzabanConnection();
  conn.init();
  latest().accept();
  latest().receive({ t: "hello", s: 4, resumed: false, token });
  return conn;
}

describe("resume token", () => {
  it("presents the token the portal issued, and the last seq applied", () => {
    connected("T1");
    expect(storage.get("underspire.client.token")).toBe("T1");
    latest().receive({ t: "text", s: 5, html: "x" });
    latest().drop();
    vi.advanceTimersByTime(3000);
    latest().accept();
    const hello = latest().sent[0];
    expect(hello.t).toBe("hello");
    expect(hello.resume).toEqual({ token: "T1", last_seq: 5 });
  });

  it("keeps the replacement the portal hands out on a resume", () => {
    connected("T1");
    latest().receive({ t: "hello", s: 9, resumed: true, token: "T2" });
    expect(storage.get("underspire.client.token")).toBe("T2");
  });
});

describe("liveness", () => {
  it("probes a quiet socket, and gives it up when no pong comes", () => {
    const conn = connected();
    const first = latest();
    vi.advanceTimersByTime(25000);
    expect(first.sent.at(-1)?.t).toBe("ping");
    vi.advanceTimersByTime(9999);
    expect(first.closedWith).toBeNull();
    vi.advanceTimersByTime(1);
    // Not 1000/1001: those would end the session the portal is holding.
    expect(first.closedWith).toBe(4000);
    expect(conn.state).toBe("closed");
    vi.advanceTimersByTime(500);
    expect(latest()).not.toBe(first);
    expect(conn.state).toBe("connecting");
  });

  it("takes any frame as the answer", () => {
    connected();
    const sock = latest();
    vi.advanceTimersByTime(30000);
    sock.receive({ t: "pong", n: 1 });
    vi.advanceTimersByTime(10000);
    expect(sock.closedWith).toBeNull();
    expect(FakeSocket.made.length).toBe(1);
  });

  it("probes at once when the tab is shown again", () => {
    connected();
    const sock = latest();
    const before = sock.sent.length;
    visibility = "visible";
    listeners.visibilitychange?.();
    expect(sock.sent.length).toBe(before + 1);
    expect(sock.sent.at(-1)?.t).toBe("ping");
  });

  it("cuts a pending backoff short when the network returns", () => {
    connected();
    latest().drop();
    const count = FakeSocket.made.length;
    listeners.online?.();
    expect(FakeSocket.made.length).toBe(count + 1);
  });
});

describe("a session taken over by another window", () => {
  it("raises the quit menu instead of reconnecting", () => {
    const conn = connected();
    latest().drop(4001);
    expect(conn.loggedOut).toBe(true);
    expect(conn.logoutReason).toBe("superseded");
    vi.advanceTimersByTime(60000);
    expect(FakeSocket.made.length).toBe(1);
  });
});
