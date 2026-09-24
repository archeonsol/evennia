import { describe, expect, it } from "vitest";

import { comboFromEvent, reviewIndex } from "./keybinds.svelte";

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
