<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Section from "../components/Section.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import Picker from "../components/Picker.svelte";
  import SearchField from "../components/SearchField.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { view } from "../lib/state.svelte";

  interface Row {
    name: string;
    event: string;
    phase: string;
    returns: string;
    discipline: string;
  }
  interface Finding {
    name: string;
    problem: string;
  }

  interface Payload {
    rows?: {
      rows?: Row[];
      findings?: Finding[];
      events?: string[];
      available?: boolean;
      reason?: string;
      hook_count?: number;
      note?: string;
    };
  }

  const data = new Loader<Payload>();
  $effect(() => {
    data.load(rowsPath("hooks", { event: view.hookEvent, search: view.hookSearch }));
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);
  const findings = $derived(body.findings ?? []);
</script>

<PanelHead title="HOOKS" count={body.hook_count ? `${body.hook_count} HOOKS` : ""}>
  {#snippet toolbar()}
    <Picker
      id="hook-event"
      label="Event"
      key="hookEvent"
      values={body.events ?? []}
      blank="ALL EVENTS"
    />
    <SearchField key="hookSearch" label="FILTER BY HOOK OR EVENT" />
    <span class="spacer"></span>
    {#if findings.length}
      <Lamp label="{findings.length} FINDINGS" state="fail" />
    {:else}
      <Lamp label="LINT CLEAN" state="ok" />
    {/if}
  {/snippet}
</PanelHead>

<div class="panel-body">
  {#if body.available === false}
    <Empty line="THE HOOK REGISTRY IS NOT LOADED." hint={body.reason || ""} />
  {:else}
    {#if findings.length}
      <Section label="Lint findings" />
      <DataTable
        columns={[
          { key: "name", label: "HOOK" },
          { key: "problem", label: "PROBLEM" },
        ]}
        rows={findings}
        key={(row) => row.name + row.problem}
      >
        {#snippet row(item)}
          <tr>
            <td class="fail-text">{item.name}</td>
            <Cell value={item.problem} />
          </tr>
        {/snippet}
      </DataTable>
    {/if}

    <Section label="Declared" />
    {#if !rows.length}
      <Empty line="NO HOOK MATCHES THIS FILTER." />
    {:else}
      <DataTable
        columns={[
          { key: "name", label: "HOOK" },
          { key: "event", label: "EVENT" },
          { key: "phase", label: "PHASE" },
          { key: "returns", label: "RETURNS" },
          { key: "discipline", label: "DISCIPLINE" },
        ]}
        {rows}
        key={(row) => row.name + row.event}
      >
        {#snippet row(item)}
          <tr>
            <Cell value={item.name} />
            <Cell value={item.event} />
            <Cell value={item.phase} />
            <Cell value={item.returns} />
            <Cell value={item.discipline} />
          </tr>
        {/snippet}
      </DataTable>
    {/if}
    <p class="empty-hint">{body.note || ""}</p>
  {/if}
</div>
