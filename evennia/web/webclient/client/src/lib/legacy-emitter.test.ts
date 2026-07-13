import { describe, expect, it, vi } from "vitest";

import { createLegacyEmitter } from "./legacy-emitter";

describe("legacy emitter", () => {
  it("delivers editor OOB arguments to the registered listener", () => {
    const emitter = createLegacyEmitter();
    const listener = vi.fn();
    emitter.on("editor_open", listener);

    emitter.emit("editor_open", ["sid", "draft", { mode: "prose" }], {});

    expect(listener).toHaveBeenCalledWith(["sid", "draft", { mode: "prose" }], {});
  });

  it("stops delivery after off", () => {
    const emitter = createLegacyEmitter();
    const listener = vi.fn();
    emitter.on("editor_close", listener);
    emitter.off("editor_close");

    emitter.emit("editor_close", ["sid"], {});

    expect(listener).not.toHaveBeenCalled();
  });
});
