// Attention signals for events that arrive while the tab is unfocused:
// a flashing title and a dot on the tab icon (lib/tabalert.ts), an optional
// desktop notification, and a sound. In-app toasts (toasts.svelte) still fire
// regardless; this is the "you're not looking at the tab" layer.

import { settings } from "./settings.svelte";
import { playMention } from "./audio";
import { TabAlert } from "./tabalert";

/** Size the badged icon is drawn at; browsers scale it for the tab strip. */
const ICON_PX = 32;

class Notify {
  private focused = true;
  private baseTitle = "";
  private alert: TabAlert | null = null;
  private iconLink: HTMLLinkElement | null = null;
  private iconOriginal: string | null = null;
  private iconBadged: string | null = null;

  init(): void {
    this.baseTitle = document.title || "Underspire";
    this.focused = document.hasFocus() && !document.hidden;
    this.alert = new TabAlert(this.baseTitle, {
      setTitle: (t) => {
        document.title = t;
      },
      setBadge: (on) => this.setBadge(on),
      mode: () => settings.tabAlert,
      calm: () => settings.reduceMotion || settings.screenreader,
    });
    void this.prepareBadge();
    window.addEventListener("focus", () => {
      this.focused = true;
      this.clear();
    });
    window.addEventListener("blur", () => {
      this.focused = false;
    });
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        this.focused = true;
        this.clear();
      }
    });
  }

  private clear(): void {
    this.alert?.clear();
  }

  private away(): boolean {
    return !this.focused || document.hidden;
  }

  /**
   * New output of any kind (a line in the terminal, a channel message). Flashes
   * the tab while the player is elsewhere, if they asked for that; no sound and
   * no desktop notification, which stay for messages meant for them.
   */
  activity(): void {
    if (this.away()) this.alert?.activity();
  }

  /**
   * Build the badged tab icon once: the page's own icon with a dot in the
   * corner, or a lone dot when the icon cannot be drawn (none declared, or
   * served from another origin, which taints the canvas).
   */
  private async prepareBadge(): Promise<void> {
    const link = document.querySelector<HTMLLinkElement>('link[rel~="icon"]');
    this.iconLink = link;
    this.iconOriginal = link?.href ?? null;
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = ICON_PX;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const src = this.iconOriginal ?? "/favicon.ico";
    const drawn = await new Promise<boolean>((resolve) => {
      const img = new Image();
      img.onload = () => {
        try {
          ctx.drawImage(img, 0, 0, ICON_PX, ICON_PX);
          resolve(true);
        } catch {
          resolve(false);
        }
      };
      img.onerror = () => resolve(false);
      img.src = src;
    });
    if (!drawn) ctx.clearRect(0, 0, ICON_PX, ICON_PX);
    const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent-bright").trim() || "#ff4a3d";
    const r = drawn ? ICON_PX * 0.28 : ICON_PX * 0.4;
    ctx.beginPath();
    ctx.arc(ICON_PX - r - 1, ICON_PX - r - 1, r, 0, Math.PI * 2);
    ctx.fillStyle = accent;
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#000";
    ctx.stroke();
    try {
      this.iconBadged = canvas.toDataURL("image/png");
    } catch {
      this.iconBadged = null; // a cross-origin icon tainted the canvas
    }
  }

  private setBadge(on: boolean): void {
    if (on) {
      if (!this.iconBadged) return;
      if (!this.iconLink) {
        this.iconLink = document.createElement("link");
        this.iconLink.rel = "icon";
        document.head.appendChild(this.iconLink);
      }
      this.iconLink.href = this.iconBadged;
      return;
    }
    if (!this.iconLink) return;
    if (this.iconOriginal) this.iconLink.href = this.iconOriginal;
    else {
      // The page declared no icon; take ours away so the browser's default
      // comes back.
      this.iconLink.remove();
      this.iconLink = null;
    }
  }

  /** Ask the browser for desktop-notification permission (from a user gesture). */
  requestPermission(): void {
    if ("Notification" in window && Notification.permission === "default") {
      try {
        Notification.requestPermission();
      } catch {
        /* ignore */
      }
    }
  }

  /** Signal an event. No-op while the tab is focused. */
  ping(title: string, body = "", sound = true): void {
    if (!this.away()) return;
    this.alert?.direct();
    if (sound && settings.notifySound) {
      try {
        playMention();
      } catch {
        /* ignore */
      }
    }
    if (
      settings.notifyDesktop &&
      "Notification" in window &&
      Notification.permission === "granted"
    ) {
      try {
        const n = new Notification(title, { body, silent: true });
        n.onclick = () => {
          window.focus();
          n.close();
        };
      } catch {
        /* ignore */
      }
    }
  }
}

export const notify = new Notify();
