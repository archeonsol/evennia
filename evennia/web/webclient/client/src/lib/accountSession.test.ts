import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// append() projects HTML to text through the DOM, which this environment lacks.
vi.mock("./text", () => ({ htmlToText: (html: string) => html.replace(/<[^>]*>/g, "") }));

import { handshake, loggedIn, loggedOut, roleArrived } from "./accountSession";
import { chat } from "./chat.svelte";
import { commands } from "./commands.svelte";
import { compose } from "./compose.svelte";
import { connection } from "./evennia.svelte";
import { help } from "./help.svelte";
import { puppets } from "./puppets.svelte";
import { routing } from "./routing.svelte";
import { scene } from "./scene.svelte";
import { session } from "./session.svelte";
import { settings } from "./settings.svelte";

function storage(): void {
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  });
}

/** Sign account 7 in as staff and give every store something of its own. */
function signInAndFill(): void {
  loggedIn();
  chat.handleOob("ticket_role", [], { staff: true, account: 7 });
  roleArrived();
  chat.handleOob("channels_list", [{ key: "staff", name: "Staff" }], {});
  chat.handleOob("channel_msg", [], { channel: "staff", text: "private", sender: "Mira", ts: 1, msg_id: "m1" });
  chat.ticketHistory = [{ id: "old" }];
  chat.ticket = { id: "a", view: "staff", messages: [{ text: "internal" }] };
  commands.run("page bob meet me at the docks");
  compose.setText("a half-written pose");
  session.append("Mira tells you: the code is 4471", "tell");
  routing.buffers = { Tells: [{ id: 1, html: "Mira tells you: the code is 4471", ts: 1 }] };
  puppets.setManifest([{ npc_id: 5, slot: 1, name: "Bartender" }]);
  scene.apply("scene", [{ op: "set", path: "/", value: { room: { name: "Staff lounge" } } }]);
  help.show({ kind: "topic", query: "staff ban" });
  const unfilled = residue().flatMap((v, i) => (JSON.stringify(v) === JSON.stringify(EMPTY[i]) ? [i] : []));
  expect(unfilled).toEqual([]);
}

/** Everything the last account left that the next person could read. */
function residue(): unknown[] {
  return [
    chat.channels,
    chat.messages,
    chat.ticket,
    chat.ticketHistory,
    chat.staff,
    commands.recent,
    compose.text,
    session.lines.map((l) => l.text),
    routing.buffers,
    puppets.list,
    scene.present,
    help.page,
  ];
}

const EMPTY = [[], {}, null, [], false, [], "", [], {}, [], false, null];

describe("the end of a signed-in session", () => {
  let sent: string[];

  beforeEach(() => {
    storage();
    settings.echoCommands = true;
    sent = [];
    vi.spyOn(connection, "sendCommand").mockImplementation(() => {});
    vi.spyOn(connection, "request").mockResolvedValue({ tickets: [] } as any);
    vi.spyOn(connection, "dropReplayBuffer").mockImplementation(() => void sent.push("resume_reset"));
    vi.spyOn(connection, "markLoggedOut").mockImplementation(() => void sent.push("close"));
    // A page that ended its last test signed in would carry it into this one.
    handshake(false);
    sent = [];
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("forgets the account on logout, and drops the replay window before the socket closes", () => {
    signInAndFill();
    loggedOut("quit");
    expect(residue()).toEqual(EMPTY);
    expect(sent).toEqual(["resume_reset", "close"]);
  });

  it("forgets the account when a reconnect cannot resume it", () => {
    signInAndFill();
    handshake(false);
    expect(residue()).toEqual(EMPTY);
  });

  it("forgets it after a reload that resumed it, which replays no login", () => {
    chat.handleOob("ticket_role", [], { staff: false, account: 7 });
    roleArrived();
    session.append("Mira tells you: the code is 4471", "tell");
    handshake(false);
    expect(session.lines).toEqual([]);
  });

  it("keeps the scrollback across a reconnect that resumed", () => {
    signInAndFill();
    handshake(true);
    expect(session.lines.map((l) => l.text)).toContain("Mira tells you: the code is 4471");
    expect(chat.channels).not.toEqual([]);
  });

  it("keeps the lines before a first login", () => {
    session.append("Welcome to Underspire", "text");
    handshake(false);
    expect(session.lines.map((l) => l.text)).toEqual(["Welcome to Underspire"]);
  });

  it("forgets the last account when a second login arrives without its end", () => {
    signInAndFill();
    loggedIn();
    expect(residue()).toEqual(EMPTY);
  });
});

describe("the local echo", () => {
  beforeEach(() => {
    storage();
    settings.echoCommands = true;
    vi.spyOn(connection, "sendCommand").mockImplementation(() => {});
    vi.spyOn(connection, "dropReplayBuffer").mockImplementation(() => {});
    session.clear();
    commands.useAccount(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows nothing typed before login", () => {
    commands.run("connect someone hunter2");
    expect(session.lines).toEqual([]);
  });

  it("shows a signed-in account's command", () => {
    commands.useAccount(1);
    commands.run("look");
    expect(session.lines).toHaveLength(1);
  });
});
