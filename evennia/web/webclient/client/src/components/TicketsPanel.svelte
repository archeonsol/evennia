<script lang="ts">
  // The staff Ticket Queue: find a ticket, read it, act on it.
  //
  // Actions go through the ticket_act RPC (chat.ticketAct), so each button's
  // answer shows here, not as "Posted." or "Ticket #... closed." in the
  // terminal beside the panel.
  import { chat, type TicketResult } from "../lib/chat.svelte";
  import { renderBody, renderSender } from "../lib/markup";

  type Show = "all" | "pending" | "waiting" | "unclaimed";

  let reply = $state("");
  let internal = $state(false);
  let history = $state(false);
  let kindFilter = $state("all");
  let show = $state<Show>("all");
  let search = $state("");
  let reason = $state("");
  let deciding = $state<"approve" | "deny" | null>(null);
  let feedback = $state<TicketResult | null>(null);
  let busy = $state(false);
  let bugDetail = $state<any | null>(null);
  const ticket = $derived(chat.ticket);
  // Kinds present in the current list, for the filter bar.
  const source = $derived(history ? chat.ticketHistory : chat.tickets);
  const kinds = $derived([
    "all",
    ...Array.from(new Set(source.map((t: any) => t.kind))),
  ]);
  const q = $derived(search.trim().toLowerCase());
  // The open queue is small and already here, so it narrows as you type, on
  // the fields the server's search reads. The history is searched on the
  // server (it is paged there, and the conversation is not in the rows).
  function matches(t: any): boolean {
    if (!q) return true;
    return [t.short_id, t.subject, t.requester_name, t.account_name, t.preview, t.label, t.assignee]
      .some((v) => String(v ?? "").toLowerCase().includes(q));
  }
  // Filter by kind and state, then highest priority first, then longest-waiting.
  const rows = $derived(
    [...source]
      .filter((t: any) => kindFilter === "all" || t.kind === kindFilter)
      .filter((t: any) =>
        history || show === "all" ? true : show === "unclaimed" ? !t.assignee : t.status === show,
      )
      .filter((t: any) => history || matches(t))
      .sort(
        (a, b) => (b.priority ?? 0) - (a.priority ?? 0) || (a.updated ?? 0) - (b.updated ?? 0),
      ),
  );
  const counts = $derived({
    pending: chat.tickets.filter((t: any) => t.status === "pending").length,
    waiting: chat.tickets.filter((t: any) => t.status === "waiting").length,
    unclaimed: chat.tickets.filter((t: any) => !t.assignee).length,
  });
  function kindLabel(k: string) {
    if (k === "all") return "All";
    const t = source.find((x: any) => x.kind === k);
    return t ? t.label : k;
  }

  let historyTimer: ReturnType<typeof setTimeout> | null = null;
  $effect(() => {
    const term = search.trim();
    if (!history) return;
    if (historyTimer) clearTimeout(historyTimer);
    historyTimer = setTimeout(() => void chat.loadTicketHistory(term), term ? 300 : 0);
    return () => {
      if (historyTimer) clearTimeout(historyTimer);
    };
  });

  function open(t: any) {
    feedback = null;
    deciding = null;
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
    feedback = null;
    deciding = null;
  }
  async function act(run: () => Promise<TicketResult>) {
    if (busy) return;
    busy = true;
    feedback = await run();
    busy = false;
  }
  async function send() {
    const text = reply.trim();
    if (!text || !ticket) return;
    await act(() => chat.ticketReply(ticket.id, text, internal));
    if (feedback?.ok) reply = "";
  }
  async function decide() {
    if (!ticket || !deciding) return;
    const id = ticket.id;
    const why = reason.trim();
    await act(() => (deciding === "approve" ? chat.ticketApprove(id, why) : chat.ticketDeny(id, why)));
    if (feedback?.ok) {
      deciding = null;
      reason = "";
    }
  }
  // Enter sends; Shift+Enter is a new line.
  function onKey(e: KeyboardEvent) {
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
    return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  const isOpen = $derived(!!ticket && (ticket.status === "pending" || ticket.status === "waiting"));
  // The deep report fields ride in the payload too. The bug block renders them
  // from ticket_bug_detail with its own bounds; dumped inline they are an 8KB
  // traceback with collapsed newlines, one wall of text that buries the thread.
  const DEEP_PAYLOAD_KEYS = new Set(["traceback", "character_state", "subject"]);
  function payloadEntries(p: any): [string, any][] {
    return p ? Object.entries(p).filter(([k]) => !DEEP_PAYLOAD_KEYS.has(k)) : [];
  }
  function hasPayload(p: any) {
    return payloadEntries(p).length > 0;
  }
</script>

<div class="tickets">
  <div class="hd">
    <span class="tag glow-text">Ticket queue</span>
    {#if ticket}
      <button class="back" onclick={back}>‹ queue</button>
    {:else}
      <button class="tab" class:on={!history} onclick={() => (history = false)}>Open</button>
      <button class="tab" class:on={history} onclick={() => (history = true)}>History</button>
      <span class="count">{rows.length}</span>
    {/if}
  </div>

  {#if feedback}
    <p class="fb" class:err={!feedback.ok} role="status">{feedback.message}</p>
  {/if}

  {#if !ticket}
    <div class="filters">
      <input class="search" bind:value={search} placeholder={history ? "Search the record…" : "Search the queue…"}
        aria-label={history ? "Search closed tickets" : "Search open tickets"} />
      {#if !history}
        <div class="fl" role="radiogroup" aria-label="Show">
          <button class="fchip" role="radio" aria-checked={show === "all"} class:on={show === "all"} onclick={() => (show = "all")}>All</button>
          <button class="fchip" role="radio" aria-checked={show === "pending"} class:on={show === "pending"} onclick={() => (show = "pending")}>Needs reply {counts.pending}</button>
          <button class="fchip" role="radio" aria-checked={show === "waiting"} class:on={show === "waiting"} onclick={() => (show = "waiting")}>On player {counts.waiting}</button>
          <button class="fchip" role="radio" aria-checked={show === "unclaimed"} class:on={show === "unclaimed"} onclick={() => (show = "unclaimed")}>Unclaimed {counts.unclaimed}</button>
        </div>
      {/if}
      {#if kinds.length > 2}
        <div class="fl">
          {#each kinds as k}
            <button class="fchip" class:on={kindFilter === k} onclick={() => (kindFilter = k)}>
              {kindLabel(k)}
            </button>
          {/each}
        </div>
      {/if}
    </div>
    <div class="list">
      {#if rows.length}
        {#each rows as t (t.id)}
          <button class="row" data-kind={t.kind} onclick={() => open(t)}>
            <span class="r1">
              <span class="kind">{t.label} <span class="sid">#{t.short_id}</span></span>
              <span class="meta">
                {#if t.priority > 0}<span class="pri" title="priority">▲{t.priority}</span>{/if}
                {#if t.assignee}<span class="asg" title="claimed by {t.assignee}">◆ {t.assignee}</span>{/if}
                <span class="status s-{t.status}">{t.status}</span>
                <span class="age">{ageOf(t.updated)}</span>
              </span>
            </span>
            <span
              class="who"
              title={t.account_name ? `account: ${t.account_name}` : undefined}
            >{t.requester_name || t.account_name || t.short_id}</span>
            {#if t.subject}<span class="subject">{t.subject}</span>{/if}
            <span class="prev">{t.preview || "-"}</span>
          </button>
        {/each}
      {:else}
        <p class="empty">
          {#if q}Nothing {history ? "in the record " : "in the queue "}matches “{search.trim()}”.
          {:else}No {kindFilter === "all" ? "" : kindLabel(kindFilter).toLowerCase() + " "}tickets{history ? " in history" : ""}.{/if}
        </p>
      {/if}
    </div>
  {:else}
    <div class="convo">
      <div class="head">
        <span class="petitioner">
          {#if ticket.subject}{ticket.subject}{:else}{ticket.label}{/if}
          <span class="sub">
            {ticket.label} #{ticket.short_id}: {ticket.requester_name || ticket.account_name || ticket.short_id}
            {#if ticket.requester_name && ticket.account_name && ticket.requester_name !== ticket.account_name}
              <span class="acct">({ticket.account_name})</span>
            {/if}
            · <span class="status s-{ticket.status}">{ticket.status}</span>
            {#if ticket.assignee}· ◆ {ticket.assignee}{/if}
          </span>
        </span>
        <span class="actions">
          {#if isOpen}
            <button class="act" disabled={busy} onclick={() => act(() => chat.ticketClaim(ticket.id))}>Claim</button>
            {#if ticket.approvable}
              <button class="act ok" disabled={busy} onclick={() => (deciding = "approve")}>Approve</button>
              <button class="act no" disabled={busy} onclick={() => (deciding = "deny")}>Deny</button>
            {:else}
              <button class="act" disabled={busy} onclick={() => act(() => chat.ticketResolve(ticket.id))}>Close</button>
            {/if}
          {:else if !ticket.approvable}
            <button class="act" disabled={busy} onclick={() => act(() => chat.ticketReopen(ticket.id))}>Reopen</button>
          {/if}
        </span>
      </div>

      {#if deciding}
        <div class="decide">
          <input bind:value={reason} placeholder={deciding === "approve" ? "note for the player (optional)…" : "reason, shown to the player…"}
            aria-label="Reason" onkeydown={(e) => e.key === "Enter" && (e.preventDefault(), void decide())} />
          <button class="act" class:ok={deciding === "approve"} class:no={deciding === "deny"} disabled={busy} onclick={decide}>
            {deciding === "approve" ? "Approve" : "Deny"}
          </button>
          <button class="act" onclick={() => { deciding = null; reason = ""; }}>Cancel</button>
        </div>
      {/if}

      <div class="body">
        {#if hasPayload(ticket.payload)}
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

        <div class="msgs" role="log" aria-label="Conversation">
          {#each ticket.messages ?? [] as m, i (i)}
            {#if m.origin === "system"}
              <div class="sys"><span class="mts">{stamp(m.ts)}</span> {m.text}</div>
            {:else}
              <div class="m" class:note={m.visibility === "internal"} class:staffmsg={m.origin === "staff"}>
                <span class="s">{@html renderSender(m.sender_html ?? m.senderHtml, m.sender)}</span>
                {#if m.origin === "player"}<span class="role">player</span>{/if}
                <span class="mts">{stamp(m.ts)}</span>
                <span class="t">{@html renderBody(m.html, m.text)}</span>
              </div>
            {/if}
          {/each}
          {#if !(ticket.messages ?? []).length}<p class="empty">No messages yet.</p>{/if}
        </div>
      </div>

      <div class="reply">
        <label class="int"><input type="checkbox" bind:checked={internal} /> note</label>
        <textarea
          bind:value={reply}
          onkeydown={onKey}
          rows="2"
          placeholder={internal ? "internal staff note… (Enter sends)" : "reply to player… (Enter sends, Shift+Enter for a new line)"}
          aria-label="ticket reply"
        ></textarea>
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
  .asg { color: var(--accent-bright); font-size: 0.6rem; text-transform: uppercase; }
  .age { color: var(--fg-faint); font-size: 0.66rem; }
  .status {
    font-size: 0.56rem; text-transform: uppercase; letter-spacing: 0.08em;
    padding: 1px 5px; border: 1px solid currentColor; border-radius: 2px;
  }
  .s-pending { color: var(--accent-bright); }
  .s-waiting { color: var(--gold); }
  .s-closed, .s-approved, .s-denied, .s-resolved, .s-withdrawn { color: var(--fg-faint); }
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
  .ck { color: var(--accent-bright); text-transform: uppercase; font-size: 0.62rem; letter-spacing: 0.06em; }
  .cv { color: var(--fg); }
  .body { flex: 1; min-height: 0; overflow-y: auto; }
  .msgs { padding: 6px 10px; line-height: 1.5; }
  .m { padding: 2px 0; font-size: 0.85rem; }
  .m .s { color: var(--accent-bright); margin-right: 0.6ch; }
  .m .t { color: var(--fg); white-space: pre-wrap; }
  .m.note { opacity: 0.8; }
  .m.note .s::after { content: " (note)"; color: var(--danger, #e5484d); font-size: 0.7em; }
  .reply {
    display: flex; align-items: center; gap: 0.6rem; padding: 6px 10px;
    border-top: 1px solid var(--accent); flex: 0 0 auto;
  }
  .int { color: var(--fg-dim); font-size: 0.62rem; text-transform: uppercase; display: flex; align-items: center; gap: 3px; }
  .reply textarea {
    flex: 1; background: transparent; border: none; outline: none; resize: vertical;
    color: var(--fg); font-family: inherit; font-size: 0.85rem; caret-color: var(--accent-bright);
  }
  .reply textarea::placeholder { color: var(--fg-faint); font-style: italic; }
  .fb { margin: 0; padding: 4px 10px; font-size: 0.74rem; color: var(--ok, var(--accent-bright)); border-bottom: 1px solid var(--border); }
  .fb.err { color: var(--alert); }
  .filters { flex-direction: column; align-items: stretch; }
  .fl { display: flex; flex-wrap: wrap; gap: 4px; }
  .search { background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg); font-family: inherit; font-size: 0.8rem; padding: 4px 7px; min-height: 26px; }
  .search:focus { outline: none; border-color: var(--accent); }
  .sid { color: var(--fg-faint); font-size: 0.62rem; letter-spacing: 0; }
  .decide { display: flex; gap: 4px; padding: 5px 10px; border-bottom: 1px solid var(--border); }
  .decide input { flex: 1; background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg); font-family: inherit; font-size: 0.78rem; padding: 3px 6px; }
  .act:disabled { opacity: 0.5; cursor: default; }
  .sys { color: var(--fg-dim); font-size: 0.74rem; font-style: italic; text-align: center; padding: 2px 0; }
  .mts { color: var(--fg-faint); font-size: 0.66rem; margin-right: 0.6ch; }
  .role { font-size: 0.56rem; text-transform: uppercase; letter-spacing: 0.12em; color: var(--gold); border: 1px solid currentColor; padding: 0 4px; margin-right: 0.6ch; }
  .m.staffmsg .s { color: var(--accent-bright); }
  .subject { color: var(--fg); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .petitioner .sub { display: block; color: var(--fg-dim); font-weight: 400; font-size: 0.72rem; }
  .acct { color: var(--fg-faint); }
</style>
