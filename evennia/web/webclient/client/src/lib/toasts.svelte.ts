// Transient toast notifications - used for community events (kudos, milestones,
// announcements) delivered as Azaban `oob`. Diegetic "vox" pings rather than
// generic browser notifications.

import type { CommunityKudosPayload, CommunityMilestonePayload } from "./oob-events";
import { announcer } from "./announce.svelte";

export interface Toast {
  id: number;
  kind: string;
  title: string;
  body: string;
  /** What a click does besides dismissing: open the thing the toast is about. */
  open?: () => void;
}

let seq = 0;
const TTL = 20000;
// Toasts held in a hidden tab would otherwise stack up the whole screen.
const MAX_SHOWN = 5;

class Toasts {
  list = $state<Toast[]>([]);
  private timers = new Map<number, { left: number; started: number; handle: ReturnType<typeof setTimeout> | null }>();
  private pointerHeld = false;
  private tabHidden = typeof document !== "undefined" && document.hidden;

  constructor() {
    // A toast that counts down in a background tab is never seen.
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", () => this.update(() => (this.tabHidden = document.hidden)));
    }
  }

  private get paused(): boolean {
    return this.pointerHeld || this.tabHidden;
  }

  /**
   * Show a toast. `speak` false is for a toast whose news a screen reader
   * already heard another way (a mention echoed into the terminal); the stack
   * itself is not a live region, so nothing is said twice.
   */
  push(kind: string, title: string, body: string, speak = true, open?: () => void): void {
    const id = ++seq;
    if (speak) announcer.now(body ? `${title}: ${body}` : title);
    this.list = [...this.list, { id, kind, title, body, open }];
    this.timers.set(id, { left: TTL, started: Date.now(), handle: null });
    if (!this.paused) this.arm(id);
    while (this.list.length > MAX_SHOWN) this.dismiss(this.list[0].id);
  }

  dismiss(id: number): void {
    const t = this.timers.get(id);
    if (t?.handle) clearTimeout(t.handle);
    this.timers.delete(id);
    this.list = this.list.filter((t) => t.id !== id);
  }

  /** Stop every countdown while the player is looking at the toasts. */
  hold(): void {
    this.update(() => (this.pointerHeld = true));
  }

  release(): void {
    this.update(() => (this.pointerHeld = false));
  }

  private update(change: () => void): void {
    const was = this.paused;
    change();
    if (!was && this.paused) this.stopAll();
    else if (was && !this.paused) for (const id of this.timers.keys()) this.arm(id);
  }

  private stopAll(): void {
    for (const t of this.timers.values()) {
      if (t.handle) clearTimeout(t.handle);
      t.handle = null;
      t.left = Math.max(1500, t.left - (Date.now() - t.started));
    }
  }

  private arm(id: number): void {
    const t = this.timers.get(id);
    if (!t) return;
    t.started = Date.now();
    t.handle = setTimeout(() => this.dismiss(id), t.left);
  }

  /** Map a community_* oob event to a toast (defensive about field names). */
  // Field names come from the generated catalog (lib/oob-events.ts), not from
  // guesswork: this used to read `from`/`to`/`reason` off a payload that ships
  // `giver`/`receiver`/`message`, so every kudos toast rendered "Someone → ".
  fromCommunity(event: string, kwargs: Record<string, any>): void {
    const k = kwargs ?? {};
    if (event === "community_kudos") {
      const p = k as CommunityKudosPayload;
      const giver = p.anonymous ? "Someone" : (p.giver ?? "Someone");
      const line = `${giver} → ${p.receiver ?? ""}`;
      this.push("kudos", "Kudos", p.message ? `${line}: ${p.message}` : line);
    } else if (event === "community_milestone") {
      this.push("milestone", "Milestone", (k as CommunityMilestonePayload).label ?? "");
    } else {
      this.push("info", event.replace(/^community_/, ""), k.text ?? k.message ?? "");
    }
  }
}

export const toasts = new Toasts();
