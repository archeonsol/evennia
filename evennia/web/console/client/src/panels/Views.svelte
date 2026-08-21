<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Section from "../components/Section.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import Picker from "../components/Picker.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import { Loader, rowsPath, runAction } from "../lib/load.svelte";
  import { view } from "../lib/state.svelte";

  interface Row {
    id: number;
    name: string;
    panel: string;
    description: string;
    created_by_name: string;
    pinned: boolean;
    url: string;
  }

  interface Present {
    actor_name: string;
    panel: string;
    record: string;
  }

  interface Payload {
    rows?: {
      rows?: Row[];
      present?: Present[];
      note?: string;
      presence_note?: string;
    };
  }

  const data = new Loader<Payload>();
  let reload = $state(0);

  $effect(() => {
    void reload;
    data.load(rowsPath("views", { panel: view.viewPanel }));
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);
  const present = $derived(body.present ?? []);
  const panels = $derived([...new Set(rows.map((row) => row.panel))]);

  async function forget(id: number) {
    if ((await runAction("views", "forget", { view_id: id })) !== null) reload += 1;
  }
</script>

<PanelHead title="SAVED VIEWS" count={rows.length ? `${rows.length} SAVED` : ""}>
  {#snippet toolbar()}
    <Picker
      id="view-panel"
      label="Panel"
      key="viewPanel"
      values={panels}
      blank="ALL PANELS"
    />
  {/snippet}
</PanelHead>

<div class="panel-body">
  <Section label="Saved views" />
  {#if !rows.length}
    <Empty
      line="NO SAVED VIEWS."
      hint="Open a panel, set the filters you want, then select SAVE THIS VIEW."
    />
  {:else}
    <DataTable
      label="Saved views"
      columns={[
        { key: "name", label: "NAME" },
        { key: "panel", label: "PANEL" },
        { key: "description", label: "DESCRIPTION" },
        { key: "created_by_name", label: "SAVED BY" },
        { key: "pinned", label: "PINNED" },
        { key: "act", label: "" },
      ]}
      {rows}
      key={(row) => row.id}
    >
      {#snippet row(item)}
        <tr>
          <td>
            <button
              type="button"
              class="linkish"
              onclick={() => (location.hash = item.url.slice(1))}
            >
              {item.name}
            </button>
          </td>
          <Cell value={item.panel} />
          <Cell value={item.description} />
          <Cell value={item.created_by_name} />
          <td>{#if item.pinned}<Lamp label="PINNED" state="ok" />{/if}</td>
          <td><button type="button" onclick={() => forget(item.id)}>FORGET</button></td>
        </tr>
      {/snippet}
    </DataTable>
  {/if}
  <p class="empty-hint">{body.note || ""}</p>

  <Section label="Operators here now" />
  {#if !present.length}
    <Empty line="NOBODY ELSE HAS THE CONSOLE OPEN." />
  {:else}
    <DataTable
      label="Operators present"
      columns={[
        { key: "actor_name", label: "OPERATOR" },
        { key: "panel", label: "PANEL" },
        { key: "record", label: "RECORD" },
      ]}
      rows={present}
      key={(row, index) => `${row.actor_name}:${index}`}
    >
      {#snippet row(item)}
        <tr>
          <Cell value={item.actor_name} />
          <Cell value={item.panel} />
          <Cell value={item.record} />
        </tr>
      {/snippet}
    </DataTable>
  {/if}
  <p class="empty-hint">{body.presence_note || ""}</p>
</div>
