// The vista: the picture of what lies ahead, sent by the game as the scene field
// `vista` and drawn in the room panel (`VistaView.svelte`). Rune-free, so it can be
// unit tested the way `scene-ops.ts` is.
//
// The payload is plain data. The game draws the scene itself (the sky, the ground,
// the weather and the places in view, from the real land and the hour) and sends the
// pixels, so the shell holds no scene logic and a game with another look sends another
// picture:
//
//   { width, height, span, bearing, horizon, facing,
//     rgb:  base64, width * height * 3 bytes, row by row,
//     kind: base64, width * height bytes, what each pixel shows,
//     kinds: { name: number },   the legend of `kind`
//     ruler: [{ label, column }] the compass ways inside the picture }
//
// `kind` is what lets the shell move a little without knowing the scene: stars
// twinkle and lamps pulse. Nothing else is animated, and a viewer who has asked for
// less motion, or reads by screen reader, gets the still picture.

export interface VistaMark {
  label: string;
  column: number;
}

export interface Vista {
  width: number;
  height: number;
  span: number;
  bearing: number;
  horizon: number;
  facing: string;
  rgb: Uint8Array;
  kind: Uint8Array;
  kinds: Record<string, number>;
  ruler: VistaMark[];
}

/** The legend the game sends today; a payload that names its own kinds wins. */
export const DEFAULT_KINDS: Record<string, number> = {
  sky: 0,
  cloud: 1,
  star: 2,
  sun: 3,
  moon: 4,
  smoke: 5,
  road: 6,
  river: 7,
  roof: 8,
  light: 9,
  mast: 10,
  tower: 11,
  glow: 12,
  rain: 13,
  ruin: 14,
  beam: 15,
  ground: 16,
};

/** The largest picture the shell will draw: a payload past this is not ours. */
const MAX_SIDE = 512;

export function decodeBytes(text: string): Uint8Array | null {
  try {
    const raw = atob(text);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  } catch {
    return null;
  }
}

function whole(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0 && value <= MAX_SIDE ? value : null;
}

function number(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/** Read a scene field as a vista, or `null` when it is not one (absent, the wrong shape, bytes that do not fit). */
export function parseVista(raw: unknown): Vista | null {
  if (!raw || typeof raw !== "object") return null;
  const v = raw as Record<string, unknown>;
  const width = whole(v.width);
  const height = whole(v.height);
  if (width === null || height === null || typeof v.rgb !== "string" || typeof v.kind !== "string") return null;
  const rgb = decodeBytes(v.rgb);
  const kind = decodeBytes(v.kind);
  if (!rgb || !kind || rgb.length !== width * height * 3 || kind.length !== width * height) return null;
  const kinds: Record<string, number> = { ...DEFAULT_KINDS };
  if (v.kinds && typeof v.kinds === "object") {
    for (const [name, id] of Object.entries(v.kinds as Record<string, unknown>)) {
      if (typeof id === "number" && Number.isInteger(id)) kinds[name] = id;
    }
  }
  const ruler: VistaMark[] = [];
  if (Array.isArray(v.ruler)) {
    for (const mark of v.ruler as unknown[]) {
      const m = mark as Record<string, unknown> | null;
      if (m && typeof m.label === "string" && typeof m.column === "number" && Number.isFinite(m.column)) {
        ruler.push({ label: m.label, column: m.column });
      }
    }
  }
  return {
    width,
    height,
    span: number(v.span, 120),
    bearing: number(v.bearing),
    horizon: number(v.horizon, height / 2),
    facing: typeof v.facing === "string" ? v.facing : "",
    rgb,
    kind,
    kinds,
    ruler,
  };
}

/** A steady number from 0 to 1 for a pixel and a beat, the same each time. */
export function noise(index: number, beat: number): number {
  let h = (Math.imul(index, 374761393) + Math.imul(beat, 668265263)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

/**
 * Paint the picture into an RGBA buffer (`width * height * 4`). With `beat` and `live`, a few
 * stars brighten or dim and the lamps pulse; without, the pixels are exactly the game's.
 */
export function paintVista(out: Uint8ClampedArray, vista: Vista, beat = 0, live = false): void {
  const { rgb, kind, kinds } = vista;
  const count = vista.width * vista.height;
  const star = kinds.star;
  const light = kinds.light;
  for (let i = 0; i < count; i++) {
    let factor = 1;
    if (live) {
      const k = kind[i];
      if (k === star) {
        const roll = noise(i, beat);
        if (roll < 0.35) factor = 0.55 + 0.9 * noise(i, beat + 7919);
      } else if (k === light) {
        factor = 0.82 + 0.18 * Math.sin(beat * 1.3 + (i % 17));
      }
    }
    out[i * 4] = rgb[i * 3] * factor;
    out[i * 4 + 1] = rgb[i * 3 + 1] * factor;
    out[i * 4 + 2] = rgb[i * 3 + 2] * factor;
    out[i * 4 + 3] = 255;
  }
}

/** The words a screen reader gets for the picture: which way it faces and what it is. */
export function describeVista(vista: Vista): string {
  return vista.facing ? `Panorama facing ${vista.facing}` : "Panorama";
}
