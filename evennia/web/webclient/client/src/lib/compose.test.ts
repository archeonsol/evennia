import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { compose, type ComposePreview } from "./compose.svelte";
import type { ComposePreviewRequest } from "./compose-modes";

describe("compose preview requests", () => {
  let answers: Array<(p: Partial<ComposePreview>) => void>;
  let sent: ComposePreviewRequest[];

  beforeEach(() => {
    vi.useFakeTimers();
    answers = [];
    sent = [];
    compose.setPreviewRequest((req) => {
      sent.push(req);
      return new Promise((resolve) => answers.push(resolve));
    });
    compose.useAccount(null);
    compose.show();
  });
  afterEach(() => {
    compose.hide();
    vi.useRealTimers();
  });

  it("asks for the draft's mode and text once typing pauses", () => {
    compose.setMode("say");
    compose.setText("hello\nthere");
    expect(sent).toEqual([]);
    vi.runAllTimers();
    expect(sent).toEqual([{ mode: "say", text: "hello there" }]);
  });

  it("shows only the answer to the latest request", async () => {
    compose.setText("waves");
    vi.runAllTimers();
    compose.setText("waves twice");
    vi.runAllTimers();

    answers[1]({ you: "new" });
    answers[0]({ you: "old" });
    await vi.runAllTimersAsync();

    expect(compose.preview.you).toBe("new");
  });

  it("drops an answer that lands after the draft was sent", async () => {
    compose.setText("waves");
    vi.runAllTimers();
    compose.take();

    answers[0]({ you: "late" });
    await vi.runAllTimersAsync();

    expect(compose.preview.you).toBe("");
  });

  it("says so when the request fails", async () => {
    compose.setPreviewRequest(() => Promise.reject(new Error("offline")));
    compose.setText("waves");
    vi.runAllTimers();
    await vi.runAllTimersAsync();

    expect(compose.preview.error).toBe("Preview unavailable.");
  });
});
