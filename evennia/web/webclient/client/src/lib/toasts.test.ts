import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// A stand-in for the page: enough of `document` for the visibility pause.
function fakeDocument() {
  const listeners: (() => void)[] = [];
  return {
    hidden: false,
    addEventListener: (_: string, fn: () => void) => listeners.push(fn),
    setHidden(this: { hidden: boolean }, hidden: boolean) {
      this.hidden = hidden;
      for (const fn of listeners) fn();
    },
  };
}

let doc: ReturnType<typeof fakeDocument>;
let toasts: typeof import("./toasts.svelte").toasts;

beforeEach(async () => {
  vi.useFakeTimers();
  vi.resetModules();
  doc = fakeDocument();
  vi.stubGlobal("document", doc);
  ({ toasts } = await import("./toasts.svelte"));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("how long a toast stays", () => {
  it("stays twenty seconds", () => {
    toasts.push("kudos", "Kudos", "", false);
    vi.advanceTimersByTime(19_000);
    expect(toasts.list).toHaveLength(1);
    vi.advanceTimersByTime(1_000);
    expect(toasts.list).toHaveLength(0);
  });

  it("waits while the tab is hidden", () => {
    toasts.push("kudos", "Kudos", "", false);
    vi.advanceTimersByTime(5_000);
    doc.setHidden(true);
    vi.advanceTimersByTime(60_000);
    expect(toasts.list).toHaveLength(1);
    doc.setHidden(false);
    vi.advanceTimersByTime(14_000);
    expect(toasts.list).toHaveLength(1);
    vi.advanceTimersByTime(1_000);
    expect(toasts.list).toHaveLength(0);
  });

  it("does not start counting a toast that arrives in a hidden tab", () => {
    doc.setHidden(true);
    toasts.push("kudos", "Kudos", "", false);
    vi.advanceTimersByTime(60_000);
    expect(toasts.list).toHaveLength(1);
  });

  it("keeps waiting when the pointer leaves a hidden tab's stack", () => {
    toasts.push("kudos", "Kudos", "", false);
    toasts.hold();
    doc.setHidden(true);
    toasts.release();
    vi.advanceTimersByTime(60_000);
    expect(toasts.list).toHaveLength(1);
  });

  it("keeps only the newest five", () => {
    doc.setHidden(true);
    for (let i = 1; i <= 7; i++) toasts.push("kudos", `Kudos ${i}`, "", false);
    expect(toasts.list.map((t) => t.title)).toEqual(["Kudos 3", "Kudos 4", "Kudos 5", "Kudos 6", "Kudos 7"]);
  });
});
