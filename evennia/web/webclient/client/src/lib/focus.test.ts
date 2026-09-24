import { describe, expect, it } from "vitest";

import { isTypingTarget, shouldTypeCommand } from "./focus";

const key = (over: Partial<KeyboardEvent> = {}) => ({
  key: "a",
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  isComposing: false,
  ...over,
});

describe("isTypingTarget", () => {
  it("knows the fields that own their keys", () => {
    expect(isTypingTarget({ tagName: "INPUT" })).toBe(true);
    expect(isTypingTarget({ tagName: "textarea" })).toBe(true);
    expect(isTypingTarget({ tagName: "SELECT" })).toBe(true);
    expect(isTypingTarget({ tagName: "DIV", isContentEditable: true })).toBe(true);
  });

  it("treats plain elements and non-elements as not typing", () => {
    expect(isTypingTarget({ tagName: "DIV" })).toBe(false);
    expect(isTypingTarget({ tagName: "BUTTON" })).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
    expect(isTypingTarget(undefined)).toBe(false);
    expect(isTypingTarget("input")).toBe(false);
  });
});

describe("shouldTypeCommand", () => {
  it("redirects a plain character from anywhere else", () => {
    expect(shouldTypeCommand(key(), { tagName: "DIV" }, false)).toBe(true);
    expect(shouldTypeCommand(key(), null, false)).toBe(true);
  });

  it("leaves fields alone", () => {
    expect(shouldTypeCommand(key(), { tagName: "INPUT" }, false)).toBe(false);
    expect(shouldTypeCommand(key(), { tagName: "TEXTAREA" }, false)).toBe(false);
  });

  it("leaves dialogs alone", () => {
    expect(shouldTypeCommand(key(), { tagName: "DIV" }, true)).toBe(false);
  });

  it("leaves shortcuts alone", () => {
    expect(shouldTypeCommand(key({ ctrlKey: true }), { tagName: "DIV" }, false)).toBe(false);
    expect(shouldTypeCommand(key({ metaKey: true }), { tagName: "DIV" }, false)).toBe(false);
    expect(shouldTypeCommand(key({ altKey: true }), { tagName: "DIV" }, false)).toBe(false);
  });

  it("leaves named keys alone", () => {
    expect(shouldTypeCommand(key({ key: "Enter" }), { tagName: "DIV" }, false)).toBe(false);
    expect(shouldTypeCommand(key({ key: "Backspace" }), { tagName: "DIV" }, false)).toBe(false);
    expect(shouldTypeCommand(key({ key: " " }), { tagName: "DIV" }, false)).toBe(false);
  });

  it("leaves an IME composition alone", () => {
    expect(shouldTypeCommand(key({ isComposing: true }), { tagName: "DIV" }, false)).toBe(false);
  });
});
