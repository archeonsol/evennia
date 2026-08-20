<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Lamp from "../components/Lamp.svelte";
  import Empty from "../components/Empty.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";

  interface Row {
    app: string;
    name: string;
    applied: boolean;
  }

  interface Payload {
    rows?: { rows?: Row[]; pending_count?: number };
  }

  const data = new Loader<Payload>();
  $effect(() => {
    data.load(rowsPath("migrations"));
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);
  const pending = $derived(body.pending_count ?? 0);
</script>

<PanelHead title="MIGRATIONS" count="{rows.length} TOTAL">
  {#snippet toolbar()}
    <Lamp
      label={pending ? `${pending} PENDING` : "NONE PENDING"}
      state={pending ? "attn" : "ok"}
    />
    <span class="legend">
      {pending ? "MIGRATE RUNS THESE ON THE NEXT RELOAD." : "MIGRATE HAS NOTHING TO APPLY."}
    </span>
  {/snippet}
</PanelHead>

<div class="panel-body">
  {#if rows.length === 0}
    <Empty line="NO MIGRATIONS." />
  {:else}
    <DataTable
      label="Migrations"
      columns={[
        { key: "state", label: "STATE" },
        { key: "app", label: "APP" },
        { key: "name", label: "NAME" },
      ]}
      {rows}
      key={(row) => `${row.app}.${row.name}`}
    >
      {#snippet row(item)}
        <tr>
          <td>
            <Lamp
              label={item.applied ? "APPLIED" : "PENDING"}
              state={item.applied ? "ok" : "attn"}
            />
          </td>
          <Cell value={item.app} />
          <Cell value={item.name} />
        </tr>
      {/snippet}
    </DataTable>
  {/if}
</div>
