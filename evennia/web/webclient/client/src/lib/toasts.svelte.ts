// Transient toast notifications - used for community events (kudos, milestones,
// announcements) delivered as Azaban `oob`. Diegetic "vox" pings rather than
// generic browser notifications.

import type { CommunityKudosPayload, CommunityMilestonePayload } from "./oob-events";

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

  push(kind: string, title: string, body: string, ttl = TTL): void {
    const id = ++seq;
    this.list = [...this.list, { id, kind, title, body }];
    setTimeout(() => this.dismiss(id), ttl);
  }

  dismiss(id: number): void {
    this.list = this.list.filter((t) => t.id !== id);
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
