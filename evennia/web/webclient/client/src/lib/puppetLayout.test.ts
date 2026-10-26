import { beforeEach, describe, expect, it, vi } from "vitest";

import { MIN_TILE_WIDTH, parseSaved, shownIds, tileColumns, toggled } from "./puppetLayout";
import { puppetLayout, PUPPET_LAYOUT_KEY } from "./puppetLayout.svelte";

describe("tileColumns", () => {
  it("is one column for zero or one terminal", () => {
    expect(tileColumns(0, "grid")).toBe(1);
    expect(tileColumns(1, "columns")).toBe(1);
  });

  it("makes a grid as square as it can", () => {
    expect([2, 3, 4, 5, 6, 9].map((n) => tileColumns(n, "grid"))).toEqual([2, 2, 2, 3, 3, 3]);
  });

  it("puts columns side by side and rows in one stack", () => {
    expect(tileColumns(5, "columns")).toBe(5);
    expect(tileColumns(5, "rows")).toBe(1);
  });

  it("never asks for more columns than the width holds", () => {
    expect(tileColumns(5, "columns", MIN_TILE_WIDTH * 2 + 10)).toBe(2);
    expect(tileColumns(5, "grid", 120)).toBe(1);
    expect(tileColumns(4, "grid", 0)).toBe(2);
  });
});

describe("shownIds", () => {
  it("shows every puppet until the player chooses", () => {
    expect(shownIds(null, [3, 1, 2])).toEqual([3, 1, 2]);
  });

  it("keeps the chosen order and drops puppets that are gone", () => {
    expect(shownIds([2, 9, 1], [1, 2, 3])).toEqual([2, 1]);
    expect(shownIds([], [1, 2])).toEqual([]);
  });

  it("toggles an id in and out", () => {
    expect(toggled([1, 2], 3)).toEqual([1, 2, 3]);
    expect(toggled([1, 2, 3], 2)).toEqual([1, 3]);
  });
});

describe("parseSaved", () => {
  it("reads what was saved", () => {
    expect(parseSaved(JSON.stringify({ open: [4, 5], arrange: "rows" }))).toEqual({ open: [4, 5], arrange: "rows" });
  });

  it("treats anything else as no choice", () => {
    const none = { open: null, arrange: "grid" };
    expect(parseSaved(null)).toEqual(none);
    expect(parseSaved("{not json")).toEqual(none);
    expect(parseSaved(JSON.stringify({ open: "all", arrange: "diagonal" }))).toEqual(none);
    expect(parseSaved(JSON.stringify({ open: [1, "x", -2, 3.5, 7] }))).toEqual({ open: [1, 7], arrange: "grid" });
  });
});

describe("the layout store", () => {
  beforeEach(() => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    });
    puppetLayout.useAccount(null);
  });

  it("is kept per account", () => {
    puppetLayout.useAccount(7);
    puppetLayout.setArrange("columns");
    puppetLayout.hideAll();

    puppetLayout.useAccount(null);
    expect(puppetLayout.open).toBeNull();
    puppetLayout.useAccount(8);
    expect(puppetLayout.arrange).toBe("grid");

    puppetLayout.useAccount(7);
    expect(puppetLayout.arrange).toBe("columns");
    expect(puppetLayout.open).toEqual([]);
    expect(localStorage.getItem(`${PUPPET_LAYOUT_KEY}:7`)).toContain("columns");
  });

  it("saves nothing without an account", () => {
    puppetLayout.setArrange("rows");
    expect(localStorage.getItem(`${PUPPET_LAYOUT_KEY}:null`)).toBeNull();
  });

  it("hides one puppet from the default of all", () => {
    puppetLayout.useAccount(7);
    puppetLayout.toggle(2, [1, 2, 3]);
    expect(puppetLayout.shown([1, 2, 3])).toEqual([1, 3]);
    puppetLayout.toggle(2, [1, 2, 3]);
    expect(puppetLayout.shown([1, 2, 3])).toEqual([1, 3, 2]);
  });

  it("opens a puppet added after the first roster, and respects a saved choice for the first", () => {
    puppetLayout.useAccount(7);
    puppetLayout.hideAll();
    puppetLayout.sync([1, 2]);
    expect(puppetLayout.shown([1, 2])).toEqual([]);

    puppetLayout.sync([1, 2, 3]);
    expect(puppetLayout.shown([1, 2, 3])).toEqual([3]);
  });

  it("leaves the default of all alone when a puppet arrives", () => {
    puppetLayout.useAccount(7);
    puppetLayout.sync([1]);
    puppetLayout.sync([1, 2]);
    expect(puppetLayout.open).toBeNull();
    expect(puppetLayout.shown([1, 2])).toEqual([1, 2]);
  });

  it("expands one terminal and lets go of it when it leaves or another is picked", () => {
    puppetLayout.useAccount(7);
    puppetLayout.toggleSolo(2);
    expect(puppetLayout.shown([1, 2, 3])).toEqual([2]);

    puppetLayout.toggle(3, [1, 2, 3]);
    expect(puppetLayout.solo).toBeNull();

    puppetLayout.toggleSolo(1);
    puppetLayout.sync([2, 3]);
    expect(puppetLayout.solo).toBeNull();
  });
});
