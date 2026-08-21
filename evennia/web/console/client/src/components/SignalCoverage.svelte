<script lang="ts">
  import Section from "./Section.svelte";
  import Empty from "./Empty.svelte";
  import Lamp from "./Lamp.svelte";
  import DataTable from "./DataTable.svelte";
  import Cell from "./Cell.svelte";
  import { runAction } from "../lib/load.svelte";
  import type { SignalRow } from "../lib/types";

  /* Which signals are arriving at all.
   *
   * A signal that was configured and is silently absent leaves the queue
   * looking calm for the wrong reason, and nothing else in the console would
   * ever say so. This is the only place that reports the difference between
   * "nobody is evading" and "the server cannot see them". */

  interface Coverage {
    sample?: number;
    rows?: SignalRow[];
    note?: string;
  }

  const LAMPS: Record<string, string> = {
    ok: "ARRIVING",
    attn: "PARTIAL",
    fail: "ABSENT",
    off: "NO DATA",
  };

  let data = $state<Coverage | null>(null);

  $effect(() => {
    runAction<Coverage>("moderation", "signals").then((found) => (data = found));
  });
</script>

<Section label="Signal coverage" />

{#if data && !data.sample}
  <Empty line="NO CONNECTIONS RECORDED." hint={data.note || ""} />
{:else if data}
  <DataTable
    label="Signal coverage"
    columns={[
      { key: "label", label: "SIGNAL" },
      { key: "seen", label: "SEEN", numeric: true },
      { key: "state", label: "" },
      { key: "advice", label: "WHAT TO DO" },
    ]}
    rows={data.rows ?? []}
    key={(row) => row.field}
  >
    {#snippet row(item)}
      <tr>
        <Cell value={item.label.toUpperCase()} />
        <td class="num">{item.of ? `${item.seen} / ${item.of}` : "--"}</td>
        <td><Lamp label={LAMPS[item.state] ?? item.state} state={item.state} /></td>
        <Cell value={item.advice} />
      </tr>
    {/snippet}
  </DataTable>
  <p class="legend">{data.note || ""}</p>
{/if}
