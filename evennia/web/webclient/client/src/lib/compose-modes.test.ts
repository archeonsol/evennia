// The compose pad's mode -> command mapping is the difference between a pose
// reaching the room and a stray word hitting the command parser, so pin it.

import { describe, expect, it } from "vitest";
import { COMPOSE_MODES, composeToCommand, composeToPreview, specFor } from "./compose-modes";

describe("composeToCommand", () => {
  it("adds the pose verb marker", () => {
    expect(composeToCommand("pose", "waves")).toBe(".waves");
  });

  it("does not double up an existing pose marker", () => {
    // The emote parser treats a leading "." as significant; ".." is not "."
    expect(composeToCommand("pose", ".waves")).toBe(".waves");
    expect(composeToCommand("pose", ",waves")).toBe(",waves");
  });

  it("maps the other modes to their verbs", () => {
    expect(composeToCommand("emote", "waves")).toBe("emote waves");
    expect(composeToCommand("say", "hello")).toBe("say hello");
    expect(composeToCommand("looc", "brb")).toBe("looc brb");
    expect(composeToCommand("lookplace", "by the bar")).toBe("@lp by the bar");
  });

  it("is empty for an empty draft", () => {
    for (const m of COMPOSE_MODES) {
      expect(composeToCommand(m.id, "   ")).toBe("");
    }
  });

  it("trims before mapping", () => {
    expect(composeToCommand("say", "  hi  ")).toBe("say hi");
  });
});

describe("composeToPreview", () => {
  it("names the mode the server previews under", () => {
    expect(composeToPreview("looc", "brb")).toBe("@preview_rp looc brb");
  });

  it("sends nothing for an empty draft", () => {
    expect(composeToPreview("pose", "")).toBe("");
    expect(composeToPreview("pose", "  ")).toBe("");
  });

  it("covers every mode the pad offers", () => {
    // A mode with no preview string would silently show a stale preview.
    for (const m of COMPOSE_MODES) {
      expect(composeToPreview(m.id, "x")).toBe(`@preview_rp ${m.id} x`);
    }
  });
});

describe("specFor", () => {
  it("returns the matching spec", () => {
    expect(specFor("say").label).toBe("Say");
  });

  it("falls back rather than returning undefined", () => {
    expect(specFor("nonsense" as never).id).toBe("pose");
  });
});
