<script lang="ts">
  import { onMount, tick, untrack } from "svelte";
  import { activity, type Category, type Reference } from "../lib/activity.svelte";
  import { puppets } from "../lib/puppets.svelte";
  import { settings } from "../lib/settings.svelte";
  import { createLogVirtualizer, type LogVirtualizer } from "../lib/logvirtual";

  let category = $state<Category>("all");
  let watched = $state(false);
  let filter = $state("");
  let watchOpen = $state(false);
  let watchQuery = $state("");
  let selected = $state<string | null>(null);
  let scroll = $state<HTMLDivElement | null>(null);
  let handle = $state<LogVirtualizer<HTMLDivElement> | null>(null);
  let revision = $state(0);
  let following = $state(true);
  let seenSeq = $state(0);
  let started = false;
  let revisionQueued = false;
  let followQueued = false;
  const rows = $derived(activity.filtered(category, watched, filter));
  const virtualItems = $derived.by(() => { void revision; return handle?.instance.getVirtualItems() ?? []; });
  const totalSize = $derived.by(() => { void revision; return handle?.instance.getTotalSize() ?? 0; });
  const watchCount = $derived(activity.watches.characters.length + activity.watches.locations.length);
  const visible = $derived(settings.screenreader ? rows.map((row, index) => ({ index, start: 0, key: row.seq })) : virtualItems);
  const unseen = $derived(activity.countAfter(seenSeq, category, watched, filter));
  const categories: { id: Category; label: string }[] = [{ id: "all", label: "All" }, { id: "text", label: "Text" }, { id: "looc", label: "LOOC" }, { id: "npc", label: "NPC" }];

  onMount(() => { activity.open(); return () => activity.close(); });

  $effect(() => {
    void activity.epoch;
    selected = null;
    watchQuery = "";
    watchOpen = false;
    filter = "";
    following = true;
    seenSeq = 0;
    started = false;
  });

  $effect(() => {
    const query = watchQuery;
    if (!watchOpen) return;
    const timer = setTimeout(() => void activity.search(query), 250);
    return () => clearTimeout(timer);
  });

  function bump() {
    if (revisionQueued) return;
    revisionQueued = true;
    queueMicrotask(() => { revisionQueued = false; revision++; });
  }

  function follow() {
    if (followQueued) return;
    followQueued = true;
    queueMicrotask(async () => {
      followQueued = false;
      if (!following || activity.paused || !scroll?.clientHeight) return;
      await tick();
      handle?.instance.scrollToEnd({ behavior: "auto" });
      seenSeq = activity.lastSeq;
    });
  }

  $effect(() => {
    const node = scroll;
    const screenreader = settings.screenreader;
    void activity.epoch;
    if (!node || screenreader) return;
    return untrack(() => {
      const v = createLogVirtualizer({
        getScrollElement: () => node, getCount: () => rows.length,
        getKey: (i) => rows[i]?.seq ?? i, estimateSize: () => 76,
        getPinned: () => rows.map((row, index) => row.id === selected ? index : -1).filter((i) => i >= 0),
        onChange: () => { bump(); follow(); },
      });
      handle = v;
      const cleanup = v.instance._didMount();
      v.instance._willUpdate();
      return () => { cleanup(); if (handle === v) handle = null; };
    });
  });

  $effect(() => {
    const v = handle;
    void rows;
    void selected;
    void activity.paused;
    if (!v) return;
    untrack(() => {
      v.sync();
      v.instance._willUpdate();
      bump();
      if (!started && rows.length) { started = true; following = true; }
      follow();
    });
  });

  function measure(node: HTMLElement) {
    handle?.instance.measureElement(node);
    return { destroy: () => queueMicrotask(() => handle?.instance.measureElement(null)) };
  }

  function onScroll() {
    if (!scroll) return;
    if (scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight <= 32) {
      following = true;
      if (!activity.paused) seenSeq = activity.lastSeq;
    } else following = false;
  }

  function jump() {
    if (activity.paused) activity.togglePause();
    following = true;
    follow();
  }

  function isWatched(ref: Reference) {
    return (ref.kind === "location" ? activity.watches.locations : activity.watches.characters).some((watch) => watch.id === ref.id);
  }

  function watch(ref: Reference) {
    return activity.mutate(isWatched(ref) ? "unwatch" : "watch", { id: ref.id, kind: ref.kind === "location" ? "locations" : "characters" });
  }

  function time(ms: number) {
    return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  }
  function kind(kind: string) { return kind.startsWith("handset.") ? "Text" : kind === "looc" ? "LOOC" : "NPC"; }
</script>

<div class="activity" aria-label="Staff Activity">
  <div class="controls">
    <div class="control-row">
      <div class="segment" aria-label="Activity category">
        {#each categories as item}<button aria-pressed={category === item.id} onclick={() => category = item.id}>{item.label}</button>{/each}
      </div>
      <div class="segment scope" aria-label="Activity scope">
        <button aria-pressed={!watched} onclick={() => watched = false}>Global</button>
        <button aria-pressed={watched} onclick={() => watched = true}>Watched</button>
      </div>
    </div>
    <div class="control-row">
      <input class="filter" aria-label="Filter activity" placeholder="Filter activity…" bind:value={filter} />
      <button class="watch-toggle" aria-expanded={watchOpen} onclick={() => watchOpen = !watchOpen}>Watch ({watchCount})</button>
    </div>
    {#if watchOpen}
      <div class="watch-manager">
        <input aria-label="Find a character, NPC or location to watch" placeholder="Name or #id to watch…" bind:value={watchQuery} />
        {#if activity.searching}<p class="hint">Searching…</p>
        {:else if watchQuery.trim().length >= 2 && !activity.results.length}<p class="hint">No matching character, NPC or location.</p>{/if}
        <div class="watch-results">
          {#each activity.results as ref (ref.id)}
            <button disabled={activity.busy} onclick={() => void watch(ref)}><span>{ref.name} <small>#{ref.id} · {ref.kind}</small></span><span>{isWatched(ref) ? "Unwatch" : "Watch"}</span></button>
          {/each}
        </div>
        {#if watchCount}
          <div class="watch-list">
            {#each ["characters", "locations"] as watchKind}
              {#each activity.watches[watchKind as "characters" | "locations"] as item (item.id)}
                <button disabled={activity.busy} aria-label={`Unwatch ${item.label}`} onclick={() => void activity.mutate("unwatch", { kind: watchKind, id: item.id })}>{item.label}<span aria-hidden="true"> ×</span></button>
              {/each}
            {/each}
          </div>
        {:else}<p class="hint">Watch any character, NPC or location by name or #id.</p>{/if}
      </div>
    {/if}
  </div>
  {#if activity.error}<div class="notice" role="alert">{activity.error}<button onclick={() => void activity.ensureSubscribed()}>Retry</button></div>{/if}
  {#if activity.gap}<div class="notice" role="status">Some activity was missed.<button onclick={() => activity.gap = false}>Dismiss</button></div>{/if}
  {#if activity.paused && activity.events[0]?.seq > activity.pauseCutoff}<div class="notice">Paused activity has rolled out of the feed.</div>{/if}
  <div class="feed" bind:this={scroll} onscroll={onScroll} onwheel={(event) => { if (event.deltaY < 0) following = false; }}>
    {#if !rows.length}
      <p class="empty">{activity.loading ? "Loading activity…" : activity.paused ? "Feed paused. Resume to see new activity." : watched && !watchCount ? "Add a watch to see activity for a character or location." : filter || watched || category !== "all" ? "No activity matches these filters." : "New player activity will appear here."}</p>
    {/if}
    <div class="feed-space" style:height={settings.screenreader ? "auto" : `${totalSize}px`}>
      {#each visible as item (item.key)}
        {@const event = rows[item.index]}
        {#if event}
          <div class:selected={selected === event.id} class="event" class:reader={settings.screenreader} data-index={item.index} style:transform={settings.screenreader ? "none" : `translateY(${item.start}px)`} use:measure>
            <button class="event-trigger" aria-expanded={selected === event.id} onclick={() => selected = selected === event.id ? null : event.id}>
              <span class="event-top"><time datetime={new Date(event.ts_ms).toISOString()}>{time(event.ts_ms)}</time><span class="kind">{kind(event.kind)}</span><span class="summary"><strong>{event.actor?.name ?? "Unknown"}</strong>{#if event.kind === "handset.group"} → {event.meta.group_name}{:else if event.targets.length} → {event.targets.map((ref) => ref.name).join(", ")}{/if}</span></span>
              <span class="body" class:expanded={selected === event.id}>{event.body}</span>
              {#if event.location}<span class="location">{event.location.name}</span>{/if}
            </button>
            {#if selected === event.id}
              <div class="actions">
                {#if event.actor}<button disabled={activity.busy} onclick={() => void watch(event.actor!)}>{isWatched(event.actor) ? "Unwatch" : "Watch"} {event.actor.name}</button>{/if}
                {#each event.npc_targets as npc (npc.id)}
                  {#if activity.canPuppet}<button class="add-puppet" disabled={activity.busy || puppets.feeds.has(String(npc.id))} onclick={() => void activity.mutate("puppet_add", { npc_id: npc.id })}>{puppets.feeds.has(String(npc.id)) ? "In puppets" : "Add puppet"}{event.npc_targets.length > 1 ? `: ${npc.name}` : ""}</button>{/if}
                  <button disabled={activity.busy} onclick={() => void watch(npc)}>{isWatched(npc) ? "Unwatch" : "Watch"} {npc.name}</button>
                {/each}
                {#if event.location}<button disabled={activity.busy} onclick={() => void watch(event.location!)}>{isWatched(event.location) ? "Unwatch" : "Watch"} location</button>{/if}
              </div>
            {/if}
          </div>
        {/if}
      {/each}
    </div>
  </div>
  <div class="footer">
    <span>{activity.paused ? "Paused" : following ? "Following" : "Reading"} · {rows.length} shown</span>
    {#if unseen && (!following || activity.paused)}<button onclick={jump}>Latest ({unseen})</button>{/if}
    <button aria-pressed={activity.paused} onclick={() => { activity.togglePause(); if (!activity.paused) jump(); }}>{activity.paused ? "Resume" : "Pause"}</button>
  </div>
</div>

<style>
  .activity { display: flex; flex-direction: column; height: 100%; min-height: 0; min-width: 0; background: var(--bg); color: var(--fg); font-size: max(14px, var(--shell-font-size, 14px)); line-height: 1.35; }
  button, input { font: inherit; color: inherit; }
  button { cursor: pointer; border: 1px solid var(--border-bright); background: var(--bg-elev); border-radius: 2px; min-height: 27px; padding: 3px 7px; }
  button:hover { background: var(--bg-elev); color: var(--accent-bright); }
  button:focus-visible, input:focus-visible { outline: 2px solid var(--accent-bright); outline-offset: 1px; }
  button:disabled { opacity: .6; cursor: default; }
  input { width: 100%; min-width: 0; padding: 4px 7px; border: 1px solid var(--border-bright); background: var(--bg-deep); border-radius: 2px; min-height: 29px; caret-color: var(--accent-bright); }
  input::placeholder { color: var(--fg-dim); opacity: 1; }
  .controls { flex: 0 0 auto; padding: 6px; border-bottom: 1px solid var(--border-bright); background: var(--bg-elev); }
  .control-row { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
  .control-row + .control-row { margin-top: 5px; }
  .segment { display: flex; }
  .segment button { font-size: 12px; border-radius: 0; padding-inline: 6px; }
  .segment button + button { border-left: none; }
  .segment button[aria-pressed="true"] { color: var(--bg); background: var(--fg); }
  .scope { margin-left: auto; }
  .filter { flex: 1; width: 100px; }
  .watch-toggle { font-size: 12px; white-space: nowrap; }
  .watch-manager { margin-top: 6px; }
  .watch-results, .watch-list { max-height: 140px; overflow: auto; }
  .watch-results button { width: 100%; display: flex; gap: 5px; justify-content: space-between; align-items: center; text-align: left; font-size: 13px; margin-top: 3px; }
  .watch-results button span:first-child { min-width: 0; overflow-wrap: anywhere; }
  small { display: block; font-size: 12px; color: var(--fg-dim); }
  .watch-list { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
  .watch-list button { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; color: var(--accent-bright); }
  .hint { font-size: 12px; color: var(--fg-dim); margin: 5px 0; }
  .notice { padding: 5px 7px; font-size: 13px; background: var(--bg-elev); border-bottom: 1px solid var(--border-bright); display: flex; align-items: center; gap: 6px; }
  .notice button { margin-left: auto; font-size: 12px; }
  .feed { flex: 1; min-height: 0; overflow: auto; overflow-anchor: none; }
  .feed-space { position: relative; width: 100%; }
  .event { position: absolute; top: 0; left: 0; width: 100%; border-bottom: 1px solid var(--border); }
  .event.reader { position: relative; }
  .event.selected { background: var(--bg-elev); }
  .event-trigger { display: block; text-align: left; width: 100%; padding: 5px 7px; background: transparent; border: none; border-radius: 0; }
  .event-trigger:hover { color: var(--fg); background: var(--bg-elev); }
  .event-top { display: flex; gap: 5px; align-items: baseline; }
  time { font-size: 11px; color: var(--fg-dim); font-variant-numeric: tabular-nums; flex: none; }
  .kind { font-size: 11px; color: var(--accent-bright); flex: none; min-width: 28px; }
  .summary { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  strong { font-weight: 600; }
  .body { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-clamp: 2; overflow: hidden; overflow-wrap: anywhere; margin-top: 2px; }
  .body.expanded { display: block; white-space: pre-wrap; }
  .location { display: block; color: var(--fg-dim); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px; }
  .actions { display: flex; flex-wrap: wrap; gap: 4px; padding: 2px 7px 7px; }
  .actions button { font-size: 12px; max-width: 100%; overflow-wrap: anywhere; }
  .add-puppet { color: var(--accent-bright); border-color: var(--accent); }
  .footer { flex: 0 0 auto; display: flex; gap: 5px; padding: 4px 7px; border-top: 1px solid var(--border-bright); align-items: center; font-size: 12px; color: var(--fg-dim); background: var(--bg-elev); }
  .footer span { flex: 1; }
  .footer button { font-size: 12px; }
  .empty { padding: 12px; font-size: 14px; color: var(--fg-dim); }
  ::selection { background: var(--accent-bright); color: var(--bg); }
</style>
