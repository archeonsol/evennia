<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import CopyLinkButton from "../components/CopyLinkButton.svelte";
  import SaveViewButton from "../components/SaveViewButton.svelte";
  import RecordEditor from "../components/RecordEditor.svelte";
  import BulkEditor from "../components/BulkEditor.svelte";
  import { call } from "../lib/api";
  import { report } from "../lib/report";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { previewDelete, runExport } from "../lib/records";
  import { view, local } from "../lib/state.svelte";

  interface Model {
    label: string;
    verbose_name_plural: string;
  }

  type Row = Record<string, unknown>;

  interface Body {
    rows?: Row[];
    columns?: string[];
    available_columns?: string[];
    field_specs?: FieldSpec[];
    writable?: boolean;
    write_via?: string;
    storage?: string;
    has_more?: boolean;
    next_cursor?: string;
  }

  interface FieldSpec {
    name: string;
    kind?: string;
    relation?: string;
  }

  interface RecordFilter {
    field: string;
    lookup: string;
    value: string;
  }

  const data = new Loader<{ rows?: Body }>();
  let models = $state<Model[]>([]);
  let modelsFailed = $state(false);
  let reload = $state(0);
  let bulkEditing = $state(false);
  let filtersOpen = $state(false);

  const LOOKUPS = [
    ["exact", "IS"],
    ["iexact", "IS (CASE-INSENSITIVE)"],
    ["contains", "CONTAINS"],
    ["startswith", "STARTS WITH"],
    ["gt", "GREATER THAN"],
    ["gte", "AT LEAST"],
    ["lt", "LESS THAN"],
    ["lte", "AT MOST"],
    ["isnull", "IS EMPTY"],
    ["in", "IS ONE OF"],
  ] as const;

  function parseFilters(raw: string): RecordFilter[] {
    try {
      const parsed = JSON.parse(raw || "[]");
      return Array.isArray(parsed)
        ? parsed.filter((item) => item && item.field && item.lookup).slice(0, 12)
        : [];
    } catch {
      return [];
    }
  }

  const filters = $derived(parseFilters(view.recordFilters));

  function storeFilters(next: RecordFilter[]) {
    relist(() => (view.recordFilters = next.length ? JSON.stringify(next) : ""));
  }

  function filterParams(): Record<string, string> {
    return Object.fromEntries(
      filters
        .filter((item) => item.field && (item.lookup === "isnull" || item.value.trim()))
        .map((item) => [`f.${item.field}__${item.lookup}`, item.lookup === "isnull" ? item.value || "true" : item.value]),
    );
  }

  $effect(() => {
    loadModels();
  });

  async function loadModels() {
    const result = await call<{ result?: Model[] }>("panels/records/actions/models/", { body: {} });
    if (!report(result)) {
      modelsFailed = true;
      return;
    }
    models = result.payload.result || [];
    if (!view.model && models.length) view.model = models[0].label;
  }

  $effect(() => {
    void reload;
    if (!view.model) return;
    data.load(
      rowsPath("records", {
        model: view.model,
        search: view.search,
        order: view.order,
        columns: view.columns,
        cursor: view.cursor,
        ...filterParams(),
      }),
    );
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);
  const columns = $derived(body.columns ?? []);
  const availableColumns = $derived(body.available_columns ?? []);
  const idOf = $derived((row: Row) => String(row[columns[0]]));

  const allPicked = $derived(local.chosen.length > 0 && local.chosen.length === rows.length);

  /* Changing what is listed invalidates the cursor: a keyset cursor points into
   * an ordering, and keeping it across a re-sort lands on an arbitrary page. */
  function relist(change: () => void) {
    change();
    view.cursor = "";
    local.trail = [];
    local.chosen = [];
  }

  function sortBy(field: string) {
    relist(() => {
      view.order = view.order === field ? `-${field}` : field;
    });
  }

  function toggleColumn(field: string, on: boolean) {
    const current = view.columns ? view.columns.split(",").filter(Boolean) : [...columns];
    const next = on ? [...new Set([...current, field])] : current.filter((item) => item !== field);
    if (!next.length) return;
    relist(() => (view.columns = next.join(",")));
  }

  function addFilter() {
    const first = availableColumns[0] || columns[0];
    if (!first) return;
    storeFilters([...filters, { field: first, lookup: "exact", value: "" }]);
    filtersOpen = true;
  }

  function updateFilter(index: number, patch: Partial<RecordFilter>) {
    storeFilters(filters.map((item, at) => (at === index ? { ...item, ...patch } : item)));
  }

  function pick(id: string, on: boolean) {
    local.chosen = on
      ? [...new Set([...local.chosen, id])]
      : local.chosen.filter((value) => value !== id);
  }

  let counted = $state("COUNT ROWS");
  let countNote = $state("");

  async function count() {
    counted = "COUNTING";
    const result = await call<{ result?: { rows?: number; exact?: boolean; reason?: string } }>(
      "panels/records/actions/count/",
      { body: { model: view.model, search: view.search, ...filterParams() } },
    );
    const payload = result.payload.result || {};
    counted = payload.exact ? `${payload.rows} ROWS` : `ABOUT ${payload.rows} ROWS`;
    countNote = payload.reason || "";
  }
</script>

<PanelHead title="RECORDS" count={body.storage ? body.storage.toUpperCase() : ""}>
  {#snippet toolbar()}
    <div class="field">
      <label class="legend" for="model-select">Model</label>
      <select
        id="model-select"
        value={view.model}
        onchange={(event) => {
          const chosen = event.currentTarget.value;
          relist(() => {
            view.model = chosen;
            view.order = "";
            view.columns = "";
          });
        }}
      >
        {#each models as item (item.label)}
          <option value={item.label}>{item.label} ({item.verbose_name_plural})</option>
        {/each}
      </select>
    </div>

    <div class="field">
      <input
        type="search"
        value={view.search}
        placeholder="SEARCH BY PREFIX"
        aria-label="Search identifying columns by prefix"
        onchange={(event) => {
          const wanted = event.currentTarget.value;
          relist(() => (view.search = wanted));
        }}
      />
    </div>

    <button type="button" aria-expanded={filtersOpen} onclick={() => (filtersOpen = !filtersOpen)}>
      FILTERS{filters.length ? ` ${filters.length}` : ""}
    </button>

    <details class="column-picker">
      <summary>COLUMNS {columns.length}/{availableColumns.length}</summary>
      <div class="column-menu">
        {#each availableColumns as field (field)}
          <label>
            <input
              type="checkbox"
              checked={columns.includes(field)}
              onchange={(event) => toggleColumn(field, event.currentTarget.checked)}
            />
            <span>{field}</span>
          </label>
        {/each}
      </div>
    </details>

    <span class="spacer"></span>

    {#if body.writable}
      <Lamp label="WRITE ENABLED" state="ok" />
    {:else}
      <Lamp label="DOMAIN OWNED" state="attn" />
    {/if}

    <button
      type="button"
      disabled={!body.writable}
      title={body.writable ? "" : `Write it through ${body.write_via}`}
      onclick={() => (view.editing = "new")}
    >
      ADD ROW
    </button>

    {#if local.chosen.length}
      <button type="button" onclick={() => (bulkEditing = true)}>
        EDIT {local.chosen.length} SELECTED
      </button>
      <button
        type="button"
        id="bulk-delete"
        onclick={async () => {
          if (await previewDelete(local.chosen)) reload += 1;
        }}
      >
        DELETE {local.chosen.length} SELECTED
      </button>
    {/if}

    <CopyLinkButton />
    <SaveViewButton />
    <button
      type="button"
      title="Download these rows. The console records the export."
      onclick={() => runExport("csv")}
    >
      EXPORT CSV
    </button>
    <button
      type="button"
      title="Download these rows. The console records the export."
      onclick={() => runExport("json")}
    >
      EXPORT JSON
    </button>
    <button type="button" title={countNote} onclick={count}>{counted}</button>

    <!-- Paging is by cursor, so there is no page number to show and no total to
         count for one. PREVIOUS walks back through the cursors already visited. -->
    <button
      type="button"
      disabled={local.trail.length === 0 && !view.cursor}
      onclick={() => {
        view.cursor = "";
        local.trail = [];
      }}
    >
      FIRST
    </button>
    <button
      type="button"
      disabled={local.trail.length === 0}
      onclick={() => (view.cursor = local.trail.pop() || "")}
    >
      PREVIOUS
    </button>
    <button
      type="button"
      disabled={!body.has_more}
      onclick={() => {
        local.trail.push(view.cursor);
        view.cursor = body.next_cursor || "";
      }}
    >
      NEXT
    </button>
  {/snippet}
</PanelHead>

<div class="panel-body">
  {#if filtersOpen || filters.length}
    <section class="filter-builder" aria-label="Record filters">
      <div class="filter-builder-head">
        <span class="legend">MATCH EVERY CONDITION</span>
        <span class="spacer"></span>
        <button type="button" onclick={addFilter}>ADD CONDITION</button>
        {#if filters.length}<button type="button" onclick={() => storeFilters([])}>CLEAR</button>{/if}
      </div>
      {#each filters as filter, index (`${index}-${filter.field}`)}
        <div class="filter-row">
          <select
            aria-label={`Field for filter ${index + 1}`}
            value={filter.field}
            onchange={(event) => updateFilter(index, { field: event.currentTarget.value })}
          >
            {#each availableColumns as field (field)}<option value={field}>{field}</option>{/each}
          </select>
          <select
            aria-label={`Comparison for filter ${index + 1}`}
            value={filter.lookup}
            onchange={(event) => updateFilter(index, { lookup: event.currentTarget.value })}
          >
            {#each LOOKUPS as option (option[0])}<option value={option[0]}>{option[1]}</option>{/each}
          </select>
          {#if filter.lookup === "isnull"}
            <select
              aria-label={`Empty state for filter ${index + 1}`}
              value={filter.value || "true"}
              onchange={(event) => updateFilter(index, { value: event.currentTarget.value })}
            ><option value="true">YES</option><option value="false">NO</option></select>
          {:else}
            <input
              type="text"
              aria-label={`Value for filter ${index + 1}`}
              value={filter.value}
              placeholder={filter.lookup === "in" ? "VALUE, VALUE, VALUE" : "VALUE"}
              onchange={(event) => updateFilter(index, { value: event.currentTarget.value })}
            />
          {/if}
          <button type="button" aria-label={`Remove filter ${index + 1}`} onclick={() => storeFilters(filters.filter((_item, at) => at !== index))}>REMOVE</button>
        </div>
      {/each}
      {#if !filters.length}<p class="empty-hint">Add conditions to narrow by any field. The address bar preserves the complete query.</p>{/if}
    </section>
  {/if}

  {#if modelsFailed}
    <Empty line="THE MODEL LIST IS NOT AVAILABLE." />
  {:else}
    {#if !body.writable && body.write_via}
      <Empty line="THIS MODEL IS READ ONLY." hint="Change it here: {body.write_via}" />
    {/if}

    {#if view.editing}
      <RecordEditor onDone={() => (reload += 1)} />
    {/if}

    {#if bulkEditing}
      <BulkEditor
        ids={local.chosen}
        onCancel={() => (bulkEditing = false)}
        onDone={() => {
          bulkEditing = false;
          local.chosen = [];
          reload += 1;
        }}
      />
    {/if}

    {#if data.loading}
      <Empty line="LOADING RECORDS…" hint="The model and filters are being read." />
    {:else if data.failed}
      <Empty line="THE ROWS ARE NOT AVAILABLE." />
    {:else if rows.length === 0}
      <Empty
        line="NO ROWS."
        hint={view.search
          ? "No row starts with the search text. Search matches the start of a value, not the middle."
          : "This model has no rows."}
      />
    {:else}
      <DataTable
        label="Records"
        columns={[
          ...(body.writable ? [{ key: "__pick", label: "" }] : []),
          ...columns.map((field) => ({ key: field, label: field })),
          { key: "__open", label: "" },
        ]}
        {rows}
        key={(row) => idOf(row)}
      >
        {#snippet head()}
          <tr>
            {#if body.writable}
              <th scope="col" class="pick">
                <input
                  type="checkbox"
                  aria-label="Select every row on this page"
                  checked={allPicked}
                  onchange={(event) => {
                    local.chosen = event.currentTarget.checked ? rows.map(idOf) : [];
                  }}
                />
              </th>
            {/if}
            {#each columns as field (field)}
              <th scope="col">
                <button type="button" onclick={() => sortBy(field)}>
                  {field}{view.order === field ? " ↑" : view.order === `-${field}` ? " ↓" : ""}
                </button>
              </th>
            {/each}
            <th scope="col"><span class="visually-hidden">Actions</span></th>
          </tr>
        {/snippet}

        {#snippet row(item, absoluteIndex)}
          <tr aria-rowindex={absoluteIndex + 2}>
            {#if body.writable}
              <td class="pick">
                <!-- The row itself opens the editor. Without stopping the click
                     here, selecting a row for deletion opens it instead. -->
                <input
                  type="checkbox"
                  aria-label="Select row {idOf(item)}"
                  checked={local.chosen.includes(idOf(item))}
                  onclick={(event) => event.stopPropagation()}
                  onchange={(event) => pick(idOf(item), event.currentTarget.checked)}
                />
              </td>
            {/if}
            {#each columns as field (field)}
              <Cell value={item[field]} />
            {/each}
            <td><button type="button" onclick={() => (view.editing = idOf(item))}>OPEN</button></td>
          </tr>
        {/snippet}
      </DataTable>
      {#if data.refreshing}<p class="refresh-note" role="status">REFRESHING THIS VIEW…</p>{/if}
    {/if}
  {/if}
</div>

<style>
  .filter-builder { border-block: 1px solid var(--rule); background: var(--panel); }
  .filter-builder-head, .filter-row { display: flex; align-items: center; gap: 8px; padding: 8px 14px; }
  .filter-row { display: grid; grid-template-columns: minmax(130px, .8fr) minmax(170px, 1fr) minmax(180px, 1.4fr) auto; border-top: 1px solid var(--rule-soft); }
  .filter-row select, .filter-row input { width: 100%; min-width: 0; }
  .column-picker { position: relative; }
  .column-picker summary { cursor: pointer; list-style: none; padding: 7px 9px; border: 1px solid var(--rule); color: var(--ink); font: 600 10px/1 var(--mono); letter-spacing: .06em; }
  .column-menu { position: absolute; z-index: 20; inset-block-start: calc(100% + 4px); inset-inline-end: 0; display: grid; grid-template-columns: repeat(2, minmax(150px, 1fr)); max-height: 55vh; min-width: min(480px, 88vw); overflow: auto; padding: 8px; border: 1px solid var(--rule); background: var(--ground); box-shadow: 0 10px 30px rgb(0 0 0 / .45); }
  .column-menu label { display: flex; align-items: center; gap: 7px; min-height: 30px; padding: 3px 6px; font: 11px/1.3 var(--mono); }
  .refresh-note { position: sticky; inset-block-end: 0; margin: 0; padding: 6px 14px; border-top: 1px solid var(--rule); background: var(--panel); color: var(--attn); font: 600 10px/1.2 var(--mono); letter-spacing: .08em; }
  @media (max-width: 620px) { .filter-row { grid-template-columns: 1fr; } .column-menu { grid-template-columns: 1fr; } }
</style>
