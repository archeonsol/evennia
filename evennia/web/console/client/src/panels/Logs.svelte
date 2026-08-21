<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Section from "../components/Section.svelte";
  import Lamp from "../components/Lamp.svelte";
  import SearchField from "../components/SearchField.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { view } from "../lib/state.svelte";
  import { live } from "../lib/feed.svelte";

  interface Line {
    source: string;
    line: string;
  }

  interface Payload {
    rows?: {
      rows?: Line[];
      files?: { label: string; size_bytes: number }[];
      file?: string;
      line_count?: number;
      backups?: unknown[];
    };
  }

  const data = new Loader<Payload>();

  $effect(() => {
    data.load(rowsPath("logs", { file: view.logFile, search: view.logSearch, lines: "200" }));
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);

  let tail: HTMLDivElement | null = $state(null);

  // The live view follows the newest line. The vanilla client rebuilt the whole
  // list on every arriving line to do this; here only the scroll moves.
  $effect(() => {
    void live.log.length;
    if (tail) tail.scrollTop = tail.scrollHeight;
  });
</script>

<PanelHead title="LOGS" count={body.line_count ? `${body.line_count} LINES` : ""}>
  {#snippet toolbar()}
    <div class="field">
      <label class="legend" for="log-file">File</label>
      <select
        id="log-file"
        value={view.logFile || body.file || ""}
        onchange={(event) => (view.logFile = event.currentTarget.value)}
      >
        {#each body.files ?? [] as file (file.label)}
          <option value={file.label}>{file.label} ({file.size_bytes} bytes)</option>
        {/each}
      </select>
    </div>
    <SearchField key="logSearch" label="FILTER BY TEXT OR PATTERN" />
    <span class="spacer"></span>
    <Lamp label="{(body.backups ?? []).length} BACKUPS" state="off" />
  {/snippet}
</PanelHead>

<div class="panel-body">
  <Section label="Recorded" />
  <div class="log-view">
    {#each rows as entry, index (index)}
      <div class="log-line">
        <span class="log-source">{entry.source}</span>
        <span class="log-text">{entry.line}</span>
      </div>
    {/each}
  </div>

  <Section label="Live" />
  <div class="log-view" bind:this={tail}>
    {#each live.log as entry, index (index)}
      <div class="log-line">
        <span class="log-source">{entry.source}</span>
        <span class="log-text">{entry.line}</span>
      </div>
    {/each}
  </div>
</div>
