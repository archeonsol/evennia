<script lang="ts">
  // A player's own tickets: list -> conversation -> reply. Driven by the
  // my_tickets / my_ticket RPCs; live staff replies arrive via ticket_msg.
  import { chat } from "../lib/chat.svelte";
  import { renderBody, renderSender } from "../lib/markup";

  let reply = $state("");
  let showClosed = $state(false);
  const ticket = $derived(chat.myTicket);

  // Load on first mount and whenever the closed filter flips.
  $effect(() => {
    void showClosed;
    chat.loadMyTickets(showClosed);
  });

  function open(t: any) {
    chat.openMyTicket(t.id);
  }
  function back() {
    chat.myTicket = null;
    chat.loadMyTickets(showClosed);
  }
  function send() {
    if (reply.trim() && ticket) {
      chat.replyMyTicket(ticket.id, reply);
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
</script>

<div class="mine">
  <div class="hd">
    <span class="tag glow-text">My tickets</span>
    {#if ticket}
      <button class="back" onclick={back}>‹ back</button>
    {:else}
      <button class="tab" class:on={!showClosed} onclick={() => (showClosed = false)}>Open</button>
      <button class="tab" class:on={showClosed} onclick={() => (showClosed = true)}>All</button>
    {/if}
  </div>

  {#if !ticket}
    <div class="list">
      {#if chat.myTickets.length}
        {#each chat.myTickets as t (t.id)}
          <button class="row" onclick={() => open(t)}>
            <span class="r1">
              <span class="kind">{t.label}</span>
              <span class="status s-{t.status}">{t.status}</span>
              <span class="age">{ageOf(t.updated)}</span>
            </span>
            {#if t.subject}<span class="subject">{t.subject}</span>{/if}
            <span class="prev">{t.preview || "…"}</span>
          </button>
        {/each}
      {:else}
        <p class="empty">You have no {showClosed ? "" : "open "}tickets. Use <b>@request subject = what you need</b>, <b>@bug</b> or <b>@puppetrequest</b> in the game to open one.</p>
      {/if}
    </div>
  {:else}
    <div class="convo">
      <div class="chead">
        <span class="ctitle">{ticket.subject || ticket.label}</span>
        {#if ticket.subject}<span class="ckind">{ticket.label}</span>{/if}
        <span class="status s-{ticket.status}">{ticket.status}</span>
      </div>
      <div class="msgs">
        {#each ticket.messages ?? [] as m, i (i)}
          <div class="m">
            <span class="s">{@html renderSender(m.sender_html ?? m.senderHtml, m.sender)}</span>
            <span class="t">{@html renderBody(m.html, m.text)}</span>
          </div>
        {/each}
        {#if !(ticket.messages ?? []).length}<p class="empty">No messages yet. Add one below.</p>{/if}
      </div>
      {#if ticket.status !== "closed" && ticket.status !== "resolved" && ticket.status !== "denied"}
        <div class="reply">
          <span class="chev glow-text" aria-hidden="true">❯</span>
          <input bind:value={reply} onkeydown={onKey} placeholder="reply to staff…" aria-label="reply" />
        </div>
      {:else}
        <div class="closed-note">This ticket is closed. Open a new one if you still need help.</div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .mine { display: flex; flex-direction: column; height: 100%; background: var(--bg-elev); }
  .hd { display: flex; align-items: baseline; gap: 1ch; padding: 6px 10px; border-bottom: 1px solid var(--accent); flex: 0 0 auto; }
  .tag { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.2em; font-size: 0.76rem; }
  .tab { background: none; border: none; color: var(--fg-faint); font-family: inherit; font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.12em; cursor: pointer; padding: 0; }
  .tab.on { color: var(--accent-bright); }
  .back { margin-left: auto; background: none; border: none; color: var(--accent-bright); font-family: inherit; font-size: 0.7rem; cursor: pointer; }
  .list { overflow-y: auto; padding: 6px; display: flex; flex-direction: column; gap: 5px; }
  .row { display: flex; flex-direction: column; gap: 3px; text-align: left; padding: 8px 10px; background: var(--bg); border: 1px solid var(--border); border-left: 3px solid var(--accent-bright); color: var(--fg); font-family: inherit; cursor: pointer; }
  .row:hover { border-color: var(--accent); }
  .r1 { display: flex; align-items: baseline; gap: 1ch; }
  .kind { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.72rem; }
  .age { margin-left: auto; color: var(--fg-faint); font-size: 0.66rem; }
  .status { font-size: 0.56rem; text-transform: uppercase; letter-spacing: 0.08em; padding: 1px 5px; border: 1px solid currentColor; border-radius: 2px; }
  .s-pending { color: var(--accent-bright); }
  .s-waiting { color: var(--gold); }
  .s-closed, .s-approved, .s-denied, .s-resolved { color: var(--fg-faint); }
  .prev { color: var(--fg-dim); font-size: 0.76rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .empty { color: var(--fg-faint); font-style: italic; padding: 10px; font-size: 0.78rem; line-height: 1.5; }
  .convo { display: flex; flex-direction: column; min-height: 0; flex: 1; }
  .chead { display: flex; align-items: center; gap: 1ch; padding: 5px 10px; border-bottom: 1px solid var(--border); }
  .ctitle { color: var(--gold); text-transform: uppercase; letter-spacing: 0.1em; font-size: 0.78rem; }
  .msgs { flex: 1; overflow-y: auto; padding: 6px 10px; line-height: 1.5; }
  .m { padding: 2px 0; font-size: 0.85rem; }
  .m .s { color: var(--accent); margin-right: 0.6ch; }
  .m .t { color: var(--fg); white-space: pre-wrap; }
  .reply { display: flex; align-items: center; gap: 0.6rem; padding: 6px 10px; border-top: 1px solid var(--accent); flex: 0 0 auto; }
  .chev { color: var(--accent-bright); }
  .reply input { flex: 1; background: transparent; border: none; outline: none; color: var(--fg); font-family: inherit; font-size: 0.85rem; caret-color: var(--accent-bright); }
  .closed-note { padding: 6px 10px; border-top: 1px solid var(--border); color: var(--fg-faint); font-size: 0.72rem; font-style: italic; }
  .subject { color: var(--fg); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ckind { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.7rem; }
</style>
