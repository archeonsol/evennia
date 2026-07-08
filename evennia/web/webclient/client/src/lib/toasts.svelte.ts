// Transient toast notifications - used for community events (kudos, milestones,
// announcements) delivered as Azaban `oob`. Diegetic "vox" pings rather than
// generic browser notifications.

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
  fromCommunity(event: string, kwargs: Record<string, any>): void {
    const k = kwargs ?? {};
    if (event === "community_kudos") {
      const line = `${k.from ?? k.sender ?? "Someone"} → ${k.to ?? k.target ?? ""}`;
      this.push("kudos", "Kudos", k.reason ? `${line}: ${k.reason}` : line);
    } else if (event === "community_milestone") {
      this.push("milestone", "Milestone", k.text ?? k.title ?? k.message ?? "");
    } else if (event === "community_announcement") {
      this.push("announce", "Announcement", k.text ?? k.message ?? k.body ?? "");
    } else {
      this.push("info", event.replace(/^community_/, ""), k.text ?? k.message ?? "");
    }
  }
}

export const toasts = new Toasts();
