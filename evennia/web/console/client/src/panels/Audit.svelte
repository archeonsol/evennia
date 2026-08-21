<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import SearchField from "../components/SearchField.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import AuditDetail from "../components/AuditDetail.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { view } from "../lib/state.svelte";
  import { OUTCOME_STATE } from "../lib/outcomes";

  interface Row {
    id: number;
    created_at: string;
    actor_name: string;
    panel: string;
    operation: string;
    outcome: string;
    target_ref: string;
  }

  interface Payload {
    rows?: {
      rows?: Row[];
      outcomes?: { value: string; meaning: string }[];
      panels?: string[];
      next_cursor?: string;
      note?: string;
    };
  }

  const data = new Loader<Payload>();
  let reload = $state(0);

  $effect(() => {
    void reload;
    data.load(
      rowsPath("audit", {
        outcome: view.auditOutcome,
        panel: view.auditPanel,
        actor: view.auditActor,
        target: view.auditTarget,
        cursor: view.auditCursor,
      }),
    );
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);

  const stamp = (value: string) => (value || "").replace("T", " ").slice(0, 19);
</script>

<PanelHead title="AUDIT" count={rows.length ? `${rows.length} SHOWN` : ""}>
  {#snippet toolbar()}
    <div class="field">
      <label class="legend" for="audit-outcome">Outcome</label>
      <select
        id="audit-outcome"
        aria-label="Filter by outcome"
        value={view.auditOutcome}
        onchange={(event) => {
          view.auditOutcome = event.currentTarget.value;
          view.auditCursor = "";
        }}
      >
        <option value="">ALL OUTCOMES</option>
        {#each body.outcomes ?? [] as item (item.value)}
          <option value={item.value}>{item.value.toUpperCase()} - {item.meaning}</option>
        {/each}
      </select>
    </div>

    <div class="field">
      <label class="legend" for="audit-panel">Panel</label>
      <select
        id="audit-panel"
        aria-label="Filter by panel"
        value={view.auditPanel}
        onchange={(event) => {
          view.auditPanel = event.currentTarget.value;
          view.auditCursor = "";
        }}
      >
        <option value="">ALL PANELS</option>
        {#each body.panels ?? [] as item (item)}
          <option value={item}>{item}</option>
        {/each}
      </select>
    </div>

    <SearchField key="auditActor" label="OPERATOR NAME OR ID" />
    <SearchField key="auditTarget" label="TARGET PREFIX" />
  {/snippet}
</PanelHead>

<div class="panel-body">
  {#if view.auditOpen}
    <AuditDetail
      id={view.auditOpen}
      onClose={() => (view.auditOpen = "")}
      onUndone={() => {
        view.auditCursor = "";
        reload += 1;
      }}
    />
  {/if}

  {#if !rows.length}
    <Empty line="NO RECORDED OPERATION MATCHES THIS FILTER." />
  {:else}
    <DataTable
      label="Recorded operations"
      columns={[
        { key: "created_at", label: "WHEN" },
        { key: "actor_name", label: "OPERATOR" },
        { key: "operation", label: "OPERATION" },
        { key: "outcome", label: "OUTCOME" },
        { key: "target_ref", label: "TARGET" },
        { key: "act", label: "" },
      ]}
      {rows}
      key={(row) => row.id}
    >
      {#snippet row(item)}
        <tr>
          <Cell value={stamp(item.created_at)} />
          <Cell value={item.actor_name} />
          <Cell value="{item.panel}.{item.operation}" />
          <td>
            <Lamp label={item.outcome.toUpperCase()} state={OUTCOME_STATE[item.outcome] || "off"} />
          </td>
          <Cell value={item.target_ref} />
          <td>
            <button type="button" onclick={() => (view.auditOpen = String(item.id))}>OPEN</button>
          </td>
        </tr>
      {/snippet}
    </DataTable>
  {/if}

  {#if body.next_cursor}
    <div class="fault-actions">
      <button type="button" onclick={() => (view.auditCursor = body.next_cursor || "")}>
        NEXT PAGE
      </button>
    </div>
  {/if}
  <p class="empty-hint">{body.note || ""}</p>
</div>
