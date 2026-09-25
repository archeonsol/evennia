<script lang="ts">
  // A player's own tickets: find one, read it, answer it, file a new one.
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
    <span class="tag glow-text">My tickets</span>
    {#if ticket}
      <button class="back" onclick={back}>‹ all tickets</button>
    {:else if composing}
      <button class="back" onclick={() => { composing = false; feedback = null; }}>‹ cancel</button>
    {:else}
      <button class="new" onclick={() => { composing = true; feedback = null; }}>+ New request</button>
    {/if}
  </div>

  {#if feedback}
    <p class="fb" class:err={!feedback.ok} role="status">{feedback.message}</p>
  {/if}

  {#if composing}
    <form class="compose" onsubmit={(e) => { e.preventDefault(); void file(); }}>
      <label>
        <span class="lbl">Subject <span class="dim">(a few words staff read first)</span></span>
        <input bind:value={subject} maxlength="120" placeholder="e.g. Stuck door in the Warrens" use:focusOnMount />
      </label>
      <label>
        <span class="lbl">What do you need?</span>
        <textarea bind:value={details} rows="5" placeholder="Where you are, what happened, what you expected."></textarea>
      </label>
      <div class="formkeys">
        <span class="dim">For a bug, <b>@bug</b> gathers the detail staff need. For a harassment report, use <b>@report</b>.</span>
        <button class="go" type="submit" disabled={busy}>File request</button>
      </div>
    </form>
  {:else if !ticket}
    <div class="tools">
      <input class="search" bind:value={search} placeholder="Search your tickets…" aria-label="Search your tickets" />
      <div class="chips" role="radiogroup" aria-label="Show">
        <button role="radio" aria-checked={view === "open"} class:on={view === "open"} onclick={() => (view = "open")}>Open</button>
        <button role="radio" aria-checked={view === "waiting"} class:on={view === "waiting"} onclick={() => (view = "waiting")}>
          Waiting on you{#if waitingCount}<span class="n">{waitingCount}</span>{/if}
        </button>
        <button role="radio" aria-checked={view === "all"} class:on={view === "all"} onclick={() => (view = "all")}>All</button>
        <select bind:value={sort} aria-label="Sort">
          <option value="recent">Latest activity</option>
          <option value="oldest">Oldest first</option>
        </select>
      </div>
    </div>
    <div class="list">
      {#if chat.myTicketsError}
        <p class="empty err" role="alert">
          {chat.myTicketsError}
          <button class="retry" onclick={() => chat.myTicketsRev++}>Try again</button>
        </p>
      {/if}
      {#each rows as t (t.id)}
        <button class="row" class:unseen={chat.unseen(t)} onclick={() => open(t)}>
          <span class="r1">
            {#if chat.unseen(t)}<span class="dot" aria-label="new"></span>{/if}
            <span class="kind">{t.label}</span>
            <span class="id">#{t.short_id}</span>
            <span class="status s-{t.status}">{STATUS[t.status] ?? t.status}</span>
            <span class="age">{ageOf(t.updated)}</span>
          </span>
          {#if t.subject}<span class="subject">{t.subject}</span>{/if}
          <span class="prev">{t.preview || "…"}</span>
        </button>
      {:else}
        {#if !chat.myTicketsError}
          <p class="empty">
            {#if search.trim()}Nothing of yours matches “{search.trim()}”.
            {:else if view === "waiting"}Nothing is waiting on you.
            {:else}You have no {view === "open" ? "open " : ""}tickets. <button class="link" onclick={() => (composing = true)}>File a request</button>, or use <b>@bug</b> or <b>@puppetrequest</b> in the game.{/if}
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
          <span class="status s-{ticket.status}">{STATUS[ticket.status] ?? ticket.status}</span>
          {#if ticket.assignee}<span class="handler">handled by {ticket.assignee}</span>{/if}
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
                {#if m.origin === "staff"}<span class="role">staff</span>{:else if m.origin === "player"}<span class="role you">you</span>{/if}
                <span class="mts">{stamp(m.ts)}</span>
              </span>
              <span class="t">{@html renderBody(m.html, m.text)}</span>
            </div>
          {/if}
        {/each}
        {#if !(ticket.messages ?? []).length}<p class="empty">No messages yet. Add one below.</p>{/if}
      </div>
      {#if canReply}
        <div class="reply">
          <textarea
            bind:value={reply}
            onkeydown={onReplyKey}
            rows="2"
            placeholder={OPEN.has(ticket.status) ? "Reply to staff… (Enter sends, Shift+Enter for a new line)" : "Reply to reopen this ticket…"}
            aria-label="Reply"
          ></textarea>
          <div class="rkeys">
            {#if canWithdraw}
              <button class="wd" class:armed={confirmWithdraw} onclick={withdraw} disabled={busy}>
                {confirmWithdraw ? "Withdraw it?" : "Withdraw"}
              </button>
            {/if}
            <button class="go" onclick={send} disabled={busy || !reply.trim()}>{OPEN.has(ticket.status) ? "Send" : "Reopen"}</button>
          </div>
        </div>
      {:else}
        <div class="closed-note">This decision is final. File a new request if you still need help.</div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .mine { display: flex; flex-direction: column; height: 100%; background: var(--bg-elev); }
  .hd { display: flex; align-items: center; gap: 1ch; padding: 6px 10px; border-bottom: 1px solid var(--accent); flex: 0 0 auto; }
  .tag { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.2em; font-size: 0.76rem; }
  .back, .new { margin-left: auto; background: none; border: 1px solid var(--border-bright); color: var(--accent-bright); font-family: inherit; font-size: 0.7rem; padding: 2px 8px; min-height: 24px; cursor: pointer; }
  .fb { margin: 0; padding: 4px 10px; font-size: 0.74rem; color: var(--ok, var(--accent-bright)); border-bottom: 1px solid var(--border); }
  .fb.err, .err { color: var(--alert); }
  .tools { display: flex; flex-direction: column; gap: 5px; padding: 6px 8px; border-bottom: 1px solid var(--border); flex: 0 0 auto; }
  .search { background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg); font-family: inherit; font-size: 0.8rem; padding: 4px 7px; min-height: 26px; }
  .search:focus { outline: none; border-color: var(--accent); }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
  .chips button { background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg-dim); font-family: inherit; font-size: 0.68rem; padding: 1px 8px; min-height: 24px; cursor: pointer; }
  .chips button.on { color: var(--accent-bright); border-color: var(--accent); }
  .chips .n { margin-left: 5px; background: var(--accent); color: var(--bg-deep); padding: 0 4px; font-size: 0.6rem; }
  .chips select { margin-left: auto; background: var(--bg); color: var(--fg-dim); border: 1px solid var(--border-bright); font-family: inherit; font-size: 0.68rem; min-height: 24px; }
  .retry, .link { margin-left: 1ch; background: none; border: 1px solid var(--border-bright); color: var(--fg-dim); font-family: inherit; cursor: pointer; }
  .link { border: none; margin: 0; padding: 0; color: var(--accent-bright); text-decoration: underline; }
  .list { overflow-y: auto; padding: 6px; display: flex; flex-direction: column; gap: 5px; flex: 1; min-height: 0; }
  .row { display: flex; flex-direction: column; gap: 3px; text-align: left; padding: 8px 10px; background: var(--bg); border: 1px solid var(--border); border-left: 3px solid var(--border-bright); color: var(--fg); font-family: inherit; cursor: pointer; }
  .row.unseen { border-left-color: var(--accent-bright); }
  .row:hover { border-color: var(--accent); }
  .r1 { display: flex; align-items: baseline; gap: 1ch; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent-bright); align-self: center; flex: none; }
  .kind { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.72rem; }
  .id { color: var(--fg-faint); font-size: 0.66rem; }
  .age { margin-left: auto; color: var(--fg-faint); font-size: 0.66rem; }
  .status { font-size: 0.56rem; text-transform: uppercase; letter-spacing: 0.08em; padding: 1px 5px; border: 1px solid currentColor; border-radius: 2px; white-space: nowrap; }
  .s-pending { color: var(--accent-bright); }
  .s-waiting { color: var(--gold); }
  .s-approved { color: var(--ok, var(--accent-bright)); }
  .s-closed, .s-denied, .s-resolved, .s-withdrawn { color: var(--fg-faint); }
  .subject { color: var(--fg); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .prev { color: var(--fg-dim); font-size: 0.76rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .empty { color: var(--fg-faint); font-style: italic; padding: 10px; font-size: 0.78rem; line-height: 1.5; }
  .compose { display: flex; flex-direction: column; gap: 8px; padding: 10px; overflow-y: auto; }
  .compose label { display: flex; flex-direction: column; gap: 3px; }
  .lbl { color: var(--fg-dim); font-size: 0.7rem; letter-spacing: 0.08em; text-transform: uppercase; }
  .dim { color: var(--fg-faint); text-transform: none; letter-spacing: 0; font-size: 0.7rem; }
  .compose input, .compose textarea, .reply textarea {
    background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg);
    font-family: inherit; font-size: 0.82rem; padding: 5px 7px; resize: vertical;
  }
  .compose input:focus, .compose textarea:focus, .reply textarea:focus { outline: none; border-color: var(--accent); }
  .formkeys { display: flex; align-items: center; gap: 10px; }
  .go { margin-left: auto; background: var(--bg); border: 1px solid var(--accent); color: var(--accent-bright); font-family: inherit; font-size: 0.74rem; padding: 3px 12px; min-height: 26px; cursor: pointer; }
  .go:disabled { opacity: 0.5; cursor: default; }
  .convo { display: flex; flex-direction: column; min-height: 0; flex: 1; }
  .chead { display: flex; flex-direction: column; gap: 3px; padding: 6px 10px; border-bottom: 1px solid var(--border); }
  .ctitle { color: var(--gold); letter-spacing: 0.06em; font-size: 0.86rem; font-weight: 600; }
  .cmeta { display: flex; flex-wrap: wrap; align-items: center; gap: 1ch; }
  .ckind { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.68rem; }
  .handler { color: var(--fg-dim); font-size: 0.7rem; }
  .msgs { flex: 1; overflow-y: auto; padding: 6px 10px; line-height: 1.5; display: flex; flex-direction: column; gap: 6px; }
  .m { display: flex; flex-direction: column; gap: 1px; padding: 4px 8px; border-left: 2px solid var(--border-bright); font-size: 0.85rem; }
  .m.staffmsg { border-left-color: var(--accent); background: color-mix(in srgb, var(--accent) 6%, transparent); }
  .m.me { border-left-color: var(--border); }
  .who { display: flex; align-items: baseline; gap: 0.8ch; }
  .s { color: var(--accent-bright); }
  .role { font-size: 0.56rem; text-transform: uppercase; letter-spacing: 0.12em; color: var(--accent-bright); border: 1px solid currentColor; padding: 0 4px; }
  .role.you { color: var(--fg-faint); }
  .mts { color: var(--fg-faint); font-size: 0.66rem; }
  .t { color: var(--fg); white-space: pre-wrap; }
  .sys { color: var(--fg-dim); font-size: 0.74rem; font-style: italic; text-align: center; padding: 2px 0; }
  .reply { display: flex; flex-direction: column; gap: 5px; padding: 6px 10px; border-top: 1px solid var(--accent); flex: 0 0 auto; }
  .rkeys { display: flex; gap: 8px; align-items: center; }
  .wd { background: none; border: 1px solid var(--border-bright); color: var(--fg-dim); font-family: inherit; font-size: 0.72rem; padding: 2px 10px; min-height: 26px; cursor: pointer; }
  .wd.armed { color: var(--alert); border-color: var(--alert); }
  .closed-note { padding: 6px 10px; border-top: 1px solid var(--border); color: var(--fg-faint); font-size: 0.72rem; font-style: italic; }
</style>
