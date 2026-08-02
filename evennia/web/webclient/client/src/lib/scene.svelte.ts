// Reactive scene model, synced from Azaban `patch` deltas. The server keeps a
// per-viewer scene (room, occupants, exits, fields, …) and streams patches as it
// changes; this holds the model and the shell renders it reactively - the room
// panel/HUD update without spamming the log.
//
// The op vocabulary and its reduction live in `scene-ops.ts`, rune-free so they
// can be unit tested. This file is only the reactive container.

import { emptyScene, reduceScene } from "./scene-ops";
import type { Occupant, SceneExit, SceneRoom, SceneState } from "./scene-ops";

export type { Occupant, SceneExit, SceneRoom, SceneState };
export { emptyScene, reduceScene };

class Scene {
  room = $state<SceneRoom>({});
  occupants = $state<Occupant[]>([]);
  exits = $state<SceneExit[]>([]);
  fields = $state<Record<string, unknown>>({});
  present = $state(false); // whether a scene has been received yet
  rev = $state(0); // server scene revision, when one is supplied in patch meta

  /** Current model as a plain snapshot. */
  private snapshot(): SceneState {
    return {
      room: this.room,
      occupants: this.occupants,
      exits: this.exits,
      fields: this.fields,
      present: this.present,
      rev: this.rev,
    };
  }

  apply(target: string, ops: any[], meta?: Record<string, any>): void {
    const next = reduceScene(this.snapshot(), target, ops, meta);
    // Reassign rather than mutate so the reactive proxies see the change.
    this.room = next.room;
    this.occupants = next.occupants;
    this.exits = next.exits;
    this.fields = next.fields;
    this.present = next.present;
    this.rev = next.rev;
  }

  /** Drop everything (used on disconnect/logout). */
  reset(): void {
    const blank = emptyScene();
    this.room = blank.room;
    this.occupants = blank.occupants;
    this.exits = blank.exits;
    this.fields = blank.fields;
    this.present = blank.present;
    this.rev = blank.rev;
  }
}

export const scene = new Scene();
