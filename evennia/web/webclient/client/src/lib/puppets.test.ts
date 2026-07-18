import { describe, expect, it, vi } from "vitest";

import { PuppetScenes } from "./puppets.svelte";

function snapshot(npcId: number, slot: number, revision: number) {
  return {
    target: "puppets",
    meta: { npc_id: npcId, slot, revision, name: `NPC ${npcId}` },
    ops: [
      {
        op: "set",
        path: `/${npcId}/scene`,
        value: { room: { name: `Room ${npcId}` }, occupants: [], exits: [] },
      },
    ],
  };
}

describe("puppet scene protocol", () => {
  it("keys scene state by NPC id while slot remains display metadata", () => {
    const scenes = new PuppetScenes();

    scenes.apply(snapshot(123, 4, 1));
    scenes.apply({ ...snapshot(123, 2, 2), meta: { ...snapshot(123, 2, 2).meta, snapshot: true } });

    expect(scenes.list).toHaveLength(1);
    expect(scenes.list[0].npcId).toBe(123);
    expect(scenes.list[0].slot).toBe(2);
    expect(scenes.list[0].scene.room.name).toBe("Room 123");
  });

  it("teardown removes the complete feed", () => {
    const scenes = new PuppetScenes();
    scenes.apply(snapshot(123, 2, 1));

    scenes.apply({ target: "puppets", ops: [{ op: "del", path: "/123" }] });

    expect(scenes.list).toEqual([]);
  });

  it("manifest removes feeds left stale by a crash or reconnect", () => {
    const scenes = new PuppetScenes();
    scenes.apply(snapshot(123, 2, 1));
    scenes.apply(snapshot(456, 3, 1));

    scenes.apply({
      target: "puppets",
      ops: [
        {
          op: "sync",
          path: "/",
          ids: [456],
          entries: [{ npc_id: 456, slot: 2 }],
        },
      ],
      meta: { kind: "manifest" },
    });

    expect(scenes.list.map((feed) => feed.npcId)).toEqual([456]);
    expect(scenes.list[0].slot).toBe(2);
  });

  it("manifest establishes slot and name before the first scene snapshot", () => {
    const scenes = new PuppetScenes();

    scenes.apply({
      target: "puppets",
      ops: [
        {
          op: "sync",
          path: "/",
          ids: [71],
          entries: [{ npc_id: 71, slot: 2, name: "Kaeden Denzel" }],
        },
      ],
    });
    scenes.apply({
      target: "puppets",
      ops: [
        {
          op: "set",
          path: "/71/scene",
          value: { room: { name: "Unification area" }, occupants: [], exits: [] },
        },
      ],
    });

    expect(scenes.list).toHaveLength(1);
    expect(scenes.list[0].slot).toBe(2);
    expect(scenes.list[0].name).toBe("Kaeden Denzel");
  });

  it("applies contiguous deltas", () => {
    const scenes = new PuppetScenes();
    scenes.apply(snapshot(123, 2, 1));

    scenes.apply({
      target: "puppets",
      meta: { npc_id: 123, slot: 2, revision: 2 },
      ops: [
        {
          op: "add",
          path: "/123/scene/occupants/-",
          value: { handle: "person:7", name: "Visitor" },
        },
      ],
    });

    expect(scenes.list[0].scene.occupants).toEqual([
      { handle: "person:7", name: "Visitor" },
    ]);
    expect(scenes.list[0].revision).toBe(2);
  });

  it("requests one resync for a revision gap and ignores the delta", () => {
    const request = vi.fn();
    const scenes = new PuppetScenes(request);
    scenes.apply(snapshot(123, 2, 1));
    const gap = {
      target: "puppets",
      meta: { npc_id: 123, slot: 2, revision: 4 },
      ops: [{ op: "del", path: "/123/scene/occupants", handle: "person:7" }],
    };

    scenes.apply(gap);
    scenes.apply(gap);

    expect(request).toHaveBeenCalledOnce();
    expect(request).toHaveBeenCalledWith(123, 1);
    expect(scenes.list[0].revision).toBe(1);
  });

  it("accepts a full snapshot after a server revision reset", () => {
    const scenes = new PuppetScenes();
    scenes.apply(snapshot(123, 2, 20));
    const reset = snapshot(123, 2, 1);
    reset.meta.name = "After reload";

    scenes.apply(reset);

    expect(scenes.list[0].revision).toBe(1);
    expect(scenes.list[0].name).toBe("After reload");
  });
});
