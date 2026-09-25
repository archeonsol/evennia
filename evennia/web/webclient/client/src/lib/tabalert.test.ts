import { describe, expect, it } from "vitest";
import { FLASH_MS, TabAlert, alertTitle, type TabAlertMode } from "./tabalert";

function harness(mode: TabAlertMode = "any", calm = false) {
  const titles: string[] = [];
  const badges: boolean[] = [];
  let tickFn: (() => void) | null = null;
  let cleared = 0;
  const alert = new TabAlert("Underspire", {
    setTitle: (t) => titles.push(t),
    setBadge: (on) => badges.push(on),
    mode: () => mode,
    calm: () => calm,
    setInterval: (fn, ms) => {
      expect(ms).toBe(FLASH_MS);
      tickFn = fn;
      return 1;
    },
    clearInterval: () => {
      tickFn = null;
      cleared++;
    },
  });
  return {
    alert,
    titles,
    badges,
    tick: () => tickFn?.(),
    get running() {
      return tickFn !== null;
    },
    get cleared() {
      return cleared;
    },
  };
}

describe("alertTitle", () => {
  it("is the plain title with nothing pending", () => {
    expect(alertTitle("U", 0, false, true)).toBe("U");
  });
  it("counts direct messages on the steady phase", () => {
    expect(alertTitle("U", 3, true, false)).toBe("(3) U");
  });
  it("marks other activity on the steady phase", () => {
    expect(alertTitle("U", 0, true, false)).toBe("● U");
  });
  it("says what is new on the lit phase", () => {
    expect(alertTitle("U", 2, false, true)).toBe("▶ New message · U");
    expect(alertTitle("U", 0, true, true)).toBe("▶ New activity · U");
  });
});

describe("TabAlert", () => {
  it("flashes on new output and blinks until cleared", () => {
    const h = harness();
    h.alert.activity();
    expect(h.titles.at(-1)).toBe("▶ New activity · Underspire");
    expect(h.badges.at(-1)).toBe(true);
    h.tick();
    expect(h.titles.at(-1)).toBe("● Underspire");
    h.tick();
    expect(h.titles.at(-1)).toBe("▶ New activity · Underspire");
    h.alert.clear();
    expect(h.titles.at(-1)).toBe("Underspire");
    expect(h.badges.at(-1)).toBe(false);
    expect(h.running).toBe(false);
  });

  it("does not restart the blink for every line", () => {
    const h = harness();
    h.alert.activity();
    const n = h.titles.length;
    h.alert.activity();
    h.alert.activity();
    expect(h.titles.length).toBe(n);
  });

  it("counts direct messages and names them over plain activity", () => {
    const h = harness();
    h.alert.activity();
    h.alert.direct();
    h.alert.direct();
    expect(h.alert.count).toBe(2);
    expect(h.titles.at(-1)).toBe("▶ New message · Underspire");
    h.tick();
    expect(h.titles.at(-1)).toBe("(2) Underspire");
  });

  it("ignores ordinary output in direct-only mode", () => {
    const h = harness("direct");
    h.alert.activity();
    expect(h.titles).toEqual([]);
    expect(h.alert.alerting).toBe(false);
    h.alert.direct();
    expect(h.titles.at(-1)).toBe("▶ New message · Underspire");
    expect(h.running).toBe(true);
  });

  it("keeps the old unread count but never blinks when off", () => {
    const h = harness("off");
    h.alert.activity();
    expect(h.titles).toEqual([]);
    h.alert.direct();
    expect(h.titles.at(-1)).toBe("(1) Underspire");
    expect(h.running).toBe(false);
    expect(h.badges).toEqual([]);
  });

  it("holds a steady marker under reduced motion", () => {
    const h = harness("any", true);
    h.alert.activity();
    expect(h.titles.at(-1)).toBe("● Underspire");
    expect(h.running).toBe(false);
    h.alert.direct();
    expect(h.titles.at(-1)).toBe("(1) Underspire");
  });

  it("leaves the title alone when cleared with nothing pending", () => {
    const h = harness();
    h.alert.clear();
    expect(h.titles).toEqual([]);
    expect(h.badges).toEqual([]);
  });
});
