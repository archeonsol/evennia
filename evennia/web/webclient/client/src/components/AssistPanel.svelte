<script lang="ts">
  import { chat } from "../lib/chat.svelte";
  import { renderBody, renderSender } from "../lib/markup";

  let reply = $state("");
  const thread = $derived(chat.assistThread);
  // Oldest ticket first - triage the longest-waiting.
  const tickets = $derived(
    [...chat.assistThreads].sort((a, b) => (a.created ?? a.ts ?? 0) - (b.created ?? b.ts ?? 0)),
  );
  const STATUSES = ["open", "pending", "closed"];

  function open(t: any) {
    chat.openAssistThread(t.account_id);
  }
  function ageOf(created: number) {
    if (!created) return "";
    const mins = Math.floor((Date.now() / 1000 - created) / 60);
    if (mins < 1) return "now";
    if (mins < 60) return `${mins}m`;
    const h = Math.floor(mins / 60);
    return h < 24 ? `${h}h` : `${Math.floor(h / 24)}d`;
  }
  function back() {
    chat.assistThread = null;
  }
  function send() {
    if (reply.trim() && thread) {
      chat.assistReply(thread.accountKey || thread.accountId, reply);
      reply = "";
    }
  }
  function onKey(e: KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      send();
    }
  }
  function fmt(ts: number) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    const p = (n: number) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}`;
  }
</script>

<div class="assist">
  <div class="hd">
    <span class="tag glow-text">Assist</span>
    <span class="sub">help desk</span>
    {#if thread}
      <button class="back" onclick={back}>‹ inbox</button>
    {:else}
      <span class="count">{chat.assistThreads.length} open</span>
    {/if}
  </div>

  {#if !thread}
    <div class="list">
      {#if tickets.length}
        {#each tickets as t (t.account_id)}
          <button class="ticket" onclick={() => open(t)}>
            <span class="row1">
              <span class="who">{t.account_key || `#${t.account_id}`}</span>
              <span class="meta">
                {#if t.assignee}<span class="asg">◆ {t.assignee}</span>{/if}
                <span class="status s-{t.status || 'open'}">{t.status || "open"}</span>
                {#if t.created}<span class="age">{ageOf(t.created)}</span>{/if}
              </span>
            </span>
            <span class="prev">{t.preview || "-"}</span>
          </button>
        {/each}
      {:else}
        <p class="empty">No open tickets.</p>
      {/if}
    </div>
  {:else}
    <div class="convo">
      <div class="who-head">
        <span class="petitioner">{thread.accountKey || `#${thread.accountId}`}</span>
        <span class="actions">
          <button class="act" onclick={() => chat.assistClaim(thread.accountId)}>Claim</button>
          {#each STATUSES as st}
            <button class="act st-{st}" onclick={() => chat.assistStatus(thread.accountId, st)}>{st}</button>
          {/each}
        </span>
      </div>
      <div class="msgs">
        {#each thread.messages as m, i (i)}
          <div class="am">
            <span class="s">{@html renderSender(m.sender_html ?? m.senderHtml, m.sender)}</span>
            <span class="t">{@html renderBody(m.html, m.text)}</span>
          </div>
        {/each}
        {#if !thread.messages.length}<p class="empty">No messages in this thread.</p>{/if}
      </div>
      <div class="reply">
        <span class="chev glow-text" aria-hidden="true">❯</span>
        <input
          bind:value={reply}
          onkeydown={onKey}
          placeholder="reply to {thread.accountKey || 'petitioner'}…"
          aria-label="assist reply"
        />
      </div>
    </div>
  {/if}
</div>

<style>
  .assist { display: flex; flex-direction: column; height: 100%; background: var(--bg-elev); }
  .hd {
    display: flex; align-items: baseline; gap: 1ch; padding: 6px 10px;
    border-bottom: 1px solid var(--accent); flex: 0 0 auto;
  }
  .tag { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.22em; font-size: 0.8rem; }
  .sub { color: var(--fg-dim); text-transform: uppercase; letter-spacing: 0.18em; font-size: 0.62rem; }
  .count { margin-left: auto; color: var(--gold); font-size: 0.68rem; letter-spacing: 0.1em; }
  .back {
    margin-left: auto; background: none; border: none; color: var(--accent-bright);
    font-family: inherit; font-size: 0.7rem; letter-spacing: 0.1em; cursor: pointer;
  }
  .list { overflow-y: auto; padding: 6px; display: flex; flex-direction: column; gap: 5px; }
  .ticket {
    display: flex; flex-direction: column; gap: 3px; text-align: left;
    padding: 8px 10px; background: var(--bg); border: 1px solid var(--border-bright);
    color: var(--fg); font-family: inherit; cursor: pointer;
  }
  .ticket:hover { border-color: var(--accent); }
  .row1 { display: flex; justify-content: space-between; align-items: baseline; gap: 1ch; }
  .who { color: var(--gold); text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.78rem; }
  .meta { display: flex; align-items: baseline; gap: 0.7ch; flex: 0 0 auto; }
  .asg { color: var(--accent); font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.06em; }
  .age { color: var(--fg-faint); font-size: 0.68rem; }
  .status {
    font-size: 0.58rem; text-transform: uppercase; letter-spacing: 0.08em;
    padding: 1px 5px; border: 1px solid currentColor; border-radius: 2px;
  }
  .s-open { color: var(--accent-bright); }
  .s-pending { color: var(--gold); }
  .s-closed { color: var(--fg-faint); }
  .prev { color: var(--fg-dim); font-size: 0.76rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .empty { color: var(--fg-faint); font-style: italic; padding: 8px 10px; }

  .convo { display: flex; flex-direction: column; min-height: 0; flex: 1; }
  .who-head {
    display: flex; align-items: center; gap: 1ch; padding: 5px 10px;
    border-bottom: 1px solid var(--border);
  }
  .petitioner { color: var(--gold); text-transform: uppercase; letter-spacing: 0.1em; font-size: 0.78rem; }
  .actions { display: flex; gap: 4px; margin-left: auto; }
  .act {
    background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.06em;
    padding: 2px 6px; cursor: pointer;
  }
  .act:hover { border-color: var(--accent); color: var(--fg); }
  .act.st-closed:hover { border-color: var(--fg-faint); }
  .msgs { flex: 1; overflow-y: auto; padding: 6px 10px; line-height: 1.5; }
  .am { padding: 2px 0; font-size: 0.85rem; }
  .am .s { color: var(--accent); margin-right: 0.6ch; }
  .am .t { color: var(--fg); white-space: pre-wrap; }
  .reply {
    display: flex; align-items: center; gap: 0.6rem; padding: 6px 10px;
    border-top: 1px solid var(--accent); flex: 0 0 auto;
  }
  .chev { color: var(--accent-bright); }
  .reply input {
    flex: 1; background: transparent; border: none; outline: none;
    color: var(--fg); font-family: inherit; font-size: 0.85rem; caret-color: var(--accent-bright);
  }
  .reply input::placeholder { color: var(--fg-faint); font-style: italic; }
</style>
