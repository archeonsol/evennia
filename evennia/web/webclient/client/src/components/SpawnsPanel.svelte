<script lang="ts">
  // Feeds: lines the player's routing rules file out of the terminal, one tab
  // per feed. With `feed` set (a popped-out feed) it shows that feed alone.
  import { tick, untrack } from "svelte";
  import { routing } from "../lib/routing.svelte";
  import { dock } from "../lib/dock.svelte";

  let { feed = "" }: { feed?: string } = $props();

  const labels = $derived(feed ? [feed] : routing.labels());
  let active = $state("");
  // Default to the first feed; keep the choice valid as feeds come and go.
  $effect(() => {
    if (!labels.includes(active)) active = labels[0] ?? "";
  });
  const lines = $derived(active ? (routing.buffers[active] ?? []) : []);

  let query = $state("");
  let searching = $state(false);
  let stamps = $state(false);
  const q = $derived(query.trim().toLowerCase());
  const shown = $derived(
    q ? lines.filter((l) => htmlText(l.html).toLowerCase().includes(q)) : lines,
  );

  // Plain text of a line for search. Memoised by html: a feed can hold a
  // thousand lines and search runs on every keystroke.
  const textCache = new Map<string, string>();
  function htmlText(html: string): string {
    let t = textCache.get(html);
    if (t === undefined) {
      const d = document.createElement("div");
      d.innerHTML = html;
      t = d.textContent ?? "";
      if (textCache.size > 5000) textCache.clear();
      textCache.set(html, t);
    }
    return t;
  }

  // -- follow the newest line --------------------------------------------
  // Pinned to the bottom until the player scrolls up; then new lines are
  // counted on a "jump to latest" bar instead of yanking the view down. The
  // feed never followed at all before, so a busy one had to be scrolled by
  // hand after every line.
  let listEl = $state<HTMLDivElement | null>(null);
  let pinned = $state(true);
  let unseen = $state(0);
  let lastCount = 0;
  let lastFeed = "";

  function atBottom(el: HTMLElement): boolean {
    return el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  }
  function onScroll() {
    if (!listEl || !listEl.clientHeight) return; // hidden panel, not a scroll
    pinned = atBottom(listEl);
    if (pinned) unseen = 0;
  }
  function onWheel(e: WheelEvent) {
    if (e.deltaY < 0) pinned = false;
  }
  async function jumpToLatest() {
    pinned = true;
    unseen = 0;
    await tick();
    if (listEl) listEl.scrollTop = listEl.scrollHeight;
  }

  // Runs on new lines and feed switches only; the pinned state is read
  // untracked so scrolling never re-runs it.
  $effect(() => {
    const n = shown.length;
    const feedNow = active;
    untrack(() => {
      const switched = feedNow !== lastFeed;
      const grew = n - lastCount;
      lastFeed = feedNow;
      lastCount = n;
      if (switched) {
        pinned = true;
        unseen = 0;
      } else if (!pinned && grew > 0) {
        unseen += grew;
      }
      if (pinned) tick().then(() => listEl && (listEl.scrollTop = listEl.scrollHeight));
    });
  });

  // Coming back to a hidden panel: dockview hides inactive tabs, and a hidden
  // box forgets its scroll position.
  $effect(() => {
    const el = listEl;
    if (!el) return;
    let hidden = false;
    const ro = new ResizeObserver(() => {
      if (!el.clientHeight) {
        hidden = true;
        return;
      }
      if (hidden && pinned) el.scrollTop = el.scrollHeight;
      hidden = false;
    });
    ro.observe(el);
    return () => ro.disconnect();
  });

  // What is on screen at the bottom has been seen; the badge on the other
  // tabs is the only notice a moved line gets.
  $effect(() => {
    void lines.length;
    if (active && pinned) routing.markRead(active);
  });

  // -- tabs --------------------------------------------------------------
  let tabEls: Record<string, HTMLButtonElement> = {};
  async function onTabKey(e: KeyboardEvent, i: number) {
    const n = labels.length;
    let next = -1;
    if (e.key === "ArrowRight") next = (i + 1) % n;
    else if (e.key === "ArrowLeft") next = (i - 1 + n) % n;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = n - 1;
    if (next < 0) return;
    e.preventDefault();
    active = labels[next];
    await tick();
    tabEls[active]?.focus();
  }

  function clearFeed() {
    if (!active) return;
    if (confirm(`Clear ${active}?`)) routing.clear(active);
  }

  function openRules() {
    window.dispatchEvent(new CustomEvent("underspire:settings", { detail: { view: "feeds" } }));
  }

  function hhmm(ts: number) {
    const d = new Date(ts);
    const p = (n: number) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }
</script>

<div class="spawns">
  {#if labels.length}
    <div class="bar">
      {#if !feed}
        <div class="tabs" role="tablist" aria-label="Feeds">
          {#each labels as l, i (l)}
            <button
              bind:this={tabEls[l]}
              class="tab"
              class:on={l === active}
              role="tab"
              aria-selected={l === active}
              tabindex={l === active ? 0 : -1}
              aria-label={routing.unread[l] && l !== active ? `${l}, ${routing.unread[l]} unread` : l}
              onclick={() => (active = l)}
              onkeydown={(e) => onTabKey(e, i)}
            >{l}{#if l !== active && routing.unread[l]}<span class="badge" aria-hidden="true">{routing.unread[l]}</span>{/if}</button>
          {/each}
        </div>
      {:else}
        <span class="solo">{feed}</span>
      {/if}
      <span class="tools" role="toolbar" aria-label="Feed tools">
        <button class="t" class:on={searching} aria-pressed={searching} aria-label="Search this feed" title="search"
          onclick={() => { searching = !searching; if (!searching) query = ""; }}>⌕</button>
        <button class="t" class:on={stamps} aria-pressed={stamps} aria-label="Timestamps" title="timestamps"
          onclick={() => (stamps = !stamps)}>⏱</button>
        {#if !feed && active}
          <button class="t" aria-label="Open {active} in its own panel" title="own panel" onclick={() => dock.openFeed(active)}>⇱</button>
        {/if}
        <button class="t" aria-label="Edit feed rules" title="rules" onclick={openRules}>⚙</button>
        {#if active}
          <button class="t" aria-label="Clear {active} feed" title="clear" onclick={clearFeed}>⌫</button>
        {/if}
      </span>
    </div>

    {#if searching}
      <div class="search">
        <input bind:value={query} placeholder="search {active}…" aria-label="Search {active}"
          onkeydown={(e) => { if (e.key === "Escape") { searching = false; query = ""; } }}
          use:autofocus />
        <span class="cnt" aria-live="polite">{shown.length}<span class="sr-only"> matching lines</span></span>
      </div>
    {/if}

    <div class="lines-wrap">
      <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
      <div
        class="lines"
        bind:this={listEl}
        onscroll={onScroll}
        onwheel={onWheel}
        role="log"
        aria-live="off"
        aria-label="{active} feed"
        tabindex="0"
      >
        {#each shown as ln (ln.id)}
          <div class="line">{#if stamps}<span class="ts">{hhmm(ln.ts)}</span>{/if}{@html ln.html}</div>
        {/each}
        {#if !shown.length}
          <p class="empty">{q ? "No matches." : "Empty."}</p>
        {/if}
      </div>
      {#if !pinned && unseen > 0}
        <button class="latest" onclick={jumpToLatest}>
          {unseen} new line{unseen === 1 ? "" : "s"} ↓
        </button>
      {:else if !pinned}
        <button class="latest quiet" onclick={jumpToLatest} aria-label="Jump to the latest line">↓</button>
      {/if}
    </div>
  {:else}
    <div class="intro">
      <button class="make" onclick={openRules}>Add a feed</button>
    </div>
  {/if}
</div>

<script module lang="ts">
  // Focus the search box when it appears (autofocus is ignored after load).
  export function autofocus(node: HTMLElement) {
    queueMicrotask(() => node.focus());
  }
</script>

<style>
  .spawns { display: flex; flex-direction: column; height: 100%; background: var(--bg-elev); }
  .bar { display: flex; align-items: center; gap: 6px; padding: 4px 8px; border-bottom: 1px solid var(--accent); flex: 0 0 auto; }
  .tabs { display: flex; gap: 4px; flex-wrap: wrap; flex: 1; min-width: 0; }
  .solo { flex: 1; color: var(--accent-bright); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.12em; }
  .tab {
    background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg-dim); font-family: inherit;
    font-size: 0.64rem; text-transform: uppercase; letter-spacing: 0.08em; padding: 2px 8px; min-height: 24px; cursor: pointer;
  }
  .tab.on { color: var(--accent-bright); border-color: var(--accent); }
  .badge { margin-left: 5px; color: var(--gold); }
  .tools { display: flex; gap: 3px; flex: 0 0 auto; }
  .t {
    background: none; border: 1px solid var(--border-bright); color: var(--fg-dim); font-family: inherit;
    font-size: 0.78rem; min-width: 24px; min-height: 24px; padding: 0 5px; cursor: pointer;
  }
  .t:hover, .t.on { color: var(--accent-bright); border-color: var(--accent); }
  .search { display: flex; align-items: center; gap: 6px; padding: 4px 8px; border-bottom: 1px solid var(--border); flex: 0 0 auto; }
  .search input { flex: 1; background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg); font-family: inherit; font-size: 0.8rem; padding: 2px 6px; }
  .cnt { color: var(--fg-dim); font-size: 0.72rem; }
  .lines-wrap { position: relative; flex: 1; min-height: 0; display: flex; }
  .lines { flex: 1; overflow-y: auto; padding: 6px 10px; line-height: var(--shell-line-height, 1.5); }
  .line { white-space: pre-wrap; word-break: break-word; }
  .ts { color: var(--fg-faint); margin-right: 0.8ch; font-size: 0.82em; user-select: none; }
  .latest {
    position: absolute; right: 14px; bottom: 10px; background: var(--bg-deep); border: 1px solid var(--accent);
    color: var(--accent-bright); font-family: inherit; font-size: 0.7rem; letter-spacing: 0.06em; padding: 3px 10px; min-height: 24px; cursor: pointer;
  }
  .latest.quiet { padding: 3px 7px; }
  .empty { color: var(--fg-faint); font-style: italic; padding: 10px 0; font-size: 0.78rem; }
  .intro { padding: 12px; color: var(--fg-dim); font-size: 0.8rem; line-height: 1.5; }
  .make {
    background: none; border: 1px solid var(--accent); color: var(--accent-bright); font-family: inherit;
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.1em; padding: 4px 12px; min-height: 24px; cursor: pointer;
  }
</style>
