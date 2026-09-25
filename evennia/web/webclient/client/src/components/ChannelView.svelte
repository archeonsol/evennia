<script lang="ts">
  import { chat } from "../lib/chat.svelte";
  import { dock } from "../lib/dock.svelte";
  import { renderBody, renderSender } from "../lib/markup";
  import { htmlToText } from "../lib/text";
  import { settings } from "../lib/settings.svelte";
  import { pinAfterScroll } from "../lib/autoscroll";
  import { tick, untrack } from "svelte";

  import { focusOnMount } from "../lib/focus";
  let { channelKey = "" }: { channelKey?: string } = $props();

  let draft = $state("");
  let replyTo = $state<string | null>(null);
  let picking = $state<string | null>(null);
  let listEl = $state<HTMLDivElement | null>(null);
  const REACTS = ["👍", "❤", "😂", "🔥", "😮", "😢"];

  const key = $derived(channelKey || chat.active);
  const name = $derived(chat.channels.find((c) => c.key === key)?.name ?? key);
  const msgs = $derived(key ? (chat.messages[key] ?? []) : []);
  const topic = $derived(key ? (chat.topics[key] ?? "") : "");
  const pin = $derived(key ? chat.pins[key] : null);
  const typers = $derived(key ? (chat.typing[key] ?? []) : []);
  const muted = $derived(!!(key && chat.muted[key]));

  let search = $state("");
  let searching = $state(false);
  let configuring = $state(false);
  const color = $derived(key ? chat.channelColor(key) : undefined);
  const mark = $derived(key ? (chat.readMark[key] ?? 0) : 0);
  const q = $derived(search.trim().toLowerCase());
  const shown = $derived(
    q ? msgs.filter((m) => `${m.text} ${m.sender}`.toLowerCase().includes(q)) : msgs,
  );

  // Following new traffic works as it does in the game log. `pinned` is the
  // reader's intent, read from the wheel and from scroll events judged by
  // pinAfterScroll; while it holds, every change to the list ends at the
  // newest message.
  //
  // This used to re-derive "at the bottom" from each scroll event and follow
  // when the message *count* changed. Both failed in play: a second message
  // landing before the scroll event for the first follow read as the reader
  // having scrolled away, and a channel at its 500-message cap never changes
  // count, so a busy channel stopped following for good.
  let listInner = $state<HTMLDivElement | null>(null);
  let pinned = $state(true);
  let unseen = $state(0);
  // The top our code wrote most recently, so a scroll event can tell its own
  // echo from a real upward scroll.
  let lastTop = 0;
  // The reading position across a hide: dockview hides an inactive tab, and a
  // hidden box reports a zero scrollTop.
  let savedTop = 0;
  let hidden = false;
  let seenUid = 0;
  const newest = $derived(msgs.at(-1)?.uid ?? 0);

  function toBottom(): void {
    if (!listEl || !listEl.clientHeight) return;
    const top = Math.max(0, listEl.scrollHeight - listEl.clientHeight);
    lastTop = top;
    listEl.scrollTop = top;
  }
  function jumpToLatest(): void {
    pinned = true;
    unseen = 0;
    toBottom();
  }
  function onWheel(e: WheelEvent) {
    if (e.deltaY < 0 && listEl && listEl.scrollHeight > listEl.clientHeight + 1) pinned = false;
  }
  function onListScroll() {
    if (!listEl || !listEl.clientHeight) return; // a hide zeroes scrollTop; not a real scroll
    savedTop = listEl.scrollTop;
    // Anything but the echo of our own write is the reader moving.
    if (Math.abs(listEl.scrollTop - lastTop) > 1) anchor = null;
    const gap = listEl.scrollHeight - listEl.scrollTop - listEl.clientHeight;
    pinned = pinAfterScroll(pinned, gap, listEl.scrollTop, lastTop);
    if (pinned) unseen = 0;
  }

  // A different channel in the same view starts at its newest message.
  $effect(() => {
    void key;
    untrack(() => {
      pinned = true;
      unseen = 0;
      seenUid = newest;
    });
  });

  // A reader scrolled up keeps the message they are looking at. At the cap an
  // arrival also drops the oldest message, and without this the whole list
  // slid up under them by that message's height. Recorded before the list
  // re-renders, restored after. The offset is measured once and kept until
  // the reader scrolls: re-measuring on every arrival would start each
  // correction from a rounded scrollTop, and the rounding would add up.
  let anchor: { uid: string; offset: number } | null = null;
  $effect.pre(() => {
    void shown;
    untrack(() => {
      if (!listEl || pinned || !listEl.clientHeight) {
        anchor = null;
        return;
      }
      if (anchor && listEl.querySelector(`.msg[data-uid="${anchor.uid}"]`)) return;
      anchor = null;
      const top = listEl.getBoundingClientRect().top;
      for (const node of listEl.querySelectorAll<HTMLElement>(".msg")) {
        const r = node.getBoundingClientRect();
        if (r.bottom > top) {
          anchor = { uid: node.dataset.uid ?? "", offset: r.top - top };
          break;
        }
      }
    });
  });
  $effect(() => {
    void shown;
    untrack(() => {
      if (!listEl) return;
      if (pinned) {
        toBottom();
        return;
      }
      const node = anchor ? listEl.querySelector<HTMLElement>(`.msg[data-uid="${anchor.uid}"]`) : null;
      if (!node || !anchor) return;
      const delta = node.getBoundingClientRect().top - listEl.getBoundingClientRect().top - anchor.offset;
      if (Math.abs(delta) > 0.5) {
        lastTop = listEl.scrollTop + delta;
        listEl.scrollTop = lastTop;
      }
    });
  });

  // Arrivals while scrolled up are counted on a "new messages" bar.
  $effect(() => {
    const n = newest;
    untrack(() => {
      if (pinned || n <= seenUid) {
        if (pinned) unseen = 0;
        seenUid = Math.max(seenUid, n);
        return;
      }
      unseen += msgs.filter((m) => m.uid > seenUid).length;
      seenUid = n;
    });
  });

  // Size changes the message list does not cause: the panel shown again after
  // being a background tab, a resize, an image or a web font arriving late.
  $effect(() => {
    const box = listEl;
    const inner = listInner;
    if (!box) return;
    const ro = new ResizeObserver(() => {
      if (!box.clientHeight) {
        hidden = true;
        return;
      }
      if (pinned) toBottom();
      else if (hidden) {
        lastTop = savedTop;
        box.scrollTop = savedTop;
      }
      hidden = false;
    });
    ro.observe(box);
    if (inner) ro.observe(inner);
    return () => ro.disconnect();
  });

  // The message list is one Tab stop. Arrow keys move between messages, and
  // only the current message's reply/react/pin buttons are in the Tab order,
  // so reaching the message box no longer means tabbing through three
  // buttons per message. -1 means "the newest".
  let cur = $state(-1);
  const curIdx = $derived(cur < 0 || cur >= shown.length ? shown.length - 1 : cur);
  async function onMsgKey(e: KeyboardEvent) {
    if (e.key === "Escape" && picking) {
      e.preventDefault();
      picking = null;
      await tick();
      listEl?.querySelector<HTMLElement>('.msg[tabindex="0"] .mt[aria-expanded]')?.focus();
      return;
    }
    const n = shown.length;
    const onList = e.target === listEl;
    if (!n || (!onList && !(e.target as HTMLElement).classList?.contains("msg"))) return;
    let next = curIdx;
    // From the list itself (where Alt+C lands), any arrow enters at the
    // current message.
    if (onList && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      e.preventDefault();
      listEl?.querySelectorAll<HTMLElement>(".msg")[curIdx]?.focus();
      return;
    }
    if (e.key === "ArrowDown") next = Math.min(n - 1, curIdx + 1);
    else if (e.key === "ArrowUp") next = Math.max(0, curIdx - 1);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = n - 1;
    else return;
    e.preventDefault();
    cur = next === n - 1 ? -1 : next;
    await tick();
    listEl?.querySelectorAll<HTMLElement>(".msg")[next]?.focus();
  }
  function spoken(m: { sender: string; senderHtml?: string; html?: string; text: string; ts: number }) {
    return `${htmlToText(renderSender(m.senderHtml, m.sender))}, ${fmt(m.ts)}: ${htmlToText(renderBody(m.html, m.text))}`;
  }

  function send() {
    let t = draft.trim();
    if (!t) return;
    if (replyTo) t = `@${replyTo}: ${t}`;
    chat.post(key, t);
    draft = "";
    replyTo = null;
  }
  function onKey(e: KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      send();
    } else if (e.key === "Escape") {
      replyTo = null;
    }
  }
  // The picker unmounts under the focused emoji; hand focus back to the
  // message's react button rather than the page body.
  async function doReact(id: string, emoji: string) {
    chat.react(id, emoji);
    picking = null;
    await tick();
    listEl?.querySelector<HTMLElement>('.msg[tabindex="0"] .mt[aria-expanded]')?.focus();
  }
  function fmt(ts: number) {
    const d = new Date(ts);
    const p = (n: number) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}`;
  }
</script>

<div class="cv">
  {#if key}
    <div class="head">
      <span class="title glow-text" style={color ? `color:${color}` : ""}>{name}</span>
      {#if topic}<span class="topic">{topic}</span>{/if}
      <span class="tools">
        <button class="t" class:on={configuring} onclick={() => (configuring = !configuring)} title="channel settings"
          aria-label="{name} channel settings" aria-expanded={configuring}>Settings</button>
        <button class="t" class:on={searching} onclick={() => { searching = !searching; if (!searching) search = ""; }} title="search"
          aria-label="search {name}" aria-pressed={searching}>Search</button>
        <button class="t" class:on={muted} onclick={() => chat.toggleMute(key)} title="mute channel" aria-pressed={muted}>
          {muted ? "Muted" : "Mute"}
        </button>
        <button class="t" onclick={() => dock.openChannel(key, name)} title="pop out" aria-label="pop out {name}">Pop out</button>
      </span>
    </div>

    {#if configuring}
      <div class="cfg">
        <label class="cfg-row">
          <span>Colour</span>
          <input type="color" value={color ?? "#c9a44c"}
            oninput={(e) => chat.setChannelColor(key, e.currentTarget.value)} aria-label="channel colour" />
        </label>
        <label class="cfg-row">
          <span>Alerts</span>
          <select value={chat.channelNotify(key)} onchange={(e) => chat.setChannelNotify(key, e.currentTarget.value)} aria-label="{name} alerts">
            <option value="all">Every message</option>
            <option value="mention">Mentions only</option>
            <option value="none">None</option>
          </select>
        </label>
      </div>
    {/if}

    {#if searching}
      <div class="csearch">
        <span class="s-glyph" aria-hidden="true">⌕</span>
        <input bind:value={search} placeholder="search {name}…" aria-label="search channel" use:focusOnMount />
        <span class="cnt" aria-live="polite">{shown.length}<span class="sr-only"> matching messages</span></span>
      </div>
    {/if}

    {#if pin}
      <div class="pin">
        <span class="pin-tag">pinned</span>
        <span class="pin-text">{@html renderBody(undefined, pin.text)}</span>
        {#if chat.staff}<button class="pin-x" onclick={() => chat.unpin(key)} aria-label="unpin">×</button>{/if}
      </div>
    {/if}

    <!-- Live only when channel traffic is not already echoed to the terminal,
         where the announcer speaks it; otherwise each message would be heard twice.
         Arrow keys move between messages (roving tabindex). -->
    <div class="msgs-wrap">
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
    <div
      class="msgs"
      bind:this={listEl}
      onscroll={onListScroll}
      onwheel={onWheel}
      onkeydown={onMsgKey}
      role="log"
      aria-live={settings.channelEcho ? "off" : "polite"}
      aria-label="{name} messages"
      data-focus-region="channels"
      tabindex="-1"
    >
      <div class="msgs-inner" bind:this={listInner}>
      {#each shown as m, i (m.uid)}
        {@const isNew = !searching && mark > 0 && m.ts > mark && (i === 0 || shown[i - 1].ts <= mark)}
        {@const grouped = i > 0 && shown[i - 1].sender === m.sender && m.ts - shown[i - 1].ts < 300000}
        {@const current = i === curIdx}
        {#if isNew}<div class="divider" role="separator" aria-label="new messages"><span aria-hidden="true">new</span></div>{/if}
        <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
        <div class="msg" class:disc={m.platform === "discord"} class:grouped data-uid={m.uid}
          role="article" aria-label={spoken(m)} tabindex={current ? 0 : -1}
          onfocus={() => (cur = i === shown.length - 1 ? -1 : i)}>
          {#if !grouped}
            <span class="mts">{fmt(m.ts)}</span>
            <span class="sender">{@html renderSender(m.senderHtml, m.sender)}</span>
          {/if}
          <span class="text">{@html renderBody(m.html, m.text)}</span>
          {#if Object.keys(m.reactions).length}
            <span class="reacts">
              {#each Object.entries(m.reactions) as [emoji, count]}
                <button class="react" tabindex={current ? 0 : -1} onclick={() => doReact(m.msgId, emoji)}
                  aria-label="{emoji} {count}, react with {emoji}">{emoji}&nbsp;{count}</button>
              {/each}
            </span>
          {/if}
          <span class="mtools">
            <button class="mt" tabindex={current ? 0 : -1} onclick={() => (replyTo = m.sender)} title="reply" aria-label="reply to {m.sender}">↩</button>
            {#if m.msgId}
              <button class="mt" tabindex={current ? 0 : -1} onclick={() => (picking = picking === m.msgId ? null : m.msgId)} title="react"
                aria-label="react to {m.sender}" aria-expanded={picking === m.msgId}>＋</button>
            {/if}
            {#if chat.staff}
              <button class="mt" tabindex={current ? 0 : -1} onclick={() => chat.pin(key, m.text)} title="pin" aria-label="pin message from {m.sender}">⚑</button>
            {/if}
            {#if picking === m.msgId}
              <span class="picker" role="group" aria-label="reactions">
                {#each REACTS as e}<button onclick={() => doReact(m.msgId, e)} aria-label="react {e}">{e}</button>{/each}
              </span>
            {/if}
          </span>
        </div>
      {/each}
      {#if !shown.length}<p class="empty">{q ? "no matches" : "no traffic"}</p>{/if}
      </div>
    </div>
    {#if !pinned && unseen > 0}
      <button class="latest" onclick={jumpToLatest}>{unseen} new message{unseen === 1 ? "" : "s"} ↓</button>
    {/if}
    </div>

    {#if typers.length}
      <div class="typing">{typers.join(", ")} {typers.length === 1 ? "is" : "are"} transmitting…</div>
    {/if}
    {#if replyTo}
      <div class="replybar">
        <span>↩ replying to {replyTo}</span>
        <button onclick={() => (replyTo = null)} aria-label="cancel reply to {replyTo}">×</button>
      </div>
    {/if}
    <div class="composer">
      <span class="chev glow-text" aria-hidden="true">❯</span>
      <input bind:value={draft} onkeydown={onKey} placeholder="transmit to {name}…" aria-label="Message {name}" autocomplete="off" />
    </div>
  {:else}
    <p class="empty">No channel.</p>
  {/if}
</div>

<style>
  .cv { display: flex; flex-direction: column; min-height: 0; height: 100%; background: var(--bg-elev); }
  .head { display: flex; align-items: baseline; gap: 1ch; padding: 5px 10px; border-bottom: 1px solid var(--border); flex: 0 0 auto; }
  .title { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.16em; font-size: 0.78rem; }
  .topic { color: var(--fg-dim); font-size: 0.72rem; font-style: italic; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tools { margin-left: auto; display: flex; gap: 4px; flex-wrap: wrap; justify-content: flex-end; }
  .t { background: none; border: 1px solid var(--border-bright); color: var(--fg-dim); font-family: inherit; font-size: 0.7rem; padding: 1px 8px; cursor: pointer; white-space: nowrap; }
  .t:hover, .t.on { color: var(--accent-bright); border-color: var(--accent); }
  .pin { display: flex; align-items: baseline; gap: 0.6ch; padding: 3px 10px; border-bottom: 1px solid var(--border); background: color-mix(in srgb, var(--gold) 8%, transparent); font-size: 0.76rem; }
  .pin-tag { color: var(--gold); font-size: 0.58rem; letter-spacing: 0.2em; text-transform: uppercase; }
  .pin-text { color: var(--fg); flex: 1; }
  .pin-x { background: none; border: none; color: var(--fg-faint); cursor: pointer; }
  .cfg { display: flex; flex-wrap: wrap; gap: 12px; padding: 6px 10px; border-bottom: 1px solid var(--accent); background: var(--bg-deep); flex: 0 0 auto; }
  .cfg-row { display: flex; align-items: center; gap: 6px; font-size: 0.72rem; color: var(--fg-dim); }
  .cfg-row input[type="color"] { width: 2.2em; height: 1.5em; padding: 1px; border: 1px solid var(--border-bright); background: var(--bg); cursor: pointer; }
  .cfg-row select { background: var(--bg); color: var(--fg); border: 1px solid var(--border-bright); font-family: inherit; font-size: 0.72rem; padding: 2px 4px; }
  .csearch { display: flex; align-items: center; gap: 0.5ch; padding: 3px 10px; border-bottom: 1px solid var(--accent); background: var(--bg-deep); flex: 0 0 auto; }
  .csearch .s-glyph { color: var(--accent); }
  .csearch input { flex: 1; background: transparent; border: none; outline: none; color: var(--fg); font-family: inherit; font-size: 0.82rem; }
  .csearch .cnt { color: var(--fg-dim); font-size: 0.72rem; }
  .divider { display: flex; align-items: center; gap: 0.6ch; margin: 4px 0; color: var(--accent-bright); font-size: 0.6rem; letter-spacing: 0.24em; text-transform: uppercase; }
  .divider::before, .divider::after { content: ""; flex: 1; height: 1px; background: color-mix(in srgb, var(--accent) 50%, transparent); }
  .msgs-wrap { flex: 1; min-height: 0; position: relative; display: flex; flex-direction: column; }
  /* This view anchors a scrolled-up reader itself; the browser's own
     anchoring would correct the same shift a second time. */
  .msgs { flex: 1; min-height: 0; overflow-y: auto; overflow-anchor: none; padding: 6px 10px; line-height: 1.5; }
  .latest {
    position: absolute; right: 14px; bottom: 8px; z-index: 5;
    background: var(--bg-deep); border: 1px solid var(--accent); color: var(--accent-bright);
    font-family: inherit; font-size: 0.7rem; letter-spacing: 0.06em; padding: 3px 10px; min-height: 24px; cursor: pointer;
  }
  .msg { padding: 1px 0; font-size: 0.85rem; word-break: break-word; }
  .msg.grouped { padding-left: 2.4ch; }
  .mts { color: var(--fg-faint); font-size: 0.72em; margin-right: 0.6ch; user-select: none; }
  .sender { color: var(--gold); margin-right: 0.6ch; }
  .msg.disc .sender { color: var(--accent-bright); }
  .text { color: var(--fg); white-space: pre-wrap; }
  .reacts { margin-left: 0.5ch; }
  .react { border: 1px solid var(--border-bright); background: var(--bg); color: var(--fg-dim); font-family: inherit; font-size: 0.72em; padding: 0 5px; margin-left: 3px; cursor: pointer; }
  .react:hover { border-color: var(--accent); color: var(--fg); }
  .mtools { position: relative; margin-left: 4px; white-space: nowrap; }
  .mt { background: none; border: none; color: var(--fg-faint); cursor: pointer; font-size: 0.8em; opacity: 0; transition: opacity 0.1s; }
  .msg:hover .mt, .msg:focus-within .mt, .msg:focus .mt { opacity: 1; }
  .mt, .t { min-height: 24px; min-width: 24px; }
  .mt:hover { color: var(--accent-bright); }
  .picker { position: absolute; right: 0; bottom: 1.4em; z-index: 5; display: flex; gap: 2px; padding: 3px 4px; background: var(--bg-elev); border: 1px solid var(--accent); }
  .picker button { background: none; border: none; cursor: pointer; font-size: 0.95em; padding: 1px 3px; }
  .picker button:hover { background: color-mix(in srgb, var(--accent) 20%, transparent); }
  .empty { color: var(--fg-faint); font-style: italic; }
  .typing { padding: 2px 10px; color: var(--accent-bright); font-size: 0.72rem; font-style: italic; border-top: 1px solid var(--border); flex: 0 0 auto; }
  .replybar { display: flex; justify-content: space-between; align-items: center; padding: 2px 10px; font-size: 0.72rem; color: var(--gold); border-top: 1px solid var(--border); }
  .replybar button { background: none; border: none; color: var(--fg-faint); cursor: pointer; }
  .composer { display: flex; align-items: center; gap: 0.6rem; padding: 6px 10px; border-top: 1px solid var(--accent); flex: 0 0 auto; }
  .chev { color: var(--accent-bright); }
  .composer input { flex: 1; background: transparent; border: none; outline: none; color: var(--fg); font-family: inherit; font-size: 0.85rem; caret-color: var(--accent-bright); }
  .composer input::placeholder { color: var(--fg-faint); font-style: italic; }
</style>
