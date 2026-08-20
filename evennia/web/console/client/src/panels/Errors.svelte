<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Annunciator from "../components/Annunciator.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import Picker from "../components/Picker.svelte";
  import SearchField from "../components/SearchField.svelte";
  import Fault from "../components/Fault.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { view } from "../lib/state.svelte";
  import type { FaultGroup } from "../lib/types";

  interface Payload {
    rows?: {
      rows?: FaultGroup[];
      states?: string[];
      group_count?: number;
      occurrence_count?: number;
    };
  }

  const data = new Loader<Payload>();
  let reload = $state(0);

  $effect(() => {
    void reload;
    data.load(rowsPath("errors", { state: view.errorState, search: view.errorSearch }));
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);

  /* A fault nobody has judged is the one that wants a person. A muted or
   * acknowledged one has already had the decision it needs. */
  const unjudged = $derived(rows.filter((group) => group.state === "open"));
  const loud = $derived(unjudged.filter((group) => (group.count || 0) >= 100));
</script>

<PanelHead title="ERRORS" count={body.group_count ? `${body.group_count} FAULTS` : ""}>
  {#snippet toolbar()}
    <Picker
      id="error-state"
      label="State"
      key="errorState"
      values={body.states ?? []}
      blank="ALL STATES"
    />
    <SearchField key="errorSearch" label="SEARCH EXCEPTION OR MESSAGE" />
    <span class="spacer"></span>
    <Lamp label="{body.occurrence_count ?? 0} OCCURRENCES" state="off" />
  {/snippet}
</PanelHead>

<div class="panel-body">
  <Annunciator
    alarms={[
      loud.length > 0 && {
        text: `${loud.length} FAULTS OVER 100 OCCURRENCES`,
        state: "fail" as const,
      },
      unjudged.length > 0 && {
        text: `${unjudged.length} FAULTS NOBODY HAS JUDGED`,
        state: "attn" as const,
      },
    ]}
    calm="EVERY FAULT HAS BEEN JUDGED"
  />

  {#if rows.length === 0}
    <Empty
      line="NO FAULTS."
      hint={view.errorSearch || view.errorState
        ? "No fault matches the filter. Clear it to show all faults."
        : "No traceback appears in the recent end of the log files."}
    />
  {:else}
    {#each rows as group (group.signature)}
      <Fault {group} onReviewed={() => (reload += 1)} />
    {/each}
  {/if}
</div>
