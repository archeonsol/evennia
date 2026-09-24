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

<!-- An instrument strip, like the Nous deck's: labelled readout cells split by
     rules, then the function keys. -->
<header class="hud" data-state={connection.state}>
  <div class="plate" aria-hidden="true"><span class="plate-mark">U</span><span class="plate-rest">NDERSPIRE</span></div>
  <span class="sr-only">Underspire</span>

  <div class="cell">
    <span class="lbl" aria-hidden="true">LINK</span>
    <span class="val conn"><span class="dot" aria-hidden="true"></span><span class="sr-only">Connection: </span>{labels[connection.state] ?? connection.state}</span>
  </div>

  <div class="cell grow">
    {#if scene.present && scene.room.name}
      <span class="lbl" aria-hidden="true">LOC</span>
      <span class="val loc glow-text"><span class="sr-only">Location: </span>{@html scene.room.name}</span>
    {/if}
  </div>

  {#if media.nowPlaying}
    <div class="cell">
      <span class="lbl" aria-hidden="true">AUDIO</span>
      <button
        type="button"
        class="now-playing"
        title="Open media panel"
        onclick={() => media.openNowPlaying()}
      >
        {media.nowPlayingLabel()}
      </button>
    </div>
  {/if}

  <div class="cell keys">
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
      <span class="lbl" aria-hidden="true">VOL</span>
      <span class="vol-val" aria-hidden="true">{media.volume === 0 ? "OFF" : String(media.volume).padStart(3, "0")}</span>
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
    <div class="menu">
      <button class="cfg" bind:this={viewsBtn} onclick={() => (viewsOpen = !viewsOpen)} aria-expanded={viewsOpen} aria-label="Views">VIEWS</button>
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
    <button class="cfg" onclick={onsettings} aria-label="Settings">CFG</button>
  </div>
</header>

<svelte:window onclick={(e) => {
  if (viewsOpen && !(e.target as HTMLElement)?.closest?.(".menu")) viewsOpen = false;
}} />

<style>
  .hud {
    display: flex;
    align-items: stretch;
    min-height: 2.1rem;
    padding: 0 0.5rem;
    border-bottom: 1px solid var(--accent);
    background: var(--bg-elev);
    color: var(--fg-dim);
    font-size: 0.72rem;
    letter-spacing: 0.16em;
    text-transform: uppercase;
  }
  /* Nameplate: the first letter keyed in a box, as on the Nous deck. */
  .plate {
    display: flex; align-items: center; padding-right: 0.8rem; margin-right: 0.2rem;
    border-right: 1px solid var(--border-bright); white-space: nowrap;
  }
  .plate-mark {
    border: 1px solid var(--accent-bright); color: var(--accent-bright);
    padding: 0 0.3em; margin-right: 0.15em; font-weight: 500; letter-spacing: 0;
    text-shadow: 0 0 6px var(--glow);
  }
  .plate-rest { color: var(--accent-bright); letter-spacing: 0.3em; font-weight: 500; }
  /* Readout cell: a small label over, or beside, its value. */
  .cell {
    display: flex; align-items: center; gap: 0.6rem; padding: 0 0.8rem;
    border-right: 1px solid var(--border); min-width: 0; white-space: nowrap;
  }
  .cell.grow { flex: 1 1 0; overflow: hidden; }
  .hud { min-width: 0; }
  /* Narrow screens: keep the values and keys, drop the labels first. */
  @media (max-width: 820px) {
    .plate-rest, .lbl { display: none; }
    .cell { padding: 0 0.5rem; }
  }
  .cell.keys { border-right: none; padding-right: 0; gap: 0.5rem; }
  .lbl { color: var(--fg-faint); font-size: 0.6rem; letter-spacing: 0.18em; }
  .val { display: flex; align-items: center; gap: 0.5ch; color: var(--fg); min-width: 0; }
  .vol-val { color: var(--fg); font-variant-numeric: tabular-nums; min-width: 3ch; }
  .dot { width: 0.5rem; height: 0.5rem; background: var(--fg-faint); }
  .hud[data-state="open"] .dot { background: var(--ok); }
  .hud[data-state="connecting"] .dot { background: var(--gold); }
  .hud[data-state="closed"] .dot,
  .hud[data-state="error"] .dot { background: var(--alert); }
  .loc {
    color: var(--gold);
    letter-spacing: 0.2em;
    display: block;
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

  .vol {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    height: 1.25rem;
    cursor: default;
  }

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
  /* Function keys: labelled, boxed, lit on hover or while open. */
  .cfg {
    background: var(--bg);
    border: 1px solid var(--border-bright);
    color: var(--fg-dim);
    font-family: inherit;
    font-size: 0.66rem;
    letter-spacing: 0.16em;
    cursor: pointer;
    padding: 3px 9px;
    min-height: 24px;
    white-space: nowrap;
  }
  .cfg:hover, .cfg[aria-expanded="true"] { color: var(--accent-bright); border-color: var(--accent); }
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
