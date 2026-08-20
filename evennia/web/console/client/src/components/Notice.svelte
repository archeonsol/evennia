<script lang="ts">
  import { notice } from "../lib/state.svelte";

  /* One alarm line for the whole console.
   *
   * The legend is a separate element from the detail because they come from
   * different places and carry different weight: the legend is the boundary's
   * outcome vocabulary, and the detail is whatever the server chose to say. An
   * operator has to be able to read the first without the second. */
</script>

<div class="notice" role="alert" data-kind={notice.kind} hidden={!notice.shown}>
  <strong class="notice-legend">{notice.legend}</strong>{notice.detail}
</div>
