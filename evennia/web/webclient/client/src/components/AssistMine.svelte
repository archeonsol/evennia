<script lang="ts">
  // The Mine tab of the Assist panel: the caller's own tickets. Find one,
  // read it, answer it, file a new one.
  //
  // Driven by the my_tickets / my_ticket / my_ticket_act / my_ticket_open
  // RPCs, so nothing a player does here prints into their terminal. Live staff
  // replies and status changes arrive via ticket_msg; with the panel closed
  // the store raises a toast instead (chat.announceTicket).
  import { chat } from "../lib/chat.svelte";
  import { renderBody, renderSender } from "../lib/markup";
  import { connection } from "../lib/evennia.svelte";
  import { focusOnMount } from "../lib/focus";

  type View = "open" | "waiting" | "all";
  type Sort = "recent" | "oldest";

  let view = $state<View>("open");
  let sort = $state<Sort>("recent");
  let search = $state("");
  let reply = $state("");
  let composing = $state(false);
  let subject = $state("");
  let details = $state("");
  let feedback = $state<{ ok: boolean; message: string } | null>(null);
  let busy = $state(false);
  let confirmWithdraw = $state(false);
  const ticket = $derived(chat.myTicket);

  const STATUS: Record<string, string> = {
    pending: "with staff",
    waiting: "waiting on you",
    approved: "approved",
    denied: "denied",
    closed: "closed",
    resolved: "closed",
    withdrawn: "withdrawn",
  };
  // The colour of a status plate: your move stands out, staff's move is hot,
  // finished tickets fade.
  const PLATE: Record<string, string> = {
    pending: "hot",
    waiting: "gold",
    approved: "ok",
  };
  const OPEN = new Set(["pending", "waiting"]);

  // Load when the socket is open (a layout restored at page load mounts this
  // panel before it is), whenever the filter or search changes, and when a
  // ticket of ours changes. The search is debounced; a status filter is not.
  let searchTimer: ReturnType<typeof setTimeout> | null = null;
  $effect(() => {
    const q = search.trim();
    const includeClosed = view === "all" || !!q;
    void chat.myTicketsRev;
    if (connection.state !== "open") return;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      chat.myTicketsSearch = q;
      void chat.loadMyTickets(includeClosed, q);
    }, q ? 250 : 0);
    return () => {
      if (searchTimer) clearTimeout(searchTimer);
    };
  });

  const rows = $derived.by(() => {
    let list = [...chat.myTickets];
    if (!search.trim()) {
      if (view === "open") list = list.filter((t) => OPEN.has(t.status));
      else if (view === "waiting") list = list.filter((t) => t.status === "waiting");
    }
    list.sort((a, b) => (sort === "recent" ? (b.updated ?? 0) - (a.updated ?? 0) : (a.updated ?? 0) - (b.updated ?? 0)));
    return list;
  });
  const waitingCount = $derived(chat.myTickets.filter((t) => t.status === "waiting").length);

  function say(result: { ok: boolean; message: string }) {
    feedback = result;
  }
  function open(t: any) {
    feedback = null;
    confirmWithdraw = false;
    reply = "";
    void chat.openMyTicket(t.id);
  }
  function back() {
    chat.myTicket = null;
    feedback = null;
    confirmWithdraw = false;
    chat.myTicketsRev += 1;
  }
  async function send() {
    const text = reply.trim();
    if (!text || !ticket || busy) return;
    busy = true;
    const result = await chat.replyMyTicket(ticket.id, text);
    busy = false;
    if (result.ok) reply = "";
    say(result);
  }
  async function withdraw() {
    if (!ticket || busy) return;
    if (!confirmWithdraw) {
      confirmWithdraw = true;
      return;
    }
    busy = true;
    say(await chat.myTicketAct(ticket.id, "withdraw"));
    busy = false;
    confirmWithdraw = false;
  }
  async function file() {
    if (busy) return;
    if (!details.trim()) {
      say({ ok: false, message: "Say what you need help with." });
      return;
    }
    busy = true;
    const result = await chat.openRequest(subject.trim(), details.trim());
    busy = false;
    say(result);
    if (result.ok) {
      composing = false;
      subject = "";
      details = "";
    }
  }
  // Enter sends; Shift+Enter is a new line, for the multi-line answer a
  // support conversation often needs.
  function onReplyKey(e: KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }
  function ageOf(ts: number) {
    if (!ts) return "";
    const m = Math.floor((Date.now() / 1000 - ts) / 60);
    if (m < 1) return "now";
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    return h < 24 ? `${h}h` : `${Math.floor(h / 24)}d`;
  }
  function stamp(ts: number) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    const p = (n: number) => String(n).padStart(2, "0");
    const today = new Date().toDateString() === d.toDateString();
    return today ? `${p(d.getHours())}:${p(d.getMinutes())}` : `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  const canReply = $derived(!!ticket && (OPEN.has(ticket.status) || !ticket.approvable));
  const canWithdraw = $derived(!!ticket && OPEN.has(ticket.status) && !ticket.approvable);
</script>

<div class="mine">
  <div class="hd">
    {#if ticket}
      <button class="sh-cmd back" onclick={back}>Back</button>
    {:else if composing}
      <button class="sh-cmd" onclick={() => { composing = false; feedback = null; }}>Cancel</button>
    {:else}
      <button class="sh-cmd primary" onclick={() => { composing = true; feedback = null; }}>Request</button>
    {/if}
  </div>

  {#if feedback}
    <p class="fb" class:err={!feedback.ok} role="status">{feedback.message}</p>
  {/if}

  {#if composing}
    <form class="compose" onsubmit={(e) => { e.preventDefault(); void file(); }}>
      <label>
        <span class="sh-label">Subject</span>
        <input class="sh-field" bind:value={subject} maxlength="120" use:focusOnMount />
      </label>
      <label>
        <span class="sh-label">Details</span>
        <textarea class="sh-field" bind:value={details} rows="5"></textarea>
      </label>
      <div class="formkeys">
        <span class="hint">Bugs: <b>@bug</b>. Harassment: <b>@report</b>.</span>
        <button class="sh-cmd primary" type="submit" disabled={busy}>Send</button>
      </div>
    </form>
  {:else if !ticket}
    <div class="tools">
      <input class="sh-field search" bind:value={search} placeholder="Search" aria-label="Search your tickets" />
      <div class="chips" role="radiogroup" aria-label="Show">
        <button class="sh-toggle" role="radio" aria-checked={view === "open"} onclick={() => (view = "open")}>Open</button>
        <button class="sh-toggle" role="radio" aria-checked={view === "waiting"} onclick={() => (view = "waiting")}>
          Waiting{#if waitingCount}<span class="sh-count">{waitingCount}</span>{/if}
        </button>
        <button class="sh-toggle" role="radio" aria-checked={view === "all"} onclick={() => (view = "all")}>All</button>
        <button class="sh-cmd sort" onclick={() => (sort = sort === "recent" ? "oldest" : "recent")}
          aria-label="Sort: {sort === 'recent' ? 'newest first' : 'oldest first'}">{sort === "recent" ? "Newest" : "Oldest"}</button>
      </div>
    </div>
    <div class="list">
      {#if chat.myTicketsError}
        <p class="empty err" role="alert">
          {chat.myTicketsError}
          <button class="sh-cmd" onclick={() => chat.myTicketsRev++}>Retry</button>
        </p>
      {/if}
      {#each rows as t (t.id)}
        <button class="sh-row" class:hot={chat.unseen(t)} onclick={() => open(t)}>
          <span class="r1">
            {#if chat.unseen(t)}<span class="sr-only">New. </span>{/if}
            <span class="kind">{t.label}</span>
            <span class="id">#{t.short_id}</span>
            <span class="sh-plate {PLATE[t.status] ?? ''}">{STATUS[t.status] ?? t.status}</span>
            <span class="age">{ageOf(t.updated)}</span>
          </span>
          {#if t.subject}<span class="subject">{t.subject}</span>{/if}
          {#if t.preview}<span class="prev">{t.preview}</span>{/if}
        </button>
      {:else}
        {#if !chat.myTicketsError}
          <p class="empty">
            {#if search.trim()}No match.
            {:else if view === "waiting"}Nothing waiting on you.
            {:else}No {view === "open" ? "open " : ""}tickets.{/if}
          </p>
        {/if}
      {/each}
    </div>
  {:else}
    <div class="convo">
      <div class="chead">
        <span class="ctitle">{ticket.subject || ticket.label}</span>
        <span class="cmeta">
          <span class="ckind">{ticket.label} #{ticket.short_id}</span>
          <span class="sh-plate {PLATE[ticket.status] ?? ''}">{STATUS[ticket.status] ?? ticket.status}</span>
          {#if ticket.assignee}<span class="handler">Handler <b>{ticket.assignee}</b></span>{/if}
        </span>
      </div>
      <div class="msgs" role="log" aria-label="Conversation">
        {#each ticket.messages ?? [] as m, i (i)}
          {#if m.origin === "system"}
            <div class="sys"><span class="mts">{stamp(m.ts)}</span> {m.text}</div>
          {:else}
            <div class="m" class:me={m.origin === "player"} class:staffmsg={m.origin === "staff"}>
              <span class="who">
                <span class="s">{@html renderSender(m.sender_html ?? m.senderHtml, m.sender)}</span>
                {#if m.origin === "staff"}<span class="sh-plate hot">Staff</span>{:else if m.origin === "player"}<span class="sh-plate dim">You</span>{/if}
                <span class="mts">{stamp(m.ts)}</span>
              </span>
              <span class="t">{@html renderBody(m.html, m.text)}</span>
            </div>
          {/if}
        {/each}
        {#if !(ticket.messages ?? []).length}<p class="empty">No messages.</p>{/if}
      </div>
      {#if canReply}
        <div class="reply">
          <div class="to">To <b>staff</b> · #{ticket.short_id}</div>
          <textarea
            class="sh-field sh-placeholder"
            bind:value={reply}
            onkeydown={onReplyKey}
            rows="2"
            placeholder={OPEN.has(ticket.status) ? "Reply" : "Reply to reopen"}
            aria-label="Reply"
            aria-describedby="mine-reply-keys"
          ></textarea>
          <span id="mine-reply-keys" class="sr-only">Enter sends. Shift+Enter starts a new line.</span>
          <div class="rkeys">
            {#if canWithdraw}
              <button class="sh-cmd warn" class:armed={confirmWithdraw} onclick={withdraw} disabled={busy}>
                {confirmWithdraw ? "Confirm withdraw" : "Withdraw"}
              </button>
            {/if}
            <button class="sh-cmd primary send" onclick={send} disabled={busy || !reply.trim()}>{OPEN.has(ticket.status) ? "Send" : "Reopen"}</button>
          </div>
        </div>
      {:else}
        <div class="closed-note">Closed. File a new request if you need more.</div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .mine { display: flex; flex-direction: column; height: 100%; background: var(--bg-elev); }
  .hd { display: flex; align-items: center; gap: 1ch; padding: 5px 8px 5px 10px; border-bottom: 1px solid var(--accent); flex: 0 0 auto; }
  .hd .sh-cmd { margin-left: auto; }
  .to { color: var(--fg-dim); font-size: 0.66rem; letter-spacing: 0.06em; }
  .to b { color: var(--gold); font-weight: normal; }
  .fb { margin: 0; padding: 4px 10px; font-size: 0.72rem; letter-spacing: 0.04em; color: var(--ok, var(--accent-bright)); border-bottom: 1px solid var(--border); }
  .fb.err, .err { color: var(--alert); }
  .tools { display: flex; flex-direction: column; gap: 4px; padding: 6px 10px; border-bottom: 1px solid var(--border); flex: 0 0 auto; }
  .chips { display: flex; flex-wrap: wrap; gap: 2px 6px; align-items: center; }
  .sort { margin-left: auto; }
  .list { overflow-y: auto; flex: 1; min-height: 0; }
  .r1 { display: flex; align-items: baseline; gap: 1ch; }
  .kind { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.12em; font-size: 0.68rem; }
  .id { color: var(--fg-faint); font-size: 0.64rem; }
  .age { margin-left: auto; color: var(--fg-faint); font-size: 0.64rem; }
  .subject { color: var(--fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sh-row.hot .subject { color: var(--gold); }
  .prev { color: var(--fg-dim); font-size: 0.76rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .empty { color: var(--fg-faint); padding: 12px 10px; margin: 0; font-size: 0.68rem; letter-spacing: 0.14em; text-transform: uppercase; }
  .compose { display: flex; flex-direction: column; gap: 12px; padding: 12px 10px; overflow-y: auto; }
  .compose label { display: flex; flex-direction: column; gap: 4px; }
  .formkeys { display: flex; align-items: center; gap: 10px; }
  .formkeys .sh-cmd { margin-left: auto; }
  .hint { color: var(--fg-faint); font-size: 0.7rem; }
  .hint b { color: var(--fg-dim); font-weight: normal; }
  .convo { display: flex; flex-direction: column; min-height: 0; flex: 1; }
  .chead { display: flex; flex-direction: column; gap: 4px; padding: 7px 10px; border-bottom: 1px solid var(--border); }
  .ctitle { color: var(--gold); letter-spacing: 0.04em; font-size: 0.88rem; }
  .cmeta { display: flex; flex-wrap: wrap; align-items: center; gap: 1ch; }
  .ckind { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.12em; font-size: 0.66rem; }
  .handler { color: var(--fg-faint); font-size: 0.64rem; letter-spacing: 0.14em; text-transform: uppercase; }
  .handler b { color: var(--fg-dim); font-weight: normal; letter-spacing: 0.04em; text-transform: none; font-size: 0.72rem; }
  .msgs { flex: 1; overflow-y: auto; padding: 8px 10px; line-height: 1.5; display: flex; flex-direction: column; gap: 10px; }
  .m { display: flex; flex-direction: column; gap: 2px; padding-left: 1.5ch; border-left: 1px solid var(--border-bright); font-size: 0.85rem; }
  .m.staffmsg { border-left-color: var(--accent); }
  .who { display: flex; align-items: baseline; gap: 0.8ch; }
  .s { color: var(--accent-bright); }
  .m.me .s { color: var(--fg-dim); }
  .mts { color: var(--fg-faint); font-size: 0.64rem; }
  .t { color: var(--fg); white-space: pre-wrap; }
  .sys { color: var(--fg-faint); font-size: 0.64rem; letter-spacing: 0.12em; text-transform: uppercase; padding: 2px 0; }
  .sys::before { content: "-- "; content: "-- " / ""; }
  .reply { display: flex; flex-direction: column; gap: 4px; padding: 7px 10px; border-top: 1px solid var(--accent); flex: 0 0 auto; }
  .rkeys { display: flex; gap: 6px; align-items: center; }
  .rkeys .send { margin-left: auto; }
  .sh-cmd.armed { background: var(--alert); color: var(--bg-deep); }
  .closed-note { padding: 7px 10px; border-top: 1px solid var(--border); color: var(--fg-faint); font-size: 0.64rem; letter-spacing: 0.14em; text-transform: uppercase; }
</style>
