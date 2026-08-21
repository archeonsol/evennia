<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Lamp from "../components/Lamp.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { live } from "../lib/feed.svelte";

  interface Row {
    check: string;
    ok: boolean;
  }

  interface Payload {
    rows?: { rows?: Row[]; healthy?: boolean; version?: string };
  }

  const data = new Loader<Payload>();

  // Re-reads whenever the feed reports new health, so the panel an operator is
  // watching during an incident follows the server rather than a stale fetch.
  $effect(() => {
    void live.health;
    data.load(rowsPath("health"));
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);
</script>

<PanelHead title="HEALTH">
  {#snippet toolbar()}
    <Lamp
      label={body.healthy ? "SERVER IS WELL" : "SERVER NEEDS ATTENTION"}
      state={body.healthy ? "ok" : "fail"}
    />
  {/snippet}
</PanelHead>

<div class="panel-body">
  <dl class="rows">
    {#each rows as row (row.check)}
      <div class="row-pair">
        <dt>{row.check.replace(/_/g, " ")}</dt>
        <dd><Lamp label={row.ok ? "OK" : "FAILED"} state={row.ok ? "ok" : "fail"} /></dd>
      </div>
    {/each}
    <div class="row-pair">
      <dt>version</dt>
      <dd>{body.version || "--"}</dd>
    </div>
  </dl>
</div>
