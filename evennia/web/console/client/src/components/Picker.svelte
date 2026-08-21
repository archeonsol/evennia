<script lang="ts">
  import { view, type ViewKey } from "../lib/state.svelte";

  interface Props {
    id: string;
    label: string;
    values: (string | number)[];
    key: ViewKey;
    /** What the empty option says. Never blank: an unlabelled empty option
     *  reads as a missing value rather than as "no filter". */
    blank: string;
  }

  const { id, label, values, key, blank }: Props = $props();
</script>

<div class="field">
  <label class="legend" for={id}>{label}</label>
  <select
    {id}
    aria-label={label}
    value={view[key]}
    onchange={(event) => (view[key] = event.currentTarget.value)}
  >
    <option value="">{blank}</option>
    {#each values as value (value)}
      <option value={String(value)}>{String(value)}</option>
    {/each}
  </select>
</div>
