// The compose pad's mode -> command mapping is the difference between a pose
// reaching the room and a stray word hitting the command parser, so pin it.

import { describe, expect, it } from "vitest";
import {
  COMPOSE_MODES,
  composeToCommand,
  composeToPreview,
  specFor,
  withBreaks,
} from "./compose-modes";

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

  it("sends the lines of a pose or emote as one command, joined by the game's break", () => {
    expect(composeToCommand("pose", "waves.\nsmiles.")).toBe(".waves.|/smiles.");
    expect(composeToCommand("emote", "waves.\r\nsmiles.")).toBe("emote waves.|/smiles.");
  });

  it("keeps a pose that already starts with its marker, across lines", () => {
    expect(composeToCommand("pose", ".waves.\nsmiles.")).toBe(".waves.|/smiles.");
  });

  it("drops blank lines and trims each line", () => {
    expect(composeToCommand("pose", "  waves.  \n\n   \n  smiles.  ")).toBe(".waves.|/smiles.");
  });

  it("makes a say, LOOC or look one line", () => {
    expect(composeToCommand("say", "hello\nthere")).toBe("say hello there");
    expect(composeToCommand("looc", "brb\n\nback soon")).toBe("looc brb back soon");
    expect(composeToCommand("lookplace", "by the bar\nunder a lamp")).toBe(
      "@lp by the bar under a lamp",
    );
  });

  it("never sends a newline", () => {
    for (const m of COMPOSE_MODES) {
      expect(composeToCommand(m.id, "one\ntwo\r\nthree\rfour")).not.toMatch(/[\r\n]/);
    }
  });
});

describe("withBreaks", () => {
  it("leaves a one-line draft as it was, trimmed", () => {
    expect(withBreaks("pose", "  waves  ")).toBe("waves");
    expect(withBreaks("say", "hi")).toBe("hi");
  });

  it("does not touch a break the writer already typed", () => {
    expect(withBreaks("pose", "waves.|/smiles.")).toBe("waves.|/smiles.");
  });

  it("is empty for nothing", () => {
    expect(withBreaks("pose", "")).toBe("");
    expect(withBreaks("pose", "\n \n")).toBe("");
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

  it("previews the lines of a pose the way the send will read them", () => {
    expect(composeToPreview("pose", "waves.\nsmiles.")).toBe("@preview_rp pose waves.|/smiles.");
    expect(composeToPreview("say", "hello\nthere")).toBe("@preview_rp say hello there");
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
