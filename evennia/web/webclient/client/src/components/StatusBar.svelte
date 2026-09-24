<script lang="ts">
  import { connection } from "../lib/evennia.svelte";
  import { scene } from "../lib/scene.svelte";
  import { dock, VIEWS } from "../lib/dock.svelte";
  import { chat } from "../lib/chat.svelte";
  import { media } from "../lib/media.svelte";
  import { settings } from "../lib/settings.svelte";
  import { focusRegion, type Region } from "../lib/regions";
  import { tick } from "svelte";

  let { onsettings }: { onsettings: () => void } = $props();

  const labels: Record<string, string> = {
    connecting: "linking",
    open: "online",
    closed: "severed",
    error: "fault",
  };

  let viewsOpen = $state(false);
  let volOpen = $state(false);
  // Panels the player can reopen (Tickets only for staff).
  const viewIds = $derived(
    Object.keys(VIEWS).filter((id) => id !== "tickets" || chat.staff),
  );
  // Where focus goes after a pick: the view's own region when it has one,
  // else the tab of the new view in the screen-reader layout, else back to
  // the Views button. Closing the list must not leave focus on the body.
  const REGION_OF: Record<string, Region> = { log: "output", scene: "scene", chat: "channels" };
  async function afterPick(id?: string) {
    viewsOpen = false;
    await tick();
    if (id && REGION_OF[id]) {
      if (await focusRegion(REGION_OF[id])) return;
    }
    const tab = id ? document.getElementById(`sv-tab-${id}`) : null;
    if (tab) tab.focus();
    else if (document.activeElement === document.body || !document.activeElement) viewsBtn?.focus();
  }
  function pick(id: string) {
    dock.openView(id);
    void afterPick(id);
  }
  function openSite() {
    dock.openWebPage("view:site", "Web", location.origin);
    void afterPick("view:site");
  }
  // The notes page is a per-character tokenised URL the server puts in the
  // scene fields; it is only offered when the character actually has one.
  const notesUrl = $derived(
    typeof scene.fields.notes_url === "string" ? scene.fields.notes_url : "",
  );
  function openNotes() {
    if (notesUrl) dock.openWebPage("view:notes", "Notes", notesUrl);
    void afterPick("view:notes");
  }
  function reset() {
    viewsOpen = false;
    if (confirm("Reset the panel layout to the default?")) dock.resetLayout();
  }
  let presets = $state<string[]>([]);
  function refreshPresets() {
    presets = dock.listLayouts();
  }
  function loadPreset(name: string) {
    dock.loadLayout(name);
    void afterPick();
  }
  function savePreset() {
    const name = prompt("Save current layout as:");
    if (name) {
      dock.saveLayout(name);
      refreshPresets();
    }
  }
  function delPreset(name: string, e: MouseEvent | KeyboardEvent) {
    e.stopPropagation();
    dock.deleteLayout(name);
    refreshPresets();
  }
  $effect(() => {
    if (viewsOpen) refreshPresets();
  });

  let viewsBtn = $state<HTMLButtonElement | null>(null);
  function focusFirst(node: HTMLElement, first: boolean) {
    if (first) node.focus();
  }
  function onDropKey(e: KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      viewsOpen = false;
      viewsBtn?.focus();
    }
  }
</script>

<header class="hud" data-state={connection.state}>
  <div class="zone left">
    <span class="dot" aria-hidden="true"></span>
    <span class="conn"><span class="sr-only">Connection: </span>{labels[connection.state] ?? connection.state}</span>
  </div>

  <div class="zone center">
    {#if scene.present && scene.room.name}
      <span class="mark" aria-hidden="true">⌁</span>
      <span class="loc glow-text">{@html scene.room.name}</span>
      <span class="mark" aria-hidden="true">⌁</span>
    {/if}
    {#if media.nowPlaying}
      <button
        type="button"
        class="now-playing"
        title="Open media panel"
        onclick={() => media.openNowPlaying()}
      >
        {media.nowPlayingLabel()}
      </button>
    {/if}
  </div>

  <div class="zone right">
    <div
      class="vol"
      class:open={volOpen}
      class:muted={media.volume === 0}
      role="group"
      aria-label="Music volume"
      title="Music volume"
      onmouseenter={() => (volOpen = true)}
      onmouseleave={() => (volOpen = false)}
      onfocusin={() => (volOpen = true)}
      onfocusout={(e) => {
        if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) {
          volOpen = false;
        }
      }}
    >
      <svg class="vol-icon" viewBox="0 0 24 24" aria-hidden="true">
        {#if media.volume === 0}
          <path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z" />
        {:else}
          <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z" />
        {/if}
      </svg>
      <label class="sr-only" for="hud-vol">Music volume</label>
      <input
        id="hud-vol"
        class="vol-slider"
        type="range"
        min="0"
        max="100"
        step="1"
        value={media.volume}
        oninput={(e) => media.setVolume(+e.currentTarget.value)}
        aria-label="Music volume"
      />
    </div>
    <span class="brand glow-text">UNDERSPIRE</span>
    <div class="menu">
      <button class="cfg" bind:this={viewsBtn} onclick={() => (viewsOpen = !viewsOpen)} aria-expanded={viewsOpen} aria-label="Views">[ VIEWS ]</button>
      {#if viewsOpen}
        <!-- Disclosure, not role="menu": a menu role promises arrow-key
             handling these items never had. Escape closes it. -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div class="drop" onkeydown={onDropKey}>
          {#each viewIds as id, i}
            <button class="mi" class:on={dock.isOpen(id)} onclick={() => pick(id)} use:focusFirst={i === 0}>
              {VIEWS[id].title}{#if dock.isOpen(id)}<span aria-hidden="true"> ·</span><span class="sr-only"> (open)</span>{/if}
            </button>
          {/each}
          <button class="mi" onclick={openSite}>Web page</button>
          {#if notesUrl}
            <button class="mi" onclick={openNotes}>Notes</button>
          {/if}
          <!-- Layouts belong to the docked workspace; the screen-reader
               layout has none to save, lock or reset. -->
          {#if !settings.screenreader}
          <div class="sep" role="separator"></div>
          {#each presets as name}
            <div class="preset-row">
              <button class="mi preset" onclick={() => loadPreset(name)}>▸ {name}</button>
              <button class="del" onclick={(e) => delPreset(name, e)} aria-label="Delete layout {name}">×</button>
            </div>
          {/each}
          <button class="mi" onclick={savePreset}>Save layout…</button>
          <button class="mi" class:on={dock.locked} onclick={() => dock.toggleLock()}>
            {dock.locked ? "Unlock layout" : "Lock layout"}
          </button>
          <button class="mi warn" onclick={reset}>Reset layout</button>
          {/if}
        </div>
      {/if}
    </div>
    <button class="cfg" onclick={onsettings} aria-label="Settings">[ CFG ]</button>
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
  .now-playing {
    background: none;
    border: 1px solid var(--border);
    color: var(--accent-bright);
    font-family: inherit;
    font-size: 0.62rem;
    letter-spacing: 0.08em;
    padding: 2px 6px;
    cursor: pointer;
    max-width: 14ch;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .now-playing:hover { color: var(--accent-bright); border-color: var(--accent); }
  .brand { color: var(--accent-bright); letter-spacing: 0.32em; font-weight: 500; }

  .vol {
    display: flex;
    align-items: center;
    gap: 0;
    height: 1.25rem;
    cursor: default;
  }
  .vol-icon {
    flex-shrink: 0;
    width: 0.85rem;
    height: 0.85rem;
    fill: var(--fg-faint);
    transition: fill 0.15s ease;
  }
  .vol:hover .vol-icon,
  .vol.open .vol-icon,
  .vol:focus-within .vol-icon {
    fill: var(--accent);
  }
  .vol.muted .vol-icon { fill: var(--fg-faint); opacity: 0.65; }

  .vol-slider {
    -webkit-appearance: none;
    appearance: none;
    width: 0;
    height: 2px;
    margin: 0;
    padding: 0;
    border: none;
    border-radius: 1px;
    background: var(--border);
    outline: none;
    opacity: 0;
    pointer-events: none;
    transition: width 0.18s ease, opacity 0.15s ease, margin 0.18s ease;
    touch-action: none;
  }
  .vol.open .vol-slider,
  .vol:focus-within .vol-slider {
    width: 4.5rem;
    margin-left: 0.45rem;
    opacity: 1;
    pointer-events: auto;
  }
  .vol-slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--accent);
    cursor: pointer;
    border: none;
    box-shadow: 0 0 0 1px var(--bg-elev);
  }
  .vol-slider::-moz-range-thumb {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--accent);
    cursor: pointer;
    border: none;
  }
  .vol-slider::-moz-range-track {
    height: 2px;
    background: var(--border);
    border-radius: 1px;
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
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
  .preset-row { display: flex; align-items: stretch; }
  .preset { flex: 1; }
  .del {
    background: none; border: none; color: var(--fg-dim); font-family: inherit;
    padding: 0 8px; min-width: 24px; cursor: pointer;
  }
  .del:hover { color: var(--alert); }
</style>
