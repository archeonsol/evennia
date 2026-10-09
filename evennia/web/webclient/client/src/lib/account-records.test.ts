import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { configEntries } from "./backup";
import { commands } from "./commands.svelte";
import { compose } from "./compose.svelte";
import { connection } from "./evennia.svelte";

function storage(): Map<string, string> {
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() {
      return store.size;
    },
  });
  return store;
}

describe("command history is kept per account", () => {
  let store: Map<string, string>;

  beforeEach(() => {
    store = storage();
    vi.spyOn(connection, "sendCommand").mockImplementation(() => {});
    commands.useAccount(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("does not record a line typed before login", () => {
    commands.run("connect someone hunter2");
    expect(commands.recent).toEqual([]);
    expect([...store.values()].join()).not.toContain("hunter2");
  });

  it("shows the next account none of the last account's commands", () => {
    commands.useAccount(1);
    commands.run("page bob meet me at the docks");
    commands.useAccount(null);
    expect(commands.recent).toEqual([]);
    commands.useAccount(2);
    expect(commands.recent).toEqual([]);
    commands.useAccount(1);
    expect(commands.recent).toEqual(["page bob meet me at the docks"]);
  });

  it("removes the history every account shared", () => {
    store.set("underspire.history.v1", JSON.stringify(["connect someone hunter2"]));
    commands.init();
    expect(store.has("underspire.history.v1")).toBe(false);
  });
});

describe("the compose draft is kept per account", () => {
  let store: Map<string, string>;

  beforeEach(() => {
    store = storage();
    compose.useAccount(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("empties the pad when the account signs out and restores it at its next login", () => {
    compose.useAccount(1);
    compose.setText("a half-written pose");
    compose.show();
    compose.useAccount(null);
    expect(compose.text).toBe("");
    expect(compose.open).toBe(false);
    compose.useAccount(2);
    expect(compose.text).toBe("");
    compose.useAccount(1);
    expect(compose.text).toBe("a half-written pose");
    compose.hide();
  });

  it("stores nothing typed before login", () => {
    compose.setText("not yet signed in");
    expect([...store.values()].join()).not.toContain("not yet signed in");
  });

  it("removes the draft every account shared", () => {
    store.set("underspire.compose.draft.v1", JSON.stringify({ mode: "pose", text: "the last user's pose" }));
    compose.init();
    expect(store.has("underspire.compose.draft.v1")).toBe(false);
  });
});

describe("a config export", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("carries preferences and no account's records", () => {
    const store = storage();
    store.set("underspire.settings.v1", "{}");
    store.set("underspire.history.v2:1", JSON.stringify(["page bob secret"]));
    store.set("underspire.compose.draft.v2:1", "{}");
    store.set("underspire.tickets.seen.v2:1", "{}");
    store.set("underspire.queue.seen.v2:1", "{}");
    store.set("underspire.assist.added:1", "1");
    store.set("underspire.tickets.seen.v1", JSON.stringify({ abc12345: 1 }));
    store.set("underspire.queue.seen.v1", JSON.stringify({ abc12345: 1 }));
    expect(Object.keys(configEntries())).toEqual(["underspire.settings.v1"]);
  });
});
