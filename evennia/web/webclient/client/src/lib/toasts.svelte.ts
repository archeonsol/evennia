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
}

let seq = 0;
const TTL = 7000;

class Toasts {
  list = $state<Toast[]>([]);
  private timers = new Map<number, { left: number; started: number; handle: ReturnType<typeof setTimeout> | null }>();
  private held = false;

  /**
   * Show a toast. `speak` false is for a toast whose news a screen reader
   * already heard another way (a mention echoed into the terminal); the stack
   * itself is not a live region, so nothing is said twice.
   */
  push(kind: string, title: string, body: string, ttl = TTL, speak = true): void {
    const id = ++seq;
    if (speak) announcer.now(body ? `${title}: ${body}` : title);
    this.list = [...this.list, { id, kind, title, body }];
    this.timers.set(id, { left: ttl, started: Date.now(), handle: null });
    if (!this.held) this.arm(id);
  }

  dismiss(id: number): void {
    const t = this.timers.get(id);
    if (t?.handle) clearTimeout(t.handle);
    this.timers.delete(id);
    this.list = this.list.filter((t) => t.id !== id);
  }

  /** Stop every countdown while the player is looking at the toasts. */
  hold(): void {
    if (this.held) return;
    this.held = true;
    for (const t of this.timers.values()) {
      if (t.handle) clearTimeout(t.handle);
      t.handle = null;
      t.left = Math.max(1500, t.left - (Date.now() - t.started));
    }
  }

  release(): void {
    if (!this.held) return;
    this.held = false;
    for (const id of this.timers.keys()) this.arm(id);
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
