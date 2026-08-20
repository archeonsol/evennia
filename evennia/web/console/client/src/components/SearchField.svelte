<script lang="ts">
  import { view, type ViewKey } from "../lib/state.svelte";

  /* One search box, bound to one view key.
   *
   * Four stations wanted this control and the vanilla client had four
   * hand-rolled copies of it, which is how three of them ended up filtering on
   * `change` and one on `input`. */

  interface Props {
    key: ViewKey;
    label: string;
  }

  const { key, label }: Props = $props();
</script>

<div class="field">
  <input
    type="search"
    value={view[key]}
    placeholder={label}
    aria-label={label}
    onchange={(event) => (view[key] = event.currentTarget.value)}
  />
</div>
