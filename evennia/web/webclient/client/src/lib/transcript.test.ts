import { describe, expect, it } from "vitest";

// `?raw` rather than node:fs: the client's tsconfig types are vite/client only,
// and this keeps the test inside the same module graph as the code it checks.
import palette from "../styles/ansi-palette.css?raw";

import { xtermHex } from "./transcript";

// The HTML export falls back to these values whenever the document has no rule
// for a palette class. Wrong numbers there would not throw - the transcript
// would just come out in the wrong colours - so hold them against the
// stylesheet the client actually renders with.
function generated(prefix: string): Map<number, string> {
  const out = new Map<number, string>();
  const re = new RegExp(`\\.${prefix}(\\d{3})\\{[a-z-]+:(#[0-9a-f]{6})\\}`, "g");
  for (const m of palette.matchAll(re)) out.set(Number(m[1]), m[2]);
  return out;
}

describe("xterm-256 fallback palette", () => {
  it("matches the generated stylesheet for every foreground index", () => {
    const fg = generated("color-");
    expect(fg.size).toBe(256);
    for (const [i, hex] of fg) expect(xtermHex(i)).toBe(hex);
  });

  it("matches the generated stylesheet for every background index", () => {
    const bg = generated("bgcolor-");
    expect(bg.size).toBe(256);
    for (const [i, hex] of bg) expect(xtermHex(i)).toBe(hex);
  });

  it("covers the three ramps at their boundaries", () => {
    expect(xtermHex(0)).toBe("#000000"); // base 16
    expect(xtermHex(15)).toBe("#ffffff");
    expect(xtermHex(16)).toBe("#000000"); // cube, first
    expect(xtermHex(231)).toBe("#ffffff"); // cube, last
    expect(xtermHex(232)).toBe("#080808"); // greyscale, first
    expect(xtermHex(255)).toBe("#eeeeee"); // greyscale, last
  });
});
