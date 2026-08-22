<script lang="ts">
  import Section from "./Section.svelte";
  import Lamp from "./Lamp.svelte";
  import ShadowTerminal from "./ShadowTerminal.svelte";
  import { live } from "../lib/feed.svelte";

  /* One operator's player-visible shadow terminals.
   *
   * Each watch is isolated in its own terminal so simultaneous sessions never
   * interleave. The server has already removed OOB traffic and rendered safe
   * colour HTML; this component only presents the stream in order.
   *
   * Submitted input appears after Enter while local echo is enabled. Secret
   * input, partially typed text, client triggers, and local UI never reach the
   * server and cannot appear here. Frames live in the feed store and nowhere
   * else: they are never posted anywhere and disappear with the page. */

  interface Props {
    /** Every running watch. Only rows marked mine may select feed frames. */
    watches: {
      watch_id: string;
      sessid: number;
      account: string;
      watcher: string;
      reason: string;
      seconds_left: number;
      mine: boolean;
    }[];
  }

  const { watches }: Props = $props();

  const mine = $derived(watches.filter((entry) => entry.mine));
  const mineIds = $derived(new Set(mine.map((entry) => entry.watch_id)));
  const lines = $derived(live.watch.filter((frame) => mineIds.has(frame.watch_id)));
</script>

{#if watches.length}
  <Section label="Active watches" />
  <ul class="watch-statuses" aria-label="Active session watches">
    {#each watches as entry (entry.watch_id)}
      <li class="watch-status">
        <Lamp
          label={`${entry.watcher || "(unknown staff)"} / ${entry.account || "(anonymous)"} / ${entry.seconds_left}s LEFT${entry.mine ? " / YOU" : ""}`}
          state="attn"
          title="This is recorded permanently once it delivers anything."
        />
        <span class="watch-reason">{entry.reason || "No reason supplied."}</span>
      </li>
    {/each}
  </ul>
{/if}

{#if mine.length}
  <Section label="Your shadow terminals" />
  <div class="shadow-grid">
    {#each mine as entry (entry.watch_id)}
      <ShadowTerminal
        account={entry.account}
        sessid={entry.sessid}
        frames={lines.filter((frame) => frame.watch_id === entry.watch_id)}
      />
    {/each}
  </div>
{/if}

<style>
  .shadow-grid {
    display: grid;
    gap: 12px;
  }
</style>
