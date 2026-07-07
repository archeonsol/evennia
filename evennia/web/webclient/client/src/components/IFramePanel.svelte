<script lang="ts">
  // Embedded web page (help, map, staff pages, wiki). URL comes from panel params.
  let { url = "" }: { url?: string } = $props();
</script>

<div class="frame">
  {#if url}
    <iframe src={url} title="embedded page" referrerpolicy="no-referrer"></iframe>
  {:else}
    <p class="empty">No page.</p>
  {/if}
</div>

<style>
  .frame { height: 100%; width: 100%; background: var(--bg); }
  iframe { width: 100%; height: 100%; border: none; background: #fff; }
  .empty { color: var(--fg-faint); font-style: italic; padding: 1rem; }
</style>
