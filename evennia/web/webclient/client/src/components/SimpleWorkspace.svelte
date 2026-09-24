<script lang="ts">
  // Screen-reader layout: one view at a time behind a tab list. Every view
  // stays mounted (hidden) so a channel draft or the log's reading position
  // survives switching away; `hidden` also takes it out of the reading order.
  import { tick } from "svelte";
  import { simple } from "../lib/simpleLayout.svelte";
  import { PANELS } from "../lib/panelRegistry";
  import { chat } from "../lib/chat.svelte";
  import { puppets } from "../lib/puppets.svelte";

  let tabEls: Record<string, HTMLButtonElement> = {};

  // Staff and puppeteers get their extra views the same way the docked
  // workspace adds them: when the data that needs them first arrives.
  $effect(() => {
    if (chat.staff && !simple.has("tickets")) {
      simple.views = [...simple.views, { id: "tickets", component: "tickets", title: "Tickets", closable: false }];
    }
  });
  $effect(() => {
    if (puppets.list.length && !simple.has("puppets")) {
      simple.views = [...simple.views, { id: "puppets", component: "puppets", title: "Puppets", closable: false }];
    }
  });

  // Whenever the view changes (a pick from Views, a page the server opened,
  // a Close button), focus that was inside the old view would fall to the
  // page body with it hidden. Put it on the new view's tab instead.
  $effect(() => {
    const id = simple.active;
    tick().then(() => {
      const a = document.activeElement;
      if (!a || a === document.body || a.closest("[role=tabpanel][hidden]")) tabEls[id]?.focus();
    });
  });

  function label(id: string, title: string): string {
    if (id === "chat") {
      const n = Object.values(chat.unread).reduce((a, b) => a + (b ?? 0), 0);
      return n ? `${title} (${n} unread)` : title;
    }
    if (id === "puppets" && puppets.totalUnread) return `${title} (${puppets.totalUnread})`;
    return title;
  }

  // Arrow keys walk the tabs (the tab list is one Tab stop), Home/End jump.
  async function onTabKey(e: KeyboardEvent, i: number) {
    const n = simple.views.length;
    let next = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % n;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (i - 1 + n) % n;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = n - 1;
    else if (e.key === "Delete" && simple.views[i].closable) {
      e.preventDefault();
      simple.close(simple.views[i].id);
      await tick();
      tabEls[simple.active]?.focus();
      return;
    }
    if (next < 0) return;
    e.preventDefault();
    simple.show(simple.views[next].id);
    await tick();
    tabEls[simple.views[next].id]?.focus();
  }
</script>

<div class="simple">
  <div class="tabs" role="tablist" aria-label="Views">
    {#each simple.views as v, i (v.id)}
      <button
        bind:this={tabEls[v.id]}
        id="sv-tab-{v.id}"
        class="tab"
        role="tab"
        aria-selected={simple.active === v.id}
        aria-controls="sv-panel-{v.id}"
        tabindex={simple.active === v.id ? 0 : -1}
        onclick={() => simple.show(v.id)}
        onkeydown={(e) => onTabKey(e, i)}
      >{label(v.id, v.title)}</button>
    {/each}
  </div>

  {#each simple.views as v (v.id)}
    {@const Panel = PANELS[v.component]}
    <div
      id="sv-panel-{v.id}"
      class="view"
      role="tabpanel"
      aria-labelledby="sv-tab-{v.id}"
      hidden={simple.active !== v.id}
    >
      {#if v.closable}
        <div class="view-bar">
          <button class="close" onclick={() => simple.close(v.id)}>Close {v.title}</button>
        </div>
      {/if}
      <div class="view-body">
        {#if Panel}<Panel {...v.params ?? {}} />{/if}
      </div>
    </div>
  {/each}
</div>

<style>
  .simple { display: flex; flex-direction: column; height: 100%; min-height: 0; }
  .tabs {
    display: flex; flex-wrap: wrap; gap: 2px; flex: 0 0 auto;
    padding: 4px 8px 0; border-bottom: 1px solid var(--border-bright); background: var(--bg-elev);
  }
  .tab {
    background: none; border: 1px solid var(--border-bright); border-bottom: none;
    color: var(--fg-dim); font-family: inherit; font-size: 0.78rem; letter-spacing: 0.08em;
    padding: 5px 12px; min-height: 28px; cursor: pointer;
  }
  .tab:hover { color: var(--fg); }
  .tab[aria-selected="true"] { color: var(--accent-bright); border-color: var(--accent); background: var(--bg); }
  .view { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  .view[hidden] { display: none; }
  .view-bar { flex: 0 0 auto; display: flex; justify-content: flex-end; padding: 3px 8px; border-bottom: 1px solid var(--border); }
  .close {
    background: none; border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.72rem; padding: 2px 8px; min-height: 24px; cursor: pointer;
  }
  .close:hover { color: var(--accent-bright); border-color: var(--accent); }
  .view-body { flex: 1; min-height: 0; overflow: hidden; }
</style>
