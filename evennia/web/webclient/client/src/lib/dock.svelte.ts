// Holds the live dockview api so any component can open panels (e.g. pop a
// channel out to its own floating window). Set by Workspace on creation.

import type { DockviewApi } from "dockview-core";

const LKEY = "underspire.layout.v2";
const PRESET_PREFIX = "underspire.layout.preset.";
const LOCK_KEY = "underspire.layout.locked";

// The standard, reopenable panels (so closing one isn't a dead end).
export const VIEWS: Record<string, { component: string; title: string }> = {
  log: { component: "log", title: "Terminal" },
  scene: { component: "scene", title: "Scene" },
  chat: { component: "chat", title: "Channels" },
  tickets: { component: "tickets", title: "Tickets" },
  media: { component: "media", title: "Media" },
  spawns: { component: "spawns", title: "Feeds" },
  mytickets: { component: "mytickets", title: "My Tickets" },
};

class Dock {
  api: DockviewApi | null = null;
  locked = $state(false);

  set(api: DockviewApi | null): void {
    this.api = api;
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
    return !!this.api?.getPanel(id);
  }

  /** Reopen (or focus) one of the standard panels. */
  openView(id: string): void {
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

  /** Open an embedded web page in a floating panel. */
  openIframe(id: string, title: string, url: string): void {
    if (!this.api) return;
    const existing = this.api.getPanel(id);
    if (existing) {
      existing.api.setActive();
      return;
    }
    this.api.addPanel({ id, component: "iframe", title, params: { url }, floating: true });
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
