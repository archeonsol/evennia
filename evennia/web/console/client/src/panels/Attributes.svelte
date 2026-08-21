<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import SearchField from "../components/SearchField.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import AttrCarriers from "../components/AttrCarriers.svelte";
  import AttrDocument from "../components/AttrDocument.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { view } from "../lib/state.svelte";

  /* Attributes inverts the usual lens. Keys are unbounded in this engine -- any
   * object may carry any key -- so a table of objects never helps you find the
   * one key you care about. You browse keys, then reach objects through them.
   * The catalogue is a sample and says so; exact counts come from the index on
   * request, one key at a time. */

  interface Row {
    key: string;
    category: string;
    objects: number;
    kinds: string[];
    example: string;
  }

  interface Payload {
    rows?: {
      rows?: Row[];
      models?: string[];
      model?: string;
      key_count?: number;
      sample?: { complete?: boolean; documents_read?: number; note?: string };
    };
  }

  const data = new Loader<Payload>();
  let reload = $state(0);

  $effect(() => {
    void reload;
    data.load(rowsPath("attributes", { model: view.attrModel, search: view.attrSearch }));
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);
  const sample = $derived(body.sample ?? {});
  const model = $derived(view.attrModel || body.model || "");
</script>

<PanelHead title="ATTRIBUTES" count={body.key_count ? `${body.key_count} KEYS` : ""}>
  {#snippet toolbar()}
    <div class="field">
      <label class="legend" for="attr-model">Model</label>
      <select
        id="attr-model"
        value={model}
        onchange={(event) => (view.attrModel = event.currentTarget.value)}
      >
        {#each body.models ?? [] as label (label)}
          <option value={label}>{label}</option>
        {/each}
      </select>
    </div>
    <SearchField key="attrSearch" label="SEARCH KEYS" />
    <span class="spacer"></span>
    <Lamp
      label={sample.complete ? "WHOLE TABLE" : `SAMPLE OF ${sample.documents_read ?? 0}`}
      state={sample.complete ? "ok" : "attn"}
    />
  {/snippet}
</PanelHead>

<div class="panel-body">
  {#if !sample.complete && sample.note}
    <Empty line="THESE COUNTS ARE NOT EXACT." hint={sample.note} />
  {/if}

  {#if view.attrObject}
    <AttrDocument {model} onChanged={() => (reload += 1)} />
  {:else if view.attrKeyOpen}
    <AttrCarriers {model} attrKey={view.attrKeyOpen} />
  {/if}

  {#if rows.length === 0}
    <Empty
      line="NO ATTRIBUTE KEYS."
      hint={view.attrSearch
        ? "No key matches the search text. Clear the search to show all keys."
        : "The objects that were read carry no attributes."}
    />
  {:else}
    <DataTable
      label="Attribute keys"
      columns={[
        { key: "key", label: "KEY" },
        { key: "category", label: "CATEGORY" },
        { key: "objects", label: "OBJECTS", numeric: true },
        { key: "kinds", label: "TYPE" },
        { key: "example", label: "EXAMPLE" },
      ]}
      {rows}
      key={(row) => `${row.key}:${row.category}`}
    >
      {#snippet row(item)}
        <tr>
          <td>
            <button
              type="button"
              class="linkish"
              title="Show the objects that hold this key."
              onclick={() => {
                view.attrKeyOpen = item.key;
                view.attrObject = "";
              }}
            >
              {item.key}
            </button>
          </td>
          <td class={item.category ? undefined : "null"}>{item.category || "(default)"}</td>
          <td class="num">{item.objects}</td>
          <Cell value={item.kinds.join(", ")} />
          <Cell value={item.example} />
        </tr>
      {/snippet}
    </DataTable>
  {/if}
</div>
