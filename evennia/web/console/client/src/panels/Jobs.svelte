<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Section from "../components/Section.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import Picker from "../components/Picker.svelte";
  import Annunciator from "../components/Annunciator.svelte";
  import CountBlocks from "../components/CountBlocks.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import JobDetail from "../components/JobDetail.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { view } from "../lib/state.svelte";

  interface Row {
    id: number;
    job_type: string;
    status: string;
    attempts: number;
    max_attempts: number;
    created_at: string;
    last_error: string;
  }

  interface Payload {
    rows?: {
      rows?: Row[];
      by_status?: { status: string; total: number }[];
      types?: string[];
      dead?: number;
      overdue_leases?: number;
      backend?: { backend?: string; enabled?: boolean };
      note?: string;
    };
  }

  const data = new Loader<Payload>();
  let reload = $state(0);

  $effect(() => {
    void reload;
    data.load(rowsPath("jobs", { status: view.jobStatus, job_type: view.jobType }));
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);
  const backend = $derived(body.backend ?? {});

  const stamp = (value: string) => (value || "").replace("T", " ").slice(0, 19);
</script>

<PanelHead title="JOBS" count={body.dead ? `${body.dead} DEAD` : ""}>
  {#snippet toolbar()}
    <Picker
      id="job-status"
      label="Status"
      key="jobStatus"
      values={(body.by_status ?? []).map((row) => row.status)}
      blank="ALL STATUSES"
    />
    <Picker id="job-type" label="Type" key="jobType" values={body.types ?? []} blank="ALL TYPES" />
    <span class="spacer"></span>
    <Lamp
      label={(backend.backend || "NO BACKEND").toUpperCase()}
      state={backend.enabled ? "ok" : "off"}
    />
  {/snippet}
</PanelHead>

<div class="panel-body">
  <Annunciator
    alarms={[
      !!body.dead && { text: `${body.dead} DEAD LETTERS`, state: "fail" as const },
      !!body.overdue_leases && {
        text: `${body.overdue_leases} LEASES OUT OF DATE`,
        state: "attn" as const,
      },
    ]}
    calm="THE QUEUE IS DRAINING"
  />

  <!-- Depth by status, as blocks that are also the filter. -->
  <CountBlocks
    items={(body.by_status ?? []).map((row) => ({
      key: row.status,
      label: row.status,
      count: row.total,
      wants: row.status === "dead" ? row.total : 0,
      sub: row.status === "dead" ? "need a person" : "",
    }))}
    current={view.jobStatus}
    onSelect={(key) => {
      view.jobStatus = key;
      view.jobOpen = "";
    }}
  />

  {#if body.overdue_leases}
    <p class="empty-hint">
      A lease that is out of date shows that the worker stopped. The next queue run takes the job
      again. You do not need to do this.
    </p>
  {/if}

  {#if view.jobOpen}
    <JobDetail
      id={view.jobOpen}
      onClose={() => (view.jobOpen = "")}
      onRequeued={() => {
        view.jobOpen = "";
        reload += 1;
      }}
    />
  {/if}

  <Section label="Queue" />
  {#if !rows.length}
    <Empty line="NO JOB MATCHES THIS FILTER." />
  {:else}
    <DataTable
      label="Job queue"
      columns={[
        { key: "job_type", label: "TYPE" },
        { key: "status", label: "STATUS" },
        { key: "attempts", label: "ATTEMPTS", numeric: true },
        { key: "created_at", label: "CREATED" },
        { key: "last_error", label: "LAST ERROR" },
        { key: "act", label: "" },
      ]}
      {rows}
      key={(row) => row.id}
    >
      {#snippet row(item)}
        <tr>
          <Cell value={item.job_type} />
          <td>
            <Lamp
              label={item.status.toUpperCase()}
              state={item.status === "dead" ? "fail" : item.status === "pending" ? "attn" : "ok"}
            />
          </td>
          <td class="num">{item.attempts}/{item.max_attempts}</td>
          <Cell value={stamp(item.created_at)} />
          <Cell value={item.last_error} />
          <td>
            <button type="button" onclick={() => (view.jobOpen = String(item.id))}>OPEN</button>
          </td>
        </tr>
      {/snippet}
    </DataTable>
  {/if}
  <p class="empty-hint">{body.note || ""}</p>
</div>
