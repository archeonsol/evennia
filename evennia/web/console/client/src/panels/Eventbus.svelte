<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Section from "../components/Section.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import Picker from "../components/Picker.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import { Loader, rowsPath, loadDetail } from "../lib/load.svelte";
  import { view } from "../lib/state.svelte";

  interface Row {
    id: number;
    created_at: string;
    subject: string;
    actor_ref: string;
  }

  interface Record_ {
    subject?: string;
    actor_ref?: string;
    created_at?: string;
    payload?: string;
  }

  interface Payload {
    rows?: {
      rows?: Row[];
      prefixes?: string[];
      subjects?: string[];
      next_before?: string;
      bus?: { backend?: string; enabled?: boolean };
      note?: string;
    };
  }

  const data = new Loader<Payload>();
  let open = $state<Record_ | null>(null);

  $effect(() => {
    data.load(
      rowsPath("eventbus", {
        prefix: view.busPrefix,
        subject: view.busSubject,
        before: view.busBefore,
      }),
    );
  });

  $effect(() => {
    if (!view.busOpen) {
      open = null;
      return;
    }
    loadDetail<Record_>("eventbus", view.busOpen).then((found) => (open = found));
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);
  const bus = $derived(body.bus ?? {});

  const stamp = (value?: string) => (value || "").replace("T", " ").slice(0, 19);
</script>

<PanelHead title="EVENT BUS" count={rows.length ? `${rows.length} SHOWN` : ""}>
  {#snippet toolbar()}
    <Picker
      id="bus-prefix"
      label="Prefix"
      key="busPrefix"
      values={body.prefixes ?? []}
      blank="ALL PREFIXES"
    />
    <Picker
      id="bus-subject"
      label="Subject"
      key="busSubject"
      values={body.subjects ?? []}
      blank="ALL SUBJECTS"
    />
    <span class="spacer"></span>
    <Lamp label={(bus.backend || "NO BACKEND").toUpperCase()} state={bus.enabled ? "ok" : "off"} />
  {/snippet}
</PanelHead>

<div class="panel-body">
  {#if view.busOpen}
    <div class="detail">
      <div class="toolbar">
        <span class="legend">BUS RECORD</span>
        <span class="spacer"></span>
        <button type="button" onclick={() => (view.busOpen = "")}>CLOSE</button>
      </div>
      {#if open}
        <dl class="rows">
          <div class="row-pair"><dt>subject</dt><dd>{open.subject || ""}</dd></div>
          <div class="row-pair"><dt>actor</dt><dd>{open.actor_ref || "--"}</dd></div>
          <div class="row-pair"><dt>when</dt><dd>{stamp(open.created_at)}</dd></div>
        </dl>
        <Section label="Payload" />
        <pre class="code">{open.payload || ""}</pre>
      {/if}
    </div>
  {/if}

  {#if !rows.length}
    <Empty line="NO RECORD MATCHES THIS FILTER." />
  {:else}
    <DataTable
      label="Event bus"
      columns={[
        { key: "created_at", label: "WHEN" },
        { key: "subject", label: "SUBJECT" },
        { key: "actor_ref", label: "ACTOR" },
        { key: "act", label: "" },
      ]}
      {rows}
      key={(row) => row.id}
    >
      {#snippet row(item)}
        <tr>
          <Cell value={stamp(item.created_at)} />
          <Cell value={item.subject} />
          <Cell value={item.actor_ref} />
          <td>
            <button type="button" onclick={() => (view.busOpen = String(item.id))}>OPEN</button>
          </td>
        </tr>
      {/snippet}
    </DataTable>
  {/if}

  {#if body.next_before}
    <div class="fault-actions">
      <button type="button" onclick={() => (view.busBefore = body.next_before || "")}>
        OLDER
      </button>
    </div>
  {/if}
  <p class="empty-hint">{body.note || ""}</p>
</div>
