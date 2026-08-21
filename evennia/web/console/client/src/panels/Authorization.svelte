<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Section from "../components/Section.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import { Loader, rowsPath, runAction } from "../lib/load.svelte";
  import { view } from "../lib/state.svelte";

  type Row = Record<string, unknown>;

  interface Capability {
    key: string;
  }

  interface Verdict {
    allowed?: boolean;
    explanation?: string;
    matching_grants?: { scope_kind: string; scope_key: string; origin: string }[];
  }

  interface Payload {
    rows?: {
      rows?: Row[];
      fields?: string[];
      views?: string[];
      view?: string;
      model?: string;
      row_count?: number;
      capabilities?: Capability[];
    };
  }

  const data = new Loader<Payload>();
  $effect(() => {
    data.load(rowsPath("authorization", { view: view.authView || "grants" }));
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);
  /* Seven columns, not all of them. These tables are wide enough that showing
   * everything makes the first column unreadable, which is the one that names
   * the row. */
  const shown = $derived((body.fields ?? []).slice(0, 7));

  let principal = $state("");
  let capability = $state("");
  let verdict = $state<Verdict | null>(null);

  async function ask() {
    verdict = await runAction<Verdict>("authorization", "probe", {
      principal_id: Number(principal),
      capability,
    });
  }
</script>

<PanelHead title="AUTHORIZATION" count={body.row_count ? `${body.row_count} ROWS` : ""}>
  {#snippet toolbar()}
    <div class="field">
      <label class="legend" for="auth-view">View</label>
      <select
        id="auth-view"
        value={view.authView || body.view || "grants"}
        onchange={(event) => (view.authView = event.currentTarget.value)}
      >
        {#each body.views ?? [] as option (option)}
          <option value={option}>{option.toUpperCase()}</option>
        {/each}
      </select>
    </div>
    <span class="spacer"></span>
    <span class="legend">{(body.capabilities ?? []).length} CAPABILITIES</span>
  {/snippet}
</PanelHead>

<div class="panel-body">
  <!-- Ask the resolver rather than reading grant rows and simulating it in your
       head. The verdict is one lamp; the sentence beside it is the point. -->
  <div class="editor">
    <div class="editor-head">
      <span class="legend">WHY CAN THIS ACCOUNT DO THAT</span>
    </div>
    <div class="fault-actions">
      <input type="text" placeholder="ACCOUNT ID" aria-label="Account id" bind:value={principal} />
      <input
        type="text"
        placeholder="CAPABILITY"
        aria-label="Capability"
        list="capability-list"
        bind:value={capability}
      />
      <datalist id="capability-list">
        {#each body.capabilities ?? [] as item (item.key)}
          <option value={item.key}></option>
        {/each}
      </datalist>
      <button type="button" onclick={ask}>ASK</button>
    </div>

    {#if verdict}
      <div class="probe-verdict">
        <Lamp
          label={verdict.allowed ? "ALLOWED" : "DENIED"}
          state={verdict.allowed ? "ok" : "fail"}
        />
        <span class="probe-text">{verdict.explanation || ""}</span>
        {#each verdict.matching_grants ?? [] as grant, index (index)}
          <div class="log-line">
            <span class="log-source">{grant.scope_kind}</span>
            <span class="log-text">{grant.scope_key}&nbsp;&nbsp;from {grant.origin}</span>
          </div>
        {/each}
      </div>
    {/if}
  </div>

  <Section label={body.model || ""} />
  {#if rows.length === 0}
    <Empty line="NO ROWS." />
  {:else}
    <DataTable
      label={body.model || "Authorization"}
      columns={shown.map((field) => ({ key: field, label: field }))}
      {rows}
      key={(row, index) => String(row[shown[0]] ?? index)}
    >
      {#snippet row(item)}
        <tr>
          {#each shown as field (field)}
            <Cell value={item[field]} />
          {/each}
        </tr>
      {/snippet}
    </DataTable>
  {/if}
</div>
