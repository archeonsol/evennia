<script lang="ts">
  import { session } from "../lib/state.svelte";
  import { PANELS } from "../lib/registry";
  import PanelHead from "./PanelHead.svelte";
  import Empty from "./Empty.svelte";

  const Panel = $derived(PANELS[session.current]);
</script>

{#key session.current}
  {#if Panel}
    <Panel />
  {:else}
    <PanelHead title={session.current.toUpperCase()} />
    <Empty
      line="THIS STATION HAS NO VIEW IN THIS RELEASE."
      hint="The server offers this station. This build of the console cannot draw it."
    />
  {/if}
{/key}
