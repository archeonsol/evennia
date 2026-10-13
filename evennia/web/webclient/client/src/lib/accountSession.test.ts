import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// append() projects HTML to text through the DOM, which this environment lacks.
vi.mock("./text", () => ({ htmlToText: (html: string) => html.replace(/<[^>]*>/g, "") }));

import { handshake, loggedIn, loggedOut, roleArrived } from "./accountSession";
import { chat } from "./chat.svelte";
import { commands } from "./commands.svelte";
import { compose } from "./compose.svelte";
import { connection } from "./evennia.svelte";
import { help } from "./help.svelte";
import { lore } from "./lore.svelte";
import { puppets } from "./puppets.svelte";
import { routing } from "./routing.svelte";
import { NOTES, noteHtml, savedLog } from "./savedlog.svelte";
import { scene } from "./scene.svelte";
import { session } from "./session.svelte";
import { settings } from "./settings.svelte";
import { tickets } from "./tickets.svelte";

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
  tickets.history = [{ id: "old" } as any];
  tickets.rows = [{ id: "a", updated: 1 } as any];
  tickets.ticket = { id: "a", view: "staff", messages: [{ text: "internal" }] };
  tickets.myRows = [{ id: "m", updated: 1 } as any];
  tickets.myTicket = { id: "m", view: "owner", messages: [{ text: "for you" }] };
  commands.run("page bob meet me at the docks");
  compose.setText("a half-written pose");
  session.append("Mira tells you: the code is 4471", "tell");
  routing.buffers = { Tells: [{ id: 1, html: "Mira tells you: the code is 4471", ts: 1 }] };
  puppets.setManifest([{ npc_id: 5, slot: 1, name: "Bartender" }]);
  scene.apply("scene", [{ op: "set", path: "/", value: { room: { name: "Staff lounge" } } }]);
  help.show({ kind: "topic", query: "staff ban" });
  lore.active = { tip: { id: "marrow", title: "Saint Marrow", blurb: "", help: "marrow", terms: ["Marrow"] }, rect: { left: 0, top: 0, right: 1, bottom: 1 }, pinned: true };
  const unfilled = residue().flatMap((v, i) => (JSON.stringify(v) === JSON.stringify(EMPTY[i]) ? [i] : []));
  expect(unfilled).toEqual([]);
}

/** Everything the last account left that the next person could read. */
function residue(): unknown[] {
  return [
    chat.channels,
    chat.messages,
    tickets.ticket,
    tickets.history,
    chat.staff,
    tickets.rows,
    tickets.myRows,
    tickets.myTicket,
    commands.recent,
    compose.text,
    session.lines.map((l) => l.text),
    routing.buffers,
    puppets.list,
    scene.present,
    help.page,
    lore.active,
  ];
}

const EMPTY = [[], {}, null, [], false, [], [], null, [], "", [], {}, [], false, null, null];

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

describe("the same person coming back", () => {
  beforeEach(() => {
    storage();
    settings.echoCommands = true;
    vi.spyOn(connection, "sendCommand").mockImplementation(() => {});
    vi.spyOn(connection, "request").mockResolvedValue({ tickets: [] } as any);
    vi.spyOn(connection, "dropReplayBuffer").mockImplementation(() => {});
    vi.spyOn(connection, "markLoggedOut").mockImplementation(() => {});
    vi.spyOn(savedLog, "hello").mockResolvedValue();
    vi.spyOn(savedLog, "signedIn").mockResolvedValue();
    vi.spyOn(savedLog, "erase").mockResolvedValue();
    vi.spyOn(savedLog, "signedOut").mockImplementation(() => {});
    vi.spyOn(savedLog, "discardUnbound").mockImplementation(() => {});
    handshake(false);
    // Earlier tests leave their lines in the shared session.
    session.lines = [];
    session.archive = [];
    routing.buffers = {};
    vi.mocked(savedLog.hello).mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("keeps the log when the session could not be resumed but account 7 is back, and says the connection broke", () => {
    signInAndFill();
    handshake(false, 7);
    const shown = session.lines.map((l) => l.text);
    expect(shown).toContain("Mira tells you: the code is 4471");
    expect(shown.at(-1)).toBe(NOTES.away);
    expect(session.lines.at(-1)?.html).toBe(noteHtml(NOTES.away));
    expect(session.lines.at(-1)?.type).toBe("system");
    expect(routing.buffers).toEqual({ Tells: [expect.objectContaining({ id: 1 })] });
    expect(savedLog.hello).toHaveBeenCalledWith({ resumed: false, account: 7, kept: true });
  });

  it("still empties everything else the game will send again", () => {
    signInAndFill();
    handshake(false, 7);
    const rest = residue().filter((_, i) => ![10, 11].includes(i));
    const empty = EMPTY.filter((_, i) => ![10, 11].includes(i));
    expect(rest).toEqual(empty);
  });

  it("leaves the signed-in state alone for the login that follows", () => {
    signInAndFill();
    handshake(false, 7);
    loggedIn();
    expect(session.lines.map((l) => l.text)).toContain("Mira tells you: the code is 4471");
    expect(chat.channels).toEqual([]);
  });

  it("empties the screen when another account is the one that came back", () => {
    signInAndFill();
    handshake(false, 9);
    expect(residue()).toEqual(EMPTY);
    expect(savedLog.hello).toHaveBeenCalledWith({ resumed: false, account: 9, kept: false });
  });

  it("empties the screen when no one is signed in any more", () => {
    signInAndFill();
    handshake(false, null);
    expect(residue()).toEqual(EMPTY);
    expect(savedLog.hello).toHaveBeenCalledWith({ resumed: false, account: null, kept: false });
  });

  it("empties it the old way when the portal does not say who came back", () => {
    signInAndFill();
    handshake(false);
    expect(residue()).toEqual(EMPTY);
    expect(savedLog.hello).not.toHaveBeenCalled();
  });

  it("lets go of the account's log, leaving the copy on the device, when it empties the screen", () => {
    signInAndFill();
    handshake(false, 9);
    expect(savedLog.signedOut).toHaveBeenCalled();
    expect(savedLog.erase).not.toHaveBeenCalled();
  });

  it("does not let go of it when the same person is back", () => {
    signInAndFill();
    handshake(false, 7);
    expect(savedLog.signedOut).not.toHaveBeenCalled();
  });

  it("tells the saved log who is signed in on a resumed handshake", () => {
    signInAndFill();
    handshake(true, 7);
    expect(savedLog.hello).toHaveBeenCalledWith({ resumed: true, account: 7, kept: false });
    const shown = session.lines.map((l) => l.text);
    expect(shown).toContain("Mira tells you: the code is 4471");
    expect(shown).not.toContain(NOTES.away);
  });

  it("tells the saved log who is signed in on a page that has not shown anyone yet", () => {
    handshake(false, 7);
    expect(savedLog.hello).toHaveBeenCalledWith({ resumed: false, account: 7, kept: false });
  });

  it("then recognises that account as the one that came back", () => {
    handshake(false, 7);
    loggedIn();
    session.append("something to keep", "text");
    handshake(false, 7);
    expect(session.lines.map((l) => l.text)).toEqual(["something to keep", NOTES.away]);
  });

  it("tells the saved log when the game names the account", () => {
    chat.handleOob("ticket_role", [], { staff: false, account: 7 });
    roleArrived();
    expect(savedLog.signedIn).toHaveBeenCalledWith(7);
  });

  it("drops what the sign-in screen printed when the login comes", () => {
    loggedIn();
    expect(savedLog.discardUnbound).toHaveBeenCalled();
  });

  it("deletes the saved copy when the player quits or is removed", () => {
    for (const reason of ["quit", "booted"]) {
      vi.mocked(savedLog.erase).mockClear();
      signInAndFill();
      loggedOut(reason);
      expect(savedLog.erase).toHaveBeenCalledTimes(1);
    }
  });

  it("leaves the saved copy when the session only moved to another window", () => {
    signInAndFill();
    loggedOut("logged in from elsewhere");
    expect(savedLog.erase).not.toHaveBeenCalled();
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
