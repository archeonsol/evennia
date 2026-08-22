import { describe, expect, it } from "vitest";

import { composePreviewToHtml } from "./compose-preview";

describe("composePreviewToHtml", () => {
  it("renders Evennia colour tags instead of showing them literally", () => {
    expect(composePreviewToHtml("|cYou|n wave your hand.")).toBe(
      '<span class="color-014">You</span> wave your hand.',
    );
  });

  it("escapes HTML in preview text", () => {
    expect(composePreviewToHtml("|cYou|n <script>alert(1)</script>")).toBe(
      '<span class="color-014">You</span> &lt;script&gt;alert(1)&lt;/script&gt;',
    );
  });
});
