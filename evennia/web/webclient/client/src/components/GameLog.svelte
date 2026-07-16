<script lang="ts">
  import { session } from "../lib/session.svelte";
  import { logview, categorize, CATS } from "../lib/logview.svelte";
  import { keybinds } from "../lib/keybinds.svelte";
  import { typewriter, markBacklog } from "../lib/typewriter";
  import { settings } from "../lib/settings.svelte";
  import { onMount } from "svelte";

  let el = $state<HTMLDivElement | null>(null);
  let searchInput = $state<HTMLInputElement | null>(null);
  let pinned = $state(true);
  let matchPos = $state(0);

  // Freeze the existing backlog so only lines that arrive after mount type in.
  onMount(() => markBacklog(session.lines.at(-1)?.id ?? -1));

  // Follow the newest line to the bottom as its characters reveal.
  function keepPinned() {
    if (el && pinned && !logview.searchOpen) el.scrollTop = el.scrollHeight;
  }

  const twEnabled = $derived(
    settings.typewriter && !settings.reduceMotion && !settings.screenreader,
  );

  const filtered = $derived(
    session.lines.filter((l) => logview.filters[categorize(l.type)]),
  );
  const query = $derived(logview.search.trim().toLowerCase());
  const matchIds = $derived(
    query ? filtered.filter((l) => l.text.toLowerCase().includes(query)).map((l) => l.id) : [],
  );

  function pad(n: number) {
    return String(n).padStart(2, "0");
  }
  function hhmmss(ts: number) {
    const d = new Date(ts);
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  // Autoscroll to newest unless the user scrolled up or is searching.
  $effect(() => {
    void session.lines.length;
    if (el && pinned && !logview.searchOpen) el.scrollTop = el.scrollHeight;
  });

  // Keep the current match in range and scroll it into view.
  $effect(() => {
    const n = matchIds.length;
    if (n === 0) return;
    if (matchPos >= n) matchPos = n - 1;
    const id = matchIds[matchPos];
    const node = el?.querySelector(`[data-lid="${id}"]`);
    node?.scrollIntoView({ block: "center" });
  });

  $effect(() => {
    if (logview.searchOpen) searchInput?.focus();
  });

  function onScroll() {
    if (!el) return;
    pinned = el.scrollHeight - el.scrollTop - el.clientHeight <= 24;
  }
  function step(d: number) {
    const n = matchIds.length;
    if (n) matchPos = (matchPos + d + n) % n;
  }
  function onGlobalKey(e: KeyboardEvent) {
    if (keybinds.match(e, "search")) {
      e.preventDefault();
      logview.searchOpen = true;
    } else if (e.key === "Escape" && logview.searchOpen) {
      logview.searchOpen = false;
    }
  }
  function onSearchKey(e: KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      step(e.shiftKey ? -1 : 1);
    } else if (e.key === "Escape") {
      logview.searchOpen = false;
    }
  }
  function clearBuffer() {
    if (session.lines.length && confirm("Clear the scrollback buffer?")) session.clear();
  }
  function downloadLog() {
    const blob = new Blob([session.transcript()], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    a.href = url;
    a.download = `underspire-log-${stamp}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }
</script>

<svelte:window onkeydown={onGlobalKey} />

<div class="log-wrap">
  <div class="log-bar">
    <div class="chips">
      {#each CATS as c}
        <button
          class="chip"
          class:off={!logview.filters[c.id]}
          onclick={() => logview.toggle(c.id)}
          title="toggle {c.label}"
        >{c.label}</button>
      {/each}
    </div>
    <button class="tool" class:on={logview.timestamps} onclick={() => (logview.timestamps = !logview.timestamps)} title="timestamps">⏱</button>
    <button class="tool" class:on={logview.searchOpen} onclick={() => (logview.searchOpen = !logview.searchOpen)} title="search (Ctrl-F)">⌕</button>
    <button class="tool" onclick={downloadLog} title="download log" aria-label="download log">⭳</button>
    <button class="tool" onclick={clearBuffer} title="clear buffer" aria-label="clear buffer">⌫</button>
  </div>

  {#if logview.searchOpen}
    <div class="search">
      <span class="s-glyph" aria-hidden="true">⌕</span>
      <input
        bind:this={searchInput}
        bind:value={logview.search}
        onkeydown={onSearchKey}
        placeholder="search scrollback"
        aria-label="search scrollback"
      />
      <span class="s-count">{matchIds.length ? matchPos + 1 : 0}/{matchIds.length}</span>
      <button class="s-btn" onclick={() => step(-1)} aria-label="previous">↑</button>
      <button class="s-btn" onclick={() => step(1)} aria-label="next">↓</button>
      <button class="s-btn" onclick={() => (logview.searchOpen = false)} aria-label="close">×</button>
    </div>
  {/if}

  <div
    class="game-log"
    bind:this={el}
    onscroll={onScroll}
    role="log"
    aria-live="polite"
    aria-atomic="false"
    aria-label="game output"
  >
    {#each filtered as line (line.id)}
      <div
        class="log-line"
        data-cat={categorize(line.type)}
        data-lid={line.id}
        class:hit={matchIds.includes(line.id)}
        class:active={matchIds[matchPos] === line.id}
      >
        {#if logview.timestamps}<span class="ts">{hhmmss(line.ts)}</span>{/if}<span class="body" use:typewriter={{ id: line.id, enabled: twEnabled, onstep: keepPinned }}>{@html line.html}</span>
      </div>
    {/each}
  </div>
</div>

<style>
  .log-wrap { display: flex; flex-direction: column; height: 100%; min-height: 0; position: relative; }
  .log-bar {
    display: flex; align-items: center; gap: 6px;
    padding: 3px 8px; border-bottom: 1px solid var(--border);
    background: var(--bg-elev); flex: 0 0 auto;
  }
  .chips { display: flex; gap: 4px; flex: 1; flex-wrap: wrap; }
  .chip {
    background: none; border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.62rem; letter-spacing: 0.12em; text-transform: uppercase;
    padding: 1px 7px; cursor: pointer;
  }
  .chip:hover { color: var(--fg); }
  .chip.off { color: var(--fg-faint); border-color: var(--border); opacity: 0.5; text-decoration: line-through; }
  .tool {
    background: none; border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.8rem; padding: 0 6px; cursor: pointer;
  }
  .tool:hover, .tool.on { color: var(--accent-bright); border-color: var(--accent); }

  .search {
    display: flex; align-items: center; gap: 6px;
    padding: 4px 8px; border-bottom: 1px solid var(--accent); background: var(--bg-deep);
    flex: 0 0 auto;
  }
  .s-glyph { color: var(--accent); }
  .search input {
    flex: 1; background: transparent; border: none; outline: none;
    color: var(--fg); font-family: inherit; font-size: 0.85rem;
  }
  .s-count { color: var(--fg-dim); font-size: 0.72rem; min-width: 3.5em; text-align: right; }
  .s-btn {
    background: none; border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; cursor: pointer; padding: 0 6px;
  }
  .s-btn:hover { color: var(--accent-bright); border-color: var(--accent); }

  .game-log { overflow-y: auto; padding: 0.7rem 1rem; line-height: var(--shell-line-height, 1.5); flex: 1; }
  .log-line { white-space: pre-wrap; word-break: break-word; }
  .log-line .ts { color: var(--fg-faint); margin-right: 0.8ch; font-size: 0.82em; user-select: none; }
  /* Category accents - only the standouts get a marker, to avoid noise. */
  .log-line[data-cat="combat"] { border-left: 2px solid var(--alert); padding-left: 7px; margin-left: -9px; }
  .log-line[data-cat="comms"] { border-left: 2px solid var(--gold); padding-left: 7px; margin-left: -9px; }
  .log-line.hit { background: color-mix(in srgb, var(--accent) 14%, transparent); }
  .log-line.active { background: color-mix(in srgb, var(--accent) 30%, transparent); outline: 1px solid var(--accent); }
</style>
