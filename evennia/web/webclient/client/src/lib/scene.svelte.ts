// Reactive scene model, synced from Azaban `patch` deltas. The server keeps a
// per-viewer scene (room, occupants, exits, …) and streams patches as it changes;
// this holds the model and the shell renders it reactively - the room panel/HUD
// update without spamming the log. Ops are minimal JSON-patch-like; for now we
// handle a whole-scene `set`, with granular sub-path deltas to follow.

export interface Occupant {
  char_id: number | string;
  name: string;
}
export interface SceneRoom {
  name?: string;
  desc?: string;
  atmosphere?: string;
}
export interface SceneExit {
  key: string;
  name: string;
}

class Scene {
  room = $state<SceneRoom>({});
  occupants = $state<Occupant[]>([]);
  exits = $state<SceneExit[]>([]);
  present = $state(false); // whether a scene has been received yet

  apply(target: string, ops: any[]): void {
    if (target !== "scene") return;
    for (const op of ops ?? []) {
      const path = op.path ?? "/";
      if (op.op === "set" && (path === "/" || path === "")) {
        // Full-scene snapshot (on look).
        const v = op.value ?? {};
        this.room = v.room ?? {};
        this.occupants = v.occupants ?? [];
        this.exits = v.exits ?? [];
        this.present = true;
      } else if (op.op === "add" && path === "/occupants/-") {
        // Someone entered - append if not already present (dedupe by id).
        const val = op.value;
        if (val && !this.occupants.some((o) => o.char_id === val.char_id)) {
          this.occupants = [...this.occupants, val];
        }
      } else if (op.op === "del" && path === "/occupants") {
        // Someone left - drop by char_id.
        this.occupants = this.occupants.filter((o) => o.char_id !== op.char_id);
      }
    }
  }
}

export const scene = new Scene();
