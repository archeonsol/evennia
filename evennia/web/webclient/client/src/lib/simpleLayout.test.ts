import { describe, expect, it } from "vitest";

import { simple } from "./simpleLayout.svelte";

describe("simple layout", () => {
  it("starts on the terminal with the three base views", () => {
    expect(simple.active).toBe("log");
    expect(simple.views.map((v) => v.id)).toEqual(["log", "scene", "chat"]);
  });

  it("opens a new view once, shows it, and closes back to the terminal", () => {
    simple.open({ id: "nous:grid", component: "iframe", title: "NOUS // DECK", params: { url: "/x" } });
    simple.open({ id: "nous:grid", component: "iframe", title: "NOUS // DECK", params: { url: "/x" } });
    expect(simple.views.filter((v) => v.id === "nous:grid")).toHaveLength(1);
    expect(simple.active).toBe("nous:grid");
    simple.close("nous:grid");
    expect(simple.has("nous:grid")).toBe(false);
    expect(simple.active).toBe("log");
  });

  it("never closes a base view", () => {
    simple.close("chat");
    expect(simple.has("chat")).toBe(true);
  });
});
