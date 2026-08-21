<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Section from "../components/Section.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import SearchField from "../components/SearchField.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { view } from "../lib/state.svelte";

  interface Orphan {
    path: string;
    instances: number;
  }
  interface Stored {
    path: string;
    instances: number;
    importable: boolean;
  }
  interface Loaded {
    name: string;
    path: string;
    base: string;
    instances: number | null;
  }

  interface Payload {
    rows?: {
      orphans?: Orphan[];
      stored?: Stored[];
      rows?: Loaded[];
      typeclass_count?: number;
      note?: string;
      importable_caveat?: string;
    };
  }

  const data = new Loader<Payload>();
  $effect(() => {
    data.load(rowsPath("objects", { search: view.objSearch }));
  });

  const body = $derived(data.value?.rows ?? {});
  const orphans = $derived(body.orphans ?? []);
  const stored = $derived(body.stored ?? []);
  const loaded = $derived(body.rows ?? []);
</script>

<PanelHead title="OBJECTS" count={body.typeclass_count ? `${body.typeclass_count} CLASSES` : ""}>
  {#snippet toolbar()}
    <SearchField key="objSearch" label="FILTER BY CLASS OR PATH" />
    <span class="spacer"></span>
    {#if orphans.length}
      <Lamp label="{orphans.length} DO NOT IMPORT" state="fail" />
    {:else}
      <Lamp label="ALL STORED PATHS IMPORT" state="ok" />
    {/if}
  {/snippet}
</PanelHead>

<div class="panel-body">
  <!-- Orphans first, and only when there are some. A stored path that no longer
       imports is the one finding on this station that needs an operator; the
       rest is reference. -->
  {#if orphans.length}
    <Section label="Paths that do not import" />
    <DataTable
      columns={[
        { key: "path", label: "PATH" },
        { key: "instances", label: "ROWS", numeric: true },
      ]}
      rows={orphans}
      key={(row) => row.path}
    >
      {#snippet row(item)}
        <tr>
          <td class="fail-text">{item.path}</td>
          <Cell value={item.instances} />
        </tr>
      {/snippet}
    </DataTable>
    <p class="empty-hint">
      These rows still load through a fallback. Nothing else reports this.
    </p>
  {/if}

  <Section label="Stored" />
  {#if !stored.length}
    <Empty line="NO ROWS CARRY A TYPECLASS PATH." />
  {:else}
    <DataTable
      columns={[
        { key: "path", label: "PATH" },
        { key: "instances", label: "ROWS", numeric: true },
        { key: "importable", label: "IMPORTS" },
      ]}
      rows={stored}
      key={(row) => row.path}
    >
      {#snippet row(item)}
        <tr>
          <Cell value={item.path} />
          <Cell value={item.instances} />
          <td>
            <Lamp
              label={item.importable ? "LOADED" : "NOT LOADED"}
              state={item.importable ? "ok" : "off"}
            />
          </td>
        </tr>
      {/snippet}
    </DataTable>
  {/if}
  <p class="empty-hint">{body.note || ""}</p>

  <Section label="Loaded classes" />
  {#if !loaded.length}
    <Empty line="NO CLASS MATCHES THIS SEARCH." />
  {:else}
    <DataTable
      columns={[
        { key: "name", label: "CLASS" },
        { key: "path", label: "PATH" },
        { key: "base", label: "PARENT" },
        { key: "instances", label: "ROWS", numeric: true },
      ]}
      rows={loaded}
      key={(row) => row.path + row.name}
    >
      {#snippet row(item)}
        <tr>
          <Cell value={item.name} />
          <Cell value={item.path} />
          <Cell value={item.base} />
          <Cell value={item.instances} />
        </tr>
      {/snippet}
    </DataTable>
  {/if}
  <p class="empty-hint">{body.importable_caveat || ""}</p>
</div>
