import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { chat } from "./chat.svelte";
import { connection } from "./evennia.svelte";
import { migrateAssistPanels } from "./dock.svelte";
import { triggers } from "./triggers.svelte";

function storage(): Map<string, string> {
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  });
  return store;
}

describe("chat staff role", () => {
  beforeEach(() => {
    chat.staff = false;
    chat.staffKnown = false;
    chat.tickets = [];
  });

  it("takes the server's answer over an inbox that arrived earlier", () => {
    // A stray inbox used to make a session staff for good.
    chat.handleOob("ticket_inbox", [], { tickets: [] });
    expect(chat.staff).toBe(true);
    chat.handleOob("ticket_role", [], { staff: false });
    expect(chat.staff).toBe(false);
    expect(chat.staffKnown).toBe(true);
  });

  it("marks staff when the server says so", () => {
    chat.handleOob("ticket_role", [], { staff: true });
    expect(chat.staff).toBe(true);
    expect(chat.staffKnown).toBe(true);
  });

  it("a stray inbox after the role is known does not make a player staff", () => {
    chat.handleOob("ticket_role", [], { staff: false });
    chat.handleOob("ticket_inbox", [], { tickets: [] });
    expect(chat.staff).toBe(false);
  });

  it("does not know the role before the server has said", () => {
    expect(chat.staffKnown).toBe(false);
  });

  it("drops the queue it held when the role is lost", () => {
    chat.handleOob("ticket_role", [], { staff: true });
    chat.handleOob("ticket_inbox", [], { tickets: [{ id: "a", updated: 1 }] });
    chat.ticket = { id: "a", view: "staff", messages: [] };
    chat.ticketHistory = [{ id: "done" }];
    chat.assistTab = "queue";
    chat.handleOob("ticket_role", [], { staff: false });
    expect(chat.tickets).toEqual([]);
    expect(chat.ticket).toBeNull();
    expect(chat.ticketHistory).toEqual([]);
    expect(chat.assistTab).toBe("mine");
  });
});

describe("tab badges", () => {
  beforeEach(() => {
    chat.staff = true;
    chat.staffKnown = true;
    chat.tickets = [];
    chat.queueSeen = {};
    chat.assistFocused = false;
    chat.assistTab = "queue";
    chat.unread = {};
  });

  it("flags unseen queue tickets and clears them once looked at", () => {
    chat.handleOob("ticket_inbox", [], { tickets: [{ id: "a", updated: 10 }] });
    expect(chat.queueUnseen).toBe(1);
    chat.markQueueSeen();
    expect(chat.queueUnseen).toBe(0);
  });

  it("flags a queue ticket that changed after it was seen", () => {
    chat.handleOob("ticket_inbox", [], { tickets: [{ id: "a", updated: 10 }] });
    chat.markQueueSeen();
    chat.handleOob("ticket_inbox", [], { tickets: [{ id: "a", updated: 20 }] });
    expect(chat.queueUnseen).toBe(1);
  });

  it("counts an arrival while the queue panel is open as seen", () => {
    chat.assistFocused = true;
    chat.handleOob("ticket_inbox", [], { tickets: [{ id: "a", updated: 10 }] });
    expect(chat.queueUnseen).toBe(0);
  });

  it("does not count an arrival as seen while the Mine tab shows", () => {
    chat.assistFocused = true;
    chat.assistTab = "mine";
    chat.handleOob("ticket_inbox", [], { tickets: [{ id: "a", updated: 10 }] });
    expect(chat.queueUnseen).toBe(1);
  });

  it("never flags the queue for a player", () => {
    chat.staff = false;
    chat.tickets = [{ id: "a", updated: 10 }];
    expect(chat.queueUnseen).toBe(0);
  });

  it("totals unread across channels", () => {
    chat.handleOob("channel_unread", [], { help: 2, nous: 3 });
    expect(chat.channelsUnseen).toBe(5);
  });
});

describe("ticket views", () => {
  beforeEach(() => {
    chat.resetForLogin();
  });

  it("opens an owner view in Mine, even for staff", () => {
    chat.handleOob("ticket_role", [], { staff: true });
    chat.handleOob("ticket_thread", [], { id: "a", view: "owner", messages: [] });
    expect(chat.myTicket?.id).toBe("a");
    expect(chat.ticket).toBeNull();
    expect(chat.assistTab).toBe("mine");
  });

  it("opens a staff view in the queue", () => {
    chat.handleOob("ticket_role", [], { staff: true });
    chat.handleOob("ticket_thread", [], { id: "b", view: "staff", messages: [] });
    expect(chat.ticket?.id).toBe("b");
    expect(chat.myTicket).toBeNull();
    expect(chat.assistTab).toBe("queue");
  });

  it("shows a player no staff view", () => {
    chat.handleOob("ticket_role", [], { staff: false });
    chat.handleOob("ticket_thread", [], { id: "b", view: "staff", messages: [] });
    expect(chat.ticket).toBeNull();
    expect(chat.myTicket).toBeNull();
  });

  it("shows a row with no view nowhere", () => {
    chat.handleOob("ticket_role", [], { staff: true });
    chat.handleOob("ticket_thread", [], { id: "c", messages: [] });
    expect(chat.ticket).toBeNull();
    expect(chat.myTicket).toBeNull();
  });

  it("appends a line only to the view of its audience", () => {
    chat.handleOob("ticket_role", [], { staff: true });
    chat.ticket = { id: "a", view: "staff", messages: [] };
    chat.myTicket = { id: "a", view: "owner", messages: [] };
    chat.handleOob("ticket_msg", [], { id: "a", audience: "staff", text: "internal", visibility: "internal" });
    chat.handleOob("ticket_msg", [], { id: "a", audience: "owner", text: "for you" });
    expect(chat.ticket.messages.map((m: any) => m.text)).toEqual(["internal"]);
    expect(chat.myTicket.messages.map((m: any) => m.text)).toEqual(["for you"]);
  });
});

describe("ticket state across logins", () => {
  let request: MockInstance<typeof connection.request>;
  beforeEach(() => {
    storage();
    request = vi.spyOn(connection, "request").mockResolvedValue({ tickets: [] });
    chat.resetForLogin();
  });
  afterEach(() => {
    request.mockRestore();
    vi.unstubAllGlobals();
  });

  it("clears the last account's tickets and role", () => {
    chat.handleOob("ticket_role", [], { staff: true, account: 1 });
    chat.handleOob("ticket_inbox", [], { tickets: [{ id: "a", updated: 1 }] });
    chat.myTickets = [{ id: "m", updated: 1 }];
    chat.myTicket = { id: "m", view: "owner", messages: [] };
    chat.ticketHistory = [{ id: "done" }];
    chat.resetForLogin();
    expect([chat.tickets, chat.myTickets, chat.ticket, chat.myTicket, chat.ticketHistory]).toEqual([[], [], null, null, []]);
    expect([chat.staff, chat.staffKnown, chat.account]).toEqual([false, false, null]);
  });

  it("keeps each account's seen marks apart", () => {
    chat.handleOob("ticket_role", [], { staff: false, account: 1 });
    chat.markSeen({ id: "a", updated: 50 });
    chat.handleOob("ticket_role", [], { staff: false, account: 2 });
    expect(chat.seen).toEqual({});
    chat.handleOob("ticket_role", [], { staff: false, account: 1 });
    expect(chat.seen).toEqual({ a: 50 });
  });

  it("shows a staff member's own ticket on the Mine tab", async () => {
    chat.handleOob("ticket_role", [], { staff: true, account: 7 });
    chat.assistTab = "queue";
    request.mockResolvedValueOnce({ id: "m", view: "owner", messages: [] });
    await chat.openMyTicket("m");
    expect(chat.assistTab).toBe("mine");
  });

  it("loads the caller's own tickets when the account is known", () => {
    chat.handleOob("ticket_role", [], { staff: false, account: 7 });
    expect(request).toHaveBeenCalledWith("tickets", "my_tickets", { closed: false, search: "" });
  });
});

describe("terminal echo of a speak line", () => {
  beforeEach(() => {
    chat.resetForLogin();
    chat.channels = [{ key: "ooc", name: "OOC", speakCmd: "xooc" } as any];
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("echoes the sender's own channel line once", () => {
    chat.armEcho("xooc hello");
    expect(chat.takeEcho("ooc", true)).toBe(true);
    expect(chat.takeEcho("ooc", true)).toBe(false);
  });

  it("arms the echo for a line sent through a client alias", () => {
    storage();
    triggers.aliases = [{ name: "o", command: "xooc" } as any];
    triggers.sync();
    chat.armEcho("o hello");
    triggers.aliases = [];
    triggers.sync();
    vi.unstubAllGlobals();
    expect(chat.takeEcho("ooc", true)).toBe(true);
  });

  it("never echoes someone else's line, and keeps waiting for the sender's", () => {
    chat.armEcho("xooc hi");
    expect(chat.takeEcho("ooc", false)).toBe(false);
    expect(chat.takeEcho("ooc", true)).toBe(true);
  });

  it("matches the channel the line was typed for", () => {
    chat.armEcho("xooc hello");
    expect(chat.takeEcho("game", true)).toBe(false);
    expect(chat.takeEcho("ooc", true)).toBe(true);
  });

  it("echoes two quick lines, one each", () => {
    chat.armEcho("xooc one");
    chat.armEcho("xooc two");
    expect([chat.takeEcho("ooc", true), chat.takeEcho("ooc", true), chat.takeEcho("ooc", true)]).toEqual([
      true,
      true,
      false,
    ]);
  });

  it("does not arm for a line that is not a speak verb, or says nothing", () => {
    chat.armEcho("say hello");
    chat.armEcho("xooc   ");
    expect(chat.takeEcho("ooc", true)).toBe(false);
  });

  it("forgets a line whose channel line never came", () => {
    vi.useFakeTimers();
    chat.armEcho("xooc hello");
    vi.advanceTimersByTime(11000);
    expect(chat.takeEcho("ooc", true)).toBe(false);
  });
});

describe("leaving and late answers", () => {
  let request: MockInstance<typeof connection.request>;
  let store: Map<string, string>;
  beforeEach(() => {
    store = storage();
    request = vi.spyOn(connection, "request");
    chat.resetForLogin();
  });
  afterEach(() => {
    request.mockRestore();
    vi.unstubAllGlobals();
  });

  it("forgets the tickets and channel lines of an account that quits", () => {
    chat.handleOob("ticket_role", [], { staff: true });
    chat.ticket = { id: "a", view: "staff", messages: [{ text: "internal" }] };
    chat.handleOob("channels_list", [{ key: "staff", name: "Staff" }], {});
    chat.handleOob("channel_msg", [], { channel: "staff", text: "private", sender: "Mira", ts: 1, msg_id: "m1" });
    chat.pins = { staff: { msgId: "m1", text: "private", by: "Mira" } };
    chat.topics = { staff: "the raid on Thursday" };
    chat.typing = { staff: ["Mira"] };
    chat.mentions = { staff: true };
    chat.unread = { staff: 3 };
    chat.online = { staff: 2 };
    chat.muted = { staff: true };
    chat.readMark = { staff: 1 };
    chat.active = "staff";
    chat.logout();
    expect([chat.ticket, chat.staff, chat.channels, chat.messages]).toEqual([null, false, [], {}]);
    expect([chat.pins, chat.topics, chat.typing, chat.mentions, chat.unread, chat.online, chat.muted, chat.readMark]).toEqual([
      {}, {}, {}, {}, {}, {}, {}, {},
    ]);
    expect(chat.active).toBe("");
  });

  it("drops a view answer for a ticket no longer asked for", async () => {
    chat.handleOob("ticket_role", [], { staff: true });
    let answerFirst: (v: any) => void = () => {};
    request
      .mockImplementationOnce(() => new Promise((resolve) => (answerFirst = resolve)) as any)
      .mockResolvedValueOnce({ message: "", ticket: { id: "y", view: "staff", messages: [] } });
    const first = chat.openTicket("x");
    await chat.openTicket("y");
    answerFirst({ message: "", ticket: { id: "x", view: "staff", messages: [] } });
    await first;
    expect(chat.ticket?.id).toBe("y");
  });

  it("drops a staff answer that lands after the role is gone", async () => {
    chat.handleOob("ticket_role", [], { staff: true });
    let answer: (v: any) => void = () => {};
    request.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)) as any);
    const pending = chat.openTicket("x");
    chat.handleOob("ticket_role", [], { staff: false });
    answer({ message: "", ticket: { id: "x", view: "staff", messages: [] } });
    await pending;
    expect(chat.ticket).toBeNull();
  });

  it("stops showing a ticket the action left unreadable", async () => {
    chat.handleOob("ticket_role", [], { staff: true });
    chat.ticket = { id: "x", view: "staff", messages: [] };
    request.mockResolvedValueOnce({ message: "Approved.", ticket: null });
    const result = await chat.ticketApprove("x");
    expect(result).toEqual({ ok: true, message: "Approved." });
    expect(chat.ticket).toBeNull();
  });

  it("removes the seen maps kept before they were per account", () => {
    store.set("underspire.tickets.seen.v1", '{"a":1}');
    store.set("underspire.queue.seen.v1", '{"b":1}');
    chat.resetForLogin();
    expect([...store.keys()]).toEqual([]);
  });
});

describe("assist panel migration", () => {
  function fakeApi(ids: string[]) {
    const panels = new Map<string, any>();
    const api: any = {
      added: [] as any[],
      getPanel: (id: string) => panels.get(id),
      addPanel: (opts: any) => {
        api.added.push(opts);
        panels.set(opts.id, { id: opts.id, api: { close: () => panels.delete(opts.id) } });
      },
      ids: () => [...panels.keys()],
    };
    for (const id of ids) panels.set(id, { id, api: { close: () => panels.delete(id) } });
    return api;
  }

  it("replaces the old ticket panels with one Assist panel where they were", () => {
    const api = fakeApi(["log", "tickets", "mytickets"]);
    expect(migrateAssistPanels(api)).toBe(true);
    expect(api.ids().sort()).toEqual(["assist", "log"]);
    expect(api.added[0].position).toEqual({ referencePanel: "tickets", direction: "within" });
  });

  it("leaves a layout without them alone", () => {
    const api = fakeApi(["log", "assist"]);
    expect(migrateAssistPanels(api)).toBe(false);
    expect(api.added).toEqual([]);
  });

  it("keeps an Assist panel already there", () => {
    const api = fakeApi(["assist", "mytickets"]);
    migrateAssistPanels(api);
    expect(api.ids()).toEqual(["assist"]);
    expect(api.added).toEqual([]);
  });
});
