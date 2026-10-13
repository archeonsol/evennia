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

describe("a logout", () => {
  it("reads as closed before the socket's close event", () => {
    const conn = connected();
    conn.markLoggedOut("quit");
    expect(conn.state).toBe("closed");
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

describe("starting from a saved place", () => {
  it("asks only for what came after the seq the saved scrollback reached", () => {
    storage.set("underspire.client.token", "T1");
    const conn = new AzabanConnection();
    conn.seedResume({ seq: 17 });
    conn.init();
    latest().accept();
    expect(latest().sent[0].resume).toEqual({ token: "T1", last_seq: 17 });
  });

  it("asks for nothing back when the saved copy is another tab's", () => {
    storage.set("underspire.client.token", "T1");
    const conn = new AzabanConnection();
    conn.seedResume({ replay: false });
    conn.init();
    latest().accept();
    expect(latest().sent[0].resume).toEqual({ token: "T1", last_seq: 0, replay: false });
  });

  it("keeps asking for it until the portal has answered once", () => {
    const conn = new AzabanConnection();
    conn.seedResume({ seq: 9, replay: false });
    conn.init();
    latest().accept();
    latest().drop();
    vi.advanceTimersByTime(3000);
    latest().accept();
    expect(latest().sent[0].resume).toMatchObject({ last_seq: 9, replay: false });
  });

  it("goes back to the plain resume once the portal has answered", () => {
    const conn = new AzabanConnection();
    conn.seedResume({ seq: 9, replay: false });
    conn.init();
    latest().accept();
    latest().receive({ t: "hello", s: 30, resumed: true, token: "T2" });
    latest().receive({ t: "text", s: 31, html: "x" });
    latest().drop();
    vi.advanceTimersByTime(3000);
    latest().accept();
    expect(latest().sent[0].resume).toEqual({ token: "T2", last_seq: 31 });
  });

  it("ignores a seq that is not a number", () => {
    const conn = new AzabanConnection();
    conn.seedResume({ seq: -4 });
    conn.seedResume({ seq: Number.NaN });
    conn.init();
    latest().accept();
    expect(latest().sent[0].resume.last_seq).toBe(0);
  });

  it("reports how far it has applied, and whether the portal has answered", () => {
    const conn = new AzabanConnection();
    conn.init();
    latest().accept();
    expect(conn.helloed).toBe(false);
    expect(conn.cursor).toBe(0);
    latest().receive({ t: "text", s: 3, html: "x" });
    expect(conn.cursor).toBe(3);
    latest().receive({ t: "hello", s: 4, resumed: false, token: "T" });
    expect(conn.helloed).toBe(true);
    expect(conn.cursor).toBe(4);
  });
});

describe("the time of a frame", () => {
  beforeEach(() => vi.setSystemTime(1_700_000_000_000));

  /** What the connection says about the time of each `text` frame it is sent. */
  function times(conn: AzabanConnection): number[] {
    const seen: number[] = [];
    conn.on("text", (env) => void seen.push(conn.frameTime(env)));
    return seen;
  }

  /** A page whose last hello found the portal's clock `ahead` ms ahead of its own. */
  function aheadBy(ahead: number): void {
    const first = new AzabanConnection();
    first.init();
    latest().accept(); // hello sent at 1_700_000_000_000
    vi.setSystemTime(1_700_000_000_200); // the reply takes 200 ms
    latest().receive({ t: "hello", s: 1, resumed: false, token: "T", ts: 1_700_000_000_100 + ahead });
    vi.setSystemTime(1_700_000_100_000);
  }

  it("is now for a frame that arrives live", () => {
    const conn = new AzabanConnection();
    const seen = times(conn);
    conn.init();
    latest().accept();
    latest().receive({ t: "hello", s: 1, resumed: false, token: "T", ts: 1_700_000_000_000 });
    vi.setSystemTime(1_700_000_050_000);
    latest().receive({ t: "text", s: 2, html: "x", ts: 1_700_000_049_900 });
    expect(seen).toEqual([1_700_000_050_000]);
  });

  it("is the portal's time on the browser's clock for a frame the portal replays", () => {
    aheadBy(60_000);
    // The reload's replay arrives before its hello reply.
    const conn = new AzabanConnection();
    const seen = times(conn);
    conn.init();
    latest().accept();
    latest().receive({ t: "text", s: 2, html: "x", ts: 1_700_000_090_000 });
    expect(seen).toEqual([1_700_000_030_000]);
  });

  it("is taken at the middle of the round trip, so a slow reply does not skew it", () => {
    const first = new AzabanConnection();
    first.init();
    latest().accept();
    vi.setSystemTime(1_700_000_002_000);
    latest().receive({ t: "hello", s: 1, resumed: false, token: "T", ts: 1_700_000_001_000 });
    vi.setSystemTime(1_700_000_010_000);
    const conn = new AzabanConnection();
    const seen = times(conn);
    conn.init();
    latest().accept();
    latest().receive({ t: "text", s: 2, html: "x", ts: 1_700_000_005_000 });
    expect(seen).toEqual([1_700_000_005_000]);
  });

  it("is never in the future", () => {
    aheadBy(0);
    const conn = new AzabanConnection();
    const seen = times(conn);
    conn.init();
    latest().accept();
    latest().receive({ t: "text", s: 2, html: "x", ts: 1_700_000_900_000 });
    expect(seen).toEqual([1_700_000_100_000]);
  });

  it("is now for a frame without a time", () => {
    aheadBy(60_000);
    const conn = new AzabanConnection();
    const seen = times(conn);
    conn.init();
    latest().accept();
    latest().receive({ t: "text", s: 2, html: "x" });
    latest().receive({ t: "text", s: 3, html: "x", ts: "soon" });
    latest().receive({ t: "text", s: 4, html: "x", ts: Number.NaN });
    expect(seen).toEqual([1_700_000_100_000, 1_700_000_100_000, 1_700_000_100_000]);
  });

  it("is the browser's own clock again once the hello has answered", () => {
    aheadBy(60_000);
    const conn = new AzabanConnection();
    const seen = times(conn);
    conn.init();
    latest().accept();
    latest().receive({ t: "text", s: 2, html: "replayed", ts: 1_700_000_050_000 });
    latest().receive({ t: "hello", s: 3, resumed: true, token: "T2", ts: 1_700_000_160_000 });
    latest().receive({ t: "text", s: 4, html: "live", ts: 1_700_000_160_000 });
    expect(seen).toEqual([1_699_999_990_000, 1_700_000_100_000]);
  });

  it("is given to the members of a batch, unless a member has its own", () => {
    const conn = new AzabanConnection();
    const seen: unknown[] = [];
    conn.on("text", (env) => seen.push(env.ts));
    conn.init();
    latest().accept();
    latest().receive({
      t: "batch",
      s: 5,
      ts: 1_699_999_999_000,
      frames: [{ t: "text", html: "a" }, { t: "text", html: "b", ts: 1_699_999_998_000 }],
    });
    expect(seen).toEqual([1_699_999_999_000, 1_699_999_998_000]);
  });
});

describe("being logged out", () => {
  it("tells whoever listens, however it came about", () => {
    const conn = new AzabanConnection();
    const heard = vi.fn();
    conn.onLoggedOut(heard);
    conn.init();
    latest().accept();
    conn.markLoggedOut("quit");
    expect(heard).toHaveBeenCalledTimes(1);
    expect(conn.loggedOut).toBe(true);
  });

  it("tells them when another window takes the session", () => {
    const conn = new AzabanConnection();
    const heard = vi.fn();
    conn.onLoggedOut(heard);
    conn.init();
    latest().accept();
    latest().drop(4001);
    expect(heard).toHaveBeenCalledTimes(1);
    expect(conn.logoutReason).toBe("superseded");
  });

  it("is not stopped by a listener that throws", () => {
    const conn = new AzabanConnection();
    vi.spyOn(console, "error").mockImplementation(() => {});
    const after = vi.fn();
    conn.onLoggedOut(() => {
      throw new Error("boom");
    });
    conn.onLoggedOut(after);
    conn.init();
    latest().accept();
    conn.markLoggedOut("quit");
    expect(after).toHaveBeenCalled();
  });
});
