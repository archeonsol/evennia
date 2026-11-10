// Terminal screens: a program the Server runs, drawn in a panel here (an arcade cabinet in the game).
// The Server keeps the program's screen and sends its output; this store holds each screen's spec and
// how its run ended, and hands the output to the panel that draws it.
//
// Output is a live view, not a record. The Portal does not keep it for a reconnect, so a panel that
// opens late, or a page that comes back, asks the Server to draw the whole screen again
// (`screen_sync`) instead of replaying what it missed.

export interface ScreenSpec {
  id: string;
  title: string;
  /** Columns and rows of the program's screen. */
  size: [number, number];
  /** What the keys do, as [key, action] pairs. Only a screen reader is told: the panel shows none. */
  controls: [string, string][];
  dismissible: boolean;
}

export interface ScreenEnd {
  /** The panel's word for how the run ended ("Game over"). The score is told in the game's own text. */
  headline: string;
}

/** Takes the program's output. */
export type ScreenSink = (data: string) => void;

/** What the store needs from the rest of the shell, handed over once at start. */
export interface ScreenLinks {
  /** Send an action in the `screen` namespace to the Server. */
  send: (action: string, kwargs: Record<string, unknown>) => void;
  /** Show the panel for a screen. */
  openPanel: (spec: ScreenSpec) => void;
  /** Close the panel of a screen. */
  closePanel: (id: string) => void;
  /** Say something to a screen reader, politely. */
  say: (text: string) => void;
}

const MAX_CONTROLS = 12;

function text(value: unknown, max: number): string {
  return typeof value === "string" ? value.slice(0, max) : "";
}

function whole(value: unknown, low: number, high: number, fallback: number): number {
  const n = typeof value === "number" && Number.isFinite(value) ? Math.round(value) : fallback;
  return Math.min(high, Math.max(low, n));
}

/** The spec a `ui_component` of type "screen" describes, or null when it names no screen. */
export function specOf(raw: unknown): ScreenSpec | null {
  if (!raw || typeof raw !== "object") return null;
  const c = raw as Record<string, unknown>;
  const id = text(c.id, 80);
  if (!id) return null;
  const size = Array.isArray(c.size) ? c.size : [];
  const controls: [string, string][] = [];
  for (const pair of Array.isArray(c.controls) ? c.controls : []) {
    if (!Array.isArray(pair) || controls.length >= MAX_CONTROLS) continue;
    const key = text(pair[0], 40);
    if (key) controls.push([key, text(pair[1], 60)]);
  }
  return {
    id,
    title: text(c.title, 60) || "Screen",
    size: [whole(size[0], 20, 200, 80), whole(size[1], 8, 80, 24)],
    controls,
    dismissible: c.dismissible !== false,
  };
}

/** The id and result a `screen_end` carries, or null when it names no screen. */
export function endOf(raw: unknown): (ScreenEnd & { id: string }) | null {
  if (!raw || typeof raw !== "object") return null;
  const c = raw as Record<string, unknown>;
  const id = text(c.id, 80);
  if (!id) return null;
  return { id, headline: text(c.headline, 60) || "Game over" };
}

export class ScreenStore {
  /** The open screens, by id. */
  screens = $state<Record<string, ScreenSpec>>({});
  /** How each finished run ended, by id. */
  ended = $state<Record<string, ScreenEnd>>({});
  private sinks = new Map<string, ScreenSink>();
  private links: ScreenLinks | null = null;

  connect(links: ScreenLinks): void {
    this.links = links;
  }

  /** The Server opened a screen. A second open for the same id (a replay) only shows it again. */
  open(raw: unknown): void {
    const spec = specOf(raw);
    if (!spec) return;
    const fresh = !(spec.id in this.screens);
    this.screens = { ...this.screens, [spec.id]: spec };
    this.links?.openPanel(spec);
    if (fresh) {
      const keys = spec.controls.map(([key, action]) => (action ? `${key}, ${action}.` : `${key}.`));
      this.links?.say(
        [
          `${spec.title}. A game on a screen.`,
          "Keys go to the game while the screen has focus.",
          ...keys,
          "Shift and Escape leave the screen.",
        ].join(" "),
      );
    }
  }

  /** The Server closed a screen. */
  close(id: string): void {
    if (!(id in this.screens)) return;
    this.forget(id);
    this.links?.closePanel(id);
  }

  /** The program printed something. Dropped when no panel is drawing: the panel asks for it again. */
  frame(id: string, data: unknown): void {
    if (typeof data !== "string" || !data) return;
    this.sinks.get(id)?.(data);
  }

  /** The run is over. */
  end(raw: unknown): void {
    const end = endOf(raw);
    if (!end || !(end.id in this.screens)) return;
    const { id, ...result } = end;
    this.ended = { ...this.ended, [id]: result };
  }

  /**
   * A panel started drawing a screen. Asks the Server for the whole picture, which is how a panel
   * that opened late, or one that came back after a reconnect, catches up. Returns the detach.
   */
  attach(id: string, sink: ScreenSink): () => void {
    this.sinks.set(id, sink);
    if (!(id in this.ended)) this.links?.send("screen_sync", { id });
    return () => {
      if (this.sinks.get(id) === sink) this.sinks.delete(id);
    };
  }

  /** The characters a key press made. Nothing is sent once the run is over. */
  key(id: string, data: string): void {
    if (!data || id in this.ended || !(id in this.screens)) return;
    this.links?.send("screen_key", { id, data });
  }

  /** The connection came back: every panel still drawing a live screen asks for it again. */
  resync(): void {
    for (const id of this.sinks.keys()) {
      if (!(id in this.ended)) this.links?.send("screen_sync", { id });
    }
  }

  /** The player is done with a screen and closed its panel. */
  dismiss(id: string): void {
    this.forget(id);
    this.links?.closePanel(id);
  }

  /** A finished screen's panel went away, by whatever means: forget it. A live one is kept. */
  released(id: string): void {
    if (id in this.ended) this.forget(id);
  }

  private forget(id: string): void {
    if (id in this.screens) {
      const { [id]: _spec, ...rest } = this.screens;
      this.screens = rest;
    }
    if (id in this.ended) {
      const { [id]: _end, ...rest } = this.ended;
      this.ended = rest;
    }
    this.sinks.delete(id);
  }
}

export const screens = new ScreenStore();
