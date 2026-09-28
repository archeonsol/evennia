<script lang="ts">
  // Embedded web page (help, map, staff pages, wiki). URL comes from panel params.
  let { url = "", title = "Web page" }: { url?: string; title?: string } = $props();

  // A window of its own is the better home for a full page app like the grid:
  // real size, its own history, and a screen reader treats it as a document
  // instead of a frame inside a panel. Opening from a click is never blocked.
  function popOut() {
    window.open(url, "_blank");
  }
</script>

<div class="frame">
  {#if url}
    <div class="bar">
      <span class="name">{title}</span>
      <button class="sh-cmd pop" onclick={popOut}>New window</button>
    </div>
    <iframe src={url} {title} referrerpolicy="no-referrer"></iframe>
  {:else}
    <p class="empty">No page.</p>
  {/if}
</div>

<style>
  .frame { height: 100%; width: 100%; background: var(--bg); display: flex; flex-direction: column; }
  .bar {
    display: flex; align-items: center; gap: 1ch; flex: 0 0 auto;
    padding: 3px 8px; border-bottom: 1px solid var(--border); background: var(--bg-elev);
  }
  .name {
    flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    color: var(--fg-dim); font-size: 0.7rem; letter-spacing: 0.12em; text-transform: uppercase;
  }
  .pop { flex: none; }
  iframe { flex: 1; width: 100%; min-height: 0; border: none; background: #fff; }
  .empty { color: var(--fg-faint); padding: 1rem; margin: 0; font-size: 0.64rem; letter-spacing: 0.14em; text-transform: uppercase; }
</style>
