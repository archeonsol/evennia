import { describe, expect, it } from "vitest";

import { DEFAULT_KINDS, describeVista, noise, paintVista, parseVista } from "./vista";

const bytes = (values: number[]) => btoa(String.fromCharCode(...values));

/** A 4 by 2 picture: a row of sky over a row of ground, with one star and one lamp. */
function payload(extra: Record<string, unknown> = {}) {
  const rgb: number[] = [];
  for (let i = 0; i < 4; i++) rgb.push(80, 120, 200);
  for (let i = 0; i < 4; i++) rgb.push(60, 90, 40);
  const kind = [0, 2, 0, 0, 16, 16, 9, 16];
  return {
    width: 4,
    height: 2,
    span: 120,
    bearing: 270,
    horizon: 1,
    facing: "west",
    rgb: bytes(rgb),
    kind: bytes(kind),
    ruler: [
      { label: "W", column: 2 },
      { label: "SW", column: 0 },
    ],
    ...extra,
  };
}

describe("parseVista", () => {
  it("reads a payload into bytes and marks", () => {
    const v = parseVista(payload())!;
    expect(v).not.toBeNull();
    expect([v.width, v.height, v.span, v.bearing, v.horizon, v.facing]).toEqual([4, 2, 120, 270, 1, "west"]);
    expect(v.rgb).toHaveLength(24);
    expect(Array.from(v.kind)).toEqual([0, 2, 0, 0, 16, 16, 9, 16]);
    expect(v.ruler).toEqual([
      { label: "W", column: 2 },
      { label: "SW", column: 0 },
    ]);
  });

  it("is not a vista when it is absent, the wrong shape, or the bytes do not fit", () => {
    expect(parseVista(undefined)).toBeNull();
    expect(parseVista("nope")).toBeNull();
    expect(parseVista({})).toBeNull();
    expect(parseVista(payload({ width: 0 }))).toBeNull();
    expect(parseVista(payload({ width: 5 }))).toBeNull();
    expect(parseVista(payload({ width: 100000 }))).toBeNull();
    expect(parseVista(payload({ rgb: bytes([1, 2, 3]) }))).toBeNull();
    expect(parseVista(payload({ kind: "!!!" }))).toBeNull();
  });

  it("keeps the legend it knows and takes the game's own kinds over it", () => {
    expect(parseVista(payload())!.kinds).toEqual(DEFAULT_KINDS);
    const own = parseVista(payload({ kinds: { star: 40, comet: 41, bad: "x" } }))!;
    expect(own.kinds.star).toBe(40);
    expect(own.kinds.comet).toBe(41);
    expect(own.kinds.bad).toBeUndefined();
    expect(own.kinds.light).toBe(DEFAULT_KINDS.light);
  });

  it("drops a ruler mark that is not one and defaults what is missing", () => {
    const v = parseVista(payload({ ruler: [{ label: "N", column: 1 }, { label: 3, column: 1 }, null, { label: "E" }], span: undefined, horizon: undefined, facing: undefined }))!;
    expect(v.ruler).toEqual([{ label: "N", column: 1 }]);
    expect(v.span).toBe(120);
    expect(v.horizon).toBe(1);
    expect(v.facing).toBe("");
  });
});

describe("paintVista", () => {
  const v = () => parseVista(payload())!;

  it("paints the game's pixels exactly, opaque, when nothing moves", () => {
    const out = new Uint8ClampedArray(4 * 2 * 4);
    paintVista(out, v());
    expect(Array.from(out.slice(0, 4))).toEqual([80, 120, 200, 255]);
    expect(Array.from(out.slice(16, 20))).toEqual([60, 90, 40, 255]);
    for (let i = 3; i < out.length; i += 4) expect(out[i]).toBe(255);
    const still = new Uint8ClampedArray(out.length);
    paintVista(still, v(), 5, false);
    expect(Array.from(still)).toEqual(Array.from(out));
  });

  it("moves only the stars and the lamps, and the same way for the same beat", () => {
    const base = new Uint8ClampedArray(32);
    paintVista(base, v());
    const seen = new Set<string>();
    for (let beat = 0; beat < 40; beat++) {
      const a = new Uint8ClampedArray(32);
      const b = new Uint8ClampedArray(32);
      paintVista(a, v(), beat, true);
      paintVista(b, v(), beat, true);
      expect(Array.from(a)).toEqual(Array.from(b));
      for (let i = 0; i < 8; i++) {
        const same = a[i * 4] === base[i * 4] && a[i * 4 + 1] === base[i * 4 + 1] && a[i * 4 + 2] === base[i * 4 + 2];
        if (i === 1 || i === 6) {
          if (!same) seen.add(String(i));
        } else {
          expect(same).toBe(true);
        }
      }
    }
    expect(seen.has("6")).toBe(true);
    expect(seen.has("1")).toBe(true);
  });
});

describe("noise", () => {
  it("is a steady number from nought to one", () => {
    for (let i = 0; i < 200; i++) {
      const n = noise(i, 3);
      expect(n).toBeGreaterThanOrEqual(0);
      expect(n).toBeLessThan(1);
      expect(noise(i, 3)).toBe(n);
    }
    expect(noise(1, 1)).not.toBe(noise(1, 2));
  });
});

describe("describeVista", () => {
  it("says which way the picture faces", () => {
    expect(describeVista(parseVista(payload())!)).toBe("Panorama facing west");
    expect(describeVista(parseVista(payload({ facing: undefined }))!)).toBe("Panorama");
  });
});
