<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Section from "../components/Section.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import DocumentSizes from "../components/DocumentSizes.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { bytes } from "../lib/format";

  interface Payload {
    rows?: {
      supported?: boolean;
      reason?: string;
      vendor?: string;
      connections?: {
        by_state?: { state: string; total: number }[];
        headroom?: number;
        max_connections?: number;
      };
      long_running?: { pid: number; seconds: number; state: string; query: string }[];
      tables?: {
        table: string;
        rows: number;
        total_bytes: number;
        heap_bytes: number;
        index_bytes: number;
        dead_rows: number;
        last_autovacuum?: string;
        last_vacuum?: string;
      }[];
      unused_indexes?: {
        rows?: { table: string; index: string; bytes: number }[];
        note?: string;
        stats_reset?: string;
      };
      indexes?: {
        table: string;
        index: string;
        scans: number;
        tuples_read: number;
        bytes: number;
      }[];
      note?: string;
    };
  }

  const data = new Loader<Payload>();
  $effect(() => {
    data.load(rowsPath("database"));
  });

  const body = $derived(data.value?.rows ?? {});
  const connections = $derived(body.connections ?? {});
  const slow = $derived(body.long_running ?? []);
  const tables = $derived(body.tables ?? []);
  const unused = $derived(body.unused_indexes ?? {});
</script>

<PanelHead title="DATABASE">
  {#snippet toolbar()}
    {#if body.supported === false}
      <Lamp label={(body.vendor || "UNKNOWN").toUpperCase()} state="off" />
    {:else}
      <Lamp label={(body.vendor || "").toUpperCase()} state="ok" />
      <Lamp label="{tables.length} TABLES" state="off" />
    {/if}
  {/snippet}
</PanelHead>

<div class="panel-body">
  {#if body.supported === false}
    <Empty line="THIS BACKEND CANNOT ANSWER." hint={body.reason || ""} />
  {:else}
    <Section label="Connections" />
    <dl class="rows">
      {#each connections.by_state ?? [] as row (row.state)}
        <div class="row-pair"><dt>{row.state}</dt><dd class="num">{row.total}</dd></div>
      {/each}
      <div class="row-pair">
        <dt>headroom</dt>
        <dd>
          <Lamp
            label="{connections.headroom} OF {connections.max_connections}"
            state={(connections.headroom ?? 0) > 10 ? "ok" : "fail"}
          />
        </dd>
      </div>
    </dl>

    <Section label="Long-running statements" />
    {#if !slow.length}
      <Empty line="NOTHING IS RUNNING LONG." />
    {:else}
      <DataTable
        label="Long-running statements"
        columns={[
          { key: "pid", label: "PID", numeric: true },
          { key: "seconds", label: "SECONDS", numeric: true },
          { key: "state", label: "STATE" },
          { key: "query", label: "STATEMENT" },
        ]}
        rows={slow}
        key={(row) => row.pid}
      >
        {#snippet row(item)}
          <tr>
            <td class="num">{item.pid}</td>
            <td class="num fail-text">{item.seconds}</td>
            <Cell value={item.state} />
            <Cell value={item.query} />
          </tr>
        {/snippet}
      </DataTable>
    {/if}

    <Section label="Tables" />
    <DataTable
      label="Tables"
      columns={[
        { key: "table", label: "TABLE" },
        { key: "rows", label: "ROWS", numeric: true },
        { key: "total", label: "TOTAL", numeric: true },
        { key: "heap", label: "HEAP", numeric: true },
        { key: "indexes", label: "INDEXES", numeric: true },
        { key: "dead", label: "DEAD ROWS", numeric: true },
        { key: "vacuum", label: "LAST VACUUM" },
      ]}
      rows={tables}
      key={(row) => row.table}
    >
      {#snippet row(item)}
        <tr>
          <Cell value={item.table} />
          <td class="num">{item.rows}</td>
          <td class="num">{bytes(item.total_bytes)}</td>
          <td class="num">{bytes(item.heap_bytes)}</td>
          <td class="num">{bytes(item.index_bytes)}</td>
          <td class="num">{item.dead_rows}</td>
          <Cell value={String(item.last_autovacuum || item.last_vacuum || "never").slice(0, 19)} />
        </tr>
      {/snippet}
    </DataTable>
    <p class="empty-hint">{body.note || ""}</p>

    <Section label="Indexes never scanned" />
    {#if !(unused.rows ?? []).length}
      <Empty line="EVERY INDEX HAS BEEN SCANNED." />
    {:else}
      <DataTable
        label="Indexes never scanned"
        columns={[
          { key: "table", label: "TABLE" },
          { key: "index", label: "INDEX" },
          { key: "bytes", label: "SIZE", numeric: true },
        ]}
        rows={unused.rows ?? []}
        key={(row) => row.index}
      >
        {#snippet row(item)}
          <tr>
            <Cell value={item.table} />
            <td class="fail-text">{item.index}</td>
            <td class="num">{bytes(item.bytes)}</td>
          </tr>
        {/snippet}
      </DataTable>
    {/if}
    <p class="empty-hint">
      {unused.note || ""} Statistics reset: {unused.stats_reset || "unknown"}.
    </p>

    <Section label="Index usage" />
    <DataTable
      label="Index usage"
      columns={[
        { key: "table", label: "TABLE" },
        { key: "index", label: "INDEX" },
        { key: "scans", label: "SCANS", numeric: true },
        { key: "tuples_read", label: "TUPLES READ", numeric: true },
        { key: "bytes", label: "SIZE", numeric: true },
      ]}
      rows={body.indexes ?? []}
      key={(row) => row.index}
    >
      {#snippet row(item)}
        <tr>
          <Cell value={item.table} />
          <Cell value={item.index} />
          <td class="num">{item.scans}</td>
          <td class="num">{item.tuples_read}</td>
          <td class="num">{bytes(item.bytes)}</td>
        </tr>
      {/snippet}
    </DataTable>

    <Section label="Attribute document sizes" />
    <DocumentSizes />
  {/if}
</div>
