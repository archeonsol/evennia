<script lang="ts">
  import { chat } from "../lib/chat.svelte";

  let reply = $state("");
  let internal = $state(false);
  let history = $state(false);
  let kindFilter = $state("all");
  let bugDetail = $state<any | null>(null);
  const ticket = $derived(chat.ticket);
  // Kinds present in the current list, for the filter bar.
  const source = $derived(history ? chat.ticketHistory : chat.tickets);
  const kinds = $derived([
    "all",
    ...Array.from(new Set(source.map((t: any) => t.kind))),
  ]);
  // Filter by kind, then highest priority first, then longest-waiting.
  const rows = $derived(
    [...source]
      .filter((t: any) => kindFilter === "all" || t.kind === kindFilter)
      .sort(
        (a, b) => (b.priority ?? 0) - (a.priority ?? 0) || (a.updated ?? 0) - (b.updated ?? 0),
      ),
  );
  function kindLabel(k: string) {
    if (k === "all") return "All";
    const t = source.find((x: any) => x.kind === k);
    return t ? t.label : k;
  }

  function toggleHistory() {
    history = !history;
    if (history) chat.loadTicketHistory();
  }
  function open(t: any) {
    chat.openTicket(t.id);
  }
  async function loadBug() {
    if (ticket) bugDetail = await chat.loadBugDetail(ticket.id);
  }
  // Reset the loaded bug detail whenever the open ticket changes.
  $effect(() => {
    ticket?.id;
    bugDetail = null;
  });
  function back() {
    chat.ticket = null;
  }
  function send() {
    if (reply.trim() && ticket) {
      chat.ticketReply(ticket.id, reply, internal);
      reply = "";
    }
  }
  function onKey(e: KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      send();
    }
  }
  function ageOf(ts: number) {
    if (!ts) return "";
    const m = Math.floor(Date.now() / 1000 - ts) / 60;
    if (m < 1) return "now";
    if (m < 60) return `${Math.floor(m)}m`;
    const h = Math.floor(m / 60);
    return h < 24 ? `${h}h` : `${Math.floor(h / 24)}d`;
  }
  function payloadEntries(p: any) {
    return p ? Object.entries(p) : [];
  }
</script>

<div class="tickets">
  <div class="hd">
    <span class="tag glow-text">Tickets</span>
    {#if ticket}
      <button class="back" onclick={back}>‹ inbox</button>
    {:else}
      <button class="tab" class:on={!history} onclick={() => (history = false)}>Open</button>
      <button class="tab" class:on={history} onclick={toggleHistory}>History</button>
      <span class="count">{rows.length}</span>
    {/if}
  </div>

  {#if !ticket}
    {#if kinds.length > 2}
      <div class="filters">
        {#each kinds as k}
          <button class="fchip" class:on={kindFilter === k} onclick={() => (kindFilter = k)}>
            {kindLabel(k)}
          </button>
        {/each}
      </div>
    {/if}
    <div class="list">
      {#if rows.length}
        {#each rows as t (t.id)}
          <button class="row" data-kind={t.kind} onclick={() => open(t)}>
            <span class="r1">
              <span class="kind">{t.label}</span>
              <span class="meta">
                {#if t.priority > 0}<span class="pri" title="priority">▲{t.priority}</span>{/if}
                {#if t.assignee}<span class="asg" title="claimed by {t.assignee}">◆ {t.assignee}</span>{/if}
                <span class="status s-{t.status}">{t.status}</span>
                <span class="age">{ageOf(t.updated)}</span>
              </span>
            </span>
            <span class="who">{t.account_name || t.short_id}</span>
            <span class="prev">{t.preview || "-"}</span>
          </button>
        {/each}
      {:else}
        <p class="empty">No {kindFilter === "all" ? "" : kindLabel(kindFilter).toLowerCase() + " "}tickets{history ? " in history" : ""}.</p>
      {/if}
    </div>
  {:else}
    <div class="convo">
      <div class="head">
        <span class="petitioner">{ticket.label}: {ticket.account_name || ticket.short_id}</span>
        <span class="actions">
          <button class="act" onclick={() => chat.ticketClaim(ticket.id)}>Claim</button>
          {#if ticket.approvable}
            <button class="act ok" onclick={() => chat.ticketApprove(ticket.id)}>Approve</button>
            <button class="act no" onclick={() => chat.ticketDeny(ticket.id)}>Deny</button>
          {:else}
            <button class="act" onclick={() => chat.ticketResolve(ticket.id)}>Resolve</button>
          {/if}
        </span>
      </div>

      {#if payloadEntries(ticket.payload).length}
        <div class="ctx">
          {#each payloadEntries(ticket.payload) as [k, v]}
            <div class="cx"><span class="ck">{k}</span> <span class="cv">{v}</span></div>
          {/each}
          {#if ticket.kind === "bug" && !bugDetail}
            <button class="loadbug" onclick={loadBug}>Load report detail ▾</button>
          {/if}
        </div>
      {/if}

      {#if bugDetail?.available}
        <div class="bug">
          <div class="bl"><span class="ck">reporter</span> {bugDetail.reporter} / {bugDetail.character}</div>
          <div class="bl"><span class="ck">location</span> {bugDetail.location}</div>
          {#if bugDetail.traceback_command}
            <div class="bl"><span class="ck">last cmd</span> {bugDetail.traceback_command} <span class="dim">({bugDetail.traceback_time})</span></div>
          {/if}
          {#if bugDetail.traceback}
            <div class="ck">traceback</div>
            <pre class="tb">{bugDetail.traceback}</pre>
          {/if}
          {#if Object.keys(bugDetail.character_state ?? {}).length}
            <div class="ck">character state</div>
            <pre class="tb">{Object.entries(bugDetail.character_state).map(([k, v]) => `${k}: ${v}`).join("\n")}</pre>
          {/if}
        </div>
      {:else if bugDetail && !bugDetail.available}
        <div class="ctx"><span class="dim">No detailed bug report attached.</span></div>
      {/if}

      <div class="msgs">
        {#each ticket.messages ?? [] as m, i (i)}
          <div class="m" class:note={m.visibility === "internal"}>
            <span class="s">{m.sender}</span>
            <span class="t">{@html m.html ?? m.text}</span>
          </div>
        {/each}
        {#if !(ticket.messages ?? []).length}<p class="empty">No messages yet.</p>{/if}
      </div>

      <div class="reply">
        <label class="int"><input type="checkbox" bind:checked={internal} /> note</label>
        <input
          bind:value={reply}
          onkeydown={onKey}
          placeholder={internal ? "internal staff note…" : "reply to player…"}
          aria-label="ticket reply"
        />
      </div>
    </div>
  {/if}
</div>

<style>
  .tickets { display: flex; flex-direction: column; height: 100%; background: var(--bg-elev); }
  .hd {
    display: flex; align-items: baseline; gap: 1ch; padding: 6px 10px;
    border-bottom: 1px solid var(--accent); flex: 0 0 auto;
  }
  .tag { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.22em; font-size: 0.8rem; }
  .count { margin-left: auto; color: var(--gold); font-size: 0.68rem; letter-spacing: 0.1em; }
  .tab {
    background: none; border: none; color: var(--fg-faint); font-family: inherit;
    font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.14em; cursor: pointer; padding: 0;
  }
  .tab.on { color: var(--accent-bright); }
  .filters {
    display: flex; flex-wrap: wrap; gap: 4px; padding: 5px 8px;
    border-bottom: 1px solid var(--border); flex: 0 0 auto;
  }
  .fchip {
    background: none; border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.08em;
    padding: 2px 8px; border-radius: 999px; cursor: pointer;
  }
  .fchip:hover { color: var(--fg); border-color: var(--accent); }
  .fchip.on { color: var(--bg-deep); background: var(--accent-bright); border-color: var(--accent-bright); }
  .loadbug {
    align-self: flex-start; margin-top: 3px; background: none; border: none;
    color: var(--accent-bright); font-family: inherit; font-size: 0.66rem; cursor: pointer; padding: 0;
  }
  .bug { padding: 6px 10px; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 3px; }
  .bl { font-size: 0.74rem; color: var(--fg); }
  .dim { color: var(--fg-faint); }
  .tb {
    margin: 2px 0 4px; padding: 6px; background: var(--bg); border: 1px solid var(--border);
    color: var(--fg-dim); font-size: 0.7rem; max-height: 180px; overflow: auto; white-space: pre-wrap;
  }
  .back {
    margin-left: auto; background: none; border: none; color: var(--accent-bright);
    font-family: inherit; font-size: 0.7rem; letter-spacing: 0.1em; cursor: pointer;
  }
  .list { overflow-y: auto; padding: 6px; display: flex; flex-direction: column; gap: 6px; }
  .row {
    display: flex; flex-direction: column; gap: 4px; text-align: left;
    padding: 8px 10px 8px 12px; background: var(--bg); border: 1px solid var(--border);
    border-left: 3px solid var(--border-bright); color: var(--fg); font-family: inherit; cursor: pointer;
    transition: border-color 0.12s, background 0.12s;
  }
  .row:hover { border-color: var(--accent); border-left-color: var(--accent); background: var(--bg-elev); }
  /* Kind accent stripe on the left edge. */
  .row[data-kind="bug"] { border-left-color: var(--alert, #e5484d); }
  .row[data-kind="puppet"] { border-left-color: var(--gold); }
  .row[data-kind="report"] { border-left-color: #c14bd8; }
  .row[data-kind="request"] { border-left-color: var(--accent-bright); }
  .r1 { display: flex; justify-content: space-between; align-items: baseline; gap: 1ch; }
  .kind { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.72rem; }
  .meta { display: flex; align-items: baseline; gap: 0.7ch; }
  .pri { color: var(--danger, #e5484d); font-size: 0.62rem; font-weight: bold; }
  .asg { color: var(--accent); font-size: 0.6rem; text-transform: uppercase; }
  .age { color: var(--fg-faint); font-size: 0.66rem; }
  .status {
    font-size: 0.56rem; text-transform: uppercase; letter-spacing: 0.08em;
    padding: 1px 5px; border: 1px solid currentColor; border-radius: 2px;
  }
  .s-pending { color: var(--accent-bright); }
  .s-waiting { color: var(--gold); }
  .s-closed, .s-approved, .s-denied, .s-resolved { color: var(--fg-faint); }
  .who { color: var(--gold); font-size: 0.78rem; }
  .prev { color: var(--fg-dim); font-size: 0.74rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .empty { color: var(--fg-faint); font-style: italic; padding: 8px 10px; }

  .convo { display: flex; flex-direction: column; min-height: 0; flex: 1; }
  .head {
    display: flex; align-items: center; gap: 1ch; padding: 5px 10px;
    border-bottom: 1px solid var(--border);
  }
  .petitioner { color: var(--gold); text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.76rem; }
  .actions { display: flex; gap: 4px; margin-left: auto; }
  .act {
    background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.06em;
    padding: 2px 6px; cursor: pointer;
  }
  .act:hover { border-color: var(--accent); color: var(--fg); }
  .act.ok:hover { border-color: #3fb950; color: #3fb950; }
  .act.no:hover { border-color: #e5484d; color: #e5484d; }
  .ctx { padding: 5px 10px; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 2px; }
  .cx { font-size: 0.74rem; }
  .ck { color: var(--accent); text-transform: uppercase; font-size: 0.62rem; letter-spacing: 0.06em; }
  .cv { color: var(--fg); }
  .msgs { flex: 1; overflow-y: auto; padding: 6px 10px; line-height: 1.5; }
  .m { padding: 2px 0; font-size: 0.85rem; }
  .m .s { color: var(--accent); margin-right: 0.6ch; }
  .m .t { color: var(--fg); white-space: pre-wrap; }
  .m.note { opacity: 0.8; }
  .m.note .s::after { content: " (note)"; color: var(--danger, #e5484d); font-size: 0.7em; }
  .reply {
    display: flex; align-items: center; gap: 0.6rem; padding: 6px 10px;
    border-top: 1px solid var(--accent); flex: 0 0 auto;
  }
  .int { color: var(--fg-dim); font-size: 0.62rem; text-transform: uppercase; display: flex; align-items: center; gap: 3px; }
  .reply input[type="text"], .reply input:not([type]) {
    flex: 1; background: transparent; border: none; outline: none;
    color: var(--fg); font-family: inherit; font-size: 0.85rem; caret-color: var(--accent-bright);
  }
  .reply input::placeholder { color: var(--fg-faint); font-style: italic; }
</style>
