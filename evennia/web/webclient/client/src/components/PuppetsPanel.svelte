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
      <button class="sh-cmd back" onclick={close} title="Back to puppet list" aria-label="Back to puppet list">Back</button>
      <strong>P{active.slot}</strong>
      <span class="name">{active.name}</span>
      <small>#{active.npcId}</small>
      {#if active.scene.room.name}
        <span class="where">{@html active.scene.room.name}</span>
      {/if}
      <button class="sh-cmd tool" onclick={downloadFeed} title="download buffer" aria-label="download buffer" disabled={!active.feed.length}>Save</button>
      <button class="sh-cmd tool" onclick={clearFeed} title="clear buffer" aria-label="clear buffer" disabled={!active.feed.length}>Clear</button>
    </header>

    <div class="term-feed" bind:this={feedEl}>
      {#if active.resyncing && !active.feed.length}
        <div class="sync">Loading viewpoint</div>
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
        class="sh-placeholder"
        placeholder="Act as {active.name}"
        autocomplete="off"
        use:focusOnMount
      />
    </form>
  {:else if puppets.list.length}
    <!-- Roster: click a puppet to open its terminal. -->
    {#each puppets.list as feed (feed.npcId)}
      <button class="sh-row row" onclick={() => open(feed.npcId)}>
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
  .puppets { display: flex; flex-direction: column; height: 100%; overflow: hidden; background: var(--bg-elev); }

  /* Roster rows */
  .row { flex-direction: row; gap: 1ch; align-items: baseline; }
  .row strong, .term-head strong, .prompt { color: var(--gold); font-weight: normal; }
  .row .where, .term-head .where { margin-left: auto; color: var(--fg-dim); font-size: 0.8em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badge {
    margin-left: 6px; min-width: 1.4em; padding: 0 0.5ch;
    background: var(--gold); color: var(--bg-deep); font-size: 0.64rem; text-align: center;
  }
  .row .where + .badge { margin-left: 6px; }

  /* Terminal */
  .term-head { display: flex; gap: 1ch; align-items: center; padding: 5px 8px 5px 10px; border-bottom: 1px solid var(--accent); }
  .term-head .tool { flex: none; }
  .term-head .where { max-width: 40%; }
  .term-feed { flex: 1; overflow-y: auto; padding: 8px 10px; display: flex; flex-direction: column; gap: 2px; }
  .term-line { color: var(--fg); font-size: 0.9em; white-space: pre-wrap; word-break: break-word; }
  .term-input { display: flex; gap: 0.8ch; align-items: center; padding: 6px 10px; border-top: 1px solid var(--accent); }
  .term-input input {
    flex: 1; background: transparent; border: 0; outline: none;
    color: var(--fg); padding: 3px 0; font: inherit; caret-color: var(--accent-bright);
  }
  small { color: var(--fg-faint); }
  .sync, .empty { color: var(--fg-faint); font-size: 0.64rem; letter-spacing: 0.14em; text-transform: uppercase; padding: 10px; }
</style>
