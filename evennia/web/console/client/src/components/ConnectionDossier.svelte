<script lang="ts">
  import { runAction } from "../lib/load.svelte";
  import type { ConnectionDetail } from "../lib/types";

  interface Props {
    sessionId: number;
    account: string;
    protocol: string;
    onClose: () => void;
  }

  const { sessionId, account, protocol, onClose }: Props = $props();
  let detail = $state<ConnectionDetail | null>(null);
  let failed = $state(false);

  $effect(() => {
    const wanted = sessionId;
    detail = null;
    failed = false;
    runAction<ConnectionDetail>("moderation", "connection", { session_id: wanted }).then(
      (found) => {
        if (wanted !== sessionId) return;
        detail = found;
        failed = found === null;
      },
    );
  });

  function isStructured(value: unknown): boolean {
    return typeof value === "object" && value !== null;
  }

  function display(value: unknown): string {
    if (value === null || value === undefined || value === "") return "NOT RECORDED";
    if (typeof value === "boolean") return value ? "YES" : "NO";
    if (isStructured(value)) return JSON.stringify(value, null, 2);
    return String(value);
  }
</script>

<section class="connection-dossier" aria-label={`Full record for connection ${sessionId}`}>
  <header class="dossier-head">
    <div>
      <strong>CONNECTION #{sessionId}</strong>
      <span>{account || "(anonymous)"} / {protocol || "unknown protocol"}</span>
    </div>
    <span class="audit-note">FULL RECORD / VIEW AUDITED</span>
    <button type="button" onclick={onClose}>CLOSE</button>
  </header>

  {#if failed}
    <p class="dossier-state">THE CONNECTION RECORD COULD NOT BE LOADED. TRY OPENING IT AGAIN.</p>
  {:else if !detail}
    <p class="dossier-state" aria-live="polite">READING THE COMPLETE SESSION RECORD…</p>
  {:else}
    <div class="dossier-groups">
      {#each detail.groups as group (group.label)}
        <section class="field-group" aria-label={group.label}>
          <h3>{group.label}</h3>
          <dl>
            {#each group.fields as field (field.name)}
              <div class:unrecorded={!field.recorded} class:structured={isStructured(field.value)}>
                <dt>
                  <span>{field.label}</span>
                  <code>{field.name}</code>
                  {#if field.sensitive}<em>IDENTIFIER</em>{/if}
                </dt>
                <dd>
                  {#if isStructured(field.value)}
                    <pre>{display(field.value)}</pre>
                  {:else}
                    <code>{display(field.value)}</code>
                  {/if}
                </dd>
              </div>
            {/each}
          </dl>
        </section>
      {/each}
    </div>
  {/if}
</section>

<style>
  .connection-dossier {
    border-top: 1px solid var(--rule);
    background: var(--panel);
  }

  .dossier-head {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto;
    align-items: center;
    gap: 12px;
    padding: 10px 12px;
    border-bottom: 1px solid var(--rule);
    background: var(--raised);
  }

  .dossier-head > div {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
  }

  .dossier-head strong,
  .dossier-head span,
  .audit-note {
    font: 500 11px/1.35 var(--mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .dossier-head strong { color: var(--ink); }
  .dossier-head span { color: var(--ink-dim); }
  .dossier-head .audit-note {
    margin-inline-start: auto;
    color: var(--attn);
  }

  .dossier-state {
    margin: 0;
    padding: 18px 12px;
    color: var(--ink-dim);
    font: 500 11px/1.5 var(--mono);
    letter-spacing: 0.06em;
  }

  .dossier-groups {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .field-group {
    min-width: 0;
    border-inline-end: 1px solid var(--rule);
    border-bottom: 1px solid var(--rule);
  }

  .field-group:nth-child(2n) { border-inline-end: 0; }

  h3 {
    margin: 0;
    padding: 8px 10px;
    border-bottom: 1px solid var(--rule-soft);
    color: var(--ink-dim);
    font: 600 11px/1.2 var(--mono);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  dl { margin: 0; }

  dl > div {
    display: grid;
    grid-template-columns: minmax(138px, 32%) minmax(0, 1fr);
    min-width: 0;
    border-bottom: 1px solid var(--rule-soft);
  }

  dl > div:last-child { border-bottom: 0; }
  dl > div.structured { grid-template-columns: 1fr; }

  dt,
  dd {
    min-width: 0;
    margin: 0;
    padding: 7px 10px;
  }

  dt {
    display: flex;
    flex-wrap: wrap;
    align-content: flex-start;
    gap: 3px 7px;
    border-inline-end: 1px solid var(--rule-soft);
    color: var(--ink-dim);
    font: 500 10px/1.35 var(--mono);
    letter-spacing: 0.07em;
    text-transform: uppercase;
  }

  .structured dt { border-inline-end: 0; border-bottom: 1px solid var(--rule-soft); }
  dt code { color: var(--ink-faint); font: inherit; letter-spacing: 0; text-transform: none; }
  dt em { color: var(--attn); font: inherit; font-style: normal; }

  dd,
  dd code,
  pre {
    color: var(--ink);
    font: 400 11px/1.5 var(--mono);
    overflow-wrap: anywhere;
    white-space: pre-wrap;
    word-break: break-word;
  }

  pre {
    max-height: 260px;
    margin: 0;
    overflow: auto;
    scrollbar-color: var(--idle) var(--ground);
  }

  .unrecorded dd code { color: var(--ink-faint); }

  @media (max-width: 900px) {
    .dossier-groups { grid-template-columns: minmax(0, 1fr); }
    .field-group,
    .field-group:nth-child(2n) { border-inline-end: 0; }
  }

  @media (max-width: 560px) {
    .dossier-head {
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: start;
    }
    .dossier-head .audit-note {
      grid-column: 1 / -1;
      grid-row: 2;
      margin-inline-start: 0;
    }
    dl > div { grid-template-columns: minmax(110px, 38%) minmax(0, 1fr); }
  }
</style>
