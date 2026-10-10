import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { chat } from "./chat.svelte";
import { migrateAssistPanels } from "./dock.svelte";
import { tickets } from "./tickets.svelte";
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
    tickets.reset();
  });

  it("follows the ticket role the server states", () => {
    chat.handleOob("ticket_role", [], { staff: true });
    expect(chat.staff).toBe(true);
    expect(chat.staffKnown).toBe(true);
    chat.handleOob("ticket_role", [], { staff: false });
    expect(chat.staff).toBe(false);
  });

  it("does not know the role before the server has said", () => {
    expect(chat.staffKnown).toBe(false);
  });

  it("names the account the server says is signed in", () => {
    chat.handleOob("ticket_role", [], { staff: false, account: 7 });
    expect(chat.account).toBe(7);
  });
});

describe("tab badges", () => {
  beforeEach(() => {
    chat.unread = {};
  });

  it("totals unread across channels", () => {
    chat.handleOob("channel_unread", [], { help: 2, nous: 3 });
    expect(chat.channelsUnseen).toBe(5);
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

describe("leaving", () => {
  beforeEach(() => {
    storage();
    chat.resetForLogin();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("forgets the tickets and channel lines of an account that quits", () => {
    chat.handleOob("ticket_role", [], { staff: true });
    tickets.ticket = { id: "a", view: "staff", messages: [{ text: "internal" }] };
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
    expect([tickets.ticket, chat.staff, chat.channels, chat.messages]).toEqual([null, false, [], {}]);
    expect([chat.pins, chat.topics, chat.typing, chat.mentions, chat.unread, chat.online, chat.muted, chat.readMark]).toEqual([
      {}, {}, {}, {}, {}, {}, {}, {},
    ]);
    expect(chat.active).toBe("");
  });

  it("forgets the typed line waiting to be echoed", () => {
    chat.channels = [{ key: "ooc", name: "OOC", speakCmd: "xooc" } as any];
    chat.armEcho("xooc hello");
    chat.resetForLogin();
    expect(chat.takeEcho("ooc", true)).toBe(false);
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
