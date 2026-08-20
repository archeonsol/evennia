/* How a value is written into a cell.
 *
 * One place, because the rule has to be the same in every table: an operator
 * comparing two panels must not have to work out whether a blank cell means the
 * same thing in both.
 */

export interface CellShape {
  text: string;
  /** "null" for absent, "num" for a number, "" otherwise. */
  kind: "" | "null" | "num";
  /** The full value, for a hover title when the cell is clipped. */
  title: string;
}

/**
 * Describe one value for display.
 *
 * Absent renders as `--` rather than as an empty cell. An empty cell and a cell
 * that is still loading look identical, and a table of them looks broken.
 */
export function cellShape(value: unknown): CellShape {
  if (value === null || value === undefined || value === "") {
    return { text: "--", kind: "null", title: "" };
  }
  const text = String(value);
  return { text, kind: typeof value === "number" ? "num" : "", title: text };
}

/** Bytes as an operator reads them, not as the database reports them. */
export function bytes(value: unknown): string {
  const size = Number(value);
  if (!Number.isFinite(size)) return "--";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let scaled = size;
  let unit = 0;
  while (scaled >= 1024 && unit < units.length - 1) {
    scaled /= 1024;
    unit += 1;
  }
  return `${scaled >= 10 || unit === 0 ? Math.round(scaled) : scaled.toFixed(1)} ${units[unit]}`;
}

/** Upper case for a legend, without assuming the value is a string. */
export function legend(value: unknown): string {
  return String(value ?? "").toUpperCase();
}
