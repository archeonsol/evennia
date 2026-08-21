<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Section from "../components/Section.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { live } from "../lib/feed.svelte";

  interface Alarm {
    name: string;
    ok: boolean;
    value: number;
    meaning: string;
  }
  interface Cache {
    name: string;
    hit_rate: number | null;
    invalidation: string;
  }
  interface System {
    name: string;
    cadence: string;
    scope: string;
    workload: string;
    fires: number;
    skips: number;
    last_run: string;
    in_flight: boolean;
  }

  interface Payload {
    rows?: {
      alarms?: Alarm[];
      caches?: Cache[];
      systems?: { available?: boolean; reason?: string; rows?: System[] };
      tasks?: Record<string, number>;
      metrics_available?: boolean;
      reason?: string;
    };
  }

  const data = new Loader<Payload>();
  $effect(() => {
    data.load(rowsPath("runtime"));
  });

  const body = $derived(data.value?.rows ?? {});
  const systems = $derived(body.systems ?? {});
  const tasks = $derived(body.tasks ?? {});

  /* Task roots. The unmanaged-access counter is an alarm and is already above;
   * these three are the supervision picture behind it. */
  const TASK_ROWS: [string, string][] = [
    ["active task roots", "active"],
    ["task roots started", "started"],
    ["database scope closes", "db_scope_closes"],
  ];
</script>

<PanelHead title="RUNTIME">
  {#snippet toolbar()}
    <Lamp
      label={body.metrics_available ? "METRICS LIVE" : "NO METRICS"}
      state={body.metrics_available ? "ok" : "attn"}
    />
    {#if body.reason}<span class="legend">{body.reason}</span>{/if}
  {/snippet}
</PanelHead>

<div class="panel-body">
  <Section label="Alarms" />
  <dl class="rows">
    {#each body.alarms ?? [] as alarm (alarm.name)}
      <div class="row-pair">
        <dt>{alarm.name.replace(/^evennia_/, "").replace(/_/g, " ")}</dt>
        <dd>
          <Lamp label={alarm.ok ? "ZERO" : String(alarm.value)} state={alarm.ok ? "ok" : "fail"} />
          <span class="empty-hint">&nbsp;{alarm.meaning}</span>
        </dd>
      </div>
    {/each}
  </dl>

  <Section label="Caches" />
  <dl class="rows">
    {#each body.caches ?? [] as cache (cache.name)}
      <div class="row-pair">
        <dt>{cache.name}</dt>
        <dd>
          <span>
            {cache.hit_rate === null ? "not used yet" : `${(cache.hit_rate * 100).toFixed(1)}% hit`}
          </span>
          <span class="empty-hint">&nbsp;{cache.invalidation}</span>
        </dd>
      </div>
    {/each}
  </dl>

  <!-- The scheduler, which is "@systems as a page". A system that skips is one
       whose run outlasts its own cadence, and no other number on this station
       shows that. -->
  <Section label="Scheduled systems" />
  {#if systems.available === false}
    <Empty line="THE SCHEDULER CANNOT BE READ." hint={systems.reason || ""} />
  {:else if !(systems.rows ?? []).length}
    <Empty line="NO SYSTEM IS REGISTERED." hint={systems.reason || ""} />
  {:else}
    <DataTable
      label="Scheduled systems"
      columns={[
        { key: "name", label: "SYSTEM" },
        { key: "cadence", label: "CADENCE" },
        { key: "scope", label: "SCOPE" },
        { key: "workload", label: "WORKLOAD" },
        { key: "fires", label: "FIRES", numeric: true },
        { key: "skips", label: "SKIPS", numeric: true },
        { key: "last_run", label: "LAST RUN" },
      ]}
      rows={systems.rows ?? []}
      key={(row) => row.name}
    >
      {#snippet row(item)}
        <tr>
          <Cell value={item.name} />
          <Cell value={item.cadence} />
          <Cell value={item.scope} />
          <Cell value={item.workload} />
          <Cell value={item.fires} />
          <td class={item.skips ? "num fail-text" : "num"}>{item.skips}</td>
          <td>
            {#if item.in_flight}<Lamp label="RUNNING" state="attn" />{/if}
            <span>{item.last_run || "--"}</span>
          </td>
        </tr>
      {/snippet}
    </DataTable>
  {/if}

  <Section label="Supervised tasks" />
  <dl class="rows">
    {#each TASK_ROWS as [label, key] (key)}
      <div class="row-pair">
        <dt>{label}</dt>
        <dd class="num">{tasks[key] ?? 0}</dd>
      </div>
    {/each}
  </dl>

  <Section label="Metrics" />
  <div id="live-metrics">
    {#if !live.metrics}
      <p class="empty-hint">Waiting for the first sample from the live feed.</p>
    {:else if !live.metrics.available}
      <p class="empty-hint">{live.metrics.reason || ""}</p>
    {:else}
      <DataTable
        label="Metrics"
        columns={[
          { key: "name", label: "METRIC" },
          { key: "value", label: "VALUE", numeric: true },
        ]}
        rows={(live.metrics.samples ?? []).slice(0, 200)}
        key={(row) => row.name}
      >
        {#snippet row(item)}
          <tr>
            <Cell value={item.name} />
            <td class="num">{item.value}</td>
          </tr>
        {/snippet}
      </DataTable>
    {/if}
  </div>
</div>
