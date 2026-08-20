<script lang="ts">
  import Lamp from "./Lamp.svelte";

  /* The row of alarms at the top of a panel.
   *
   * When nothing is lit it says so, rather than rendering nothing. An empty
   * space cannot be distinguished from a panel that has not finished loading,
   * and "nothing needs a person" is the reading an operator actually wants. */

  export interface Alarm {
    text: string;
    state: "ok" | "attn" | "fail";
  }

  interface Props {
    alarms: (Alarm | false | 0 | null | undefined)[];
    calm?: string;
  }

  const { alarms, calm = "NOTHING NEEDS A PERSON" }: Props = $props();
  const lit = $derived(alarms.filter(Boolean) as Alarm[]);
</script>

<div class="annunciator">
  {#if lit.length === 0}
    <Lamp label={calm} state="ok" />
  {:else}
    {#each lit as alarm (alarm.text)}
      <Lamp label={alarm.text} state={alarm.state} />
    {/each}
  {/if}
</div>
