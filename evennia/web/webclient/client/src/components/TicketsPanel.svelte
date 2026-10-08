<script lang="ts">
  // The staff Ticket Queue: find a ticket, read it, act on it.
  //
  // Actions go through the ticket_act RPC (chat.ticketAct), so each button's
  // answer shows here, not as "Posted." or "Ticket #... closed." in the
  // terminal beside the panel.
  import { chat, type TicketResult } from "../lib/chat.svelte";
  import { renderBody, renderSender } from "../lib/markup";
  import { TICKET_SORTS, loadTicketSort, saveTicketSort, sortTickets, type TicketSort } from "../lib/ticketSort";

  type Show = "all" | "pending" | "waiting" | "unclaimed";

  let reply = $state("");
  let internal = $state(false);
  let history = $state(false);
  let kindFilter = $state("all");
  let show = $state<Show>("all");
  let sort = $state<TicketSort>(loadTicketSort());
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
  // A kind filter whose last ticket closed would hide every row, and the
  // filter bar that could clear it is gone below two kinds.
  const kind = $derived(kinds.includes(kindFilter) ? kindFilter : "all");
  const q = $derived(search.trim().toLowerCase());
  // The open queue is small and already here, so it narrows as you type, on
  // the fields the server's search reads. The history is searched on the
  // server (it is paged there, and the conversation is not in the rows).
  function matches(t: any): boolean {
    if (!q) return true;
    return [t.short_id, t.subject, t.requester_name, t.account_name, t.preview, t.label, t.assignee]
      .some((v) => String(v ?? "").toLowerCase().includes(q));
  }
  // The history keeps the server's order: latest decision first.
  const filtered = $derived(
    source
      .filter((t: any) => kind === "all" || t.kind === kind)
      .filter((t: any) =>
        history || show === "all" ? true : show === "unclaimed" ? !t.assignee : t.status === show,
      )
      .filter((t: any) => history || matches(t)),
  );
  const rows = $derived(history ? filtered : sortTickets(filtered, sort));
  const ageTitle = $derived(sort === "activity" || history ? "since last activity" : "since filed");
  function setSort(next: TicketSort) {
    sort = next;
    saveTicketSort(next);
  }
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

  // Status plate colours, shared with the player's panel: the move that is
  // due stands out, finished tickets fade.
  const PLATE: Record<string, string> = { pending: "hot", waiting: "gold", approved: "ok" };
</script>

<div class="tickets">
  <div class="hd">
    <span class="title glow-text">Ticket queue</span>
    {#if ticket}
      <button class="sh-cmd back" onclick={back}>Back</button>
    {:else}
      <button class="sh-toggle" aria-pressed={!history} onclick={() => (history = false)}>Open</button>
      <button class="sh-toggle" aria-pressed={history} onclick={() => (history = true)}>History</button>
      <span class="count" title={rows.length < source.length ? "shown of loaded" : undefined}
        >{rows.length < source.length ? `${rows.length} / ${source.length}` : rows.length}</span>
    {/if}
  </div>

  {#if feedback}
    <p class="fb" class:err={!feedback.ok} role="status">{feedback.message}</p>
  {/if}

  {#if !ticket}
    <div class="filters">
      <input class="sh-field search" bind:value={search} placeholder={history ? "Search record" : "Search queue"}
        aria-label={history ? "Search closed tickets" : "Search open tickets"} />
      {#if !history}
        <div class="fl" role="radiogroup" aria-label="Show">
          <button class="sh-toggle" role="radio" aria-checked={show === "all"} onclick={() => (show = "all")}>All</button>
          <button class="sh-toggle" role="radio" aria-checked={show === "pending"} onclick={() => (show = "pending")}>Needs reply<span class="sh-count">{counts.pending}</span></button>
          <button class="sh-toggle" role="radio" aria-checked={show === "waiting"} onclick={() => (show = "waiting")}>On player<span class="sh-count">{counts.waiting}</span></button>
          <button class="sh-toggle" role="radio" aria-checked={show === "unclaimed"} onclick={() => (show = "unclaimed")}>Unclaimed<span class="sh-count">{counts.unclaimed}</span></button>
        </div>
        <div class="fl" role="radiogroup" aria-label="Sort">
          {#each TICKET_SORTS as o (o.key)}
            <button class="sh-toggle" role="radio" aria-checked={sort === o.key} onclick={() => setSort(o.key)}>{o.label}</button>
          {/each}
        </div>
      {/if}
      {#if kinds.length > 2}
        <div class="fl">
          {#each kinds as k}
            <button class="sh-toggle" aria-pressed={kind === k} onclick={() => (kindFilter = k)}>
              {kindLabel(k)}
            </button>
          {/each}
        </div>
      {/if}
    </div>
    <div class="list">
      {#if rows.length}
        {#each rows as t (t.id)}
          <button class="sh-row row" data-kind={t.kind} onclick={() => open(t)}>
            <span class="r1">
              <span class="kind">{t.label} <span class="sid">#{t.short_id}</span></span>
              <span class="meta">
                {#if t.priority > 0}<span class="pri" title="priority">▲{t.priority}</span>{/if}
                {#if t.assignee}<span class="asg" title="claimed by {t.assignee}">◆ {t.assignee}</span>{/if}
                <span class="sh-plate {PLATE[t.status] ?? ''}">{t.status}</span>
                <span class="age" title={ageTitle}>{ageOf(sort === "activity" || history ? t.updated : t.created)}</span>
              </span>
            </span>
            <span
              class="who"
              title={t.account_name ? `account: ${t.account_name}` : undefined}
            >{t.requester_name || t.account_name || t.short_id}</span>
            {#if t.subject}<span class="subject">{t.subject}</span>{/if}
            {#if t.preview}<span class="prev">{t.preview}</span>{/if}
          </button>
        {/each}
      {:else}
        <p class="empty">
          {#if q}No match.
          {:else}No {kind === "all" ? "" : kindLabel(kind).toLowerCase() + " "}tickets{history ? " in history" : ""}.{/if}
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
            <span class="sh-plate {PLATE[ticket.status] ?? ''}">{ticket.status}</span>
            {#if ticket.assignee}· ◆ {ticket.assignee}{/if}
          </span>
        </span>
        <span class="actions">
          {#if isOpen}
            <button class="sh-cmd act" disabled={busy} onclick={() => act(() => chat.ticketClaim(ticket.id))}>Claim</button>
            {#if ticket.approvable}
              <button class="sh-cmd primary act" disabled={busy} onclick={() => (deciding = "approve")}>Approve</button>
              <button class="sh-cmd warn act" disabled={busy} onclick={() => (deciding = "deny")}>Deny</button>
            {:else}
              <button class="sh-cmd act" disabled={busy} onclick={() => act(() => chat.ticketResolve(ticket.id))}>Close</button>
            {/if}
          {:else if !ticket.approvable}
            <button class="sh-cmd act" disabled={busy} onclick={() => act(() => chat.ticketReopen(ticket.id))}>Reopen</button>
          {/if}
        </span>
      </div>

      {#if deciding}
        <div class="decide">
          <input class="sh-field" bind:value={reason} placeholder={deciding === "approve" ? "Note to player (optional)" : "Reason (the player sees it)"}
            aria-label="Reason" onkeydown={(e) => e.key === "Enter" && (e.preventDefault(), void decide())} />
          <button class="sh-cmd act" class:primary={deciding === "approve"} class:warn={deciding === "deny"} disabled={busy} onclick={decide}>
            {deciding === "approve" ? "Approve" : "Deny"}
          </button>
          <button class="sh-cmd act" onclick={() => { deciding = null; reason = ""; }}>Cancel</button>
        </div>
      {/if}

      <div class="body">
        {#if hasPayload(ticket.payload)}
          <div class="ctx">
            {#each payloadEntries(ticket.payload) as [k, v]}
              <div class="cx"><span class="ck">{k}</span> <span class="cv">{v}</span></div>
            {/each}
            {#if ticket.kind === "bug" && !bugDetail}
              <button class="sh-cmd loadbug" onclick={loadBug}>Report detail</button>
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
                {#if m.origin === "player"}<span class="sh-plate gold">Player</span>{/if}
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
          class="sh-placeholder"
          placeholder={internal ? "Staff note" : "Reply to player"}
          aria-label="ticket reply"
          aria-describedby="queue-reply-keys"
        ></textarea>
        <span id="queue-reply-keys" class="sr-only">Enter sends. Shift+Enter starts a new line.</span>
      </div>
    </div>
  {/if}
</div>

<style>
  .tickets { display: flex; flex-direction: column; height: 100%; background: var(--bg-elev); }
  .hd {
    display: flex; align-items: center; gap: 0.6ch; padding: 5px 8px 5px 10px;
    border-bottom: 1px solid var(--accent); flex: 0 0 auto;
  }
  .title { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.2em; font-size: 0.74rem; margin-right: 1ch; }
  .count { margin-left: auto; color: var(--gold); font-size: 0.66rem; letter-spacing: 0.1em; }
  .back { margin-left: auto; }
  .filters {
    display: flex; flex-direction: column; align-items: stretch; gap: 4px; padding: 6px 10px;
    border-bottom: 1px solid var(--border); flex: 0 0 auto;
  }
  .fl { display: flex; flex-wrap: wrap; gap: 2px 6px; }
  .loadbug { align-self: flex-start; margin: 3px 0 0 -0.5ch; }
  .bug { padding: 6px 10px; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 3px; }
  .bl { font-size: 0.74rem; color: var(--fg); }
  .dim { color: var(--fg-faint); }
  .tb {
    margin: 2px 0 4px; padding: 6px; background: var(--bg-deep); border-left: 1px solid var(--border-bright);
    color: var(--fg-dim); font-size: 0.7rem; max-height: 180px; overflow: auto; white-space: pre-wrap;
  }
  .list { overflow-y: auto; flex: 1; min-height: 0; }
  .r1 { display: flex; justify-content: space-between; align-items: baseline; gap: 1ch; }
  .kind { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.12em; font-size: 0.68rem; }
  .row[data-kind="bug"] .kind { color: var(--alert); }
  .row[data-kind="puppet"] .kind { color: var(--gold); }
  .row[data-kind="report"] .kind { color: var(--fg); }
  .meta { display: flex; align-items: baseline; gap: 0.8ch; }
  .pri { color: var(--alert); font-size: 0.62rem; }
  .asg { color: var(--fg-dim); font-size: 0.6rem; letter-spacing: 0.1em; text-transform: uppercase; }
  .age { color: var(--fg-faint); font-size: 0.64rem; }
  .who { color: var(--gold); font-size: 0.78rem; }
  .prev { color: var(--fg-dim); font-size: 0.74rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .empty { color: var(--fg-faint); padding: 12px 10px; margin: 0; font-size: 0.68rem; letter-spacing: 0.14em; text-transform: uppercase; }

  .convo { display: flex; flex-direction: column; min-height: 0; flex: 1; }
  .head {
    display: flex; align-items: flex-start; gap: 1ch; padding: 7px 10px;
    border-bottom: 1px solid var(--border);
  }
  .petitioner { color: var(--gold); letter-spacing: 0.04em; font-size: 0.86rem; }
  .petitioner .sub { display: flex; flex-wrap: wrap; align-items: center; gap: 0.8ch; margin-top: 3px; color: var(--fg-dim); font-size: 0.7rem; }
  .acct { color: var(--fg-faint); }
  .actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 2px; margin-left: auto; }
  .ctx { padding: 6px 10px; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 2px; }
  .cx { font-size: 0.74rem; }
  .ck { color: var(--fg-faint); text-transform: uppercase; font-size: 0.6rem; letter-spacing: 0.14em; }
  .cv { color: var(--fg); }
  .body { flex: 1; min-height: 0; overflow-y: auto; }
  .msgs { padding: 8px 10px; line-height: 1.5; display: flex; flex-direction: column; gap: 8px; }
  .m { padding-left: 1.5ch; border-left: 1px solid var(--border-bright); font-size: 0.85rem; }
  .m.staffmsg { border-left-color: var(--accent); }
  .m .s { color: var(--accent-bright); margin-right: 0.6ch; }
  .m .t { display: block; color: var(--fg); white-space: pre-wrap; }
  .m.note { border-left-style: dashed; }
  .m.note .s::after { content: " (note)"; content: " (note)" / ""; color: var(--alert); font-size: 0.7em; letter-spacing: 0.1em; text-transform: uppercase; }
  .m .sh-plate { margin-right: 0.6ch; }
  .reply {
    display: flex; align-items: center; gap: 0.8rem; padding: 7px 10px;
    border-top: 1px solid var(--accent); flex: 0 0 auto;
  }
  .int { color: var(--fg-dim); font-size: 0.6rem; letter-spacing: 0.14em; text-transform: uppercase; display: flex; align-items: center; gap: 4px; }
  .reply textarea {
    flex: 1; background: transparent; border: none; outline: none; resize: vertical;
    color: var(--fg); font-family: inherit; font-size: 0.85rem; caret-color: var(--accent-bright);
  }
  .fb { margin: 0; padding: 4px 10px; font-size: 0.72rem; color: var(--ok, var(--accent-bright)); border-bottom: 1px solid var(--border); }
  .fb.err { color: var(--alert); }
  .sid { color: var(--fg-faint); font-size: 0.62rem; letter-spacing: 0; }
  .decide { display: flex; align-items: center; gap: 4px; padding: 6px 10px; border-bottom: 1px solid var(--border); }
  .decide input { flex: 1; }
  .sys { color: var(--fg-faint); font-size: 0.64rem; letter-spacing: 0.12em; text-transform: uppercase; padding: 2px 0; }
  .sys::before { content: "-- "; content: "-- " / ""; }
  .mts { color: var(--fg-faint); font-size: 0.64rem; margin-right: 0.6ch; }
  .subject { color: var(--fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
