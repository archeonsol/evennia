// The terminal's size in characters, reported to the server the way a telnet
// client reports its window with NAWS.
//
// The server lays out everything width-aware from the session's SCREENWIDTH
// and SCREENHEIGHT flags: help text, tables, headers, `who`, paged output. A
// telnet client negotiates those flags and updates them as its window
// changes. The web client never reported them, so every web session was laid
// out for the 78x45 default whatever the window: tables broke across lines on a
// narrow window and sat in a strip down the left of a wide one, and paging cut
// at 45 lines on any screen.
//
// The report goes through the stock `client_options` inputfunc, the same one a
// telnet client's settings use. Screen size is a session flag, so it is sent
// again on every new connection.

/** How long a resize must be still before it is reported. */
export const REPORT_DELAY_MS = 250;

/** What the server is asked to lay out for. */
export const MIN_COLS = 20;
export const MAX_COLS = 400;
export const MIN_ROWS = 5;
export const MAX_ROWS = 400;

export interface ScreenGrid {
  cols: number;
  rows: number;
}

interface Box {
  width: number;
  height: number;
}

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));

/**
 * The character grid that fits in a text box.
 *
 * `box` is the width and height text can use (the content box, without
 * padding or scrollbar), `cell` one character's advance and the line height.
 * Only whole cells count: the browser breaks a line before a character that
 * does not fit. Returns null for a box that is not laid out (a hidden panel is
 * 0x0), which is not a size to tell anyone about.
 */
export function gridFor(box: Box, cell: Box): ScreenGrid | null {
  const values = [box.width, box.height, cell.width, cell.height];
  if (!values.every((v) => Number.isFinite(v) && v > 0)) return null;
  return {
    cols: clamp(Math.floor(box.width / cell.width + 1e-6), MIN_COLS, MAX_COLS),
    rows: clamp(Math.floor(box.height / cell.height + 1e-6), MIN_ROWS, MAX_ROWS),
  };
}

type Send = (grid: ScreenGrid) => void;

export interface ScreenReporterDeps {
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (handle: unknown) => void;
}

const same = (a: ScreenGrid | null, b: ScreenGrid | null) =>
  !!a && !!b && a.cols === b.cols && a.rows === b.rows;

/**
 * Debounces measured sizes into reports.
 *
 * A window drag produces a size every frame; the server needs the one it ends
 * on. Only a change is sent, and `resend` repeats the latest size for a
 * connection that has just opened.
 */
export class ScreenReporter {
  private send: Send | null = null;
  private sent: ScreenGrid | null = null;
  private next: ScreenGrid | null = null;
  private timer: unknown = null;
  private readonly deps: ScreenReporterDeps;

  constructor(deps?: Partial<ScreenReporterDeps>) {
    this.deps = {
      setTimeout: deps?.setTimeout ?? ((fn, ms) => globalThis.setTimeout(fn, ms)),
      clearTimeout:
        deps?.clearTimeout ?? ((handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>)),
    };
  }

  /** The latest measured size, reported or about to be. */
  get current(): ScreenGrid | null {
    return this.next ?? this.sent;
  }

  /** Where reports go; the shell wires this to the socket. */
  connect(send: Send): void {
    this.send = send;
  }

  /** A fresh measurement of the terminal. Null (not laid out) is ignored. */
  update(grid: ScreenGrid | null): void {
    if (!grid) return;
    if (same(grid, this.sent)) {
      this.cancel();
      this.next = null;
      return;
    }
    if (same(grid, this.next)) return;
    this.next = { ...grid };
    this.cancel();
    this.timer = this.deps.setTimeout(() => {
      this.timer = null;
      this.flush();
    }, REPORT_DELAY_MS);
  }

  /** Report the latest size now: a new connection starts without one. */
  resend(): void {
    this.cancel();
    const grid = this.current;
    if (!grid) return;
    this.next = null;
    this.sent = grid;
    this.send?.(grid);
  }

  private flush(): void {
    if (!this.next) return;
    this.sent = this.next;
    this.next = null;
    this.send?.(this.sent);
  }

  private cancel(): void {
    if (this.timer !== null) this.deps.clearTimeout(this.timer);
    this.timer = null;
  }
}

/** The shell's one reporter: the game log measures, main.ts sends. */
export const screenSize = new ScreenReporter();
