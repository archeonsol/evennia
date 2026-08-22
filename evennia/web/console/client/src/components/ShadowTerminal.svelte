<script lang="ts">
  import { tick } from "svelte";

  import type { WatchEntry } from "../lib/feed.svelte";

  interface Props {
    account: string;
    sessid: number;
    frames: WatchEntry[];
  }

  const { account, sessid, frames }: Props = $props();
  let viewport: HTMLDivElement;
  let followsTail = true;

  function rememberScrollPosition(): void {
    const distanceFromTail = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    followsTail = distanceFromTail < 24;
  }

  async function followNewOutput(): Promise<void> {
    frames.length;
    if (!followsTail) return;
    await tick();
    if (viewport) viewport.scrollTop = viewport.scrollHeight;
  }

  $effect(() => {
    void followNewOutput();
  });
</script>

<section class="shadow-station" aria-label={`Watch of ${account || "anonymous session"}`}>
  <header class="shadow-head">
    <strong>{account || "(anonymous)"}</strong>
    <span>SESSION {sessid} / SHADOW</span>
  </header>
  <div
    class="shadow-terminal"
    bind:this={viewport}
    onscroll={rememberScrollPosition}
    role="log"
    aria-live="polite"
    aria-relevant="additions"
    aria-label={`Shadow terminal for ${account || `session ${sessid}`}`}
  >
    <pre>{#if frames.length === 0}<span class="terminal-waiting">Waiting for session output…</span>{:else}{#each frames as frame}<span
            class="terminal-frame"
            data-kind={frame.kind || (frame.dir === "in" ? "input" : "output")}>{#if frame.kind === "input" || !frame.html}{frame.line}{:else}{@html frame.html}{/if}</span>{#if frame.newline !== false}<br />{/if}{/each}{/if}</pre>
  </div>
</section>

<style>
  .shadow-station {
    min-width: 0;
    border: 1px solid var(--rule);
    background: var(--ground);
  }

  .shadow-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 16px;
    min-width: 0;
    padding: 7px 10px;
    border-bottom: 1px solid var(--rule);
    background: var(--raised);
    font: 500 11px/1.2 var(--mono);
    letter-spacing: 0.09em;
    text-transform: uppercase;
  }

  .shadow-head strong {
    min-width: 0;
    overflow-wrap: anywhere;
    color: var(--ink);
    font: inherit;
  }

  .shadow-head span {
    flex: none;
    color: var(--ink-dim);
  }

  .shadow-terminal {
    min-height: 180px;
    max-height: 48vh;
    overflow: auto;
    overscroll-behavior: contain;
    scrollbar-color: var(--idle) var(--ground);
  }

  pre {
    min-height: 180px;
    margin: 0;
    padding: 12px;
    color: var(--ink);
    font: 400 13px/1.48 var(--mono);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    tab-size: 4;
  }

  .terminal-waiting {
    color: var(--ink-faint);
  }

  @media (max-width: 640px) {
    .shadow-head {
      align-items: flex-start;
      flex-direction: column;
      gap: 4px;
    }

    pre {
      padding: 10px;
      font-size: 12px;
    }
  }
</style>
