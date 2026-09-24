import { beforeEach, describe, expect, it, vi } from "vitest";

import { Macros } from "./macros.svelte";

const KEY = "underspire.macros.v1";

function storage(initial: Record<string, string> = {}): Map<string, string> {
  const store = new Map(Object.entries(initial));
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() {
      return store.size;
    },
  });
  return store;
}

describe("macro defaults", () => {
  beforeEach(() => {
    storage();
  });

  it("binds F5 to @stats", () => {
    const m = new Macros();
    m.init();

    expect(m.list.find((x) => x.key === "F5")).toMatchObject({
      label: "Stats",
      command: "@stats",
    });
  });

  it("migrates the old score default and saves the result", () => {
    const store = storage({
      [KEY]: JSON.stringify([{ id: "score", label: "Score", command: "score", key: "F5" }]),
    });
    const m = new Macros();
    m.init();

    expect(m.list[0]).toMatchObject({ label: "Stats", command: "@stats" });
    expect(JSON.parse(store.get(KEY)!)[0]).toMatchObject({ label: "Stats", command: "@stats" });
  });

  it("relabels the interim @stats default", () => {
    storage({
      [KEY]: JSON.stringify([{ id: "score", label: "Score", command: "@stats", key: "F5" }]),
    });
    const m = new Macros();
    m.init();

    expect(m.list[0]).toMatchObject({ label: "Stats", command: "@stats" });
  });

  it("leaves a player-edited macro untouched", () => {
    storage({
      [KEY]: JSON.stringify([{ id: "score", label: "Sheet", command: "sheet" }]),
    });
    const m = new Macros();
    m.init();

    expect(m.list[0]).toMatchObject({ label: "Sheet", command: "sheet" });
  });

  it("leaves a retargeted macro untouched even with the stock label", () => {
    storage({
      [KEY]: JSON.stringify([{ id: "score", label: "Score", command: "look" }]),
    });
    const m = new Macros();
    m.init();

    expect(m.list[0]).toMatchObject({ label: "Score", command: "look" });
  });
});
