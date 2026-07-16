import { describe, expect, it } from "vitest";

import { sliceChunks } from "./typewriter";

describe("sliceChunks", () => {
  const chunks = ["ab", "cde"]; // two colour-span text nodes

  it("reveals nothing at target 0", () => {
    expect(sliceChunks(chunks, 0)).toEqual(["", ""]);
  });

  it("fills the first node before touching the second", () => {
    expect(sliceChunks(chunks, 1)).toEqual(["a", ""]);
    expect(sliceChunks(chunks, 2)).toEqual(["ab", ""]);
  });

  it("carries the overflow into the next node", () => {
    expect(sliceChunks(chunks, 3)).toEqual(["ab", "c"]);
  });

  it("reveals everything once target reaches the total", () => {
    expect(sliceChunks(chunks, 5)).toEqual(["ab", "cde"]);
  });

  it("clamps a target past the end", () => {
    expect(sliceChunks(chunks, 99)).toEqual(["ab", "cde"]);
  });
});
