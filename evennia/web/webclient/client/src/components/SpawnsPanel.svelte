<script lang="ts">
  import { routing } from "../lib/routing.svelte";

  const labels = $derived(routing.labels());
  let active = $state("");
  // Default to the first label; keep valid as labels change.
  $effect(() => {
    if (!labels.includes(active)) active = labels[0] ?? "";
  });
  const lines = $derived(active ? (routing.buffers[active] ?? []) : []);
</script>

<div class="spawns">
  <div class="tabs">
    {#each labels as l}
      <button class="tab" class:on={l === active} onclick={() => (active = l)}>{l}</button>
    {/each}
    {#if active}<button class="clr" onclick={() => routing.clear(active)} title="clear">⌫</button>{/if}
  </div>
  {#if labels.length}
    <div class="lines">
      {#each lines as ln, i (i)}
        <div class="line">{@html ln.html}</div>
      {/each}
      {#if !lines.length}<p class="empty">Nothing routed here yet.</p>{/if}
    </div>
  {:else}
    <p class="empty">No routes. Add them in Settings → Triggers → Routing.</p>
  {/if}
</div>

<style>
  .spawns { display: flex; flex-direction: column; height: 100%; background: var(--bg-elev); }
  .tabs { display: flex; align-items: center; gap: 4px; padding: 4px 8px; border-bottom: 1px solid var(--accent); flex: 0 0 auto; flex-wrap: wrap; }
  .tab { background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg-dim); font-family: inherit; font-size: 0.64rem; text-transform: uppercase; letter-spacing: 0.08em; padding: 2px 8px; cursor: pointer; }
  .tab.on { color: var(--accent-bright); border-color: var(--accent); }
  .clr { margin-left: auto; background: none; border: none; color: var(--fg-faint); font-family: inherit; cursor: pointer; }
  .clr:hover { color: var(--alert); }
  .lines { flex: 1; overflow-y: auto; padding: 6px 10px; line-height: 1.5; }
  .line { white-space: pre-wrap; word-break: break-word; }
  .empty { color: var(--fg-faint); font-style: italic; padding: 10px; font-size: 0.78rem; }
</style>
