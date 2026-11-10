import { describe, expect, it, vi } from "vitest";
import { ScreenStore, endOf, specOf, type ScreenLinks } from "./screens.svelte";

function links() {
  const l = {
    send: vi.fn(),
    openPanel: vi.fn(),
    closePanel: vi.fn(),
    say: vi.fn(),
  } satisfies ScreenLinks;
  return l;
}

function component(extra: Record<string, unknown> = {}) {
  return {
    id: "arcade-7",
    type: "screen",
    mode: "terminal",
    title: "Invaders",
    size: [80, 24],
    controls: [["Left, Right", "move the gun"], ["Space", "fire"]],
    dismissible: true,
    ...extra,
  };
}

function store() {
  const l = links();
  const s = new ScreenStore();
  s.connect(l);
  return { s, l };
}

describe("screen spec", () => {
  it("reads what the server sends", () => {
    expect(specOf(component())).toEqual({
      id: "arcade-7",
      title: "Invaders",
      size: [80, 24],
      controls: [["Left, Right", "move the gun"], ["Space", "fire"]],
      dismissible: true,
    });
  });

  it("needs an id", () => {
    expect(specOf(component({ id: "" }))).toBeNull();
    expect(specOf(null)).toBeNull();
    expect(specOf("arcade-7")).toBeNull();
  });

  it("keeps the size sane", () => {
    expect(specOf(component({ size: [5000, 1] }))?.size).toEqual([200, 8]);
    expect(specOf(component({ size: ["x"] }))?.size).toEqual([80, 24]);
    expect(specOf(component({ size: undefined }))?.size).toEqual([80, 24]);
  });

  it("drops a control that is not a pair with a key, and caps the list", () => {
    const controls = [["Space", "fire"], "Q", [], ["", "nothing"], ...Array.from({ length: 30 }, (_, i) => [`K${i}`, "x"])];
    const spec = specOf(component({ controls }));
    expect(spec?.controls[0]).toEqual(["Space", "fire"]);
    expect(spec?.controls.length).toBe(12);
    expect(spec?.controls.every(([key]) => key.length > 0)).toBe(true);
  });

  it("is dismissible unless the server says no", () => {
    expect(specOf(component({ dismissible: undefined }))?.dismissible).toBe(true);
    expect(specOf(component({ dismissible: false }))?.dismissible).toBe(false);
  });
});

describe("screen end", () => {
  it("reads the headline", () => {
    expect(endOf({ id: "arcade-7", headline: "Timed out", why: "idle" })).toEqual({ id: "arcade-7", headline: "Timed out" });
  });

  it("says game over when the server gives no words", () => {
    expect(endOf({ id: "arcade-7" })).toEqual({ id: "arcade-7", headline: "Game over" });
    expect(endOf({ id: "arcade-7", headline: "" })?.headline).toBe("Game over");
    expect(endOf({ id: "arcade-7", headline: 5 })?.headline).toBe("Game over");
  });

  it("keeps the headline short", () => {
    expect(endOf({ id: "arcade-7", headline: "x".repeat(200) })?.headline.length).toBe(60);
  });

  it("needs an id", () => {
    expect(endOf({ headline: "Game over" })).toBeNull();
    expect(endOf(null)).toBeNull();
  });
});

describe("screens", () => {
  it("opens a panel and says so once", () => {
    const { s, l } = store();
    s.open(component());
    s.open(component());
    expect(l.openPanel).toHaveBeenCalledTimes(2);
    expect(l.openPanel.mock.calls[0][0].id).toBe("arcade-7");
    expect(l.say).toHaveBeenCalledTimes(1);
    expect(l.say.mock.calls[0][0]).toContain("Invaders");
    expect(Object.keys(s.screens)).toEqual(["arcade-7"]);
  });

  it("tells a screen reader the keys, since the panel lists none", () => {
    const { s, l } = store();
    s.open(component());
    const said = l.say.mock.calls[0][0];
    expect(said).toContain("Left, Right, move the gun.");
    expect(said).toContain("Space, fire.");
    expect(said).toContain("Shift and Escape leave the screen.");
    s.open(component({ id: "arcade-8", controls: [] }));
    expect(l.say.mock.calls[1][0]).not.toMatch(/\.\s{2,}/);
  });

  it("opens nothing for a spec with no id", () => {
    const { s, l } = store();
    s.open({ title: "Nameless" });
    expect(l.openPanel).not.toHaveBeenCalled();
    expect(s.screens).toEqual({});
  });

  it("hands output to the panel that is drawing", () => {
    const { s } = store();
    s.open(component());
    const seen: string[] = [];
    s.attach("arcade-7", (data) => seen.push(data));
    s.frame("arcade-7", "hello");
    s.frame("arcade-7", "");
    s.frame("arcade-7", 42);
    s.frame("arcade-8", "elsewhere");
    expect(seen).toEqual(["hello"]);
  });

  it("drops output when no panel is drawing", () => {
    const { s } = store();
    s.open(component());
    expect(() => s.frame("arcade-7", "nobody home")).not.toThrow();
  });

  it("asks for the whole screen when a panel starts drawing", () => {
    const { s, l } = store();
    s.open(component());
    s.attach("arcade-7", () => {});
    expect(l.send).toHaveBeenCalledWith("screen_sync", { id: "arcade-7" });
  });

  it("stops handing output over once the panel lets go", () => {
    const { s } = store();
    s.open(component());
    const seen: string[] = [];
    const detach = s.attach("arcade-7", (data) => seen.push(data));
    detach();
    s.frame("arcade-7", "late");
    expect(seen).toEqual([]);
  });

  it("keeps a newer panel when an older one lets go", () => {
    const { s } = store();
    s.open(component());
    const seen: string[] = [];
    const detachOld = s.attach("arcade-7", () => {});
    s.attach("arcade-7", (data) => seen.push(data));
    detachOld();
    s.frame("arcade-7", "still here");
    expect(seen).toEqual(["still here"]);
  });

  it("sends keys for a live screen", () => {
    const { s, l } = store();
    s.open(component());
    s.key("arcade-7", " ");
    s.key("arcade-7", "");
    s.key("arcade-9", "x");
    expect(l.send).toHaveBeenCalledTimes(1);
    expect(l.send).toHaveBeenCalledWith("screen_key", { id: "arcade-7", data: " " });
  });

  it("sends no key once the run is over", () => {
    const { s, l } = store();
    s.open(component());
    s.end({ id: "arcade-7" });
    s.key("arcade-7", " ");
    expect(l.send).not.toHaveBeenCalled();
  });

  it("keeps how a run ended", () => {
    const { s } = store();
    s.open(component());
    s.end({ id: "arcade-7", headline: "Timed out" });
    expect(s.ended["arcade-7"]).toEqual({ headline: "Timed out" });
  });

  it("ignores an end for a screen it never opened", () => {
    const { s } = store();
    s.end({ id: "arcade-1" });
    expect(s.ended).toEqual({});
  });

  it("does not ask for a finished screen to be drawn again", () => {
    const { s, l } = store();
    s.open(component());
    s.attach("arcade-7", () => {});
    s.end({ id: "arcade-7" });
    l.send.mockClear();
    s.resync();
    s.attach("arcade-7", () => {});
    expect(l.send).not.toHaveBeenCalled();
  });

  it("asks for each live screen again when the connection returns", () => {
    const { s, l } = store();
    s.open(component());
    s.open(component({ id: "arcade-8" }));
    s.attach("arcade-7", () => {});
    s.attach("arcade-8", () => {});
    s.end({ id: "arcade-8" });
    l.send.mockClear();
    s.resync();
    expect(l.send.mock.calls).toEqual([["screen_sync", { id: "arcade-7" }]]);
  });

  it("forgets a screen the server closes, and closes its panel", () => {
    const { s, l } = store();
    s.open(component());
    s.close("arcade-7");
    expect(s.screens).toEqual({});
    expect(l.closePanel).toHaveBeenCalledWith("arcade-7");
  });

  it("leaves other panels alone when the server closes an id that is not a screen", () => {
    const { s, l } = store();
    s.open(component());
    s.close("inventory");
    expect(Object.keys(s.screens)).toEqual(["arcade-7"]);
    expect(l.closePanel).not.toHaveBeenCalled();
  });

  it("forgets a screen the player dismisses", () => {
    const { s, l } = store();
    s.open(component());
    s.end({ id: "arcade-7" });
    s.dismiss("arcade-7");
    expect(s.screens).toEqual({});
    expect(s.ended).toEqual({});
    expect(l.closePanel).toHaveBeenCalledWith("arcade-7");
  });

  it("keeps a live screen when its panel goes away, and forgets a finished one", () => {
    const { s } = store();
    s.open(component());
    s.open(component({ id: "arcade-8" }));
    s.end({ id: "arcade-8" });
    s.released("arcade-7");
    s.released("arcade-8");
    expect(Object.keys(s.screens)).toEqual(["arcade-7"]);
  });
});
