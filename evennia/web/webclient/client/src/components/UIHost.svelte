<script lang="ts">
  // Floating overlay for server-driven UI components. Docked gauges (dock:true)
  // stack top-left as a HUD; everything else stacks bottom-right as cards.
  import { ui } from "../lib/ui.svelte";
  import UICard from "./UICard.svelte";

  const all = $derived(Object.values(ui.components));
  const hud = $derived(all.filter((c) => c.dock));
  const cards = $derived(all.filter((c) => !c.dock));
</script>

{#if hud.length}
  <div class="ui-hud">
    {#each hud as comp (comp.id)}<UICard {comp} />{/each}
  </div>
{/if}
{#if cards.length}
  <div class="ui-cards">
    {#each cards as comp (comp.id)}<UICard {comp} />{/each}
  </div>
{/if}

<style>
  .ui-hud {
    position: absolute; top: 46px; left: 16px; z-index: 60;
    display: flex; flex-direction: column; gap: 6px; width: 220px; pointer-events: auto;
  }
  .ui-cards {
    position: absolute; right: 16px; bottom: 92px; z-index: 60;
    display: flex; flex-direction: column; gap: 8px; width: min(320px, 40vw);
    max-height: 70vh; overflow-y: auto; pointer-events: auto;
  }
</style>
