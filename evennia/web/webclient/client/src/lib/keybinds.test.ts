import { describe, expect, it } from "vitest";

import { comboFromEvent, displayCombo, isModifierOnly, keybinds, reviewIndex } from "./keybinds.svelte";

function key(init: Partial<KeyboardEvent>): KeyboardEvent {
  return { ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, key: "", code: "", ...init } as KeyboardEvent;
}

describe("comboFromEvent", () => {
  it("ignores plain typing", () => {
    expect(comboFromEvent(key({ key: "o", code: "KeyO" }))).toBeNull();
  });

  it("names an Alt letter by its physical key, so macOS Option symbols still match", () => {
    expect(comboFromEvent(key({ altKey: true, key: "ø", code: "KeyO" }))).toBe("Alt+O");
    expect(comboFromEvent(key({ altKey: true, key: "Dead", code: "KeyI" }))).toBe("Alt+I");
  });

  it("keeps Ctrl combos and function keys as before", () => {
    expect(comboFromEvent(key({ ctrlKey: true, key: "k", code: "KeyK" }))).toBe("Ctrl+K");
    expect(comboFromEvent(key({ key: "F5", code: "F5" }))).toBe("F5");
  });
});

describe("reviewIndex", () => {
  it("reads Alt+1 to Alt+9 from the digit row", () => {
    expect(reviewIndex(key({ altKey: true, key: "1", code: "Digit1" }))).toBe(1);
    expect(reviewIndex(key({ altKey: true, key: "¡", code: "Digit9" }))).toBe(9);
  });

  it("ignores Alt+0, other modifiers and plain digits", () => {
    expect(reviewIndex(key({ altKey: true, key: "0", code: "Digit0" }))).toBeNull();
    expect(reviewIndex(key({ altKey: true, ctrlKey: true, key: "1", code: "Digit1" }))).toBeNull();
    expect(reviewIndex(key({ key: "1", code: "Digit1" }))).toBeNull();
  });
});

describe("bare modifiers", () => {
  it("are never a combo, so capture waits for the real key", () => {
    expect(comboFromEvent(key({ ctrlKey: true, key: "Control", code: "ControlLeft" }))).toBeNull();
    expect(comboFromEvent(key({ altKey: true, key: "Alt", code: "AltLeft" }))).toBeNull();
    expect(comboFromEvent(key({ ctrlKey: true, key: "k", code: "KeyK" }))).toBe("Ctrl+K");
  });

  it("are recognised in saved combos from the old capture bug", () => {
    expect(isModifierOnly("Ctrl+Control")).toBe(true);
    expect(isModifierOnly("Ctrl+Shift+Shift")).toBe(true);
    expect(isModifierOnly("Ctrl+K")).toBe(false);
    expect(isModifierOnly(undefined)).toBe(false);
  });
});

describe("review cursor keys", () => {
  it("default to Alt+Up/Down, with Shift for the ends of the scrollback", () => {
    keybinds.init();
    expect(keybinds.match(key({ altKey: true, key: "ArrowUp", code: "ArrowUp" }), "reviewOlder")).toBe(true);
    expect(keybinds.match(key({ altKey: true, key: "ArrowDown", code: "ArrowDown" }), "reviewNewer")).toBe(true);
    expect(keybinds.match(key({ altKey: true, shiftKey: true, key: "ArrowUp", code: "ArrowUp" }), "reviewOldest")).toBe(true);
    expect(keybinds.match(key({ altKey: true, shiftKey: true, key: "ArrowDown", code: "ArrowDown" }), "reviewNewest")).toBe(true);
    // Plain arrows stay with the command line's history.
    expect(keybinds.match(key({ key: "ArrowUp", code: "ArrowUp" }), "reviewOlder")).toBe(false);
  });

  it("do not collide with any other default", () => {
    keybinds.init();
    const combos = keybinds.list.map((b) => b.combo);
    expect(new Set(combos).size).toBe(combos.length);
  });
});

describe("displayCombo", () => {
  it("shows arrow keys by their direction", () => {
    expect(displayCombo("Alt+ArrowUp")).toBe("Alt+Up");
    expect(displayCombo("Alt+Shift+ArrowDown")).toBe("Alt+Shift+Down");
  });

  it("leaves every other combo as it is", () => {
    expect(displayCombo("Ctrl+K")).toBe("Ctrl+K");
    expect(displayCombo("F1")).toBe("F1");
  });
});
