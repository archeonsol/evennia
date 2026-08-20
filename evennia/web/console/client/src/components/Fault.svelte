<script lang="ts">
  import Lamp from "./Lamp.svelte";
  import { view } from "../lib/state.svelte";
  import { runAction } from "../lib/load.svelte";
  import type { FaultGroup } from "../lib/types";

  /* One fault, collapsed.
   *
   * The count is the point: an operator needs to know a thing is happening
   * constantly before they need its stack. */

  interface Props {
    group: FaultGroup;
    onReviewed: () => void;
  }

  const { group, onReviewed }: Props = $props();

  const open = $derived(view.errorOpen === group.signature);
  let note = $state(group.note ?? "");

  const lampState = $derived(
    group.state === "muted" ? "off" : group.state === "acknowledged" ? "attn" : "fail",
  );
  const lampLabel = $derived(
    group.state === "muted" ? "MUTED" : group.state === "acknowledged" ? "SEEN" : "OPEN",
  );

  async function review(wanted: string) {
    const done = await runAction("errors", "review", {
      signature: group.signature,
      state: wanted,
      note,
    });
    if (done !== null) onReviewed();
  }
</script>

<div class="fault">
  <button
    class="fault-head"
    type="button"
    aria-expanded={open}
    onclick={() => (view.errorOpen = open ? "" : group.signature)}
  >
    <Lamp label={lampLabel} state={lampState} />
    <span class="fault-name">{group.exception}</span>
    <span class="fault-message">{group.message}</span>
    <span class="fault-count">x{group.count}</span>
    <span class="fault-when">{group.last_seen}</span>
  </button>

  {#if open}
    <div class="fault-frames">
      {#each group.frames ?? [] as frame, index (index)}
        <div class="log-line">
          <span class="log-source">{frame.line}</span>
          <span class="log-text">{frame.function}&nbsp;&nbsp;{frame.file}</span>
        </div>
      {/each}
    </div>

    <div class="fault-actions">
      <input
        type="text"
        placeholder="WHY, FOR WHOEVER READS THIS NEXT"
        aria-label="Review note"
        bind:value={note}
      />
      <button type="button" onclick={() => review("acknowledged")}>ACKNOWLEDGE</button>
      <button
        type="button"
        title="Hide until this fault's signature changes"
        onclick={() => review("muted")}
      >
        MUTE
      </button>
      <button type="button" onclick={() => review("open")}>REOPEN</button>
    </div>

    {#if group.reviewed_by}
      <p class="empty-hint">Last reviewed by {group.reviewed_by}</p>
    {/if}
  {/if}
</div>
