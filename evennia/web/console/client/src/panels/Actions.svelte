<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Section from "../components/Section.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import SearchField from "../components/SearchField.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import { Loader, rowsPath, runAction } from "../lib/load.svelte";
  import { view } from "../lib/state.svelte";

  interface Row {
    name: string;
    verbs?: string[];
    capability: string;
    summary: string;
  }

  interface Probe {
    matched?: { action: string; module: string; verb: string; score: number | null } | null;
    explanation: string;
    suggestions?: string[];
  }

  interface Payload {
    rows?: {
      rows?: Row[];
      available?: boolean;
      reason?: string;
      action_count?: number;
      verbs?: string[];
      note?: string;
    };
  }

  const data = new Loader<Payload>();
  $effect(() => {
    data.load(rowsPath("actions", { search: view.actionSearch }));
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);

  /* The question an operator has is "why did that not work". Answering it by
   * reading the verb trie by hand is what this replaces. Nothing runs. */
  let text = $state("");
  let probe = $state<Probe | null>(null);

  async function resolve() {
    const wanted = text.trim();
    if (!wanted) return;
    probe = await runAction<Probe>("actions", "resolve", { text: wanted });
  }
</script>

<PanelHead title="ACTIONS" count={body.action_count ? `${body.action_count} ACTIONS` : ""}>
  {#snippet toolbar()}
    <SearchField key="actionSearch" label="FILTER BY ACTION OR VERB" />
    <span class="spacer"></span>
    <Lamp label="{(body.verbs ?? []).length} VERBS" state="off" />
  {/snippet}
</PanelHead>

<div class="panel-body">
  {#if body.available === false}
    <Empty line="THE ACTION REGISTRY IS NOT LOADED." hint={body.reason || ""} />
  {:else}
    <Section label="Resolve one line" />
    <div class="toolbar">
      <div class="field grow">
        <input
          id="action-probe"
          type="text"
          placeholder="A LINE OF PLAYER INPUT, FOR EXAMPLE: DROP SWORD"
          aria-label="Player input to resolve"
          spellcheck="false"
          bind:value={text}
          onkeydown={(event) => {
            if (event.key === "Enter") resolve();
          }}
        />
      </div>
      <button type="button" onclick={resolve}>RESOLVE</button>
    </div>

    {#if probe}
      <div id="action-verdict">
        <p>
          <Lamp label={probe.matched ? "MATCH" : "NO MATCH"} state={probe.matched ? "ok" : "attn"} />
          <span class="legend">&nbsp;{probe.explanation}</span>
        </p>
        {#if probe.matched}
          <dl class="rows">
            <div class="row-pair"><dt>action</dt><dd>{probe.matched.action}</dd></div>
            <div class="row-pair"><dt>module</dt><dd>{probe.matched.module}</dd></div>
            <div class="row-pair"><dt>matched verb</dt><dd>{probe.matched.verb}</dd></div>
            <div class="row-pair">
              <dt>score</dt>
              <dd>{probe.matched.score === null ? "--" : probe.matched.score}</dd>
            </div>
          </dl>
        {:else if (probe.suggestions ?? []).length}
          <p class="empty-hint">Near it: {(probe.suggestions ?? []).join(", ")}</p>
        {/if}
      </div>
    {/if}

    <Section label="Registered" />
    {#if !rows.length}
      <Empty line="NO ACTION MATCHES THIS SEARCH." />
    {:else}
      <DataTable
        columns={[
          { key: "name", label: "ACTION" },
          { key: "verbs", label: "VERBS" },
          { key: "capability", label: "CAPABILITY" },
          { key: "summary", label: "SUMMARY" },
        ]}
        {rows}
        key={(row) => row.name}
      >
        {#snippet row(item)}
          <tr>
            <Cell value={item.name} />
            <Cell value={(item.verbs ?? []).join(" ")} />
            <Cell value={item.capability} />
            <Cell value={item.summary} />
          </tr>
        {/snippet}
      </DataTable>
    {/if}
    <p class="empty-hint">{body.note || ""}</p>
  {/if}
</div>
