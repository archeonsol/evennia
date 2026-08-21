<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import DisabledNotice from "../components/DisabledNotice.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { call } from "../lib/api";
  import { report } from "../lib/report";
  import { withPresence } from "../lib/presence";
  import { local } from "../lib/state.svelte";

  interface Payload {
    rows?: {
      enabled?: boolean;
      setting?: string;
      allowed?: string[];
      timeout_ms?: number;
      max_rows?: number;
    };
  }

  interface QueryResult {
    columns?: string[];
    rows?: unknown[][];
    capped?: boolean;
    message?: string;
  }

  const data = new Loader<Payload>();
  $effect(() => {
    data.load(rowsPath("sql"));
  });

  const body = $derived(data.value?.rows ?? {});
  let answer = $state<QueryResult | null>(null);

  async function run() {
    await withPresence(async () => {
      const done = await call<{ result?: QueryResult }>("panels/sql/actions/query/", {
        body: { sql: local.sqlText },
      });
      answer = null;
      if (!report(done)) return false;
      answer = done.payload.result || {};
      return true;
    });
  }
</script>

<PanelHead title="SQL">
  {#snippet toolbar()}
    <Lamp label={body.enabled ? "ENABLED" : "DISABLED"} state={body.enabled ? "attn" : "off"} />
    <span class="legend">
      {body.enabled ? `${body.timeout_ms}MS TIMEOUT, ${body.max_rows} ROW CAP` : ""}
    </span>
  {/snippet}
</PanelHead>

<div class="panel-body">
  {#if !body.enabled}
    <DisabledNotice setting={body.setting} />
  {:else}
    <div class="editor">
      <textarea
        id="sql-text"
        rows="4"
        spellcheck="false"
        aria-label="Read-only SQL"
        placeholder="READ-ONLY SQL. ALLOWED: {(body.allowed ?? []).join(', ').toUpperCase()}"
        bind:value={local.sqlText}
      ></textarea>
      <div class="fault-actions">
        <button type="button" onclick={run}>RUN</button>
      </div>
    </div>

    <div id="sql-results">
      {#if answer?.message}
        <Empty line="THE QUERY DID NOT RUN." hint={answer.message} />
      {:else if answer}
        <DataTable
          label="Query result"
          columns={(answer.columns ?? []).map((name) => ({ key: name, label: name }))}
          rows={answer.rows ?? []}
        >
          {#snippet row(item)}
            <tr>
              {#each item as value, index (index)}
                <Cell {value} />
              {/each}
            </tr>
          {/snippet}
        </DataTable>
        {#if answer.capped}
          <p class="empty-hint">
            Capped at {body.max_rows} rows. Narrow the query to see more.
          </p>
        {/if}
      {/if}
    </div>
  {/if}
</div>
