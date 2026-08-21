<script lang="ts">
  import Picker from "./Picker.svelte";
  import DataTable from "./DataTable.svelte";
  import { runAction } from "../lib/load.svelte";
  import { bytes } from "../lib/format";
  import { view } from "../lib/state.svelte";
  import Lamp from "./Lamp.svelte";

  /* How large the JSONB documents are, per model.
   *
   * Sampled rather than aggregated, and the panel says which. `length(db_attrs
   * ::text)` across a table is exactly the sequential scan this station exists
   * to discourage, so the readout must not be the thing that causes one. */

  interface Sizes {
    sampled?: number;
    complete?: boolean;
    median_bytes?: number;
    threshold_bytes?: number;
    fat_count?: number;
    largest?: { id: number; bytes: number }[];
    note?: string;
  }

  let models = $state<string[]>([]);
  let sizes = $state<Sizes | null>(null);

  $effect(() => {
    runAction<{ models?: string[] }>("database", "models").then((found) => {
      models = found?.models ?? [];
      if (!view.dbModel && models.length) view.dbModel = models[0];
    });
  });

  $effect(() => {
    if (!view.dbModel) return;
    runAction<Sizes>("database", "sizes", { model: view.dbModel }).then(
      (found) => (sizes = found),
    );
  });
</script>

{#if models.length}
  <div class="toolbar">
    <Picker id="db-model" label="Model" key="dbModel" values={models} blank="CHOOSE A MODEL" />
  </div>

  {#if sizes}
    <dl class="rows">
      <div class="row-pair">
        <dt>documents measured</dt>
        <dd>
          <span class="num">{sizes.sampled ?? 0}</span>
          <span class="empty-hint">
            {sizes.complete ? " (every row)" : " (most recent only)"}
          </span>
        </dd>
      </div>
      <div class="row-pair">
        <dt>median size</dt>
        <dd class="num">{bytes(sizes.median_bytes)}</dd>
      </div>
      <div class="row-pair">
        <dt>over {bytes(sizes.threshold_bytes)}</dt>
        <dd><Lamp label={String(sizes.fat_count ?? 0)} state={sizes.fat_count ? "attn" : "ok"} /></dd>
      </div>
    </dl>

    {#if (sizes.largest ?? []).length}
      <DataTable
        label="Largest documents"
        columns={[
          { key: "id", label: "OBJECT", numeric: true },
          { key: "bytes", label: "DOCUMENT SIZE", numeric: true },
        ]}
        rows={sizes.largest ?? []}
        key={(row) => row.id}
      >
        {#snippet row(item)}
          <tr>
            <td class="num">#{item.id}</td>
            <td class={item.bytes > (sizes?.threshold_bytes ?? 0) ? "num fail-text" : "num"}>
              {bytes(item.bytes)}
            </td>
          </tr>
        {/snippet}
      </DataTable>
    {/if}
    <p class="empty-hint">{sizes.note || ""}</p>
  {/if}
{/if}
