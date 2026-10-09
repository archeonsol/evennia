<script lang="ts">
  // The Assist panel: the caller's own tickets (Mine), and for staff the
  // ticket queue (Queue). Both tabs stay mounted so a draft or a filter
  // survives a switch; both are rebuilt for a new account, so one person's
  // drafts and searches never reach the next.
  import { untrack } from "svelte";
  import { chat, type AssistTab } from "../lib/chat.svelte";
  import AssistMine from "./AssistMine.svelte";
  import AssistQueue from "./AssistQueue.svelte";

  const tab = $derived<AssistTab>(chat.staff ? chat.assistTab : "mine");

  // News in the queue counts as seen while the queue is on screen.
  $effect(() => {
    if (chat.queueActive) untrack(() => chat.markQueueSeen());
  });
</script>

<div class="assist">
  {#if chat.staff}
    <div class="tabs" role="tablist" aria-label="Assist">
      <button class="sh-toggle" class:off={tab !== "mine"} role="tab" aria-selected={tab === "mine"} aria-controls="assist-mine"
        onclick={() => (chat.assistTab = "mine")}
        >Mine{#if chat.mineUnseen}<span class="sh-count">{chat.mineUnseen}</span>{/if}</button>
      <button class="sh-toggle" class:off={tab !== "queue"} role="tab" aria-selected={tab === "queue"} aria-controls="assist-queue"
        onclick={() => (chat.assistTab = "queue")}
        >Queue{#if chat.queueUnseen}<span class="sh-count">{chat.queueUnseen}</span>{/if}</button>
    </div>
  {/if}
  {#key chat.account}
    <div class="pane" id="assist-mine" role={chat.staff ? "tabpanel" : undefined} hidden={tab !== "mine"}>
      <AssistMine />
    </div>
    {#if chat.staff}
      <div class="pane" id="assist-queue" role="tabpanel" hidden={tab !== "queue"}>
        <AssistQueue />
      </div>
    {/if}
  {/key}
</div>

<style>
  .assist { display: flex; flex-direction: column; height: 100%; background: var(--bg-elev); }
  .tabs { display: flex; gap: 6px; padding: 5px 10px; border-bottom: 1px solid var(--border); flex: 0 0 auto; }
  .pane { flex: 1; min-height: 0; }
  .pane[hidden] { display: none; }
</style>
