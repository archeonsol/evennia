import { describe, expect, it } from "vitest";
import { helpPlain, helpTextToHtml, reflow, sectionAnchor } from "./helpText";

describe("helpTextToHtml", () => {
  it("turns help references into in-panel links, not commands", () => {
    const html = helpTextToHtml("For clothing, see |whelp tailoring|n.");
    expect(html).toContain('<a class="help-link" href="#" data-help="tailoring">help tailoring</a>');
    expect(html).not.toContain("mxplink");
    expect(html).not.toContain("Evennia.msg");
  });

  it("keeps multi-word topics and other markup", () => {
    const html = helpTextToHtml("|wwear <coat>|n and |whelp great houses|n");
    expect(html).toContain('data-help="great houses"');
    expect(html).toContain("&lt;coat&gt;");
  });

  it("quotes attribute values safely", () => {
    const html = helpTextToHtml('|whelp a"b|n');
    expect(html).toContain('data-help="a&quot;b"');
  });

  it("returns empty for nothing", () => {
    expect(helpTextToHtml("")).toBe("");
    expect(helpTextToHtml(undefined)).toBe("");
  });
});

describe("reflow", () => {
  it("joins hard-wrapped prose into one line", () => {
    expect(reflow("Put on carried\nclothing or armor.")).toBe("Put on carried clothing or armor.");
  });

  it("keeps syntax lines and indented output as written", () => {
    const syntax = "|wwear <x>|n\n|wdon <x>|n";
    expect(reflow(syntax)).toBe(syntax);
    const example = "Example:\n  You see: a\n  Others see: b";
    expect(reflow(example)).toBe(example);
  });

  it("treats each paragraph on its own", () => {
    expect(reflow("a\nb\n\n|wc|n\n|wd|n")).toBe("a b\n\n|wc|n\n|wd|n");
  });
});

describe("helpPlain", () => {
  it("strips colour and link markup", () => {
    expect(helpPlain("|lchelp x|lt|wx|n|le and ||")).toBe("x and |");
  });
});

describe("sectionAnchor", () => {
  it("makes stable ids", () => {
    expect(sectionAnchor("hs text")).toBe("help-sec-hs-text");
    expect(sectionAnchor("@naked")).toBe("help-sec-naked");
  });
});
