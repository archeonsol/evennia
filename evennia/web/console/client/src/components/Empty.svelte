<script lang="ts">
  /* What is shown when there is nothing to show.
   *
   * Two lines, not one. The first says the state; the second says what it means
   * or what to do about it. "No results" alone leaves an operator unable to
   * tell an empty table from a filter they forgot they set. */

  interface Props {
    line: string;
    hint?: string;
  }

  const { line, hint = "" }: Props = $props();
</script>

<div class="empty">
  <p class="empty-line">{line}</p>
  {#if hint}<p class="empty-hint">{hint}</p>{/if}
</div>
