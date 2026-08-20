<script lang="ts">
  import { session, select } from "../lib/state.svelte";

  interface Props {
    onPalette: () => void;
  }

  const { onPalette }: Props = $props();
</script>

<nav class="rail" aria-label="Stations">
  <div class="rail-heading">
    <span class="legend">Stations</span>
    <!-- Discoverability: a shortcut nobody is told about is a shortcut nobody
         uses, and this one is how you reach a station without the rail. -->
    <button class="rail-palette" type="button" title="Go to a station" onclick={onPalette}>
      CTRL K
    </button>
  </div>

  {#each session.panels as panel (panel.key)}
    <button
      class="rail-item"
      type="button"
      aria-current={panel.key === session.current ? "true" : "false"}
      onclick={() => select(panel.key)}
    >
      {panel.label}
      {#if panel.description}<small>{panel.description}</small>{/if}
    </button>
  {/each}

  {#if session.degraded}
    <p class="rail-note">
      THE GAME SERVER IS NOT AVAILABLE. THE PANELS THAT READ THE DATABASE CONTINUE TO OPERATE. THE
      PANELS THAT CHANGE GAME STATE ARE DISABLED.
    </p>
  {/if}
</nav>
