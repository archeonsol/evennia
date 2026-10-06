<script lang="ts">
  import "dockview-core/dist/styles/dockview.css";
  import { createDockview } from "dockview-core";
  import type { DockviewApi } from "dockview-core";
  import { svelteComponents } from "../lib/dockAdapter.svelte";
  import { chat } from "../lib/chat.svelte";
  import { dock, VIEWS } from "../lib/dock.svelte";
  import { panelPrefs } from "../lib/panelPrefs.svelte";
  import { PANELS } from "../lib/panelRegistry";
  import { puppets } from "../lib/puppets.svelte";
  import { activity } from "../lib/activity.svelte";

  let host = $state<HTMLDivElement | null>(null);

  /** Width for the scene/channels column on a fresh layout. */
  function sideWidth(el: HTMLElement | null): number {
    const w = el?.clientWidth || window.innerWidth;
    return Math.round(Math.min(Math.max(w * 0.32, 280), 520));
  }
  let api = $state<DockviewApi | null>(null);
  const LKEY = "underspire.layout.v2";

  function applyPanelPrefs() {
    if (!api) return;
    for (const p of api.panels) {
      const el = (p as any).view?.content?.element as HTMLElement | undefined;
      if (!el) continue;
      const pref = panelPrefs.get(p.id);
      el.style.setProperty("--shell-font-size", pref.fontPx ? `${pref.fontPx}px` : "");
      el.style.opacity = pref.opacity != null ? String(pref.opacity / 100) : "";
    }
  }

  // Re-apply whenever the per-panel prefs change.
  $effect(() => {
    void panelPrefs.prefs;
    applyPanelPrefs();
  });

  $effect(() => {
    if (!host) return;
    const dv: DockviewApi = createDockview(host, {
      createComponent: svelteComponents(PANELS),
    });
    api = dv;
    dock.set(dv, host);

    let restored = false;
    try {
      const saved = localStorage.getItem(LKEY);
      if (saved) {
        dv.fromJSON(JSON.parse(saved));
        restored = true;
      }
    } catch {
      restored = false;
    }
    if (!restored) {
      // dockview measures its host later; sized panels added before then are
      // scaled against nothing. Give it the real size first.
      if (host.clientWidth && host.clientHeight) dv.layout(host.clientWidth, host.clientHeight);
      dv.addPanel({ id: "log", component: "log", title: "Terminal" });
      // A phone has no room for a side column: the terminal would be a strip
      // one word wide. Scene and Channels become tabs beside it instead.
      const narrow = (host.clientWidth || window.innerWidth) < 720;
      if (narrow) {
        dv.addPanel({ id: "scene", component: "scene", title: "Scene", position: { referencePanel: "log", direction: "within" } });
        dv.addPanel({ id: "chat", component: "chat", title: "Channels", position: { referencePanel: "log", direction: "within" } });
      } else {
        dv.addPanel({
          id: "scene",
          component: "scene",
          title: "Scene",
          position: { referencePanel: "log", direction: "right" },
          // A usable third of the width. Left to dockview it split evenly, or,
          // laid out before the window had its size, left a sliver that
          // wrapped every word.
          initialWidth: sideWidth(host),
        });
        dv.addPanel({
          id: "chat",
          component: "chat",
          title: "Channels",
          position: { referencePanel: "scene", direction: "below" },
        });
      }
      dv.getPanel("log")?.api.setActive();
    }

    const sub = dv.onDidLayoutChange(() => {
      if (dock.locked) return; // locked: don't persist accidental drags
      try {
        localStorage.setItem(LKEY, JSON.stringify(dv.toJSON()));
      } catch {
        /* ignore */
      }
    });
    // New panels pick up any saved per-panel overrides.
    const addSub = dv.onDidAddPanel(() => applyPanelPrefs());
    // The queue's badge flags what has not been looked at: focusing the panel
    // is looking at it, so its arrivals and updates count as seen from then on.
    const activeSub = dv.onDidActivePanelChange((e: any) => {
      chat.queueActive = e.panel?.id === "tickets";
      if (chat.queueActive) chat.markQueueSeen();
    });

    return () => {
      sub.dispose();
      addSub.dispose();
      activeSub.dispose();
      dv.dispose();
      api = null;
      dock.set(null);
    };
  });

  // A first remote scene proves this session has a multi-puppet feed; add its
  // stable-ID panel without forcing it into every player's saved layout.
  $effect(() => {
    if (puppets.list.length && api && !api.getPanel("puppets")) {
      try {
        api.addPanel({
          id: "puppets",
          component: "puppets",
          title: "Puppets",
          inactive: true,
          position: api.getPanel("scene")
            ? { referencePanel: "scene", direction: "within" }
            : undefined,
        });
      } catch {
        /* ignore */
      }
    }
  });

  $effect(() => {
    if (!api) return;
    const panel = api.getPanel("activity");
    if (activity.allowed && !panel) {
      api.addPanel({
        id: "activity", component: "activity", title: VIEWS.activity.title,
        position: api.getPanel("chat") ? { referencePanel: "chat", direction: "within" } : undefined,
      });
    } else if (!activity.allowed && activity.known && panel) panel.api.close();
  });

  // A tab whose feed holds something unread carries [!!] in its title, so
  // news on another tab is visible from this one. The count stays inside the
  // panel; the tab only flags that something waits.
  $effect(() => {
    if (!api) return;
    const flagged: Record<string, [string, boolean]> = {
      puppets: ["Puppets", puppets.totalUnread > 0],
      chat: ["Channels", chat.channelsUnseen > 0],
      tickets: [VIEWS.tickets.title, chat.queueUnseen > 0],
    };
    for (const [id, [base, hot]] of Object.entries(flagged)) {
      const panel: any = api.getPanel(id);
      if (!panel) continue;
      const title = hot ? `${base} [!!]` : base;
      if (panel.title === title) continue;
      try {
        if (typeof panel.setTitle === "function") panel.setTitle(title);
        else if (panel.api?.setTitle) panel.api.setTitle(title);
      } catch {
        /* dockview title update is best-effort */
      }
    }
  });

  // Staff-only: the ticket queue panel follows the server's ticket_role. A
  // saved layout (or a named preset) can hold the panel from a staff session
  // on this browser; once the server says this session is not staff, it goes.
  $effect(() => {
    if (!api) return;
    const panel: any = api.getPanel("tickets");
    if (chat.staff) {
      try {
        if (!panel) {
          api.addPanel({
            id: "tickets",
            component: "tickets",
            title: VIEWS.tickets.title,
            position: api.getPanel("chat")
              ? { referencePanel: "chat", direction: "within" }
              : undefined,
          });
        }
      } catch {
        /* ignore */
      }
    } else if (chat.staffKnown && panel) {
      panel.api.close();
    }
  });
</script>

<div class="workspace dockview-theme-dark dv-underspire" bind:this={host}></div>

<style>
  .workspace {
    height: 100%;
    width: 100%;
  }
</style>
