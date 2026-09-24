<script lang="ts">
  import { session } from "../lib/session.svelte";
  import { logview, CATS } from "../lib/logview.svelte";
  import { keybinds } from "../lib/keybinds.svelte";
  import { typewriter, markBacklog } from "../lib/typewriter";
  import { pinAfterScroll } from "../lib/autoscroll";
  import { settings } from "../lib/settings.svelte";
  import { buildTranscript, type TranscriptFormat } from "../lib/transcript";
  import { onMount, untrack } from "svelte";

  //: What each download format is for, in the order the menu offers them.
  //: HTML leads because it is the only one that keeps the colours *and* opens
  //: anywhere; .txt is last because it is the one that throws them away.
  const SAVE_FORMATS: { id: TranscriptFormat; label: string; hint: string }[] = [
    { id: "html", label: "HTML", hint: "colours, opens in a browser" },
    { id: "ansi", label: "ANSI", hint: "colour codes, for a terminal or MUD client" },
    { id: "txt", label: "Text", hint: "plain, no colour" },
  ];

  let el = $state<HTMLDivElement | null>(null);
  let searchInput = $state<HTMLInputElement | null>(null);
  let pinned = $state(true);
  let matchPos = $state(0);
  // Scroll position across a panel hide; see the ResizeObserver below.
  let savedTop = 0;
  let hidden = false;
  // The top our code wrote most recently, so a scroll event can tell its own
  // echo from a real upward scroll; see pinAfterScroll.
  let lastTop = 0;
  let saveOpen = $state(false);
  let saveEl = $state<HTMLDivElement | null>(null);

  // Freeze the existing backlog so only lines that arrive after mount type in.
  onMount(() => markBacklog(session.lines.at(-1)?.id ?? -1));

  function scrollToBottom() {
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    lastTop = el.scrollTop;
  }

  // Follow the newest line to the bottom as its characters reveal.
  function keepPinned() {
    if (pinned && !logview.searchOpen) scrollToBottom();
  }

  // Per-line reveal duration; reduce-motion / screenreader force it instant.
  const twDuration = $derived(
    settings.reduceMotion || settings.screenreader ? 0 : settings.typewriterMs,
  );

  // Every filter on is the default and the common case, and it means "no
  // filtering" — so hand back the array itself rather than rebuilding a copy of
  // the whole scrollback on every appended line.
  const allCats = $derived(CATS.every((c) => logview.filters[c.id]));
  const filtered = $derived(
    allCats ? session.lines : session.lines.filter((l) => logview.filters[l.cat]),
  );
  const query = $derived(logview.search.trim().toLowerCase());
  const matchIds = $derived(
    query ? filtered.filter((l) => l.text.toLowerCase().includes(query)).map((l) => l.id) : [],
  );
  // Membership is tested once per rendered line; a linear scan per line makes
  // marking hits quadratic in the scrollback.
  const matchSet = $derived(new Set(matchIds));

  function pad(n: number) {
    return String(n).padStart(2, "0");
  }
  function hhmmss(ts: number) {
    const d = new Date(ts);
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  // Autoscroll to newest unless the user scrolled up or is searching. While
  // scrolled up, arrivals are counted on a "jump to latest" bar instead.
  let unseen = $state(0);
  let seenCount = 0;
  $effect(() => {
    const n = session.lines.length;
    const grew = n - seenCount;
    seenCount = n;
    if (pinned && !logview.searchOpen) {
      scrollToBottom();
      unseen = 0;
    } else if (grew > 0) {
      unseen = untrack(() => unseen) + grew;
    }
  });
  $effect(() => {
    if (pinned) unseen = 0;
  });
  function jumpToLatest() {
    pinned = true;
    unseen = 0;
    scrollToBottom();
  }

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

  // A wheel up over the log is the user leaving the bottom. Reading intent from
  // the input, not from scroll positions, is the only signal a same-frame line
  // append cannot overwrite: its effect may write scrollTop after the wheel but
  // before the wheel's scroll event is delivered, hiding the movement from
  // pinAfterScroll until the next tick.
  function onWheel(e: WheelEvent) {
    if (e.deltaY < 0) pinned = false;
  }

  function onScroll() {
    if (!el || !el.clientHeight) return; // a hide zeroes scrollTop; not a real scroll
    savedTop = el.scrollTop;
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
    pinned = pinAfterScroll(pinned, gap, el.scrollTop, lastTop);
  }

  // Restore the reading position when the panel comes back.
  //
  // dockview hides an inactive panel rather than destroying it, and a hidden
  // element's scrollTop is reset to 0. Nothing puts it back on the way in: the
  // autoscroll effect only runs when the line count changes, so switching to
  // another tab and back left the log showing the top of a 5000-line buffer.
  // A zero-height box is the hide; the next non-zero one is the return.
  $effect(() => {
    const node = el;
    if (!node) return;
    const ro = new ResizeObserver(() => {
      if (!node.clientHeight) {
        hidden = true;
        return;
      }
      if (!hidden) return;
      hidden = false;
      node.scrollTop = pinned ? node.scrollHeight : savedTop;
      lastTop = node.scrollTop;
    });
    ro.observe(node);
    return () => ro.disconnect();
  });

  function step(d: number) {
    const n = matchIds.length;
    if (n) matchPos = (matchPos + d + n) % n;
  }
  function onGlobalKey(e: KeyboardEvent) {
    if (keybinds.match(e, "search")) {
      e.preventDefault();
      logview.searchOpen = true;
    } else if (e.key === "Escape") {
      if (saveOpen) saveOpen = false;
      else if (logview.searchOpen) logview.searchOpen = false;
    }
  }

  // Anything outside the menu dismisses it. The listener is on the window in
  // the capture phase so a click on some other panel closes it too, not just
  // one that happens to land in the log.
  function onGlobalPointer(e: PointerEvent) {
    if (!saveOpen) return;
    if (!(e.target instanceof Node) || !saveEl?.contains(e.target)) saveOpen = false;
  }

  // Reading the log with the keyboard, typing a command should just work: a
  // printable key (or Escape) goes back to the command line, and the key
  // itself lands there because focus moves before the character is inserted.
  // A screen reader in browse mode keeps its letter keys; they never get here.
  function onLogKey(e: KeyboardEvent) {
    const printable = e.key.length === 1 && e.key !== " " && !e.ctrlKey && !e.metaKey && !e.altKey;
    if (!printable && e.key !== "Escape") return;
    const input = document.querySelector<HTMLElement>('[data-focus-region="input"]');
    if (!input) return;
    if (e.key === "Escape") e.preventDefault();
    input.focus();
  }

  // Opening the save list moves focus into it, so Enter on the button and
  // Enter again downloads, instead of tabbing past the rest of the toolbar.
  function focusFirst(node: HTMLElement, first: boolean) {
    if (first) node.focus();
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
  function downloadLog(format: TranscriptFormat) {
    saveOpen = false;
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    const file = buildTranscript(session.lines, format, {
      timestamps: logview.timestamps,
      title: `Underspire log ${stamp}`,
    });
    const url = URL.createObjectURL(new Blob([file.body], { type: file.mime }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `underspire-log-${stamp}.${file.ext}`;
    a.click();
    URL.revokeObjectURL(url);
  }
</script>

<svelte:window onkeydown={onGlobalKey} onpointerdowncapture={onGlobalPointer} />

<div class="log-wrap">
  <div class="log-bar" role="toolbar" aria-label="Output filters and tools">
    <div class="chips">
      {#each CATS as c}
        <button
          class="chip"
          class:off={!logview.filters[c.id]}
          aria-pressed={!!logview.filters[c.id]}
          onclick={() => logview.toggle(c.id)}
          title="show {c.label} lines"
        ><span class="lamp" aria-hidden="true"></span>{c.label}</button>
      {/each}
    </div>
    <button class="tool" class:on={logview.timestamps} onclick={() => (logview.timestamps = !logview.timestamps)}
      title="timestamps" aria-label="timestamps" aria-pressed={logview.timestamps}>Times</button>
    <button class="tool" class:on={logview.searchOpen} onclick={() => (logview.searchOpen = !logview.searchOpen)}
      title="search (Ctrl-F)" aria-label="search scrollback" aria-pressed={logview.searchOpen}>Search</button>
    <div class="save" bind:this={saveEl}>
      <button class="tool" class:on={saveOpen} onclick={() => (saveOpen = !saveOpen)}
        title="save log" aria-label="save log" aria-expanded={saveOpen}>Save</button>
      {#if saveOpen}
        <div class="save-menu">
          {#each SAVE_FORMATS as f}
            <button onclick={() => downloadLog(f.id)} use:focusFirst={f === SAVE_FORMATS[0]}>
              <span class="fmt">{f.label}</span><span class="fmt-hint">{f.hint}</span>
            </button>
          {/each}
        </div>
      {/if}
    </div>
    <button class="tool" onclick={clearBuffer} title="clear buffer" aria-label="clear buffer">Clear</button>
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

  <!-- Focusable on purpose: a scrollable region must be reachable by keyboard,
       and Alt+O lands here. Its key handler only hands typing back. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
  <div
    class="game-log"
    bind:this={el}
    onscroll={onScroll}
    onwheel={onWheel}
    onkeydown={onLogKey}
    role="log"
    aria-live="off"
    aria-label="Game output"
    tabindex="0"
    data-focus-region="output"
  >
    {#each filtered as line (line.id)}
      <div
        class="log-line"
        data-cat={line.cat}
        data-lid={line.id}
        class:hit={matchSet.has(line.id)}
        class:active={matchIds[matchPos] === line.id}
      >
        {#if logview.timestamps}<span class="ts">{hhmmss(line.ts)}</span>{/if}<span class="body" use:typewriter={{ id: line.id, durationMs: twDuration, onstep: keepPinned }}>{@html line.html}</span>
      </div>
    {/each}
  </div>
  {#if !pinned && unseen > 0 && !logview.searchOpen}
    <button class="latest" onclick={jumpToLatest}>{unseen} new line{unseen === 1 ? "" : "s"} ↓</button>
  {/if}
</div>

<style>
  .log-wrap { display: flex; flex-direction: column; height: 100%; min-height: 0; position: relative; }
  .log-bar {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
    padding: 3px 8px; border-bottom: 1px solid var(--border);
    background: var(--bg-elev); flex: 0 0 auto;
  }
  .chips { display: flex; gap: 4px; flex: 1 1 auto; min-width: min(100%, 16rem); flex-wrap: wrap; }
  /* Category toggles: the light shows whether that kind of line is shown. */
  .chip {
    display: inline-flex; align-items: center; gap: 6px;
    background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg);
    font-family: inherit; font-size: 0.72rem;
    padding: 1px 9px 1px 7px; cursor: pointer; min-height: 24px;
  }
  .chip:hover { color: var(--fg); border-color: var(--accent); }
  .lamp { width: 7px; height: 7px; border-radius: 50%; background: var(--accent-bright); flex: 0 0 auto; }
  .chip.off { color: var(--fg-faint); border-color: var(--border); }
  .chip.off .lamp { background: transparent; box-shadow: none; outline: 1px solid var(--border-bright); outline-offset: -1px; }
  .tool {
    background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.72rem; padding: 0 9px; cursor: pointer; min-height: 24px; min-width: 24px;
  }
  .tool:hover, .tool.on { color: var(--accent-bright); border-color: var(--accent); }

  .save { position: relative; display: flex; }
  .save-menu {
    position: absolute; top: calc(100% + 3px); right: 0; z-index: 20;
    display: flex; flex-direction: column; min-width: 15em;
    background: var(--bg-deep); border: 1px solid var(--accent);
  }
  .save-menu button {
    display: flex; align-items: baseline; gap: 7px; text-align: left;
    background: none; border: none; color: var(--fg-dim);
    font-family: inherit; font-size: 0.72rem; padding: 5px 9px; cursor: pointer;
  }
  .save-menu button:hover { background: var(--bg-elev); color: var(--accent-bright); }
  .fmt { letter-spacing: 0.12em; text-transform: uppercase; flex: 0 0 3.2em; }
  .fmt-hint { color: var(--fg-faint); font-size: 0.66rem; }

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

  .latest {
    position: absolute; right: 18px; bottom: 12px; background: var(--bg-deep); border: 1px solid var(--accent);
    color: var(--accent-bright); font-family: inherit; font-size: 0.7rem; letter-spacing: 0.06em;
    padding: 3px 10px; min-height: 24px; cursor: pointer; z-index: 5;
  }
  .game-log { overflow-y: auto; padding: 0.7rem 1rem; line-height: var(--shell-line-height, 1.5); flex: 1; }
  .log-line { white-space: pre-wrap; word-break: break-word; }
  .log-line .ts { color: var(--fg-faint); margin-right: 0.8ch; font-size: 0.82em; user-select: none; }
  /* Category accents - only the standouts get a marker, to avoid noise. */
  .log-line[data-cat="combat"] { border-left: 2px solid var(--alert); padding-left: 7px; margin-left: -9px; }
  .log-line[data-cat="comms"] { border-left: 2px solid var(--gold); padding-left: 7px; margin-left: -9px; }
  .log-line.hit { background: color-mix(in srgb, var(--accent) 14%, transparent); }
  .log-line.active { background: color-mix(in srgb, var(--accent) 30%, transparent); outline: 1px solid var(--accent); }
</style>
