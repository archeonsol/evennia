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

  function feedEnv(npcId: number, body: string, revision = 1) {
    return {
      target: "puppets",
      meta: { npc_id: npcId, slot: 2, name: `NPC ${npcId}`, revision },
      ops: [{ op: "add", path: `/${npcId}/feed/-`, value: { body, meta: { revision } } }],
    };
  }

  it("appends prose to a per-NPC feed and counts it unread", () => {
    const scenes = new PuppetScenes();
    scenes.apply(snapshot(71, 2, 1));

    scenes.apply(feedEnv(71, "Bob waves."));
    scenes.apply(feedEnv(71, "Bob leaves.", 2));

    const feed = scenes.feeds.get("71")!;
    expect(feed.feed.map((line) => line.body)).toEqual(["Bob waves.", "Bob leaves."]);
    expect(feed.unread).toBe(2);
  });

  it("opening a terminal clears its unread and marks it active", () => {
    const request = vi.fn();
    const scenes = new PuppetScenes(request);
    scenes.apply(feedEnv(71, "Bob waves."));
    expect(scenes.feeds.get("71")!.unread).toBe(1);

    scenes.setActive(71);

    expect(scenes.activeId).toBe(71);
    expect(scenes.feeds.get("71")!.unread).toBe(0);
    expect(request).toHaveBeenCalledWith(71, expect.any(Number));

    // Feed for the active terminal does not accrue unread.
    scenes.apply(feedEnv(71, "Bob returns.", 2));
    expect(scenes.feeds.get("71")!.unread).toBe(0);
  });

  it("clearFeed wipes scrollback but keeps the puppet and its scene", () => {
    const scenes = new PuppetScenes();
    scenes.apply(snapshot(71, 2, 1));
    scenes.apply(feedEnv(71, "Bob waves."));

    scenes.clearFeed(71);

    const feed = scenes.feeds.get("71")!;
    expect(feed.feed).toEqual([]);
    expect(feed.scene.room.name).toBe("Room 71");
    expect(scenes.list).toHaveLength(1);
  });

  it("setManifest establishes the roster and prunes stale feeds", () => {
    const scenes = new PuppetScenes();
    scenes.apply(snapshot(71, 2, 1));
    scenes.apply(snapshot(72, 3, 1));

    scenes.setManifest([{ npc_id: 72, slot: 2, name: "Kept" }]);

    expect(scenes.list.map((feed) => feed.npcId)).toEqual([72]);
    expect(scenes.list[0].name).toBe("Kept");
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
