// Holds the live dockview api so any component can open panels (e.g. pop a
// channel out to its own floating window). Set by Workspace on creation.

import type { DockviewApi } from "dockview-core";
import { settings } from "./settings.svelte";
import { simple } from "./simpleLayout.svelte";
import { chat } from "./chat.svelte";
import { toasts } from "./toasts.svelte";
import { announcer } from "./announce.svelte";

const LKEY = "underspire.layout.v2";
const PRESET_PREFIX = "underspire.layout.preset.";
const LOCK_KEY = "underspire.layout.locked";

// The standard, reopenable panels (so closing one isn't a dead end).
export const VIEWS: Record<string, { component: string; title: string }> = {
  log: { component: "log", title: "Terminal" },
  scene: { component: "scene", title: "Scene" },
  chat: { component: "chat", title: "Channels" },
  // Staff-only. Named apart from My Tickets, which every player has.
  tickets: { component: "tickets", title: "Ticket Queue" },
  media: { component: "media", title: "Media" },
  spawns: { component: "spawns", title: "Feeds" },
  mytickets: { component: "mytickets", title: "My Tickets" },
};

class Dock {
  api: DockviewApi | null = null;
  private host: HTMLElement | null = null;
  locked = $state(false);

  set(api: DockviewApi | null, host: HTMLElement | null = null): void {
    this.api = api;
    this.host = host;
    try {
      this.locked = localStorage.getItem(LOCK_KEY) === "1";
    } catch {
      /* ignore */
    }
  }

  toggleLock(): void {
    this.locked = !this.locked;
    try {
      localStorage.setItem(LOCK_KEY, this.locked ? "1" : "0");
    } catch {
      /* ignore */
    }
  }

  // -- named layout presets --------------------------------------------

  listLayouts(): string[] {
    const out: string[] = [];
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith(PRESET_PREFIX)) out.push(k.slice(PRESET_PREFIX.length));
      }
    } catch {
      /* ignore */
    }
    return out.sort();
  }

  saveLayout(name: string): void {
    const n = (name || "").trim();
    if (!n || !this.api) return;
    try {
      localStorage.setItem(PRESET_PREFIX + n, JSON.stringify(this.api.toJSON()));
    } catch {
      /* ignore */
    }
  }

  loadLayout(name: string): void {
    if (!this.api) return;
    try {
      const raw = localStorage.getItem(PRESET_PREFIX + name);
      if (raw) this.api.fromJSON(JSON.parse(raw));
    } catch {
      /* ignore */
    }
  }

  deleteLayout(name: string): void {
    try {
      localStorage.removeItem(PRESET_PREFIX + name);
    } catch {
      /* ignore */
    }
  }

  isOpen(id: string): boolean {
    if (settings.screenreader) return simple.has(id);
    return !!this.api?.getPanel(id);
  }

  /** Reopen (or focus) one of the standard panels. */
  openView(id: string): void {
    if (settings.screenreader) {
      const v = VIEWS[id] ?? (id === "puppets" ? { component: "puppets", title: "Puppets" } : null);
      if (v) simple.open({ id, ...v });
      return;
    }
    if (!this.api) return;
    const existing = this.api.getPanel(id);
    if (existing) {
      existing.api.setActive();
      return;
    }
    const v = VIEWS[id];
    if (!v) return;
    this.api.addPanel({ id, component: v.component, title: v.title });
  }

  /**
   * Open a web page: in a browser window when the player chose that, else in
   * the shell. A server-opened page arrives just after the command that asked
   * for it, which is usually inside the browser's popup allowance; when it is
   * not, the page opens in the shell and a toast says why.
   */
  openWebPage(id: string, title: string, url: string): void {
    if (settings.webPages === "window") {
      const win = window.open(url, `underspire-${id.replace(/[^\w-]/g, "_")}`);
      if (win) return;
      toasts.push("web", "Popup blocked", `${title} opened in the shell instead.`);
    }
    this.openIframe(id, title, url);
  }

  /** Open an embedded web page as a panel in the shell. */
  openIframe(id: string, title: string, url: string): void {
    if (settings.screenreader) {
      const fresh = !simple.has(id);
      simple.open({ id, component: "iframe", title, params: { url, title } });
      announcer.now(`${title} ${fresh ? "opened" : "shown"} in a new view tab. Alt+O returns to the game output.`);
      return;
    }
    if (!this.api) return;
    const existing = this.api.getPanel(id);
    if (existing) {
      // Saved layouts kept the old postage-stamp float, so a page that was
      // opened small before comes back small forever. Reopen it at size.
      const g: any = existing.group;
      const floating = g?.api?.location?.type === "floating";
      const tiny = floating && (g.width < 480 || g.height < 320);
      if (!tiny) {
        existing.api.setActive();
        return;
      }
      existing.api.close();
    }
    // dockview's default float is a few hundred pixels square, which left the
    // grid a postage stamp. Open near the full workspace instead; the player
    // can still shrink or dock it.
    // The element's own size: dockview's width reads 0 until its resize
    // observer has fired once, which would collapse the float to 100px.
    const w = this.host?.clientWidth || this.api.width;
    const h = this.host?.clientHeight || this.api.height;
    const width = Math.max(w - 40, Math.min(w, 480));
    const height = Math.max(h - 40, Math.min(h, 360));
    this.api.addPanel({
      id,
      component: "iframe",
      title,
      params: { url, title },
      floating: { x: Math.max(0, (w - width) / 2), y: Math.max(0, (h - height) / 2), width, height },
    });
  }

  /**
   * Give one feed a panel of its own, so a player can keep, say, Nous traffic
   * beside the terminal while the Feeds panel shows another tab.
   */
  openFeed(label: string): void {
    const id = `feed:${label}`;
    const title = `Feed: ${label}`;
    if (settings.screenreader) {
      simple.open({ id, component: "spawns", title, params: { feed: label } });
      return;
    }
    if (!this.api) return;
    const existing = this.api.getPanel(id);
    if (existing) {
      existing.api.setActive();
      return;
    }
    const w = this.host?.clientWidth || this.api.width;
    const h = this.host?.clientHeight || this.api.height;
    const width = Math.max(Math.min(w * 0.45, 700), Math.min(w, 320));
    const height = Math.max(Math.min(h * 0.6, 600), Math.min(h, 240));
    this.api.addPanel({
      id,
      component: "spawns",
      title,
      params: { feed: label },
      floating: { x: Math.max(0, w - width - 20), y: 20, width, height },
    });
  }

  /** Pop a panel's group out into a real browser window (multi-monitor). */
  popout(id: string): void {
    if (!this.api) return;
    const panel = this.api.getPanel(id);
    if (!panel) return;
    try {
      // dockview-core 7 supports popout groups; args vary by version.
      (this.api as any).addPopoutGroup(panel.group ?? panel);
    } catch {
      /* popout unsupported in this build - ignore */
    }
  }

  /** Wipe the saved layout and reload to the default arrangement. */
  resetLayout(): void {
    try {
      localStorage.removeItem(LKEY);
    } catch {
      /* ignore */
    }
    location.reload();
  }

  /** Pop a channel out into its own floating panel (or focus it if already open). */
  openChannel(key: string, name?: string): void {
    if (settings.screenreader) {
      // One view at a time: a pop-out is just the channels view on this channel.
      chat.setActive(key);
      simple.open({ id: "chat", component: "chat", title: "Channels" });
      return;
    }
    if (!this.api) return;
    const id = `chan:${key}`;
    const existing = this.api.getPanel(id);
    if (existing) {
      existing.api.setActive();
      return;
    }
    this.api.addPanel({
      id,
      component: "channel",
      title: name || key,
      params: { channelKey: key },
      floating: true,
    });
  }
}

export const dock = new Dock();
