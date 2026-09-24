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

    expect(m.list.find((x) => x.key === "F5")?.command).toBe("@stats");
  });

  it("repoints a persisted bare score macro and saves the result", () => {
    const store = storage({
      [KEY]: JSON.stringify([{ id: "score", label: "Score", command: "score", key: "F5" }]),
    });
    const m = new Macros();
    m.init();

    expect(m.list[0].command).toBe("@stats");
    expect(JSON.parse(store.get(KEY)!)[0].command).toBe("@stats");
  });

  it("leaves a player-edited macro untouched", () => {
    storage({
      [KEY]: JSON.stringify([{ id: "score", label: "Sheet", command: "sheet" }]),
    });
    const m = new Macros();
    m.init();

    expect(m.list[0]).toMatchObject({ label: "Sheet", command: "sheet" });
  });
});
