<script lang="ts">
  import Cell from "./Cell.svelte";
  import DataTable from "./DataTable.svelte";
  import { call } from "../lib/api";
  import { askText } from "../lib/dialog.svelte";
  import { report } from "../lib/report";
  import { view } from "../lib/state.svelte";

  interface Field { name: string; types: string[]; nullable?: boolean; choices?: [string, string][]; }
  interface PreviewRow { id: number; before: Record<string, unknown>; after: Record<string, unknown>; }
  interface Props { ids: string[]; onDone: () => void; onCancel: () => void; }
  const { ids, onDone, onCancel }: Props = $props();
  let fields = $state<Field[]>([]); let selected = $state<Record<string, boolean>>({});
  let values = $state<Record<string, string>>({}); let preview = $state<PreviewRow[]>([]);
  let missing = $state<unknown[]>([]); let status = $state(""); let busy = $state(false);

  $effect(() => {
    call<{ result?: { fields?: Field[] } }>("panels/records/actions/form/", { body: { model: view.model } }).then((result) => {
      if (!report(result)) return; fields = result.payload.result?.fields || [];
      values = Object.fromEntries(fields.map((field) => [field.name, ""]));
    });
  });
  function changes(): Record<string, unknown> | null {
    const result: Record<string, unknown> = {};
    for (const field of fields.filter((item) => selected[item.name])) {
      const value = values[field.name];
      if (value === "" && field.nullable) result[field.name] = null;
      else if (field.types.includes("FrozenContainer")) {
        try { result[field.name] = JSON.parse(value); }
        catch { status = `${field.name} must contain valid JSON.`; return null; }
      } else result[field.name] = value;
    }
    return result;
  }
  async function review() {
    const wanted = changes();
    if (!wanted) return;
    if (!Object.keys(wanted).length) { status = "Select at least one field to change."; return; }
    busy = true;
    const result = await call<{ result?: { rows?: PreviewRow[]; missing?: unknown[] } }>("panels/records/actions/preview_change/", { body: { model: view.model, ids, values: wanted } });
    busy = false; if (!report(result)) return;
    preview = result.payload.result?.rows || []; missing = result.payload.result?.missing || [];
  }
  const diffs = $derived(preview.flatMap((row) => Object.keys(row.after).map((field) => ({ key: `${row.id}-${field}`, id: row.id, field, before: row.before[field], after: row.after[field] }))));
  async function apply() {
    const reason = await askText({ title: `Apply changes to ${preview.length} row(s)`, description: "Every row receives the exact field values in the preview. Each outcome is recorded separately.", label: "Bulk-change reason", input: "textarea", confirmLabel: "APPLY BULK CHANGE", danger: true });
    if (!reason) return;
    const wanted = changes();
    if (!wanted) return;
    busy = true;
    const result = await call("panels/records/actions/bulk_save/", { body: { model: view.model, ids, values: wanted, reason } });
    busy = false; if (report(result)) onDone();
  }
</script>

<section class="editor" aria-label="Bulk edit selected rows">
  <div class="editor-head"><span class="legend">BULK EDIT / {ids.length} SELECTED</span><span class="spacer"></span>{#if preview.length}<button type="button" disabled={busy} onclick={apply}>APPLY PREVIEWED CHANGE</button>{/if}<button type="button" onclick={onCancel}>CANCEL</button></div>
  {#if !preview.length}
    <p class="empty-hint">Choose only the fields that every selected row should receive. The next step is a dry-run preview.</p>
    <div class="bulk-fields">
      {#each fields as field (field.name)}
        <label class="bulk-field"><input type="checkbox" bind:checked={selected[field.name]} /><span class="legend">{field.name}</span>
          {#if (field.choices ?? []).length}<select disabled={!selected[field.name]} bind:value={values[field.name]}>{#each field.choices ?? [] as choice (choice[0])}<option value={choice[0]}>{choice[1]}</option>{/each}</select>
          {:else if field.types.includes("bool")}<select disabled={!selected[field.name]} bind:value={values[field.name]}><option value="true">TRUE</option><option value="false">FALSE</option></select>
          {:else if field.types.includes("FrozenContainer")}<textarea rows="3" placeholder="VALID JSON" disabled={!selected[field.name]} bind:value={values[field.name]}></textarea>
          {:else}<input type="text" disabled={!selected[field.name]} bind:value={values[field.name]} />{/if}
        </label>
      {/each}
    </div>
    <div class="fault-actions"><button type="button" disabled={busy} onclick={review}>{busy ? "CHECKING" : "REVIEW BULK CHANGE"}</button></div>
  {:else}
    <DataTable label="Bulk change preview" columns={[{ key: "id", label: "ROW", numeric: true }, { key: "field", label: "FIELD" }, { key: "before", label: "BEFORE" }, { key: "after", label: "AFTER" }]} rows={diffs} key={(row) => row.key}>
      {#snippet row(item)}<tr><Cell value={item.id} /><Cell value={item.field} /><Cell value={item.before} /><Cell value={item.after} /></tr>{/snippet}
    </DataTable>
    {#if missing.length}<p class="field-error">{missing.length} selected row(s) no longer exist and will not be changed.</p>{/if}
    <div class="fault-actions"><button type="button" onclick={() => (preview = [])}>CHANGE THE FIELD SET</button></div>
  {/if}
  {#if status}<p class="field-error" role="alert">{status}</p>{/if}
</section>

<style>
  .bulk-fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1px; padding: 10px 14px; background: var(--rule); }
  .bulk-field { display: grid; grid-template-columns: auto minmax(100px, .7fr) minmax(120px, 1fr); align-items: center; gap: 8px; padding: 8px; background: var(--panel); }
  .bulk-field input:not([type="checkbox"]), .bulk-field select, .bulk-field textarea { width: 100%; }
  @media (max-width: 620px) { .bulk-field { grid-template-columns: auto minmax(0, 1fr); } .bulk-field > :last-child { grid-column: 1 / -1; } }
</style>
