import { describe, expect, it } from "vitest";
import { REPIN_PX, pinAfterScroll } from "./autoscroll";

describe("pinAfterScroll", () => {
  it("keeps pinning when content grows past the threshold after our own write", () => {
    // The bug: our write set top=1000, more lines land before the scroll event
    // is delivered, so the gap balloons. This must not read as user intent.
    expect(pinAfterScroll(true, 5000, 1000, 1000)).toBe(true);
  });

  it("unpins when the user scrolls up while pinned", () => {
    expect(pinAfterScroll(true, 500, 400, 1000)).toBe(false);
  });

  it("tolerates sub-pixel drift on our own restore", () => {
    expect(pinAfterScroll(true, 500, 998, 1000)).toBe(true);
  });

  it("re-pins on reaching the bottom", () => {
    expect(pinAfterScroll(false, REPIN_PX, 300, 1000)).toBe(true);
  });

  it("stays unpinned while the user scrolls down but is still far from the bottom", () => {
    expect(pinAfterScroll(false, 500, 1002, 1000)).toBe(false);
  });

  it("stays unpinned as long as the user stays away from the bottom", () => {
    expect(pinAfterScroll(false, 500, 200, 1000)).toBe(false);
  });
});
