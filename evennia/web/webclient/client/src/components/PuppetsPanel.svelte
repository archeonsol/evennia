<script lang="ts">
  import { connection } from "../lib/evennia.svelte";
  import { looksLikeHtml, pipeToHtml } from "../lib/markup";
  import { puppets } from "../lib/puppets.svelte";

  import { focusOnMount } from "../lib/focus";
  const toHtml = (body: string) => (looksLikeHtml(body) ? body : pipeToHtml(body));

  let input = $state("");
  let activeId = $state<number | null>(null);
  let feedEl: HTMLDivElement | null = $state(null);

  // Reactive on both the local selection and the (SvelteMap) feed store.
  const active = $derived(activeId == null ? null : puppets.feeds.get(String(activeId)) ?? null);

  function open(npcId: number) {
    activeId = npcId;
    puppets.setActive(npcId);
  }
  function close() {
    activeId = null;
    puppets.setActive(null);
  }

  function toPlain(body: string): string {
    const el = document.createElement("div");
    el.innerHTML = toHtml(body);
    return el.textContent ?? "";
  }
  function clearFeed() {
    if (active && active.feed.length && confirm(`Clear ${active.name}'s buffer?`)) {
      puppets.clearFeed(active.npcId);
    }
  }
  function downloadFeed() {
    if (!active) return;
    const text = active.feed.map((line) => toPlain(line.body)).join("\n");
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    a.href = url;
    a.download = `puppet-${active.npcId}-${stamp}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // Auto-scroll the terminal to the newest line as the feed grows.
  $effect(() => {
    if (active && active.feed.length && feedEl) {
      feedEl.scrollTop = feedEl.scrollHeight;
    }
  });

  async function send(event: Event) {
    event.preventDefault();
    const cmd = input.trim();
    if (!cmd || !active) return;
    input = "";
    try {
      await connection.request("puppets", "puppet_cmd", { npc_id: active.npcId, cmd });
    } catch {
      /* the error surfaces on the feed / status; input is already cleared */
    }
  }
</script>

<section class="puppets" aria-label="Remote puppet viewpoints">
  {#if active}
    <!-- Terminal view: act as this NPC, no p<n> prefix. -->
    <header class="term-head">
      <button class="back" onclick={close} title="Back to puppet list">‹</button>
      <strong>P{active.slot}</strong>
      <span class="name">{active.name}</span>
      <small>#{active.npcId}</small>
      {#if active.scene.room.name}
        <span class="where">{@html active.scene.room.name}</span>
      {/if}
      <button class="tool" onclick={downloadFeed} title="download buffer" aria-label="download buffer" disabled={!active.feed.length}>⭳</button>
      <button class="tool" onclick={clearFeed} title="clear buffer" aria-label="clear buffer" disabled={!active.feed.length}>⌫</button>
    </header>

    <div class="term-feed" bind:this={feedEl}>
      {#if active.resyncing && !active.feed.length}
        <div class="sync">loading viewpoint…</div>
      {/if}
      {#each active.feed as line, index (index)}
        <div class="term-line">{@html toHtml(line.body)}</div>
      {/each}
    </div>

    <form class="term-input" onsubmit={send}>
      <span class="prompt">P{active.slot}&gt;</span>
      <input
        type="text"
        bind:value={input}
        placeholder="act as {active.name}…"
        autocomplete="off"
        use:focusOnMount
      />
    </form>
  {:else if puppets.list.length}
    <!-- Roster: click a puppet to open its terminal. -->
    {#each puppets.list as feed (feed.npcId)}
      <button class="row framed" onclick={() => open(feed.npcId)}>
        <strong>P{feed.slot}</strong>
        <span class="name">{feed.name}</span>
        <small>#{feed.npcId}</small>
        {#if feed.scene.room.name}
          <span class="where">{@html feed.scene.room.name}</span>
        {/if}
        {#if feed.unread}
          <span class="badge" aria-label="{feed.unread} unread">{feed.unread > 99 ? "99+" : feed.unread}</span>
        {/if}
      </button>
    {/each}
  {:else}
    <p class="empty">No remote puppet viewpoints.</p>
  {/if}
</section>

<style>
  .puppets { display: flex; flex-direction: column; gap: 6px; padding: 8px; overflow: hidden; height: 100%; }

  /* Roster rows */
  .row {
    display: flex; gap: 7px; align-items: baseline; width: 100%;
    padding: 8px; text-align: left; cursor: pointer; color: inherit;
    background: color-mix(in srgb, var(--panel-bg, #111) 92%, var(--gold));
    border: 0;
  }
  .row:hover { background: color-mix(in srgb, var(--panel-bg, #111) 84%, var(--gold)); }
  .row strong, .term-head strong, .prompt { color: var(--gold); }
  .row .where, .term-head .where { margin-left: auto; color: var(--muted, #889); font-size: 0.85em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badge {
    margin-left: 6px; min-width: 1.4em; padding: 0 5px; border-radius: 999px;
    background: var(--gold); color: #111; font-size: 0.72em; font-weight: 700; text-align: center;
  }
  .row .where + .badge { margin-left: 6px; }

  /* Terminal */
  .term-head { display: flex; gap: 7px; align-items: baseline; padding-bottom: 4px; border-bottom: 1px solid color-mix(in srgb, var(--gold) 30%, transparent); }
  .term-head .name { font-weight: 600; }
  .back { background: none; border: 0; color: var(--gold); font-size: 1.2em; line-height: 1; cursor: pointer; padding: 0 4px 0 0; }
  .term-head .tool { flex: none; background: none; border: 0; color: var(--muted, #889); cursor: pointer; padding: 0 3px; font-size: 0.95em; }
  .term-head .tool:hover:not(:disabled) { color: var(--gold); }
  .term-head .tool:disabled { opacity: 0.4; cursor: default; }
  .term-head .where { max-width: 40%; }
  .term-feed { flex: 1; overflow-y: auto; padding: 6px 2px; display: flex; flex-direction: column; gap: 2px; }
  .term-line { color: var(--fg, #cdd); font-size: 0.9em; white-space: pre-wrap; word-break: break-word; }
  .term-input { display: flex; gap: 6px; align-items: center; padding-top: 4px; border-top: 1px solid color-mix(in srgb, var(--gold) 30%, transparent); }
  .term-input input {
    flex: 1; background: var(--panel-bg, #111); border: 1px solid color-mix(in srgb, var(--gold) 30%, transparent);
    color: var(--fg, #cdd); padding: 5px 7px; font: inherit;
  }
  small { color: var(--muted, #889); }
  .sync, .empty { color: var(--muted, #aab); font-size: 0.88em; }
</style>
