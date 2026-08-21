<script lang="ts">
  import type { Snippet } from "svelte";

  interface Props {
    title: string;
    count?: string;
    toolbar?: Snippet;
  }

  const { title, count = "", toolbar }: Props = $props();
</script>

<div class="panel-head">
  <h1 class="panel-title">{title}</h1>
  {#if count}<span class="panel-count">{count}</span>{/if}
</div>

{#if toolbar}
  <div class="toolbar">{@render toolbar()}</div>
{/if}
