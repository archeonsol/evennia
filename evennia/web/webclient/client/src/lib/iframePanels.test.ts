import { describe, expect, it } from "vitest";

import { iframeTarget } from "./iframePanels";

describe("iframe panel target", () => {
  it("makes the base panel when none is open", () => {
    expect(iframeTarget([{ id: "log" }], "wiki")).toEqual({ reuse: null, freshId: "wiki" });
  });

  it("reuses the open panel for the base", () => {
    const panels = [{ id: "wiki", params: { base: "wiki", url: "/wiki/a/" } }];
    expect(iframeTarget(panels, "wiki").reuse).toBe("wiki");
  });

  it("reads a panel saved without a base as its own id", () => {
    expect(iframeTarget([{ id: "wiki", params: { url: "/x" } }], "wiki").reuse).toBe("wiki");
  });

  it("leaves a pinned panel alone and opens beside it", () => {
    const panels = [{ id: "wiki", params: { base: "wiki", pinned: true } }];
    expect(iframeTarget(panels, "wiki")).toEqual({ reuse: null, freshId: "wiki:2" });
  });

  it("reuses the unpinned panel when another is pinned", () => {
    const panels = [
      { id: "wiki", params: { base: "wiki", pinned: true } },
      { id: "wiki:2", params: { base: "wiki" } },
    ];
    expect(iframeTarget(panels, "wiki").reuse).toBe("wiki:2");
  });

  it("does not take another base's panel", () => {
    expect(iframeTarget([{ id: "nous:grid", params: { base: "nous:grid" } }], "wiki").reuse).toBeNull();
  });
});
