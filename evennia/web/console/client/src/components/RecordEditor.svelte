<script lang="ts">
  import { call } from "../lib/api";
  import { report } from "../lib/report";
  import { view, local } from "../lib/state.svelte";

  /* A form built from what the service will accept, not from the model's
   * columns.
   *
   * A field the mutation adapter does not declare cannot be written, and
   * offering it would produce a rejection after the operator has already filled
   * it in.
   *
   * The editor is a region of the station, not a modal. Nothing here needs
   * protected focus, and an operator comparing a value against the row above
   * should not have the table hidden behind a sheet. */

  interface Field {
    name: string;
    types: string[];
  }

  interface Props {
    onDone: () => void;
  }

  const { onDone }: Props = $props();

  const creating = $derived(view.editing === "new");

  let fields = $state<Field[]>([]);
  let note = $state("");
  let values = $state<Record<string, string>>({});
  let status = $state("");
  let ready = $state(false);

  $effect(() => {
    load(view.editing);
  });

  async function load(editing: string) {
    ready = false;
    status = "";

    const shape = await call<{ result?: { fields?: Field[]; note?: string } }>(
      "panels/records/actions/form/",
      { body: { model: view.model } },
    );
    if (!report(shape)) return;
    const spec = shape.payload.result || {};
    fields = spec.fields || [];
    note = spec.note || "";

    const next: Record<string, string> = {};
    if (editing !== "new") {
      const detail = await call<{ record?: Record<string, unknown> }>(
        `panels/records/detail/${encodeURIComponent(editing)}/?model=${encodeURIComponent(view.model)}`,
      );
      if (!report(detail)) return;
      const record = (detail.payload.record?.record ??
        detail.payload.record ??
        {}) as Record<string, unknown>;
      for (const field of fields) {
        const value = record[field.name];
        next[field.name] = value === null || value === undefined ? "" : String(value);
      }
    } else {
      for (const field of fields) next[field.name] = "";
    }
    values = next;
    ready = true;
  }

  async function save() {
    const result = await call<{ detail?: string; field?: string }>(
      "panels/records/actions/save/",
      {
        body: {
          model: view.model,
          pk: creating ? null : view.editing,
          values: { ...values },
        },
      },
    );
    if (!result.ok) {
      report(result);
      status = String(result.payload.detail || "THE ROW WAS NOT SAVED.");
      // The server names the field it refused. Putting the cursor there saves
      // the operator re-reading a form they have already filled in.
      const named = result.payload.field;
      if (named) document.getElementById(`edit-${named}`)?.focus();
      return;
    }
    view.editing = "";
    view.cursor = "";
    local.trail = [];
    onDone();
  }
</script>

<div class="editor">
  <div class="editor-head">
    <span class="legend">{creating ? "NEW ROW" : `ROW ${view.editing}`}</span>
    <span class="spacer"></span>
    <button type="button" disabled={!ready} onclick={save}>
      {creating ? "CREATE ROW" : "SAVE CHANGES"}
    </button>
    <button type="button" onclick={() => (view.editing = "")}>CANCEL</button>
  </div>

  <div class="editor-grid">
    {#each fields as field (field.name)}
      <div class="editor-field">
        <label class="legend" for="edit-{field.name}">{field.name}</label>
        <input
          id="edit-{field.name}"
          type="text"
          placeholder={field.types.join(" or ")}
          bind:value={values[field.name]}
        />
      </div>
    {/each}
  </div>

  {#if status}<p class="empty-hint">{status}</p>{/if}
  {#if note}<p class="empty-hint">{note}</p>{/if}
</div>
