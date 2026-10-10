import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { connection } from "./evennia.svelte";
import { notify } from "./notify.svelte";
import { mergeForm } from "./ticketForm";
import type { TicketRow } from "./ticketModel";
import { tickets } from "./tickets.svelte";
import { toasts } from "./toasts.svelte";

function row(over: Partial<TicketRow> = {}): TicketRow {
  return {
    id: "a",
    number: 1,
    ref: "#1",
    kind: "request",
    label: "Player Request",
    approvable: false,
    status: "pending",
    state: "unanswered",
    presence: "online",
    seen: 0,
    account_id: 10,
    account_name: "kade",
    account_online: true,
    requester_name: "Kade",
    assignee: "",
    assignee_id: null,
    priority: 0,
    created: 100,
    updated: 100,
    unanswered_since: 100,
    answered_at: 0,
    waited_minutes: 0,
    clock: "ok",
    preview: "",
    title: "The door",
    subject: "The door",
    sort: [0, 2, 0, -100, -1],
    ...over,
  };
}

type Answer = (ns: string, action: string, data: any) => any;
let answer: Answer;
let calls: { action: string; data: any }[];
let commands: string[];
let pushed: { kind: string; title: string; body: string; open?: () => void }[];

function reset(): void {
  tickets.staff = false;
  tickets.staffKnown = false;
  tickets.accountId = null;
  tickets.duty = true;
  tickets.canHistory = false;
  tickets.lean = false;
  tickets.rows = [];
  tickets.ticket = null;
  tickets.error = "";
  tickets.history = [];
  tickets.myRows = [];
  tickets.myTicket = null;
  tickets.myError = "";
  tickets.queueSeen = {};
  tickets.queueActive = false;
  // The first full queue is the baseline, not news.
  (tickets as any).loaded = false;
}

beforeEach(() => {
  reset();
  calls = [];
  commands = [];
  pushed = [];
  answer = () => ({});
  vi.spyOn(connection, "request").mockImplementation(async (ns: string, action: string, data?: any) => {
    calls.push({ action, data });
    const out = answer(ns, action, data);
    if (out instanceof Error) throw out;
    return out;
  });
  vi.spyOn(connection, "sendCommand").mockImplementation((line: string) => {
    commands.push(line);
  });
  vi.spyOn(toasts, "push").mockImplementation((kind, title, body, _ttl, _speak, open) => {
    pushed.push({ kind, title, body, open });
  });
  vi.spyOn(notify, "ping").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("who the server says this tab is", () => {
  it("takes the role from the server", () => {
    tickets.handleOob("ticket_role", [], { staff: true, account_id: 7, duty: false });
    expect(tickets.staff).toBe(true);
    expect(tickets.staffKnown).toBe(true);
    expect(tickets.accountId).toBe(7);
    expect(tickets.duty).toBe(false);
  });

  it("takes the server's answer over an inbox that arrived earlier", () => {
    // A stray inbox used to make a session staff for good.
    tickets.handleOob("ticket_inbox", [], { tickets: [] });
    expect(tickets.staff).toBe(true);
    tickets.handleOob("ticket_role", [], { staff: false });
    expect(tickets.staff).toBe(false);
    expect(tickets.staffKnown).toBe(true);
  });

  it("does not make a player staff when an inbox arrives after the role is known", () => {
    tickets.handleOob("ticket_role", [], { staff: false });
    tickets.handleOob("ticket_inbox", [], { tickets: [] });
    expect(tickets.staff).toBe(false);
  });
});

describe("asking for the lean protocol", () => {
  it("takes the queue and the role from the one answer", async () => {
    answer = () => ({
      v: 2,
      staff: true,
      account_id: 7,
      duty: true,
      history: true,
      tickets: [row({ id: "late", sort: [0, 2, 0, -1, -1] }), row({ id: "early", sort: [0, 1, 0, 1, 1] })],
    });
    await tickets.hello();
    expect(calls[0].action).toBe("ticket_hello");
    expect(tickets.lean).toBe(true);
    expect(tickets.staff).toBe(true);
    expect(tickets.canHistory).toBe(true);
    expect(tickets.accountId).toBe(7);
    expect(tickets.rows.map((r) => r.id)).toEqual(["early", "late"]);
    expect(pushed).toEqual([]);
  });

  it("is told it is not staff, and holds no queue", async () => {
    answer = () => ({ v: 2, staff: false, account_id: 9, duty: true, history: false });
    await tickets.hello();
    expect(tickets.staff).toBe(false);
    expect(tickets.staffKnown).toBe(true);
    expect(tickets.rows).toEqual([]);
  });

  it("carries on with whole queues when the server does not know the request", async () => {
    answer = () => new Error("not allowed");
    await tickets.hello();
    expect(tickets.lean).toBe(false);
    tickets.handleOob("ticket_role", [], { staff: true });
    tickets.handleOob("ticket_inbox", [], { tickets: [row({ id: "x" })] });
    expect(tickets.rows.map((r) => r.id)).toEqual(["x"]);
  });
});

describe("a change to the queue", () => {
  beforeEach(() => {
    tickets.handleOob("ticket_role", [], { staff: true, account_id: 7 });
    tickets.handleOob("ticket_inbox", [], { tickets: [row({ id: "a" })] });
    pushed.length = 0;
  });

  it("adds a row in the server's order, and tells staff who are on duty", () => {
    tickets.handleOob("ticket_upsert", [], {
      ticket: row({ id: "b", sort: [0, 0, 0, 1, 1], title: "Dov Kessler, the scarred broker", label: "Puppet Request", ref: "#1042", presence: "room", requester_name: "Mira Thane" }),
      why: "new",
      notify: true,
    });
    expect(tickets.rows.map((r) => r.id)).toEqual(["b", "a"]);
    expect(pushed).toHaveLength(1);
    expect(pushed[0].title).toBe("New puppet request #1042");
    expect(pushed[0].body).toBe("Dov Kessler, the scarred broker (Mira Thane is in the room)");
  });

  it("stays quiet for staff the server did not mark to be told", () => {
    tickets.handleOob("ticket_upsert", [], { ticket: row({ id: "b" }), why: "new", notify: false });
    expect(pushed).toEqual([]);
    expect(tickets.rows).toHaveLength(2);
  });

  it("is quiet about a change that is not news", () => {
    tickets.handleOob("ticket_upsert", [], { ticket: row({ id: "a", priority: 3 }), why: "changed", notify: true });
    expect(pushed).toEqual([]);
    expect(tickets.rows[0].priority).toBe(3);
  });

  it("says a closed ticket's player wrote back, not that a ticket is new", () => {
    tickets.handleOob("ticket_upsert", [], { ticket: row({ id: "a", ref: "#1" }), why: "reopened", notify: true });
    expect(pushed[0].title).toBe("Reply on #1");
    expect(pushed[0].body).toBe("Kade wrote back");
  });

  it("drops a ticket that left the queue", () => {
    tickets.handleOob("ticket_remove", [], { id: "a" });
    expect(tickets.rows).toEqual([]);
  });

  it("re-orders when a player comes or goes", () => {
    tickets.handleOob("ticket_upsert", [], { ticket: row({ id: "b", presence: "offline", sort: [0, 4, 0, 5, 5] }), why: "changed", notify: false });
    expect(tickets.rows.map((r) => r.id)).toEqual(["a", "b"]);
    tickets.handleOob("ticket_presence", [], { id: "b", presence: "room", seen: 0, sort: [0, 0, 0, 5, 5], account_online: true });
    expect(tickets.rows.map((r) => r.id)).toEqual(["b", "a"]);
    expect(tickets.rows[0].presence).toBe("room");
  });

  it("keeps the open ticket's header fresh and its conversation", async () => {
    answer = () => row({ id: "a", messages: [{ origin: "player", text: "hi", sender: "Kade", ts: 1 }] } as any);
    await tickets.openStaff("a");
    tickets.handleOob("ticket_upsert", [], { ticket: row({ id: "a", assignee: "Mira", assignee_id: 7 }), why: "changed", notify: false });
    expect(tickets.ticket.assignee).toBe("Mira");
    expect(tickets.ticket.messages).toHaveLength(1);
  });

  it("shows a player's presence change on the open ticket", async () => {
    answer = () => row({ id: "a", presence: "online" });
    await tickets.openStaff("a");
    tickets.handleOob("ticket_presence", [], { id: "a", presence: "away", seen: 55, sort: [0, 3, 0, 1, 1], account_online: false });
    expect(tickets.ticket.presence).toBe("away");
    expect(tickets.ticket.seen).toBe(55);
  });
});

describe("the whole-queue protocol", () => {
  beforeEach(() => {
    tickets.handleOob("ticket_role", [], { staff: true, account_id: 7 });
  });

  it("does not call the first queue news", () => {
    tickets.handleOob("ticket_inbox", [], { tickets: [row({ id: "a" }), row({ id: "b" })] });
    expect(pushed).toEqual([]);
  });

  it("calls a ticket that was not there news, even when the queue was empty before", () => {
    // The first ticket to arrive on an empty queue used to be swallowed.
    tickets.handleOob("ticket_inbox", [], { tickets: [] });
    tickets.handleOob("ticket_inbox", [], { tickets: [row({ id: "a", ref: "#5", label: "Bug Report" })] });
    expect(pushed).toHaveLength(1);
    expect(pushed[0].title).toBe("New bug report #5");
  });

  it("does not repeat news for a ticket it already holds", () => {
    tickets.handleOob("ticket_inbox", [], { tickets: [row({ id: "a" })] });
    tickets.handleOob("ticket_inbox", [], { tickets: [row({ id: "a", priority: 2 })] });
    expect(pushed).toEqual([]);
  });

  it("does not call an answered ticket news", () => {
    tickets.handleOob("ticket_inbox", [], { tickets: [] });
    tickets.handleOob("ticket_inbox", [], { tickets: [row({ id: "w", status: "waiting" })] });
    expect(pushed).toEqual([]);
  });
});

describe("messages", () => {
  const message = (over: Record<string, unknown> = {}) => ({
    id: "a",
    ref: "#1",
    label: "Player Request",
    title: "The door",
    text: "Try it now.",
    sender: "Mira",
    origin: "staff",
    audience: "owner",
    status: "waiting",
    visibility: "player",
    ts: 500,
    ...over,
  });

  it("adds a message to the open staff ticket and updates its row", async () => {
    tickets.handleOob("ticket_role", [], { staff: true });
    tickets.handleOob("ticket_inbox", [], { tickets: [row({ id: "a" })] });
    answer = () => row({ id: "a", messages: [] } as any);
    await tickets.openStaff("a");
    tickets.handleOob("ticket_msg", [], message({ origin: "player", audience: "staff", text: "still stuck", status: "pending" }));
    expect(tickets.ticket.messages.map((m: any) => m.text)).toEqual(["still stuck"]);
    expect(tickets.rows[0].preview).toBe("still stuck");
    expect(tickets.rows[0].updated).toBe(500);
  });

  it("does not put a staff note in a preview", () => {
    tickets.handleOob("ticket_role", [], { staff: true });
    tickets.handleOob("ticket_inbox", [], { tickets: [row({ id: "a", preview: "the real words" })] });
    tickets.handleOob("ticket_msg", [], message({ visibility: "internal", text: "private opinion", audience: "staff", origin: "staff" }));
    expect(tickets.rows[0].preview).toBe("the real words");
  });

  it("marks a player's request unread and says so when staff reply and the panel is closed", () => {
    tickets.handleOob("ticket_role", [], { staff: false });
    tickets.myRows = [row({ id: "a", status: "pending" })];
    tickets.handleOob("ticket_msg", [], message());
    expect(tickets.myRows[0].unread).toBe(true);
    expect(tickets.myRows[0].status).toBe("waiting");
    expect(tickets.myUnread).toBe(1);
    expect(pushed).toHaveLength(1);
    expect(pushed[0].title).toBe("Staff replied: #1 The door");
    expect(pushed[0].body).toBe("Mira: Try it now.");
  });

  it("does not call it unread, or toast, when the player is reading that very request", () => {
    tickets.handleOob("ticket_role", [], { staff: false });
    tickets.myRows = [row({ id: "a", status: "pending" })];
    tickets.myTicket = { ...row({ id: "a" }), messages: [] } as any;
    tickets.handleOob("ticket_msg", [], message());
    expect(tickets.myRows[0].unread).toBeFalsy();
    expect(pushed).toEqual([]);
    expect(tickets.myTicket.messages).toHaveLength(1);
  });

  it("is not news that someone picked the ticket up", () => {
    tickets.handleOob("ticket_role", [], { staff: false });
    tickets.myRows = [row({ id: "a", status: "pending" })];
    tickets.handleOob("ticket_msg", [], message({ origin: "system", news: false, sender: "System", text: "Mira is handling this.", status: "pending" }));
    expect(tickets.myRows[0].unread).toBeFalsy();
    expect(pushed).toEqual([]);
  });

  it("is news that a request was closed", () => {
    tickets.handleOob("ticket_role", [], { staff: false });
    tickets.myRows = [row({ id: "a", status: "pending" })];
    tickets.handleOob("ticket_msg", [], message({ origin: "system", news: true, sender: "System", text: "Closed by Mira.", status: "closed" }));
    expect(tickets.myRows[0].unread).toBe(true);
    expect(pushed[0].title).toBe("Update on #1 The door");
  });

  it("tells the holder a player wrote back", () => {
    tickets.handleOob("ticket_role", [], { staff: true });
    tickets.handleOob("ticket_msg", [], message({ audience: "assignee", origin: "player", sender: "Kade", text: "Hello?" }));
    expect(pushed).toHaveLength(1);
    expect(pushed[0].title).toBe("Kade replied: #1 The door");
  });
});

describe("alerts and the login line", () => {
  it("says a ticket has gone unanswered in plain words", () => {
    tickets.handleOob("ticket_alert", [], { id: "a", ref: "#1042", label: "Player Request", title: "The door", who: "Kade", age_mins: 95, level: "first", held: false });
    expect(pushed[0].title).toBe("#1042 is still unanswered");
    expect(pushed[0].body).toBe("The door (Kade), 95 minutes, nobody has it");
  });

  it("says a ticket has waited too long once it is well past its time", () => {
    tickets.handleOob("ticket_alert", [], { id: "a", ref: "#1042", label: "Player Request", title: "The door", who: "Kade", age_mins: 400, level: "warn", held: true });
    expect(pushed[0].title).toBe("#1042 has waited too long");
    expect(pushed[0].body).toContain("someone has it");
  });

  it("tells a player how many requests hold a reply they have not read", () => {
    tickets.handleOob("ticket_unread", [], { mine: 2 });
    expect(pushed[0].title).toBe("2 requests have a reply you have not read");
    pushed.length = 0;
    tickets.handleOob("ticket_unread", [], { mine: 1 });
    expect(pushed[0].title).toBe("1 request has a reply you have not read");
  });

  it("tells staff what is unanswered and what nobody has", () => {
    tickets.staff = true;
    tickets.handleOob("ticket_unread", [], { mine: 0, unanswered: 4, unclaimed: 3 });
    expect(pushed[0].title).toBe("4 tickets are unanswered");
    expect(pushed[0].body).toBe("3 nobody has picked up.");
  });

  it("says nothing when there is nothing to say", () => {
    tickets.handleOob("ticket_unread", [], { mine: 0 });
    expect(pushed).toEqual([]);
  });

  it("opens the panel when a toast is clicked", () => {
    const opened: string[] = [];
    tickets.setPanelOpener((v) => opened.push(v));
    tickets.handleOob("ticket_unread", [], { mine: 1 });
    pushed[0].open?.();
    expect(opened).toEqual(["mytickets"]);
  });
});

describe("staff actions", () => {
  beforeEach(() => {
    tickets.handleOob("ticket_role", [], { staff: true, account_id: 7 });
  });

  it("sends the id, the action and what it needs, and shows the ticket that comes back", async () => {
    answer = () => ({ message: "Claimed.", ticket: row({ id: "a", assignee: "Mira" }) });
    tickets.ticket = row({ id: "a" });
    const result = await tickets.claim("a", true);
    expect(calls[0]).toEqual({ action: "ticket_act", data: { id: "a", action: "claim", take: true } });
    expect(result).toEqual({ ok: true, message: "Claimed.", puppet: null });
    expect(tickets.ticket.assignee).toBe("Mira");
  });

  it("does not replace a different ticket the staff member has since opened", async () => {
    answer = () => ({ message: "Done.", ticket: row({ id: "a", assignee: "Mira" }) });
    tickets.ticket = row({ id: "b" });
    await tickets.close("a");
    expect(tickets.ticket.id).toBe("b");
  });

  it("reads a refusal as the sentence the server gave", async () => {
    answer = () => new Error("kai has this.");
    const result = await tickets.claim("a");
    expect(result).toEqual({ ok: false, message: "kai has this." });
  });

  it("sends replies, notes, priority, merges and assignments the way the server reads them", async () => {
    answer = () => ({ message: "ok" });
    await tickets.reply("a", "Hello", true);
    await tickets.setPriority("a", 3);
    await tickets.merge("a", "#1042");
    await tickets.assign("a", "Nora");
    await tickets.approve("a", "Fits");
    await tickets.deny("a", "No");
    await tickets.unclaim("a");
    await tickets.reopen("a");
    expect(calls.map((c) => c.data)).toEqual([
      { id: "a", action: "reply", text: "Hello", internal: true },
      { id: "a", action: "priority", value: 3 },
      { id: "a", action: "merge", into: "#1042" },
      { id: "a", action: "assign", to: "Nora" },
      { id: "a", action: "approve", text: "Fits" },
      { id: "a", action: "deny", text: "No" },
      { id: "a", action: "unclaim" },
      { id: "a", action: "reopen" },
    ]);
  });

  it("puppets the NPC only when the server moved us there", async () => {
    answer = () => ({ message: "Claimed.", puppet: { ok: true, message: "Moved you.", command: "@puppet #1412" } });
    await tickets.puppet("a");
    expect(commands).toEqual(["@puppet #1412"]);
    commands.length = 0;
    answer = () => ({ message: "Claimed.", puppet: { ok: false, message: "Go in-game first, then puppet it.", command: "@puppet #1412" } });
    const result = await tickets.puppet("a");
    expect(commands).toEqual([]);
    expect(result.puppet?.ok).toBe(false);
    tickets.enterNpc(result.puppet?.command ?? "");
    expect(commands).toEqual(["@puppet #1412"]);
  });

  it("keeps duty as the server says", async () => {
    answer = () => ({ message: "You are off duty.", duty: false });
    await tickets.setDuty(false);
    expect(calls[0].data).toEqual({ id: "", action: "duty", on: false });
    expect(tickets.duty).toBe(false);
  });

  it("reads the record in pages", async () => {
    answer = (_ns, _action, data) =>
      data.offset ? { tickets: [row({ id: "c" })], has_next: false } : { tickets: [row({ id: "a" }), row({ id: "b" })], has_next: true, capped: false };
    await tickets.loadHistory("door");
    expect(tickets.history.map((r) => r.id)).toEqual(["a", "b"]);
    expect(tickets.historyMore).toBe(true);
    await tickets.loadHistory("door", 2);
    expect(tickets.history.map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(tickets.historyMore).toBe(false);
    expect(calls[0].data).toEqual({ history: true, search: "door", offset: 0 });
  });

  it("fills a saved reply in for the ticket before it is sent", async () => {
    answer = () => ({ text: "Hi Kade, this is #1." });
    expect(await tickets.expandReply("a", "thanks")).toBe("Hi Kade, this is #1.");
    expect(calls[0].data).toEqual({ op: "expand", id: "a", name: "thanks" });
    answer = () => new Error("nope");
    expect(await tickets.expandReply("a", "gone")).toBeNull();
  });
});

describe("a player's own requests", () => {
  it("loads them and keeps the failure when it cannot", async () => {
    answer = () => ({ tickets: [row({ id: "a" })] });
    await tickets.loadMine(false, "");
    expect(tickets.myRows.map((r) => r.id)).toEqual(["a"]);
    expect(calls[0].data).toEqual({ closed: false, search: "" });
    answer = () => new Error("down");
    await tickets.loadMine(true, "door");
    expect(tickets.myError).toBe("down");
    expect(tickets.myRows).toHaveLength(1);
  });

  it("marks a request read when it is opened", async () => {
    tickets.myRows = [row({ id: "a", unread: true }), row({ id: "b", unread: true })];
    answer = () => ({ ...row({ id: "a" }), messages: [] });
    await tickets.openMine("a");
    expect(tickets.myRows.map((r) => r.unread)).toEqual([false, true]);
    expect(tickets.myUnread).toBe(1);
  });

  it("replies and withdraws through the player's own request", async () => {
    answer = () => ({ message: "Sent.", ticket: { ...row({ id: "a" }), messages: [] } });
    const result = await tickets.mineAct("a", "reply", "more");
    expect(result.message).toBe("Sent.");
    expect(calls[0]).toEqual({ action: "my_ticket_act", data: { id: "a", action: "reply", text: "more" } });
    expect(tickets.myTicket.id).toBe("a");
  });

  it("files any kind and shows what it filed", async () => {
    answer = () => ({ message: "Request sent. Staff have been told.", ticket: { ...row({ id: "n" }), messages: [] } });
    const result = await tickets.file({ kind: "request", subject: "Door", text: "It is stuck." });
    expect(calls[0]).toEqual({ action: "ticket_open", data: { kind: "request", subject: "Door", text: "It is stuck." } });
    expect(result.ok).toBe(true);
    expect(tickets.myTicket.id).toBe("n");
    answer = () => new Error("You have filed a lot of requests in the last hour.");
    expect((await tickets.file({ kind: "request", text: "again" })).message).toContain("a lot of requests");
  });

  it("suggests help only for words long enough to mean something", async () => {
    answer = () => ({ topics: [{ key: "combat", summary: "How a fight works." }] });
    expect(await tickets.suggest("hi")).toEqual([]);
    expect(calls).toHaveLength(0);
    expect(await tickets.suggest("how do I fight")).toEqual([{ key: "combat", summary: "How a fight works." }]);
  });
});

describe("the words of the new-request form", () => {
  it("shows the defaults until the game has answered", () => {
    expect(tickets.form.summary).toBe("Brief summary");
    expect(tickets.form.kinds).toHaveLength(4);
  });

  it("takes the game's words, and asks only once", async () => {
    (tickets as any).formLoaded = false;
    answer = () => ({ pick: "Tell us.", summary: "Title", kinds: [{ kind: "request", name: "Ask", hint: "Anything." }] });
    await tickets.loadForm();
    await tickets.loadForm();
    expect(calls.filter((c) => c.action === "ticket_form")).toHaveLength(1);
    expect(tickets.form.pick).toBe("Tell us.");
    expect(tickets.form.summary).toBe("Title");
    expect(tickets.form.details).toBe("Details");
    expect(tickets.form.kinds).toHaveLength(1);
  });

  it("keeps the defaults for a game that does not know the request, and asks again later", async () => {
    (tickets as any).formLoaded = false;
    tickets.form = mergeForm(null);
    answer = () => new Error("not allowed");
    await tickets.loadForm();
    expect(tickets.form.pick).toBe("What do you need?");
    answer = () => ({ pick: "Tell us." });
    await tickets.loadForm();
    expect(tickets.form.pick).toBe("Tell us.");
  });

  it("asks again after a new connection, in case the game changed", async () => {
    (tickets as any).formLoaded = true;
    answer = () => ({ v: 2, staff: false, account_id: 9 });
    await tickets.hello();
    answer = () => ({ pick: "Fresh words." });
    await tickets.loadForm();
    expect(tickets.form.pick).toBe("Fresh words.");
  });

  it("takes what each priority means from the hello answer, for staff", async () => {
    answer = () => ({
      v: 2,
      staff: true,
      account_id: 7,
      tickets: [],
      priorities: [
        { value: 0, word: "Low", hint: "Can wait." },
        { value: 3, word: "Urgent", hint: "Now." },
      ],
    });
    await tickets.hello();
    expect(tickets.priorities.map((p) => p.word)).toEqual(["Low", "Urgent"]);
  });
});

describe("the queue tab badge", () => {
  beforeEach(() => {
    tickets.handleOob("ticket_role", [], { staff: true, account_id: 7 });
  });

  it("flags unseen unanswered tickets and clears them once looked at", () => {
    tickets.handleOob("ticket_inbox", [], { tickets: [row({ id: "a", updated: 10 })] });
    expect(tickets.queueUnseen).toBe(1);
    tickets.markQueueSeen();
    expect(tickets.queueUnseen).toBe(0);
  });

  it("flags a ticket that changed after it was seen", () => {
    tickets.handleOob("ticket_inbox", [], { tickets: [row({ id: "a", updated: 10 })] });
    tickets.markQueueSeen();
    tickets.handleOob("ticket_inbox", [], { tickets: [row({ id: "a", updated: 20 })] });
    expect(tickets.queueUnseen).toBe(1);
  });

  it("counts an arrival while the queue panel is open as seen", () => {
    tickets.queueActive = true;
    tickets.handleOob("ticket_inbox", [], { tickets: [row({ id: "a", updated: 10 })] });
    expect(tickets.queueUnseen).toBe(0);
  });

  it("does not flag an answered ticket", () => {
    tickets.handleOob("ticket_inbox", [], { tickets: [row({ id: "a", updated: 10, status: "waiting" })] });
    expect(tickets.queueUnseen).toBe(0);
  });

  it("never flags the queue for a player", () => {
    tickets.handleOob("ticket_role", [], { staff: false });
    tickets.rows = [row({ id: "a", updated: 10 })];
    expect(tickets.queueUnseen).toBe(0);
  });
});
