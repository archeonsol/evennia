<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Lamp from "../components/Lamp.svelte";
  import Empty from "../components/Empty.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";

  interface Row {
    name: string;
    value: unknown;
    overridden?: boolean;
  }

  interface Payload {
    rows?: { rows?: Row[]; total?: number; overridden?: number };
  }

  const data = new Loader<Payload>();
  $effect(() => {
    data.load(rowsPath("settings"));
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);

  function written(value: unknown): string {
    return typeof value === "string" ? value : JSON.stringify(value);
  }
</script>

<PanelHead title="SETTINGS" count="{body.total ?? 0} TOTAL">
  {#snippet toolbar()}
    <Lamp label="{body.overridden ?? 0} CHANGED BY THE GAME" state="attn" />
    <span class="legend">
      A CHANGED VALUE IS SHOWN IN AMBER. A SECRET VALUE STAYS ON THE SERVER.
    </span>
  {/snippet}
</PanelHead>

<div class="panel-body">
  {#if rows.length === 0}
    <Empty line="NO SETTINGS." />
  {:else}
    <dl class="rows">
      {#each rows as row (row.name)}
        <div class="row-pair">
          <dt>{row.name}</dt>
          <dd class={row.overridden ? "overridden" : undefined}>{written(row.value)}</dd>
        </div>
      {/each}
    </dl>
  {/if}
</div>
