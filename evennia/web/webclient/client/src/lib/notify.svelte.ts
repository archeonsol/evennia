// Attention signals for events that arrive while the tab is unfocused:
// a title-bar unread counter, an optional desktop notification, and a sound.
// In-app toasts (toasts.svelte) still fire regardless; this is the "you're not
// looking at the tab" layer.

import { settings } from "./settings.svelte";
import { playMention } from "./audio";

class Notify {
  private focused = true;
  private unread = 0;
  private baseTitle = "";

  init(): void {
    this.baseTitle = document.title || "Underspire";
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
    this.unread = 0;
    document.title = this.baseTitle;
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
    if (this.focused && !document.hidden) return;
    this.unread++;
    document.title = `(${this.unread}) ${this.baseTitle}`;
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
