<script lang="ts">
  import Empty from "./Empty.svelte";
  import DataTable from "./DataTable.svelte";
  import Cell from "./Cell.svelte";
  import { runAction } from "../lib/load.svelte";
  import { view } from "../lib/state.svelte";

  /* The objects carrying one key.
   *
   * This is the step between "which keys exist" and "what does this object
   * hold": without it an operator has a catalogue and no way to reach a single
   * document from it. */

  interface Carrier {
    id: number;
    name: string;
    value: string;
  }

  interface Props {
    model: string;
    attrKey: string;
  }

  const { model, attrKey }: Props = $props();

  let rows = $state<Carrier[]>([]);
  let note = $state("");
  let loaded = $state(false);

  $effect(() => {
    loaded = false;
    runAction<{ rows?: Carrier[]; note?: string }>("attributes", "carriers", {
      model: model || "",
      key: attrKey,
    }).then((found) => {
      rows = found?.rows ?? [];
      note = found?.note ?? "";
      loaded = true;
    });
  });
</script>

<div class="detail">
  <div class="toolbar">
    <span class="legend">OBJECTS CARRYING {attrKey.toUpperCase()}</span>
    <span class="spacer"></span>
    <button type="button" onclick={() => (view.attrKeyOpen = "")}>CLOSE</button>
  </div>

  {#if loaded && !rows.length}
    <Empty line="NO OBJECT CARRIES THIS KEY." />
  {:else if rows.length}
    <DataTable
      label="Objects carrying this key"
      columns={[
        { key: "id", label: "ID", numeric: true },
        { key: "name", label: "NAME" },
        { key: "value", label: "VALUE" },
        { key: "act", label: "" },
      ]}
      {rows}
      key={(row) => row.id}
    >
      {#snippet row(item)}
        <tr>
          <td class="num">{item.id}</td>
          <Cell value={item.name || ""} />
          <Cell value={item.value || ""} />
          <td>
            <button type="button" onclick={() => (view.attrObject = String(item.id))}>
              DOCUMENT
            </button>
          </td>
        </tr>
      {/snippet}
    </DataTable>
    {#if note}<p class="empty-hint">{note}</p>{/if}
  {/if}
</div>
