<script lang="ts">
  import Section from "./Section.svelte";
  import Empty from "./Empty.svelte";
  import Lamp from "./Lamp.svelte";
  import { live } from "../lib/feed.svelte";

  /* One operator's captured session traffic.
   *
   * Deliberately a plain scrolling transcript rather than a table. What a
   * person needs from this is the shape of a conversation as it happens, and a
   * table's columns break exactly the thing that makes it readable.
   *
   * Both directions are shown unredacted, including what the player types. The
   * lines live in this component's store and nowhere else: they are never
   * posted anywhere, and they are gone when the page closes. */

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

  function stamp(at: number): string {
    return new Date(at * 1000).toLocaleTimeString();
  }
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
  <Section label="Your live transcript" />
  {#if lines.length === 0}
    <Empty
      line="NOTHING HAS PASSED YET."
      hint="Submitted lines appear after the client sends them. Local echo and partially typed text are not visible. Nothing is recorded until the first line arrives."
    />
  {:else}
    <div class="table-scroll" style:max-height="40vh" role="log" aria-label="Live watch transcript">
      <pre class="watch-feed">{#each lines as frame}<span
            class="watch-line"
            data-dir={frame.dir}><span class="legend">{stamp(frame.at)}</span> <span
            class="watch-direction">{frame.dir === "in" ? "INPUT" : "OUTPUT"}</span> {frame.line}
</span>{/each}</pre>
    </div>
  {/if}
{/if}
