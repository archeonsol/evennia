<script lang="ts">
  // Every puppet at once: a strip of chips picks which terminals are shown, and
  // the shown ones share the panel in a grid, each with its own command line.
  import { untrack } from "svelte";
  import { puppets } from "../lib/puppets.svelte";
  import { puppetLayout } from "../lib/puppetLayout.svelte";
  import { ARRANGES, shownIds, tileColumns, type PuppetArrange } from "../lib/puppetLayout";
  import PuppetTerminal from "./PuppetTerminal.svelte";

  const ARRANGE_LABEL: Record<PuppetArrange, string> = { grid: "Grid", columns: "Columns", rows: "Rows" };

  let root = $state<HTMLElement | null>(null);
  let width = $state(0);
  // A hidden dockview tab, or a view the screen-reader layout is not showing,
  // is not being looked at: its puppets badge as unread again.
  let visible = $state(true);

  const known = $derived(puppets.list.map((p) => p.npcId));
  // What the player chose to keep open, and what is on screen (one, when expanded).
  const open = $derived(shownIds(puppetLayout.open, known));
  const shown = $derived(puppetLayout.shown(known));
  const cols = $derived(tileColumns(shown.length, puppetLayout.arrange, width));
  const rows = $derived(Math.max(1, Math.ceil(shown.length / cols)));

  $effect(() => {
    const ids = known;
    untrack(() => puppetLayout.sync(ids));
  });

  $effect(() => {
    const ids = visible ? shown : [];
    untrack(() => puppets.setWatching(ids));
  });

  $effect(() => {
    const el = root;
    if (!el) return;
    width = el.clientWidth;
    // The columns follow the width, which reshapes the tiles inside; doing it
    // in the observer's own turn would make the browser report a resize loop.
    let frame = 0;
    const resize = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        width = el.clientWidth;
      });
    });
    resize.observe(el);
    let seen: IntersectionObserver | null = null;
    if (typeof IntersectionObserver !== "undefined") {
      seen = new IntersectionObserver((entries) => {
        visible = entries.some((entry) => entry.isIntersecting);
      });
      seen.observe(el);
    }
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      seen?.disconnect();
    };
  });

  function toggle(npcId: number) {
    const wasOpen = open.includes(npcId);
    puppetLayout.toggle(npcId, known);
    if (wasOpen) return;
    // A terminal the player just asked for is one they mean to type in.
    queueMicrotask(() => {
      root?.querySelector<HTMLTextAreaElement>(`[data-npc="${npcId}"] textarea`)?.focus();
    });
  }
</script>

<section class="puppets" aria-label="Remote puppet viewpoints" bind:this={root}>
  {#if puppets.list.length}
    <div class="bar">
      <div class="chips" role="group" aria-label="Shown puppets">
        {#each puppets.list as feed (feed.npcId)}
          <button
            class="sh-toggle chip"
            aria-pressed={open.includes(feed.npcId)}
            onclick={() => toggle(feed.npcId)}
            title="{open.includes(feed.npcId) ? 'Hide' : 'Show'} {feed.name} (#{feed.npcId})"
          >
            <strong>P{feed.slot}</strong> {feed.name}
            {#if feed.unread}
              <span class="badge" aria-label="{feed.unread} unread">{feed.unread > 99 ? "99+" : feed.unread}</span>
            {/if}
          </button>
        {/each}
      </div>
      <div class="actions">
        <div class="arrange" role="group" aria-label="Arrangement">
          {#each ARRANGES as arrange (arrange)}
            <button class="sh-toggle" aria-pressed={puppetLayout.arrange === arrange} onclick={() => puppetLayout.setArrange(arrange)}>{ARRANGE_LABEL[arrange]}</button>
          {/each}
        </div>
        <button class="sh-cmd" onclick={() => puppetLayout.showAll(known)} disabled={open.length === known.length}>Show all</button>
        <button class="sh-cmd" onclick={() => puppetLayout.hideAll()} disabled={!open.length}>Hide all</button>
      </div>
    </div>

    {#if shown.length}
      <div class="grid" style="grid-template-columns: repeat({cols}, minmax(0, 1fr)); grid-template-rows: repeat({rows}, minmax(240px, 1fr));">
        {#each shown as npcId (npcId)}
          <PuppetTerminal
            {npcId}
            solo={puppetLayout.solo === npcId}
            onsolo={open.length > 1 || puppetLayout.solo === npcId ? () => puppetLayout.toggleSolo(npcId) : undefined}
            onclose={() => toggle(npcId)}
          />
        {/each}
      </div>
    {:else}
      <p class="empty">No terminal shown. Pick a puppet above.</p>
    {/if}
  {:else}
    <p class="empty">No remote puppet viewpoints.</p>
  {/if}
</section>

<style>
  .puppets { display: flex; flex-direction: column; height: 100%; overflow: hidden; background: var(--bg-elev); }

  .bar { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: center; justify-content: space-between; padding: 5px 8px; border-bottom: 1px solid var(--accent); }
  .chips, .actions, .arrange { display: flex; flex-wrap: wrap; gap: 2px 6px; align-items: center; }
  .chip strong { color: var(--gold); font-weight: normal; }
  .badge {
    margin-left: 6px; min-width: 1.4em; padding: 0 0.5ch;
    background: var(--gold); color: var(--bg-deep); font-size: 0.64rem; text-align: center;
  }

  /* Each tile gets at least 240px of height; a panel too short for them scrolls. */
  .grid { flex: 1 1 0; min-height: 0; overflow-y: auto; display: grid; gap: 4px; padding: 4px; }
  .empty { color: var(--fg-faint); font-size: 0.64rem; letter-spacing: 0.14em; text-transform: uppercase; padding: 10px; }
</style>
