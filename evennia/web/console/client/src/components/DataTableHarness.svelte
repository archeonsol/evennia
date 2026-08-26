<script lang="ts">
  import DataTable from "./DataTable.svelte";
  import Cell from "./Cell.svelte";

  /* A component exists only because a snippet cannot be passed from a test
   * file. Kept beside the thing it exercises rather than in a fixtures folder,
   * so it is obvious when DataTable's contract changes and this did not. */

  interface Props {
    rows: { id: number; name: string }[];
    rowHeight?: number;
    overscan?: number;
  }

  const { rows, rowHeight = 30, overscan = 8 }: Props = $props();
</script>

<DataTable
  columns={[
    { key: "id", label: "ID", numeric: true },
    { key: "name", label: "NAME" },
  ]}
  {rows}
  {rowHeight}
  {overscan}
  key={(row) => row.id}
>
  {#snippet row(item)}
    <tr data-row={item.id}>
      <Cell value={item.id} />
      <Cell value={item.name} />
    </tr>
  {/snippet}
</DataTable>
