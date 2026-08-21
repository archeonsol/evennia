<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Section from "../components/Section.svelte";
  import Lamp from "../components/Lamp.svelte";
  import DisabledNotice from "../components/DisabledNotice.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { call } from "../lib/api";
  import { report } from "../lib/report";
  import { withPresence } from "../lib/presence";
  import { local } from "../lib/state.svelte";

  interface Payload {
    rows?: {
      enabled?: boolean;
      setting?: string;
      note?: string;
      history?: { outcome: string; source: string }[];
    };
  }

  const data = new Loader<Payload>();
  $effect(() => {
    data.load(rowsPath("repl"));
  });

  const body = $derived(data.value?.rows ?? {});

  let output = $state<string[]>([]);
  let message = $state("");
  let pane: HTMLDivElement | null = $state(null);

  async function run() {
    await withPresence(async () => {
      const done = await call<{ result?: { output?: string[]; message?: string } }>(
        "panels/repl/actions/execute/",
        { body: { source: local.replSource } },
      );
      output = [];
      message = "";
      if (!report(done)) return false;
      const payload = done.payload.result || {};
      output = payload.output || [];
      message = payload.message || "";
      return true;
    });
    if (pane) pane.scrollTop = pane.scrollHeight;
  }
</script>

<PanelHead title="REPL">
  {#snippet toolbar()}
    <Lamp label={body.enabled ? "ENABLED" : "DISABLED"} state={body.enabled ? "attn" : "off"} />
    <span class="legend">{body.note || ""}</span>
  {/snippet}
</PanelHead>

<div class="panel-body">
  {#if !body.enabled}
    <DisabledNotice setting={body.setting} />
  {:else}
    <div class="editor">
      <textarea
        id="repl-source"
        rows="6"
        spellcheck="false"
        aria-label="Python to run"
        placeholder="PYTHON. EVERY SUBMISSION IS RECORDED WITH ITS SOURCE."
        bind:value={local.replSource}
      ></textarea>
      <div class="fault-actions">
        <button type="button" onclick={run}>RUN</button>
      </div>
    </div>

    <Section label="Output" />
    <div class="log-view" id="repl-output" bind:this={pane}>
      {#each output as line, index (index)}
        <div class="log-line"><span class="log-text">{line}</span></div>
      {/each}
      {#if message}
        <div class="log-line">
          <span class="log-text" style="color:var(--fail-text)">{message}</span>
        </div>
      {/if}
    </div>

    {#if (body.history ?? []).length}
      <Section label="Your recent submissions" />
      <div class="log-view">
        {#each body.history ?? [] as item, index (index)}
          <div class="log-line">
            <span class="log-source">{item.outcome}</span>
            <span class="log-text">{item.source}</span>
          </div>
        {/each}
      </div>
    {/if}
  {/if}
</div>
