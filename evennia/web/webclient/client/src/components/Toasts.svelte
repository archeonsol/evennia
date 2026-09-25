<script lang="ts">
  import { toasts } from "../lib/toasts.svelte";
</script>

<!-- Not a live region: toasts.push speaks through the announcer, which can
     skip a toast the reader already heard. -->
<div class="toasts" role="group" aria-label="Notifications"
  onpointerenter={() => toasts.hold()} onpointerleave={() => toasts.release()}
  onfocusin={() => toasts.hold()} onfocusout={() => toasts.release()}>
  {#each toasts.list as t (t.id)}
    <button class="toast framed" data-kind={t.kind} onclick={() => { t.open?.(); toasts.dismiss(t.id); }}
      aria-label="{t.title}{t.body ? `: ${t.body}` : ''}. {t.open ? 'Open' : 'Dismiss'}">
      <span class="k">{t.kind}</span>
      <span class="title">{t.title}</span>
      {#if t.body}<span class="body">{t.body}</span>{/if}
    </button>
  {/each}
</div>

<style>
  .toasts {
    position: fixed;
    right: 18px;
    bottom: 18px;
    z-index: 120;
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-width: 22rem;
    pointer-events: none;
  }
  .toast {
    pointer-events: auto;
    display: flex;
    flex-direction: column;
    gap: 2px;
    text-align: left;
    padding: 8px 12px;
    background: var(--bg-elev);
    color: var(--fg);
    border: 1px solid var(--border-bright);
    font-family: var(--font-mono);
    cursor: pointer;
  }
  .k {
    font-size: 0.58rem;
    letter-spacing: 0.24em;
    text-transform: uppercase;
    color: var(--gold);
  }
  .toast[data-kind="announce"] .k { color: var(--alert); }
  .toast[data-kind="kudos"] .k { color: var(--ok); }
  .title {
    color: var(--accent-bright);
    letter-spacing: 0.1em;
    font-size: 0.82rem;
  }
  .body {
    color: var(--fg-dim);
    font-size: 0.78rem;
    line-height: 1.4;
  }
</style>
