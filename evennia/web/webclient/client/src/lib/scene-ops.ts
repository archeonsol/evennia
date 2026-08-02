// Scene patch vocabulary and its reduction, kept free of runes so it can be unit
// tested in vitest (the same reason `puppets.svelte.ts` keeps its state plain).
// The reactive store in `scene.svelte.ts` is a thin wrapper over `reduceScene`.
//
// Op vocabulary (JSON-patch-like, path-addressed):
//
//   {op:"set", path:"/",             value:{room,occupants,exits,fields}}  full snapshot
//   {op:"set", path:"/room"}         | "/room/<key>"    whole room or one key
//   {op:"set", path:"/exits"}                            whole exit list
//   {op:"set", path:"/fields"}       | "/fields/<key>"   live readouts
//   {op:"del", path:"/fields/<key>"}                     drop a readout
//   {op:"add", path:"/occupants/-",  value:{handle,name}}
//   {op:"del", path:"/occupants",    handle:"…"}         by opaque handle
//
// `fields` is a generic bag rather than named slots, so a new live value (an ETA,
// a dose, a progress bar) needs no client change - see "Live scene fields" in the
// engine's FUTURE-IDEAS.md.

export interface Occupant {
  handle: string;
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

export interface SceneState {
  room: SceneRoom;
  occupants: Occupant[];
  exits: SceneExit[];
  fields: Record<string, unknown>;
  present: boolean;
  rev: number;
}

/** A scene with nothing in it yet. */
export function emptyScene(): SceneState {
  return { room: {}, occupants: [], exits: [], fields: {}, present: false, rev: 0 };
}

/** Split a JSON-pointer-ish path into segments; "/" and "" are the root. */
function segments(path: string): string[] {
  if (!path || path === "/") return [];
  return path.replace(/^\//, "").split("/");
}

/**
 * Reduce a patch onto a scene snapshot, returning a new snapshot. Pure: the
 * input is never mutated, so the caller decides what to do with the result.
 */
export function reduceScene(
  current: SceneState,
  target: string,
  ops: any[],
  meta?: Record<string, any>,
): SceneState {
  if (target !== "scene") return current;
  const next: SceneState = { ...current };

  for (const op of ops ?? []) {
    const path = op.path ?? "/";
    const parts = segments(path);

    if (op.op === "set" && parts.length === 0) {
      // Full-scene snapshot (on look).
      const v = op.value ?? {};
      next.room = v.room ?? {};
      next.occupants = v.occupants ?? [];
      next.exits = v.exits ?? [];
      next.fields = v.fields ?? {};
      next.present = true;
    } else if (op.op === "set" && parts[0] === "room") {
      next.room = parts.length === 1 ? (op.value ?? {}) : { ...next.room, [parts[1]]: op.value };
    } else if (op.op === "set" && parts[0] === "exits" && parts.length === 1) {
      next.exits = (op.value as SceneExit[]) ?? [];
    } else if (op.op === "set" && parts[0] === "fields") {
      next.fields =
        parts.length === 1
          ? ((op.value as Record<string, unknown>) ?? {})
          : { ...next.fields, [parts[1]]: op.value };
    } else if (op.op === "del" && parts[0] === "fields" && parts.length > 1) {
      const { [parts[1]]: _dropped, ...rest } = next.fields;
      next.fields = rest;
    } else if (op.op === "add" && path === "/occupants/-") {
      // Someone entered - append if not already present for this perception handle.
      const val = op.value;
      if (val && !next.occupants.some((o) => o.handle === val.handle)) {
        next.occupants = [...next.occupants, val];
      }
    } else if (op.op === "del" && parts[0] === "occupants" && parts.length === 1) {
      // Someone left - drop by opaque viewer-scoped handle.
      next.occupants = next.occupants.filter((o) => o.handle !== op.handle);
    } else if (import.meta.env?.DEV) {
      console.warn("scene: unhandled op", op);
    }
  }

  if (meta && typeof meta.rev === "number") next.rev = meta.rev;
  return next;
}
