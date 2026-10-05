// The Reading page shows the game's settings and changes them through typed
// requests. The game is the source of truth, so these pin that the page only
// ever shows what the game answered, and that a refusal or a late answer never
// leaves it claiming a choice that was not made.

import { describe, expect, it } from "vitest";

import {
  aimedSample,
  aimedValue,
  coloured,
  coloursWith,
  label,
  speechSample,
  type ReadingAnswer,
  type ReadingValues,
} from "./reading";
import { ReadingStore } from "./reading.svelte";

const PLAIN: ReadingValues = {
  you: true,
  speech: "",
  speech_name: "",
  aimed_colour: "",
  aimed_colour_name: "",
  aimed_marker: false,
  spacing: false,
};

const COLOURS = [
  { name: "red", code: "|r" },
  { name: "dark red", code: "|R" },
  { name: "orange", code: "|520" },
];

function answer(over: Partial<ReadingValues> = {}): ReadingAnswer {
  return { settings: { ...PLAIN, ...over }, colours: COLOURS };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("aimedValue", () => {
  it("sends a colour, a marker, both, or off", () => {
    expect(aimedValue("|r", false)).toBe("|r");
    expect(aimedValue("", true)).toBe("marker");
    expect(aimedValue("|r", true)).toBe("|r marker");
    expect(aimedValue("", false)).toBe("off");
  });
});

describe("coloursWith", () => {
  it("leaves the offered colours alone when the current one is among them", () => {
    expect(coloursWith(COLOURS, "|r", "red")).toBe(COLOURS);
    expect(coloursWith(COLOURS, "", "")).toBe(COLOURS);
  });

  it("adds a colour that was set by its code, so the menu can show it", () => {
    const shown = coloursWith(COLOURS, "#ff8800", "ff8800");

    expect(shown).toHaveLength(COLOURS.length + 1);
    expect(shown.at(-1)).toEqual({ name: "ff8800", code: "#ff8800" });
    // The offered list itself is not changed.
    expect(COLOURS).toHaveLength(3);
  });

  it("names an unnamed code by the code without its pipe", () => {
    expect(coloursWith(COLOURS, "|345", "").at(-1)).toEqual({ name: "345", code: "|345" });
  });
});

describe("label", () => {
  it("opens a colour word with a capital", () => {
    expect(label("dark red")).toBe("Dark red");
    expect(label("")).toBe("");
  });
});

describe("samples", () => {
  it("colours only the words that are said", () => {
    expect(speechSample({ ...PLAIN, speech: "|r" })).toBe('Rook says, "|rQuiet night.|n"');
    expect(speechSample(PLAIN)).toBe('Rook says, "Quiet night."');
  });

  it("marks and colours a line aimed at the player", () => {
    expect(aimedSample({ ...PLAIN, aimed_colour: "|y", aimed_marker: true })).toBe(
      "|y» Rook nods to you.|n",
    );
    expect(aimedSample({ ...PLAIN, aimed_marker: true })).toBe("» Rook nods to you.");
    expect(aimedSample({ ...PLAIN, aimed_colour: "|y" })).toBe("|yRook nods to you.|n");
    expect(aimedSample(PLAIN)).toBe("Rook nods to you.");
  });

  it("leaves text plain with no colour", () => {
    expect(coloured("", "x")).toBe("x");
    expect(coloured("|g", "x")).toBe("|gx|n");
  });
});

describe("ReadingStore", () => {
  it("starts with nothing shown and asks the game when it loads", async () => {
    const asked: [string, unknown][] = [];
    const store = new ReadingStore(async (action, data) => {
      asked.push([action, data]);
      return answer({ spacing: true });
    });
    expect(store.state).toBe("idle");
    expect(store.values).toBeNull();

    await store.load();

    expect(asked).toEqual([["reading_get", undefined]]);
    expect(store.state).toBe("ready");
    expect(store.values?.spacing).toBe(true);
    expect(store.colours).toEqual(COLOURS);
  });

  it("says it is loading until the first answer", async () => {
    const wait = deferred<ReadingAnswer>();
    const store = new ReadingStore(() => wait.promise);

    const loading = store.load();
    expect(store.state).toBe("loading");

    wait.resolve(answer());
    await loading;
    expect(store.state).toBe("ready");
  });

  it("is unavailable when the game does not answer", async () => {
    const store = new ReadingStore(async () => {
      throw new Error("no such action");
    });

    await store.load();

    expect(store.state).toBe("unavailable");
    expect(store.values).toBeNull();
  });

  it("keeps showing what it had when a later load fails", async () => {
    let up = true;
    const store = new ReadingStore(async () => {
      if (!up) throw new Error("offline");
      return answer({ you: false });
    });
    await store.load();
    up = false;

    await store.load();

    expect(store.state).toBe("ready");
    expect(store.values?.you).toBe(false);
  });

  it("sends a change and shows the game's answer to it", async () => {
    const asked: [string, unknown][] = [];
    const store = new ReadingStore(async (action, data) => {
      asked.push([action, data]);
      return answer({ speech: "|r", speech_name: "red" });
    });

    const took = await store.set("speech", "|r");

    expect(took).toBe(true);
    expect(asked).toEqual([["reading_set", { setting: "speech", value: "|r" }]]);
    expect(store.values?.speech).toBe("|r");
    expect(store.problem).toBe("");
    expect(store.busy).toBe(false);
  });

  it("is busy while a change is on its way", async () => {
    const wait = deferred<ReadingAnswer>();
    const store = new ReadingStore(() => wait.promise);

    const change = store.set("you", "off");
    expect(store.busy).toBe(true);

    wait.resolve(answer({ you: false }));
    await change;
    expect(store.busy).toBe(false);
  });

  it("puts every setting back with reset", async () => {
    const asked: [string, unknown][] = [];
    const store = new ReadingStore(async (action, data) => {
      asked.push([action, data]);
      return answer();
    });

    await store.set("reset");

    expect(asked).toEqual([["reading_set", { setting: "reset", value: "" }]]);
  });

  it("shows the game's reason when it refuses a change, and asks again for the truth", async () => {
    const asked: string[] = [];
    const store = new ReadingStore(async (action) => {
      asked.push(action);
      if (action === "reading_set") throw new Error("Say on or off for you.");
      return answer({ spacing: true });
    });

    const took = await store.set("you", "maybe");

    expect(took).toBe(false);
    expect(store.problem).toBe("Say on or off for you.");
    expect(store.busy).toBe(false);
    expect(asked).toEqual(["reading_set", "reading_get"]);
    await new Promise((done) => setTimeout(done, 0));
    // What is shown is what the game has, not what was asked for.
    expect(store.values?.spacing).toBe(true);
    expect(store.values?.you).toBe(true);
  });

  it("gives a plain reason when the refusal has none", async () => {
    const store = new ReadingStore(async () => {
      throw undefined;
    });

    await store.set("you", "off");

    expect(store.problem).toBe("That change did not go through.");
  });

  it("clears the last refusal when the next change is made", async () => {
    let refuse = true;
    const store = new ReadingStore(async (action) => {
      if (action === "reading_set" && refuse) throw new Error("No.");
      return answer();
    });
    await store.set("you", "maybe");
    expect(store.problem).toBe("No.");

    refuse = false;
    await store.set("you", "off");

    expect(store.problem).toBe("");
  });

  it("shows the newest change's answer even when an older one arrives last", async () => {
    const first = deferred<ReadingAnswer>();
    const second = deferred<ReadingAnswer>();
    const waits = [first, second];
    const store = new ReadingStore(() => waits.shift()!.promise);

    const a = store.set("spacing", "on");
    const b = store.set("spacing", "off");
    expect(store.busy).toBe(true);

    second.resolve(answer({ spacing: false }));
    await b;
    first.resolve(answer({ spacing: true }));
    await a;

    expect(store.values?.spacing).toBe(false);
    expect(store.busy).toBe(false);
  });
});
