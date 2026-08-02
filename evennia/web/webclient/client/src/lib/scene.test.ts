import { describe, expect, it } from "vitest";

import { emptyScene, reduceScene, type SceneState } from "./scene-ops";

/** Apply a patch to a fresh scene, or to `from` when continuing a sequence. */
function apply(ops: any[], meta?: Record<string, any>, from?: SceneState): SceneState {
  return reduceScene(from ?? emptyScene(), "scene", ops, meta);
}

describe("reduceScene", () => {
  it("ignores patches for another target", () => {
    const before = emptyScene();
    const after = reduceScene(before, "puppets", [
      { op: "set", path: "/room/name", value: "nope" },
    ]);
    expect(after).toBe(before);
  });

  it("applies a whole-scene snapshot", () => {
    const s = apply([
      {
        op: "set",
        path: "/",
        value: {
          room: { name: "The Floor" },
          occupants: [{ handle: "h1", name: "someone" }],
          exits: [{ key: "out", name: "out" }],
          fields: { eta: 40 },
        },
      },
    ]);
    expect(s.room.name).toBe("The Floor");
    expect(s.occupants).toHaveLength(1);
    expect(s.exits).toHaveLength(1);
    expect(s.fields.eta).toBe(40);
    expect(s.present).toBe(true);
  });

  it("sets a single room key without dropping the others", () => {
    let s = apply([{ op: "set", path: "/room", value: { name: "A", desc: "d" } }]);
    s = apply([{ op: "set", path: "/room/name", value: "B" }], undefined, s);
    expect(s.room.name).toBe("B");
    expect(s.room.desc).toBe("d");
  });

  it("sets, overwrites and deletes a single live field", () => {
    let s = apply([{ op: "set", path: "/fields/eta", value: 40 }]);
    s = apply([{ op: "set", path: "/fields/dose", value: 3 }], undefined, s);
    expect(s.fields).toEqual({ eta: 40, dose: 3 });

    s = apply([{ op: "set", path: "/fields/eta", value: 39 }], undefined, s);
    expect(s.fields.eta).toBe(39);

    s = apply([{ op: "del", path: "/fields/eta" }], undefined, s);
    expect(s.fields).toEqual({ dose: 3 });
  });

  it("replaces the whole field bag", () => {
    let s = apply([{ op: "set", path: "/fields/eta", value: 40 }]);
    s = apply([{ op: "set", path: "/fields", value: { other: 1 } }], undefined, s);
    expect(s.fields).toEqual({ other: 1 });
  });

  it("adds an occupant once per handle", () => {
    const op = { op: "add", path: "/occupants/-", value: { handle: "h1", name: "x" } };
    let s = apply([op]);
    s = apply([op], undefined, s);
    expect(s.occupants).toHaveLength(1);
  });

  it("removes an occupant by handle", () => {
    let s = apply([
      { op: "add", path: "/occupants/-", value: { handle: "h1", name: "x" } },
      { op: "add", path: "/occupants/-", value: { handle: "h2", name: "y" } },
    ]);
    s = apply([{ op: "del", path: "/occupants", handle: "h1" }], undefined, s);
    expect(s.occupants.map((o) => o.handle)).toEqual(["h2"]);
  });

  it("replaces the exit list", () => {
    const s = apply([{ op: "set", path: "/exits", value: [{ key: "n", name: "north" }] }]);
    expect(s.exits).toEqual([{ key: "n", name: "north" }]);
  });

  it("tracks the scene revision from patch meta", () => {
    const s = apply([{ op: "set", path: "/fields/eta", value: 1 }], { rev: 7 });
    expect(s.rev).toBe(7);
  });

  it("applies ops in order within one patch", () => {
    const s = apply([
      { op: "set", path: "/fields/eta", value: 1 },
      { op: "set", path: "/fields/eta", value: 2 },
    ]);
    expect(s.fields.eta).toBe(2);
  });

  it("does not mutate the snapshot it was given", () => {
    const before = emptyScene();
    apply([{ op: "set", path: "/fields/eta", value: 1 }], undefined, before);
    expect(before.fields).toEqual({});
  });

  it("survives an empty or missing op list", () => {
    expect(() => apply([])).not.toThrow();
    expect(() => apply(undefined as any)).not.toThrow();
  });
});
