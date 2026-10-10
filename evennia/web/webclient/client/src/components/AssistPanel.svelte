<script lang="ts">
  // The Assist panel: the caller's own requests (My requests), and for staff the
  // ticket queue (Tickets). Both tabs stay mounted so a draft or a filter
  // survives a switch; both are rebuilt for a new account, so one person's
  // drafts and searches never reach the next.
  import { untrack } from "svelte";
  import { tickets, type AssistTab } from "../lib/tickets.svelte";
  import AssistMine from "./AssistMine.svelte";
  import AssistQueue from "./AssistQueue.svelte";

  const tab = $derived<AssistTab>(tickets.staff ? tickets.assistTab : "mine");

  // News in the queue counts as seen while the queue is on screen.
  $effect(() => {
    if (tickets.queueActive) untrack(() => tickets.markQueueSeen());
  });
</script>

<div class="assist">
  {#if tickets.staff}
    <div class="tabs" role="tablist" aria-label="Assist">
      <button class="sh-toggle" class:off={tab !== "mine"} role="tab" aria-selected={tab === "mine"} aria-controls="assist-mine"
        onclick={() => (tickets.assistTab = "mine")}
        >My requests{#if tickets.myUnread}<span class="sh-count">{tickets.myUnread}</span>{/if}</button>
      <button class="sh-toggle" class:off={tab !== "queue"} role="tab" aria-selected={tab === "queue"} aria-controls="assist-queue"
        onclick={() => (tickets.assistTab = "queue")}
        >Tickets{#if tickets.queueUnseen}<span class="sh-count">{tickets.queueUnseen}</span>{/if}</button>
    </div>
  {/if}
  {#key tickets.accountId}
    <div class="pane" id="assist-mine" role={tickets.staff ? "tabpanel" : undefined} hidden={tab !== "mine"}>
      <AssistMine />
    </div>
    {#if tickets.staff}
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
