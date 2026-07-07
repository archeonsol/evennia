<script lang="ts">
  import { connection } from "../lib/evennia.svelte";
  import { scene } from "../lib/scene.svelte";
  import { dock, VIEWS } from "../lib/dock.svelte";
  import { chat } from "../lib/chat.svelte";

  let { onsettings }: { onsettings: () => void } = $props();

  const labels: Record<string, string> = {
    connecting: "linking",
    open: "online",
    closed: "severed",
    error: "fault",
  };

  let viewsOpen = $state(false);
  // Panels the player can reopen (Tickets only for staff).
  const viewIds = $derived(
    Object.keys(VIEWS).filter((id) => id !== "tickets" || chat.staff),
  );
  function pick(id: string) {
    dock.openView(id);
    viewsOpen = false;
  }
  function openSite() {
    dock.openIframe("view:site", "Web", location.origin);
    viewsOpen = false;
  }
  function reset() {
    viewsOpen = false;
    dock.resetLayout();
  }
  let presets = $state<string[]>([]);
  function refreshPresets() {
    presets = dock.listLayouts();
  }
  function loadPreset(name: string) {
    dock.loadLayout(name);
    viewsOpen = false;
  }
  function savePreset() {
    const name = prompt("Save current layout as:");
    if (name) {
      dock.saveLayout(name);
      refreshPresets();
    }
  }
  function delPreset(name: string, e: MouseEvent) {
    e.stopPropagation();
    dock.deleteLayout(name);
    refreshPresets();
  }
  $effect(() => {
    if (viewsOpen) refreshPresets();
  });
</script>

<header class="hud" data-state={connection.state}>
  <div class="zone left">
    <span class="dot" aria-hidden="true"></span>
    <span class="conn">{labels[connection.state] ?? connection.state}</span>
  </div>

  <div class="zone center">
    {#if scene.present && scene.room.name}
      <span class="mark" aria-hidden="true">⌁</span>
      <span class="loc glow-text">{@html scene.room.name}</span>
      <span class="mark" aria-hidden="true">⌁</span>
    {/if}
  </div>

  <div class="zone right">
    <span class="brand glow-text">UNDERSPIRE</span>
    <div class="menu">
      <button class="cfg" onclick={() => (viewsOpen = !viewsOpen)} aria-haspopup="true" aria-expanded={viewsOpen}>[ VIEWS ]</button>
      {#if viewsOpen}
        <div class="drop" role="menu">
          {#each viewIds as id}
            <button class="mi" role="menuitem" class:on={dock.isOpen(id)} onclick={() => pick(id)}>
              {VIEWS[id].title}{dock.isOpen(id) ? " ·" : ""}
            </button>
          {/each}
          <button class="mi" role="menuitem" onclick={openSite}>Web page</button>
          <div class="sep"></div>
          {#each presets as name}
            <button class="mi preset" role="menuitem" onclick={() => loadPreset(name)}>
              ▸ {name}
              <span class="del" role="button" tabindex="0" onclick={(e) => delPreset(name, e)}>×</span>
            </button>
          {/each}
          <button class="mi" role="menuitem" onclick={savePreset}>Save layout…</button>
          <button class="mi" role="menuitem" class:on={dock.locked} onclick={() => dock.toggleLock()}>
            {dock.locked ? "Unlock layout" : "Lock layout"}
          </button>
          <button class="mi warn" role="menuitem" onclick={reset}>Reset layout</button>
        </div>
      {/if}
    </div>
    <button class="cfg" onclick={onsettings} aria-label="settings">[ CFG ]</button>
  </div>
</header>

<svelte:window onclick={(e) => {
  if (viewsOpen && !(e.target as HTMLElement)?.closest?.(".menu")) viewsOpen = false;
}} />

<style>
  .hud {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    padding: 0.35rem 0.8rem;
    border-bottom: 1px solid var(--accent);
    background: var(--bg-elev);
    color: var(--fg-dim);
    font-size: 0.72rem;
    letter-spacing: 0.16em;
    text-transform: uppercase;
  }
  .zone { display: flex; align-items: center; gap: 0.6rem; }
  .right { justify-content: flex-end; }
  .center { justify-content: center; gap: 0.8ch; }
  .dot { width: 0.5rem; height: 0.5rem; background: var(--fg-faint); }
  .hud[data-state="open"] .dot { background: var(--ok); }
  .hud[data-state="connecting"] .dot { background: var(--gold); }
  .hud[data-state="closed"] .dot,
  .hud[data-state="error"] .dot { background: var(--alert); }
  .mark { color: var(--accent); }
  .loc {
    color: var(--gold);
    letter-spacing: 0.2em;
    max-width: 44vw;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .brand { color: var(--accent-bright); letter-spacing: 0.32em; font-weight: 500; }
  .cfg {
    background: none;
    border: none;
    color: var(--fg-dim);
    font-family: inherit;
    font-size: inherit;
    letter-spacing: 0.1em;
    cursor: pointer;
    padding: 0;
  }
  .cfg:hover { color: var(--accent-bright); }
  .menu { position: relative; display: inline-flex; }
  .drop {
    position: absolute; top: 100%; right: 0; margin-top: 4px; z-index: 50;
    display: flex; flex-direction: column; min-width: 9rem;
    background: var(--bg-elev); border: 1px solid var(--accent);
  }
  .mi {
    background: none; border: none; color: var(--fg-dim); font-family: inherit;
    font-size: 0.68rem; letter-spacing: 0.1em; text-transform: uppercase;
    text-align: left; padding: 6px 10px; cursor: pointer;
  }
  .mi:hover { color: var(--accent-bright); background: var(--bg); }
  .mi.on { color: var(--gold); }
  .mi.warn:hover { color: var(--alert); }
  .sep { height: 1px; background: var(--border); margin: 2px 0; }
  .preset { display: flex; align-items: center; justify-content: space-between; }
  .preset .del { color: var(--fg-faint); padding: 0 3px; }
  .preset .del:hover { color: var(--alert); }
</style>
