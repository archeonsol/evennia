<script lang="ts">
  import { chat } from "../lib/chat.svelte";
  import { dock } from "../lib/dock.svelte";

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

  $effect(() => {
    void msgs.length;
    if (listEl) listEl.scrollTop = listEl.scrollHeight;
  });

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
  function doReact(id: string, emoji: string) {
    chat.react(id, emoji);
    picking = null;
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
        <button class="t" class:on={configuring} onclick={() => (configuring = !configuring)} title="channel settings" aria-label="channel settings">⚙</button>
        <button class="t" class:on={searching} onclick={() => { searching = !searching; if (!searching) search = ""; }} title="search" aria-label="search">⌕</button>
        <button class="t" class:on={muted} onclick={() => chat.toggleMute(key)} title="mute channel">
          {muted ? "muted" : "mute"}
        </button>
        <button class="t" onclick={() => dock.openChannel(key, name)} title="pop out" aria-label="pop out">⇱</button>
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
          <select value={chat.channelNotify(key)} onchange={(e) => chat.setChannelNotify(key, e.currentTarget.value)}>
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
        <!-- svelte-ignore a11y_autofocus -->
        <input bind:value={search} placeholder="search {name}…" aria-label="search channel" autofocus />
        <span class="cnt">{shown.length}</span>
      </div>
    {/if}

    {#if pin}
      <div class="pin">
        <span class="pin-tag">pinned</span>
        <span class="pin-text">{pin.text}</span>
        {#if chat.staff}<button class="pin-x" onclick={() => chat.unpin(key)} aria-label="unpin">×</button>{/if}
      </div>
    {/if}

    <div class="msgs" bind:this={listEl}>
      {#each shown as m, i (m.msgId || `${m.ts}-${m.sender}`)}
        {@const isNew = !searching && mark > 0 && m.ts > mark && (i === 0 || shown[i - 1].ts <= mark)}
        {@const grouped = i > 0 && shown[i - 1].sender === m.sender && m.ts - shown[i - 1].ts < 300000}
        {#if isNew}<div class="divider"><span>new</span></div>{/if}
        <div class="msg" class:disc={m.platform === "discord"} class:grouped>
          {#if !grouped}
            <span class="mts">{fmt(m.ts)}</span>
            <span class="sender">{m.sender}</span>
          {/if}
          <span class="text">{@html m.html ?? m.text}</span>
          {#if Object.keys(m.reactions).length}
            <span class="reacts">
              {#each Object.entries(m.reactions) as [emoji, count]}
                <button class="react" onclick={() => doReact(m.msgId, emoji)}>{emoji}&nbsp;{count}</button>
              {/each}
            </span>
          {/if}
          <span class="mtools">
            <button class="mt" onclick={() => (replyTo = m.sender)} title="reply" aria-label="reply">↩</button>
            {#if m.msgId}
              <button class="mt" onclick={() => (picking = picking === m.msgId ? null : m.msgId)} title="react" aria-label="react">＋</button>
            {/if}
            {#if chat.staff}
              <button class="mt" onclick={() => chat.pin(key, m.text)} title="pin" aria-label="pin">⚑</button>
            {/if}
            {#if picking === m.msgId}
              <span class="picker">
                {#each REACTS as e}<button onclick={() => doReact(m.msgId, e)}>{e}</button>{/each}
              </span>
            {/if}
          </span>
        </div>
      {/each}
      {#if !shown.length}<p class="empty">{q ? "no matches" : "no traffic"}</p>{/if}
    </div>

    {#if typers.length}
      <div class="typing">{typers.join(", ")} {typers.length === 1 ? "is" : "are"} transmitting…</div>
    {/if}
    {#if replyTo}
      <div class="replybar">
        <span>↩ replying to {replyTo}</span>
        <button onclick={() => (replyTo = null)} aria-label="cancel reply">×</button>
      </div>
    {/if}
    <div class="composer">
      <span class="chev glow-text" aria-hidden="true">❯</span>
      <input bind:value={draft} onkeydown={onKey} placeholder="transmit to {name}…" aria-label="channel message" autocomplete="off" />
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
  .tools { margin-left: auto; display: flex; gap: 4px; }
  .t { background: none; border: 1px solid var(--border-bright); color: var(--fg-dim); font-family: inherit; font-size: 0.6rem; letter-spacing: 0.1em; text-transform: uppercase; padding: 1px 6px; cursor: pointer; }
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
  .divider { display: flex; align-items: center; gap: 0.6ch; margin: 4px 0; color: var(--accent); font-size: 0.6rem; letter-spacing: 0.24em; text-transform: uppercase; }
  .divider::before, .divider::after { content: ""; flex: 1; height: 1px; background: color-mix(in srgb, var(--accent) 50%, transparent); }
  .msgs { flex: 1; overflow-y: auto; padding: 6px 10px; line-height: 1.5; }
  .msg { padding: 1px 0; font-size: 0.85rem; word-break: break-word; }
  .msg.grouped { padding-left: 2.4ch; }
  .mts { color: var(--fg-faint); font-size: 0.72em; margin-right: 0.6ch; user-select: none; }
  .sender { color: var(--gold); margin-right: 0.6ch; }
  .msg.disc .sender { color: var(--accent); }
  .text { color: var(--fg); white-space: pre-wrap; }
  .reacts { margin-left: 0.5ch; }
  .react { border: 1px solid var(--border-bright); background: var(--bg); color: var(--fg-dim); font-family: inherit; font-size: 0.72em; padding: 0 5px; margin-left: 3px; cursor: pointer; }
  .react:hover { border-color: var(--accent); color: var(--fg); }
  .mtools { position: relative; margin-left: 4px; white-space: nowrap; }
  .mt { background: none; border: none; color: var(--fg-faint); cursor: pointer; font-size: 0.8em; opacity: 0; transition: opacity 0.1s; }
  .msg:hover .mt { opacity: 1; }
  .mt:hover { color: var(--accent-bright); }
  .picker { position: absolute; right: 0; bottom: 1.4em; z-index: 5; display: flex; gap: 2px; padding: 3px 4px; background: var(--bg-elev); border: 1px solid var(--accent); }
  .picker button { background: none; border: none; cursor: pointer; font-size: 0.95em; padding: 1px 3px; }
  .picker button:hover { background: color-mix(in srgb, var(--accent) 20%, transparent); }
  .empty { color: var(--fg-faint); font-style: italic; }
  .typing { padding: 2px 10px; color: var(--accent); font-size: 0.72rem; font-style: italic; border-top: 1px solid var(--border); flex: 0 0 auto; }
  .replybar { display: flex; justify-content: space-between; align-items: center; padding: 2px 10px; font-size: 0.72rem; color: var(--gold); border-top: 1px solid var(--border); }
  .replybar button { background: none; border: none; color: var(--fg-faint); cursor: pointer; }
  .composer { display: flex; align-items: center; gap: 0.6rem; padding: 6px 10px; border-top: 1px solid var(--accent); flex: 0 0 auto; }
  .chev { color: var(--accent-bright); }
  .composer input { flex: 1; background: transparent; border: none; outline: none; color: var(--fg); font-family: inherit; font-size: 0.85rem; caret-color: var(--accent-bright); }
  .composer input::placeholder { color: var(--fg-faint); font-style: italic; }
</style>
