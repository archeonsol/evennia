<script lang="ts">
  // The staff Tickets workbench: find a ticket, read it, answer it, hand it on.
  //
  // One flat list in the server's order (players in the room first, then the
  // ones online, then the rest, the unanswered before the answered), and the open
  // ticket beside it on a wide panel or in place of it on a narrow one. Every
  // button is a request that answers with the ticket as it now stands, so
  // nothing types a command and nothing it confirms lands in the terminal.
  import "../styles/tickets.css";
  import { onMount, tick } from "svelte";
  import { isTypingTarget } from "../lib/focus";
  import { renderBody, renderSender } from "../lib/markup";
  import { tickets, type TicketResult } from "../lib/tickets.svelte";
  import {
    VIEW_LABELS,
    ageSince,
    clockClass,
    clockText,
    dotClass,
    inView,
    isFaint,
    isHot,
    isOpen,
    presenceText,
    priorityWord,
    search,
    sentence,
    step,
    viewCounts,
    whoIs,
    type TicketRow,
    type ViewKey,
  } from "../lib/ticketModel";

  const VIEW_ORDER: ViewKey[] = ["mine", "unanswered", "answered", "online", "all"];

  let query = $state("");
  let kind = $state("all");
  let history = $state(false);
  let cursor = $state<string | null>(null);
  let tab = $state<"reply" | "note">("reply");
  let reply = $state("");
  let feedback = $state<TicketResult | null>(null);
  let busy = $state(false);
  let form = $state<"" | "assign" | "merge" | "approve" | "deny">("");
  let formText = $state("");
  let menu = $state(false);
  let bug = $state<any | null>(null);
  let npc = $state<{ ok: boolean; message: string; command: string } | null>(null);
  let now = $state(Math.floor(Date.now() / 1000));
  let root = $state<HTMLElement | null>(null);
  let scroller = $state<HTMLElement | null>(null);
  let box = $state<HTMLTextAreaElement | null>(null);
  let searchBox = $state<HTMLInputElement | null>(null);

  const t = $derived(tickets.ticket);
  const me = $derived(tickets.accountId);
  const counts = $derived(viewCounts(tickets.rows, me));
  const source = $derived<TicketRow[]>(history ? tickets.history : tickets.rows);
  const kinds = $derived.by(() => {
    const seen = new Map<string, string>();
    for (const r of source) seen.set(r.kind, r.label);
    return [...seen];
  });
  const rows = $derived.by(() => {
    let out = source.filter((r) => kind === "all" || r.kind === kind);
    if (history) return out; // the record is searched on the server
    out = out.filter((r) => inView(r, tickets.view, me));
    return search(out, query);
  });
  const open = $derived(!!t && isOpen(t.status));
  const mine = $derived(!!t && t.assignee_id != null && t.assignee_id === me);
  const heldByOther = $derived(!!t && !!t.assignee && !mine);
  const canClose = $derived(!!t && !t.approvable && (mine || tickets.canHistory));
  const canReopen = $derived(
    !!t && !t.approvable && !open && (tickets.canHistory || (t.resolved_by != null && t.resolved_by === me)),
  );
  const where = $derived(
    ((t?.facts ?? []) as { label: string; value: string }[]).find((f) => f.label === "Player is in")?.value ?? "",
  );

  function stamp(ts: number): string {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    const p = (n: number) => String(n).padStart(2, "0");
    return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function emptyLine(): string {
    if (history) return query.trim() ? "Nothing on the record matches." : "Nothing on the record yet.";
    if (query.trim()) return "No ticket matches.";
    if (kind !== "all") return "No tickets of that kind here.";
    switch (tickets.view) {
      case "mine":
        return "You hold no tickets.";
      case "unanswered":
        return "Nothing is unanswered.";
      case "answered":
        return "No answered ticket is open.";
      case "online":
        return "Nobody who filed a ticket is online.";
      default:
        return "No open tickets.";
    }
  }

  // The record is searched on the server, and read page by page; the open queue
  // is small and already here, so it narrows as you type.
  let historyTimer: ReturnType<typeof setTimeout> | null = null;
  $effect(() => {
    const term = query.trim();
    if (!history) return;
    if (historyTimer) clearTimeout(historyTimer);
    historyTimer = setTimeout(() => void tickets.loadHistory(term), term ? 300 : 0);
    return () => {
      if (historyTimer) clearTimeout(historyTimer);
    };
  });

  // New messages bring the conversation to its end.
  $effect(() => {
    void t?.id;
    void t?.messages?.length;
    void tick().then(() => {
      if (scroller) scroller.scrollTop = scroller.scrollHeight;
    });
  });

  // Whatever was loaded for one ticket belongs to that ticket.
  $effect(() => {
    void t?.id;
    bug = null;
    npc = null;
    form = "";
    formText = "";
    menu = false;
  });

  onMount(() => {
    const timer = setInterval(() => (now = Math.floor(Date.now() / 1000)), 30000);
    return () => clearInterval(timer);
  });

  async function openRow(row: TicketRow): Promise<void> {
    cursor = row.id;
    feedback = null;
    await tickets.openStaff(row.id);
  }

  function back(): void {
    tickets.closeStaff();
    feedback = null;
  }

  async function run(action: () => Promise<TicketResult>): Promise<TicketResult | null> {
    if (busy) return null;
    busy = true;
    const result = await action();
    busy = false;
    feedback = result;
    if (result.puppet) npc = result.puppet;
    return result;
  }

  async function send(): Promise<void> {
    const text = reply.trim();
    if (!text || !t) return;
    const result = await run(() => tickets.reply(t.id, text, tab === "note"));
    if (result?.ok) reply = "";
  }

  async function doForm(): Promise<void> {
    if (!t) return;
    const text = formText.trim();
    const id = t.id;
    let result: TicketResult | null = null;
    if (form === "assign") {
      if (!text) return;
      result = await run(() => tickets.assign(id, text));
    } else if (form === "merge") {
      if (!text) return;
      result = await run(() => tickets.merge(id, text));
    } else if (form === "approve") {
      result = await run(() => tickets.approve(id, text));
    } else if (form === "deny") {
      result = await run(() => tickets.deny(id, text));
    }
    if (result?.ok) {
      form = "";
      formText = "";
    }
  }

  function openForm(which: typeof form): void {
    form = form === which ? "" : which;
    formText = "";
  }

  async function loadBug(): Promise<void> {
    if (t) bug = (await tickets.loadBugDetail(t.id)) ?? { available: false };
  }

  async function toggleMenu(): Promise<void> {
    menu = !menu;
    if (menu) await tickets.loadReplies();
  }

  async function useReply(name: string): Promise<void> {
    if (!t) return;
    const text = await tickets.expandReply(t.id, name);
    menu = false;
    if (text == null) {
      feedback = { ok: false, message: "That saved reply could not be filled in." };
      return;
    }
    tab = "reply";
    reply = text;
    await tick();
    box?.focus();
  }

  function onBoxKey(e: KeyboardEvent): void {
    // Enter sends; Shift+Enter is a new line.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    } else if (e.key === "k" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void toggleMenu();
    }
  }

  function wide(): boolean {
    return (root?.clientWidth ?? 0) >= 760;
  }

  async function move(delta: number): Promise<void> {
    cursor = step(rows.map((r) => r.id), cursor ?? t?.id ?? null, delta);
    await tick();
    root?.querySelector<HTMLElement>(`[data-row="${cursor}"]`)?.scrollIntoView({ block: "nearest" });
    const row = rows.find((r) => r.id === cursor);
    if (row && wide()) void openRow(row);
  }

  // j and k move, Enter opens, / searches, c claims, r replies. A field keeps its
  // own keys; the panel only listens when focus is not in one.
  function onKey(e: KeyboardEvent): void {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
    if (isTypingTarget(e.target)) {
      if (e.key === "Escape" && e.target === searchBox && query) {
        query = "";
        e.preventDefault();
      }
      return;
    }
    const onButton = !!(e.target as HTMLElement | null)?.closest?.("button");
    switch (e.key) {
      case "j":
      case "ArrowDown":
        void move(1);
        break;
      case "k":
      case "ArrowUp":
        void move(-1);
        break;
      case "Enter": {
        if (onButton) return;
        const row = rows.find((r) => r.id === cursor);
        if (!row) return;
        void openRow(row);
        break;
      }
      case "/":
        searchBox?.focus();
        break;
      case "c":
        if (!t || !open || mine) return;
        void run(() => tickets.claim(t.id, heldByOther));
        break;
      case "r":
        if (!t) return;
        tab = "reply";
        box?.focus();
        break;
      case "Escape":
        if (!t) return;
        back();
        break;
      default:
        return;
    }
    e.preventDefault();
  }

  function keys(node: HTMLElement) {
    node.addEventListener("keydown", onKey);
    return { destroy: () => node.removeEventListener("keydown", onKey) };
  }
</script>

<div class="tk" class:has-ticket={!!t} bind:this={root} use:keys role="region" aria-label="Tickets">
  <div class="tk-hd">
    {#if t}<button class="tk-btn quiet tk-back" onclick={back}>Back to the list</button>{/if}
    <span class="tk-title">Tickets</span>
    <span class="tk-sum">{counts.all} open · {counts.unanswered} unanswered</span>
    <button
      class="tk-btn quiet"
      aria-pressed={tickets.duty}
      title="Whether you are told about new tickets"
      onclick={() => run(() => tickets.setDuty(!tickets.duty))}
    >{tickets.duty ? "On duty" : "Off duty"}</button>
  </div>

  {#if feedback}
    <p class="tk-fb" class:err={!feedback.ok} role="status">
      {feedback.message}
      {#if npc && !npc.ok && npc.command}
        <button class="tk-btn quiet" onclick={() => tickets.enterNpc(npc?.command ?? "")}>Run {npc.command}</button>
      {/if}
    </p>
  {/if}

  <div class="tk-split" class:open={!!t}>
    <div class="tk-pane-list">
      <div class="tk-tools">
        <input
          class="tk-search"
          bind:this={searchBox}
          bind:value={query}
          placeholder={history ? "Search the record by number, name, subject or text" : "Search by number, name, subject or text"}
          aria-label={history ? "Search closed tickets" : "Search open tickets"}
        />
        <div class="tk-views">
          <div class="tk-radios" role="radiogroup" aria-label="Show">
          {#each VIEW_ORDER as v (v)}
            <button
              class="tk-view"
              role="radio"
              aria-checked={!history && tickets.view === v}
              onclick={() => {
                history = false;
                tickets.setView(v);
              }}
            >{VIEW_LABELS[v]}<b>{counts[v]}</b></button>
          {/each}
          {#if tickets.canHistory}
            <button class="tk-view" role="radio" aria-checked={history} onclick={() => (history = true)}>History</button>
          {/if}
          </div>
          {#if kinds.length > 1}
            <select class="tk-select" bind:value={kind} aria-label="Kind of ticket">
              <option value="all">All kinds</option>
              {#each kinds as [key, label] (key)}
                <option value={key}>{label}</option>
              {/each}
            </select>
          {/if}
        </div>
      </div>

      <div class="tk-list" role="list" aria-label={history ? "Closed tickets" : "Open tickets"}>
        {#if tickets.error && !rows.length}
          <p class="tk-empty err" role="alert">{tickets.error}</p>
        {/if}
        {#each rows as r (r.id)}
          <div role="listitem">
            <button
              class="tk-row"
              class:here={!history && r.presence === "room"}
              class:hot={!history && isHot(r)}
              class:faint={!history && isFaint(r)}
              class:cursor={cursor === r.id}
              aria-current={t?.id === r.id ? "true" : undefined}
              data-row={r.id}
              onclick={() => openRow(r)}
            >
              <span class="tk-l1">
                {#if !history}<span class="tk-dot {dotClass(r)}" title={presenceText(r, now)}></span>{/if}
                <span class="tk-row-title">{r.title || r.label}</span>
                <span class="tk-clock {history ? '' : clockClass(r)}">{history ? stamp(Number(r.resolved ?? 0)) : clockText(r, now)}</span>
              </span>
              <span class="tk-l2">
                <span>{history ? `Opened by ${whoIs(r)}` : presenceText(r, now)}</span>
                <span class="tk-id">{r.ref}</span>
                {#if !history && r.priority >= 2}<span class="tk-chip hi">{priorityWord(r.priority)}</span>{/if}
                <span class="tk-chip">{r.label}</span>
                {#if history || r.state !== tickets.view}
                  <span class="tk-state" class:mute={r.status !== "pending"}>{sentence(r.state)}</span>
                {/if}
                {#if r.assignee}<span>{r.assignee} has it</span>{/if}
              </span>
              {#if r.preview}<span class="tk-l3">{r.preview}</span>{/if}
            </button>
          </div>
        {:else}
          {#if !tickets.error}<p class="tk-empty">{emptyLine()}</p>{/if}
        {/each}
        {#if history && tickets.historyMore}
          <button class="tk-btn tk-more" onclick={() => tickets.loadHistory(query.trim(), tickets.history.length)}>Show older</button>
        {/if}
        {#if history && tickets.historyCapped}
          <p class="tk-empty">The search stopped at the most recent 2,000. Narrow the kind or the words.</p>
        {/if}
      </div>
      <div class="tk-keys"><kbd>j</kbd> <kbd>k</kbd> move, <kbd>Enter</kbd> opens, <kbd>/</kbd> searches, <kbd>c</kbd> claims, <kbd>r</kbd> replies</div>
    </div>

    <div class="tk-pane-detail">
      {#if t}
        <div class="tk-detail">
          <div class="tk-dh">
            <div class="tk-dt">{t.title || t.label}</div>
            <div class="tk-ds">
              <span>{t.label} {t.ref}</span>
              <span class:here={t.presence === "room"}>{presenceText(t, now)}{#if where}, {where}{/if}</span>
              {#if t.status === "pending" && t.unanswered_since}
                <span class="asked">asked {ageSince(t.unanswered_since, now)} ago</span>
              {/if}
              <span class="tk-state" class:mute={t.status !== "pending"}>{sentence(t.state)}</span>
              {#if t.assignee}<span>{t.assignee} has it</span>{:else if open}<span>Nobody has it</span>{/if}
              {#if t.linked}<span>Linked to another report</span>{/if}
            </div>
            {#if open}
              <div class="tk-btns">
                {#if heldByOther}
                  <button class="tk-btn primary" disabled={busy} onclick={() => run(() => tickets.claim(t.id, true))}>Take over</button>
                {:else if t.kind === "puppet"}
                  <button
                    class="tk-btn"
                    class:primary={!mine}
                    disabled={busy}
                    title="Claims it if nobody has, takes you to the NPC and puppets it"
                    onclick={() => run(() => tickets.puppet(t.id))}
                  >Puppet</button>
                  {#if !mine}
                    <button class="tk-btn quiet" disabled={busy} onclick={() => run(() => tickets.claim(t.id))}>Claim</button>
                  {/if}
                {:else if !mine}
                  <button class="tk-btn primary" disabled={busy} onclick={() => run(() => tickets.claim(t.id))}>Claim</button>
                {/if}
                {#if t.approvable}
                  <button class="tk-btn primary" disabled={busy} onclick={() => openForm("approve")}>Approve</button>
                  <button class="tk-btn warn" disabled={busy} onclick={() => openForm("deny")}>Deny</button>
                {:else if canClose}
                  <button class="tk-btn" disabled={busy} onclick={() => run(() => tickets.close(t.id))}>Close</button>
                {/if}
                <button class="tk-btn quiet" onclick={() => openForm("assign")} aria-expanded={form === "assign"}>Assign</button>
                {#if !t.approvable && tickets.canHistory}
                  <button class="tk-btn quiet" onclick={() => openForm("merge")} aria-expanded={form === "merge"}>Merge</button>
                {/if}
                {#if mine}
                  <button class="tk-btn quiet" disabled={busy} onclick={() => run(() => tickets.unclaim(t.id))}>Unclaim</button>
                {/if}
                <label class="tk-small tk-dim">
                  Priority
                  <select
                    class="tk-select"
                    style="margin-left: 0"
                    value={t.priority ?? 0}
                    title={tickets.priorities[t.priority ?? 0]?.hint ?? ""}
                    onchange={(e) => run(() => tickets.setPriority(t.id, Number((e.currentTarget as HTMLSelectElement).value)))}
                  >
                    {#each tickets.priorities as p (p.value)}<option value={p.value} title={p.hint}>{p.word}</option>{/each}
                  </select>
                </label>
              </div>
            {:else if canReopen}
              <div class="tk-btns">
                <button class="tk-btn" disabled={busy} onclick={() => run(() => tickets.reopen(t.id))}>Reopen</button>
              </div>
            {/if}
          </div>

          {#if form}
            <form class="tk-inline" onsubmit={(e) => { e.preventDefault(); void doForm(); }}>
              <input
                bind:value={formText}
                aria-label={form === "assign" ? "Staff member" : form === "merge" ? "Ticket to merge into" : "Reason"}
                placeholder={form === "assign"
                  ? "Staff member's name"
                  : form === "merge"
                    ? "Number of the ticket to keep, like #1042"
                    : form === "approve"
                      ? "Note to the player (optional)"
                      : "Reason (the player sees it)"}
              />
              <button class="tk-btn primary" type="submit" disabled={busy}>
                {form === "assign" ? "Assign" : form === "merge" ? "Merge" : form === "approve" ? "Approve" : "Deny"}
              </button>
              <button class="tk-btn quiet" type="button" onclick={() => (form = "")}>Cancel</button>
            </form>
          {/if}

          <div class="tk-scroll" bind:this={scroller}>
            {#if (t.facts ?? []).length}
              <dl class="tk-card">
                {#each t.facts as f, i (i)}
                  <dt>{f.label}</dt>
                  <dd>{f.value}</dd>
                {/each}
              </dl>
            {/if}

            {#if t.has_details && !bug}
              <div class="tk-bug"><button class="tk-btn quiet" onclick={loadBug}>Show the error details</button></div>
            {/if}
            {#if bug?.available}
              <div class="tk-bug">
                <div>Reporter: {bug.reporter}{bug.character ? ` as ${bug.character}` : ""}</div>
                <div>Where: {bug.location}</div>
                {#if bug.traceback_command}<div>Last command: {bug.traceback_command} ({bug.traceback_time})</div>{/if}
                {#if bug.traceback}<div class="tk-dim">The last error</div><pre>{bug.traceback}</pre>{/if}
                {#if Object.keys(bug.character_state ?? {}).length}
                  <div class="tk-dim">The character</div>
                  <pre>{Object.entries(bug.character_state).map(([k, v]) => `${k}: ${v}`).join("\n")}</pre>
                {/if}
              </div>
            {:else if bug}
              <div class="tk-bug tk-dim">No error details were attached.</div>
            {/if}

            <div class="tk-msgs" role="log" aria-label="Conversation">
              {#each t.messages ?? [] as m, i (i)}
                {#if m.origin === "system"}
                  <div class="tk-sys">{stamp(m.ts)} {m.text}</div>
                {:else if m.visibility === "internal"}
                  <div class="tk-msg note">
                    <span class="tk-who"><b>Staff note</b>{@html renderSender(m.sender_html ?? m.senderHtml, m.sender)}, {stamp(m.ts)}. Only staff see this.</span>
                    <span class="tk-text">{@html renderBody(m.html, m.text)}</span>
                  </div>
                {:else}
                  <div class="tk-msg" class:staff={m.origin === "staff"}>
                    <span class="tk-who"><b>{@html renderSender(m.sender_html ?? m.senderHtml, m.sender)}</b>{stamp(m.ts)}</span>
                    <span class="tk-text">{@html renderBody(m.html, m.text)}</span>
                  </div>
                {/if}
              {/each}
              {#if !(t.messages ?? []).length}<p class="tk-empty">Nobody has written anything yet.</p>{/if}
            </div>
          </div>

          <div class="tk-composer">
            <div class="tk-tabs" role="tablist" aria-label="Write">
              <button class="tk-tab" role="tab" aria-selected={tab === "reply"} onclick={() => (tab = "reply")}>Reply</button>
              <button class="tk-tab note" role="tab" aria-selected={tab === "note"} onclick={() => (tab = "note")}>Staff note</button>
            </div>
            <textarea
              class="tk-box"
              class:note={tab === "note"}
              bind:this={box}
              bind:value={reply}
              onkeydown={onBoxKey}
              rows="3"
              placeholder={tab === "note" ? "Write a note. Only staff see it." : `Write a reply. ${whoIs(t)} will see it.`}
              aria-label={tab === "note" ? "Staff note" : "Reply to the player"}
              aria-describedby="tk-keys-hint"
            ></textarea>
            {#if menu}
              <div class="tk-menu" role="menu" aria-label="Saved replies">
                {#each tickets.replies as sr (sr.name)}
                  <button role="menuitem" onclick={() => useReply(sr.name)}>
                    <span>{sr.name}{sr.shared ? " (shared)" : ""}</span>
                    <small>{sr.body}</small>
                  </button>
                {:else}
                  <p class="tk-empty">No saved replies yet. Save one with @replies/add in the game.</p>
                {/each}
              </div>
            {/if}
            <div class="tk-foot">
              <span id="tk-keys-hint">Enter sends, Shift+Enter starts a new line.</span>
              <button class="tk-btn quiet" onclick={toggleMenu} aria-expanded={menu} title="Ctrl+K">Saved replies</button>
              <button class="tk-btn primary grow" disabled={busy || !reply.trim()} onclick={send}>
                {tab === "note" ? "Save note" : "Send"}
              </button>
            </div>
          </div>
        </div>
      {:else}
        <p class="tk-empty">Pick a ticket to read it.</p>
      {/if}
    </div>
  </div>
</div>
