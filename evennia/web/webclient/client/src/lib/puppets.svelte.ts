import type { Occupant, SceneExit, SceneRoom } from "./scene.svelte";
import { SvelteMap } from "svelte/reactivity";

export interface PuppetScene {
  room: SceneRoom;
  occupants: Occupant[];
  exits: SceneExit[];
}

export interface FeedLine {
  body: string;
  revision: number;
  actionId?: string;
}

export interface PuppetFeed {
  npcId: number;
  slot: number;
  name: string;
  revision: number;
  actionId?: string;
  scene: PuppetScene;
  resyncing: boolean;
  feed: FeedLine[];
  unread: number;
}

type ResyncRequester = (npcId: number, revision: number) => void;

const MAX_FEED_LINES = 300;

function npcIdFromPath(path: string): number | null {
  const match = /^\/(\d+)(?:\/|$)/.exec(path);
  return match ? Number(match[1]) : null;
}

function blankFeed(npcId: number, slot: number, name: string): PuppetFeed {
  return {
    npcId,
    slot,
    name,
    revision: 0,
    scene: { room: {}, occupants: [], exits: [] },
    resyncing: false,
    feed: [],
    unread: 0,
  };
}

export class PuppetScenes {
  feeds = new SvelteMap<string, PuppetFeed>();
  // Which terminal is open. Plain (not a rune): the component owns the reactive
  // copy for rendering and mirrors it here so appendFeed can suppress unread for
  // the focused NPC. Keeping this store rune-free keeps it unit-testable in vitest.
  activeId: number | null = null;
  private requestResync: ResyncRequester | null;

  constructor(requestResync: ResyncRequester | null = null) {
    this.requestResync = requestResync;
  }

  get list(): PuppetFeed[] {
    return [...this.feeds.values()].sort((a, b) => a.slot - b.slot || a.npcId - b.npcId);
  }

  get totalUnread(): number {
    let total = 0;
    for (const feed of this.feeds.values()) total += feed.unread;
    return total;
  }

  setResyncRequester(requester: ResyncRequester): void {
    this.requestResync = requester;
  }

  /** Focus (or clear) the open terminal: clear its unread and pull a fresh scene. */
  setActive(npcId: number | null): void {
    this.activeId = npcId;
    if (npcId == null) return;
    this.markRead(npcId);
    const current = this.feeds.get(String(npcId));
    this.requestResync?.(npcId, current?.revision ?? 0);
  }

  /** A resync request never landed: drop the in-flight flag so it can retry. */
  resyncFailed(npcId: number): void {
    const key = String(npcId);
    const current = this.feeds.get(key);
    if (current?.resyncing) this.feeds.set(key, { ...current, resyncing: false });
  }

  /** Wipe one NPC terminal's scrollback (the structured scene is untouched). */
  clearFeed(npcId: number): void {
    const key = String(npcId);
    const current = this.feeds.get(key);
    if (current && current.feed.length) this.feeds.set(key, { ...current, feed: [] });
  }

  markRead(npcId: number): void {
    const key = String(npcId);
    const current = this.feeds.get(key);
    if (current && current.unread) this.feeds.set(key, { ...current, unread: 0 });
  }

  /** Populate/refresh the roster from a puppet_manifest RPC reply. */
  setManifest(entries: Array<{ npc_id: number; slot: number; name?: string }>): void {
    const keep = new Set(entries.map((entry) => String(entry.npc_id)));
    for (const key of this.feeds.keys()) {
      if (!keep.has(key)) this.feeds.delete(key);
    }
    for (const entry of entries) {
      const key = String(entry.npc_id);
      const current = this.feeds.get(key);
      this.feeds.set(
        key,
        current
          ? { ...current, slot: Number(entry.slot), name: String(entry.name ?? current.name) }
          : blankFeed(Number(entry.npc_id), Number(entry.slot), String(entry.name ?? `#${entry.npc_id}`)),
      );
    }
  }

  private appendFeed(ops: any[], meta: Record<string, any>): void {
    for (const op of ops) {
      const npcId = npcIdFromPath(String(op.path ?? ""));
      if (npcId == null) continue;
      const key = String(npcId);
      const value = op.value ?? {};
      const lineMeta = value.meta ?? meta;
      const line: FeedLine = {
        body: String(value.body ?? ""),
        revision: Number(lineMeta.revision ?? 0),
        actionId: lineMeta.action_id ?? undefined,
      };
      const current =
        this.feeds.get(key) ??
        blankFeed(npcId, Number(lineMeta.slot ?? 0), String(lineMeta.name ?? `#${npcId}`));
      const feed = [...current.feed, line].slice(-MAX_FEED_LINES);
      const unread = this.activeId === npcId ? 0 : current.unread + 1;
      this.feeds.set(key, { ...current, feed, unread });
    }
  }

  apply(env: Record<string, any>): void {
    if (env.target !== "puppets") return;
    const ops = Array.isArray(env.ops) ? env.ops : [];

    const manifest = ops.find((op: any) => op.op === "sync" && op.path === "/");
    if (manifest) {
      const keep = new Set((manifest.ids ?? []).map((id: any) => String(id)));
      for (const key of this.feeds.keys()) {
        if (!keep.has(key)) this.feeds.delete(key);
      }
      for (const entry of manifest.entries ?? []) {
        const key = String(entry.npc_id);
        const current = this.feeds.get(key);
        this.feeds.set(
          key,
          current
            ? {
                ...current,
                slot: Number(entry.slot),
                name: String(entry.name ?? current.name),
              }
            : blankFeed(
                Number(entry.npc_id),
                Number(entry.slot),
                String(entry.name ?? `#${entry.npc_id}`),
              ),
        );
      }
    }

    let toreDown = false;
    for (const op of ops) {
      const path = String(op.path ?? "");
      const id = npcIdFromPath(path);
      if (op.op === "del" && id != null && path === `/${id}`) {
        this.remove(id);
        toreDown = true;
      }
    }
    if (toreDown) return;

    // Prose feed (the NPC's scrolling terminal), a separate append stream from
    // the structured scene: handle and return before the scene revision logic.
    const feedOps = ops.filter(
      (op: any) => op.op === "add" && /^\/\d+\/feed\/-$/.test(String(op.path ?? "")),
    );
    if (feedOps.length) {
      this.appendFeed(feedOps, env.meta ?? {});
      return;
    }

    const meta = env.meta ?? {};
    const pathId = ops.map((op: any) => npcIdFromPath(String(op.path ?? ""))).find(Boolean);
    const npcId = Number(meta.npc_id ?? pathId);
    if (!Number.isFinite(npcId) || npcId <= 0) return;
    const snapshot = ops.find(
      (op: any) => op.op === "set" && op.path === `/${npcId}/scene`,
    );
    const key = String(npcId);
    const current = this.feeds.get(key);
    const revision = Number(meta.revision ?? 0);

    if (snapshot) {
      const value = snapshot.value ?? {};
      this.feeds.set(
        key,
        {
          npcId,
          slot: Number(meta.slot ?? current?.slot ?? 0),
          name: String(meta.name ?? current?.name ?? `#${npcId}`),
          revision,
          actionId: meta.action_id ?? undefined,
          scene: {
            room: value.room ?? {},
            occupants: value.occupants ?? [],
            exits: value.exits ?? [],
          },
          resyncing: false,
          feed: current?.feed ?? [],
          unread: current?.unread ?? 0,
        },
      );
      return;
    }

    if (!current) {
      this.requestOnce(npcId, 0);
      return;
    }
    if (revision <= current.revision) return;
    if (revision !== current.revision + 1) {
      this.requestOnce(npcId, current.revision);
      return;
    }

    let scene = current.scene;
    for (const op of ops) {
      const path = String(op.path ?? "").replace(`/${npcId}/scene`, "");
      if (op.op === "add" && path === "/occupants/-") {
        const occupant = op.value;
        if (occupant && !scene.occupants.some((item) => item.handle === occupant.handle)) {
          scene = { ...scene, occupants: [...scene.occupants, occupant] };
        }
      } else if (op.op === "del" && path === "/occupants") {
        scene = {
          ...scene,
          occupants: scene.occupants.filter((item) => item.handle !== op.handle),
        };
      }
    }
    this.feeds.set(
      key,
      {
        ...current,
        slot: Number(meta.slot ?? current.slot),
        name: String(meta.name ?? current.name),
        revision,
        actionId: meta.action_id ?? current.actionId,
        scene,
        resyncing: false,
      },
    );
  }

  private requestOnce(npcId: number, revision: number): void {
    const current = this.feeds.get(String(npcId));
    if (current?.resyncing) return;
    if (current) {
      this.feeds.set(String(npcId), { ...current, resyncing: true });
    }
    this.requestResync?.(npcId, revision);
  }

  private remove(npcId: number): void {
    this.feeds.delete(String(npcId));
  }
}

export const puppets = new PuppetScenes();
