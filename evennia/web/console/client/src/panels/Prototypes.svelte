<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import SearchField from "../components/SearchField.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { view } from "../lib/state.svelte";

  interface Row {
    key: string;
    typeclass: string;
    parent: string;
    fields?: string[];
  }

  interface Payload {
    rows?: {
      rows?: Row[];
      available?: boolean;
      reason?: string;
      count?: number;
      note?: string;
    };
  }

  const data = new Loader<Payload>();
  $effect(() => {
    data.load(rowsPath("prototypes", { search: view.protoSearch }));
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);
</script>

<PanelHead title="PROTOTYPES" count={body.count ? `${body.count} PROTOTYPES` : ""}>
  {#snippet toolbar()}
    <SearchField key="protoSearch" label="FILTER BY KEY" />
    <span class="spacer"></span>
    <Lamp label="READ ONLY" state="off" />
  {/snippet}
</PanelHead>

<div class="panel-body">
  {#if body.available === false}
    <Empty line="PROTOTYPES ARE NOT LOADED." hint={body.reason || ""} />
  {:else if !rows.length}
    <Empty line="THIS GAME DECLARES NO PROTOTYPE." />
  {:else}
    <DataTable
      columns={[
        { key: "key", label: "KEY" },
        { key: "typeclass", label: "TYPECLASS" },
        { key: "parent", label: "PARENT" },
        { key: "fields", label: "FIELDS" },
      ]}
      {rows}
      key={(row) => row.key}
    >
      {#snippet row(item)}
        <tr>
          <Cell value={item.key} />
          <Cell value={item.typeclass} />
          <Cell value={item.parent} />
          <Cell value={(item.fields ?? []).join(" ")} />
        </tr>
      {/snippet}
    </DataTable>
  {/if}
  <p class="empty-hint">{body.note || ""}</p>
</div>
