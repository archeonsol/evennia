import { describe, expect, it } from "vitest";
import { renderNodeHtml } from "./render";

describe("renderNodeHtml", () => {
  it.each(["Ann", "R&D", "$'", 'a"b'])("references do not change rendered text: %s", (name) => {
    const html = `<div>Annex R&amp;D $' a"b</div>`;
    expect(renderNodeHtml({ html, refs: [{ name, handle: "e1" }] })).toBe(html);
  });

  it("preserves authoritative empty HTML over colored blocks", () => {
    expect(renderNodeHtml({ html: "", blocks: [{ type: "line", text: "|rred|n" }] })).toBe("");
  });
});
