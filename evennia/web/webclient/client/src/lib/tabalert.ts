// The browser tab's attention signal: while the player is away from the tab,
// new output makes its title flash, and a direct message also counts up.
//
// Kept apart from the DOM so the policy can be tested on its own: what the
// title reads, when it blinks, and when it stops. `notify.svelte.ts` owns the
// document and hands this module the title and favicon setters.

/** What makes the tab flash: any new output, only messages to the player, or nothing. */
export type TabAlertMode = "any" | "direct" | "off";

/** One blink phase. Background tabs clamp timers to about a second anyway. */
export const FLASH_MS = 1000;

export interface TabAlertDeps {
  setTitle(title: string): void;
  /** Mark the tab icon (a dot on the favicon) or put the original back. */
  setBadge(on: boolean): void;
  mode(): TabAlertMode;
  /** Reduced motion: a steady marker instead of a blinking title. */
  calm(): boolean;
  setInterval?: (fn: () => void, ms: number) => unknown;
  clearInterval?: (id: unknown) => void;
}

/**
 * The title for one moment of an alert.
 *
 * @param base The page's own title.
 * @param unread Direct messages since the player left the tab.
 * @param activity Whether any other new output has arrived.
 * @param lit True on the blink's "look here" phase.
 * @returns The title to show.
 */
export function alertTitle(base: string, unread: number, activity: boolean, lit: boolean): string {
  if (!unread && !activity) return base;
  if (lit) return unread ? `▶ New message · ${base}` : `▶ New activity · ${base}`;
  return unread ? `(${unread}) ${base}` : `● ${base}`;
}

export class TabAlert {
  private unread = 0;
  private active = false;
  private lit = false;
  private timer: unknown = null;
  private readonly setIntervalFn: (fn: () => void, ms: number) => unknown;
  private readonly clearIntervalFn: (id: unknown) => void;

  /**
   * @param base The page's own title, restored when the player returns.
   * @param deps Title and badge setters and the player's settings.
   */
  constructor(
    private base: string,
    private deps: TabAlertDeps,
  ) {
    this.setIntervalFn = deps.setInterval ?? ((fn, ms) => setInterval(fn, ms));
    this.clearIntervalFn = deps.clearInterval ?? ((id) => clearInterval(id as ReturnType<typeof setInterval>));
  }

  /** Direct messages counted since the player left the tab. */
  get count(): number {
    return this.unread;
  }

  /** Whether the tab is currently asking for attention. */
  get alerting(): boolean {
    return this.unread > 0 || this.active;
  }

  /** New output of any kind arrived while the player was away. */
  activity(): void {
    if (this.deps.mode() !== "any" || this.active) return;
    this.active = true;
    this.start();
  }

  /**
   * A message for the player arrived while they were away. It is always
   * counted in the title, as it was before flashing existed; whether the tab
   * also blinks is the player's setting.
   */
  direct(): void {
    this.unread++;
    if (this.deps.mode() === "off") {
      this.deps.setTitle(alertTitle(this.base, this.unread, false, false));
      return;
    }
    this.start();
  }

  /** The player is back: stop, and put the title and icon back. */
  clear(): void {
    if (this.timer !== null) this.clearIntervalFn(this.timer);
    this.timer = null;
    const was = this.alerting;
    this.unread = 0;
    this.active = false;
    this.lit = false;
    if (was) {
      this.deps.setTitle(this.base);
      this.deps.setBadge(false);
    }
  }

  private start(): void {
    this.deps.setBadge(true);
    if (this.deps.calm()) {
      this.render();
      return;
    }
    // Light up at once, so the first thing seen is the change, not a second
    // of the old title.
    this.lit = true;
    this.render();
    if (this.timer !== null) return;
    this.timer = this.setIntervalFn(() => {
      this.lit = !this.lit;
      this.render();
    }, FLASH_MS);
  }

  private render(): void {
    this.deps.setTitle(alertTitle(this.base, this.unread, this.active, this.lit && !this.deps.calm()));
  }
}
