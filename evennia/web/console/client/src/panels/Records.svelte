<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import CopyLinkButton from "../components/CopyLinkButton.svelte";
  import SaveViewButton from "../components/SaveViewButton.svelte";
  import RecordEditor from "../components/RecordEditor.svelte";
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
    writable?: boolean;
    write_via?: string;
    storage?: string;
    has_more?: boolean;
    next_cursor?: string;
  }

  const data = new Loader<{ rows?: Body }>();
  let models = $state<Model[]>([]);
  let modelsFailed = $state(false);
  let reload = $state(0);

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
      }),
    );
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);
  const columns = $derived(body.columns ?? []);
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
      { body: { model: view.model, search: view.search } },
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
  {#if modelsFailed}
    <Empty line="THE MODEL LIST IS NOT AVAILABLE." />
  {:else}
    {#if !body.writable && body.write_via}
      <Empty line="THIS MODEL IS READ ONLY." hint="Change it here: {body.write_via}" />
    {/if}

    {#if view.editing}
      <RecordEditor onDone={() => (reload += 1)} />
    {/if}

    {#if data.failed}
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
          </tr>
        {/snippet}

        {#snippet row(item)}
          <tr
            title={body.writable ? "Open this row" : ""}
            onclick={() => {
              if (body.writable) view.editing = idOf(item);
            }}
          >
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
          </tr>
        {/snippet}
      </DataTable>
    {/if}
  {/if}
</div>
