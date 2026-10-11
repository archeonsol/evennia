<script lang="ts">
  // A player's own requests: find one, read it, answer it, send a new one.
  //
  // Every action is a request to the game that answers inside the panel, so
  // nothing a player does here prints into their terminal. Whether a reply is
  // unread is kept by the server, so another device and the login line agree
  // with this one. Live staff replies arrive as `ticket_msg`; with the panel
  // closed the tickets store raises a toast instead.
  import "../styles/tickets.css";
  import { onMount } from "svelte";
  import { connection } from "../lib/evennia.svelte";
  import { focusOnMount } from "../lib/focus";
  import { renderBody, renderSender } from "../lib/markup";
  import { tickets, type NewRequest, type TicketResult } from "../lib/tickets.svelte";
  import { ageSince, inMineView, isOpen, mineCounts, playerState, type MineView } from "../lib/ticketModel";

  type Kind = NewRequest["kind"];

  // The words of the form are the game's, asked for once (see lib/ticketForm.ts).
  const f = $derived(tickets.form);

  let view = $state<MineView>("open");
  let query = $state("");
  let reply = $state("");
  let picking = $state(false);
  let kind = $state<Kind | null>(null);
  const BLANK = { subject: "", title: "", text: "", category: "Other", severity: "Minor", npc: "", said: "", goal: "", contact: "" };
  let fields = $state({ ...BLANK });
  let tips = $state<{ key: string; summary: string }[]>([]);
  let feedback = $state<TicketResult | null>(null);
  let busy = $state(false);
  let confirmWithdraw = $state(false);
  let now = $state(Math.floor(Date.now() / 1000));

  const ticket = $derived(tickets.myTicket);
  const counts = $derived(mineCounts(tickets.myRows));
  const rows = $derived(query.trim() ? tickets.myRows : tickets.myRows.filter((r) => inMineView(r, view)));
  const canReply = $derived(!!ticket && (isOpen(ticket.status) || !ticket.approvable));
  const canWithdraw = $derived(!!ticket && isOpen(ticket.status) && !ticket.approvable);
  const composing = $derived(picking || kind !== null);

  // Load when the socket is open (a layout restored at page load mounts this
  // panel before it is), whenever the view or the search changes, and when one of
  // the requests changes. The search is debounced; a view change is not.
  let searchTimer: ReturnType<typeof setTimeout> | null = null;
  $effect(() => {
    const q = query.trim();
    const includeClosed = view === "all" || !!q;
    void tickets.myRev;
    if (connection.state !== "open") return;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      tickets.mySearch = q;
      void tickets.loadMine(includeClosed, q);
    }, q ? 250 : 0);
    return () => {
      if (searchTimer) clearTimeout(searchTimer);
    };
  });

  // Help pages that may answer the question before it goes to staff.
  let tipTimer: ReturnType<typeof setTimeout> | null = null;
  $effect(() => {
    const words = `${fields.subject} ${fields.text}`.trim();
    if (kind !== "request") {
      tips = [];
      return;
    }
    if (tipTimer) clearTimeout(tipTimer);
    tipTimer = setTimeout(async () => {
      tips = await tickets.suggest(words);
    }, 500);
    return () => {
      if (tipTimer) clearTimeout(tipTimer);
    };
  });

  onMount(() => {
    const timer = setInterval(() => (now = Math.floor(Date.now() / 1000)), 30000);
    return () => clearInterval(timer);
  });

  // The game can ask for a form (`@request`, `@bug`, `@report` or `@puppetrequest` typed
  // in the terminal): leave any open request and show the picker, or the form of the kind
  // it names, at once, whether the panel was open or has just mounted.
  $effect(() => {
    if (!tickets.wantsNewRequest) return;
    const named = tickets.composeKind;
    tickets.wantsNewRequest = false;
    tickets.composeKind = "";
    tickets.closeMine();
    startNew();
    if (named) kind = named;
  });

  function stamp(ts: number): string {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    const p = (n: number) => String(n).padStart(2, "0");
    const today = new Date().toDateString() === d.toDateString();
    return today ? `${p(d.getHours())}:${p(d.getMinutes())}` : `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function emptyLine(): string {
    if (query.trim()) return "Nothing of yours matches.";
    if (view === "answered") return "No request is waiting for your reply.";
    if (view === "open") return "You have no open requests.";
    return "You have not sent any requests.";
  }

  function open(row: { id: string }): void {
    feedback = null;
    confirmWithdraw = false;
    reply = "";
    void tickets.openMine(row.id);
  }

  function back(): void {
    feedback = null;
    confirmWithdraw = false;
    tickets.closeMine();
  }

  function startNew(): void {
    feedback = null;
    picking = true;
    kind = null;
    void tickets.loadForm();
  }

  function cancelNew(): void {
    picking = false;
    kind = null;
    feedback = null;
  }

  async function send(): Promise<void> {
    const text = reply.trim();
    if (!text || !ticket || busy) return;
    busy = true;
    const result = await tickets.mineAct(ticket.id, "reply", text);
    busy = false;
    feedback = result;
    if (result.ok) reply = "";
  }

  async function withdraw(): Promise<void> {
    if (!ticket || busy) return;
    if (!confirmWithdraw) {
      confirmWithdraw = true;
      return;
    }
    busy = true;
    feedback = await tickets.mineAct(ticket.id, "withdraw");
    busy = false;
    confirmWithdraw = false;
  }

  async function file(): Promise<void> {
    if (busy || !kind) return;
    const text = fields.text.trim();
    const body: NewRequest =
      kind === "bug"
        ? { kind, title: fields.title.trim(), text, category: fields.category, severity: fields.severity }
        : kind === "puppet"
          ? { kind, npc: fields.npc.trim(), said: fields.said.trim(), goal: fields.goal.trim(), contact: fields.contact.trim() }
          : { kind, subject: fields.subject.trim(), text };
    busy = true;
    const result = await tickets.file(body);
    busy = false;
    feedback = result;
    if (result.ok) {
      picking = false;
      kind = null;
      fields = { ...BLANK };
    }
  }

  // Enter sends; Shift+Enter is a new line, for the multi-line answer a support
  // conversation often needs.
  function onReplyKey(e: KeyboardEvent): void {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }
</script>

<div class="tk" role="region" aria-label="My requests">
  <div class="tk-hd">
    <!-- Staff have the tab bar above to say whose requests these are. -->
    {#if !tickets.staff}<span class="tk-title">My requests</span>{/if}
    {#if ticket}
      <button class="tk-btn quiet" style="margin-left:auto" onclick={back}>Back to the list</button>
    {:else if composing}
      <button class="tk-btn quiet" style="margin-left:auto" onclick={cancelNew}>Cancel</button>
    {:else}
      <button class="tk-btn primary" style="margin-left:auto" onclick={startNew}>New request</button>
    {/if}
  </div>

  {#if feedback}
    <p class="tk-fb" class:err={!feedback.ok} role="status">{feedback.message}</p>
  {/if}

  {#if picking && !kind}
    <div class="tk-pick">
      <p class="tk-note">{f.pick}</p>
      {#each f.kinds as k (k.kind)}
        <button onclick={() => (kind = k.kind)}>
          <span>{k.name}</span>
          <small>{k.hint}</small>
        </button>
      {/each}
    </div>
  {:else if kind}
    <form class="tk-form" onsubmit={(e) => { e.preventDefault(); void file(); }}>
      {#if kind === "request" || kind === "report"}
        <label>
          <span>{f.summary}</span>
          <input
            bind:value={fields.subject}
            maxlength="120"
            placeholder={f.placeholders[`${kind}_summary`] ?? ""}
            use:focusOnMount
          />
        </label>
        <label>
          <span>{f.details}</span>
          <textarea bind:value={fields.text} rows="6" placeholder={f.placeholders[`${kind}_details`] ?? ""}></textarea>
        </label>
        {#if kind === "request" && tips.length}
          <div class="tk-tips">
            These help pages may answer it:
            {#each tips as tip (tip.key)}
              <button type="button" onclick={() => connection.sendCommand(`help ${tip.key}`)}>help {tip.key}: {tip.summary}</button>
            {/each}
          </div>
        {/if}
        {#if f.notes[kind]}<p class="tk-note">{f.notes[kind]}</p>{/if}
      {:else if kind === "bug"}
        <label>
          <span>{f.summary}</span>
          <input bind:value={fields.title} maxlength="120" placeholder={f.placeholders.bug_summary ?? ""} use:focusOnMount />
        </label>
        <label>
          <span>{f.category}</span>
          <select bind:value={fields.category}>
            {#each f.categories as c (c)}<option value={c}>{c}</option>{/each}
          </select>
        </label>
        <label>
          <span>{f.severity}</span>
          <select bind:value={fields.severity} aria-describedby="tk-severity-advice">
            {#each f.severities as s (s.key)}<option value={s.key}>{s.text}</option>{/each}
          </select>
          <span id="tk-severity-advice" class="tk-note">{f.severity_advice}</span>
        </label>
        <label>
          <span>{f.details}</span>
          <textarea bind:value={fields.text} rows="6" placeholder={f.placeholders.bug_details ?? ""}></textarea>
        </label>
      {:else}
        <label>
          <span>{f.npc}</span>
          <input bind:value={fields.npc} maxlength="120" placeholder={f.placeholders.npc ?? ""} use:focusOnMount />
        </label>
        <label>
          <span>{f.said}</span>
          <textarea bind:value={fields.said} rows="3"></textarea>
        </label>
        <label>
          <span>{f.goal}</span>
          <textarea bind:value={fields.goal} rows="3"></textarea>
        </label>
        <label>
          <span>{f.contact}</span>
          <input bind:value={fields.contact} maxlength="240" placeholder={f.placeholders.contact ?? ""} />
        </label>
        {#if f.notes.puppet}<p class="tk-note">{f.notes.puppet}</p>{/if}
      {/if}
      <div class="tk-btns">
        <button class="tk-btn primary" type="submit" disabled={busy}>Send</button>
        <button class="tk-btn quiet" type="button" onclick={() => (kind = null)}>Choose another kind</button>
      </div>
    </form>
  {:else if !ticket}
    <div class="tk-tools">
      <input class="tk-search" bind:value={query} placeholder="Search by number, subject or text" aria-label="Search your requests" />
      <div class="tk-views">
        <div class="tk-radios" role="radiogroup" aria-label="Show">
          <button class="tk-view" role="radio" aria-checked={view === "open"} onclick={() => (view = "open")}>Open<b>{counts.open}</b></button>
          <button class="tk-view" role="radio" aria-checked={view === "answered"} onclick={() => (view = "answered")}>Answered<b>{counts.answered}</b></button>
          <button class="tk-view" role="radio" aria-checked={view === "all"} onclick={() => (view = "all")}>All</button>
        </div>
      </div>
    </div>
    <div class="tk-list" role="list" aria-label="Your requests">
      {#if tickets.myError}
        <p class="tk-empty err" role="alert">
          {tickets.myError}
          <button class="tk-btn" onclick={() => (tickets.myRev += 1)}>Try again</button>
        </p>
      {/if}
      {#each rows as r (r.id)}
        <div role="listitem">
          <button class="tk-row plain" class:new={r.unread} onclick={() => open(r)}>
            <span class="tk-l1">
              <span class="tk-row-title">{r.title || r.subject || r.label}</span>
              <span class="tk-clock">{ageSince(r.updated, now)}</span>
            </span>
            <span class="tk-l2">
              <span class="tk-id">{r.ref}</span>
              <span>{r.label}</span>
              <span class="tk-state" class:mute={!r.unread && r.status !== "waiting"}>
                {playerState(r)}{r.unread ? ", new reply" : ""}
              </span>
            </span>
            {#if r.preview}<span class="tk-l3">{r.preview}</span>{/if}
          </button>
        </div>
      {:else}
        {#if !tickets.myError}<p class="tk-empty">{emptyLine()}</p>{/if}
      {/each}
    </div>
  {:else}
    <div class="tk-detail">
      <div class="tk-dh">
        <div class="tk-dt">{ticket.title || ticket.subject || ticket.label}</div>
        <div class="tk-ds">
          <span>{ticket.label} {ticket.ref}</span>
          <span class="tk-state" class:mute={ticket.status !== "waiting"}>{playerState(ticket)}</span>
          {#if ticket.assignee}<span>{ticket.assignee} is handling it</span>{/if}
        </div>
      </div>
      <div class="tk-scroll">
        <div class="tk-msgs" role="log" aria-label="Conversation">
          {#each ticket.messages ?? [] as m, i (i)}
            {#if m.origin === "system"}
              <div class="tk-sys">{stamp(m.ts)} {m.text}</div>
            {:else}
              <div class="tk-msg" class:staff={m.origin === "staff"} class:mine={m.origin === "player"}>
                <span class="tk-who">
                  <b>{@html renderSender(m.sender_html ?? m.senderHtml, m.sender)}</b>
                  {m.origin === "staff" ? "Staff" : m.origin === "player" ? "You" : ""}, {stamp(m.ts)}
                </span>
                <span class="tk-text">{@html renderBody(m.html, m.text)}</span>
              </div>
            {/if}
          {/each}
          {#if !(ticket.messages ?? []).length}<p class="tk-empty">No messages yet.</p>{/if}
        </div>
      </div>
      {#if canReply}
        <div class="tk-composer">
          <div class="tk-to">To <b>staff</b> · {ticket.ref}</div>
          <textarea
            class="tk-box"
            bind:value={reply}
            onkeydown={onReplyKey}
            rows="3"
            placeholder={isOpen(ticket.status) ? "Write a reply" : "Write a reply to open this again"}
            aria-label="Reply"
            aria-describedby="mine-reply-keys"
          ></textarea>
          <div class="tk-foot">
            <span id="mine-reply-keys">Enter sends, Shift+Enter starts a new line.</span>
            {#if canWithdraw}
              <button class="tk-btn warn" onclick={withdraw} disabled={busy}>
                {confirmWithdraw ? "Yes, withdraw it" : "Withdraw"}
              </button>
            {/if}
            <button class="tk-btn primary grow" onclick={send} disabled={busy || !reply.trim()}>
              {isOpen(ticket.status) ? "Send" : "Send and open again"}
            </button>
          </div>
        </div>
      {:else}
        <p class="tk-empty">This decision is final. Send a new request if you need more.</p>
      {/if}
    </div>
  {/if}
</div>
