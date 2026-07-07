<script lang="ts">
  import { session } from "../lib/session.svelte";
  import { settings } from "../lib/settings.svelte";
  import { commands, CURATED } from "../lib/commands.svelte";
  import { chat } from "../lib/chat.svelte";
  import { playKey } from "../lib/audio";

  let value = $state("");
  // History recall over shared recents (newest-first). -1 = live/typed line.
  let histIdx = $state(-1);

  // Tab-completion cycle state.
  let compActive = false;
  let compMatches: string[] = [];
  let compIdx = -1;
  let compWordIdx = 0;

  // Ctrl-R reverse history search.
  let rSearch = $state(false);
  let rQuery = $state("");
  const rMatch = $derived(
    rQuery ? commands.recent.find((c) => c.toLowerCase().includes(rQuery.toLowerCase())) ?? "" : "",
  );

  // Multi-line compose pad.
  let composing = $state(false);
  let composeText = $state("");
  let composeEl = $state<HTMLTextAreaElement | null>(null);

  function submit() {
    commands.run(value);
    value = "";
    histIdx = -1;
  }

  function candidates(): string[] {
    const set = new Set<string>();
    for (const c of commands.recent) set.add(c.split(" ")[0]);
    for (const c of CURATED) set.add(c.cmd.trim());
    for (const ch of chat.channels) if (ch.name) set.add(ch.name);
    return [...set].filter(Boolean);
  }

  function openCompose() {
    composeText = value;
    composing = true;
    queueMicrotask(() => composeEl?.focus());
  }
  function sendCompose() {
    const t = composeText.trim();
    if (t) commands.run(t);
    composeText = "";
    composing = false;
    value = "";
  }
  function onComposeKey(e: KeyboardEvent) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      sendCompose();
    } else if (e.key === "Escape") {
      composing = false;
    }
  }

  function onRKey(e: KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      if (rMatch) value = rMatch;
      rSearch = false;
      rQuery = "";
    } else if (e.key === "Escape") {
      rSearch = false;
      rQuery = "";
    }
  }

  function tabComplete(shift: boolean) {
    if (!compActive) {
      const parts = value.split(" ");
      compWordIdx = parts.length - 1;
      const base = parts[compWordIdx] ?? "";
      if (!base) return;
      const b = base.toLowerCase();
      compMatches = candidates().filter((c) => c.toLowerCase().startsWith(b));
      if (!compMatches.length) return;
      compActive = true;
      compIdx = -1;
    }
    compIdx = (compIdx + (shift ? -1 : 1) + compMatches.length) % compMatches.length;
    const parts = value.split(" ");
    parts[compWordIdx] = compMatches[compIdx];
    value = parts.join(" ");
  }

  function onKeydown(e: KeyboardEvent) {
    if (settings.keyboardSfx && e.key.length === 1) playKey();
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "r") {
      e.preventDefault();
      rSearch = true;
      rQuery = "";
      return;
    }
    if (e.key === "Tab") {
      e.preventDefault();
      tabComplete(e.shiftKey);
      return;
    }
    compActive = false; // any other key ends a completion cycle
    const recent = commands.recent;
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    } else if (e.key === "ArrowUp") {
      if (histIdx < recent.length - 1) {
        histIdx += 1;
        value = recent[histIdx];
        e.preventDefault();
      }
    } else if (e.key === "ArrowDown") {
      if (histIdx > 0) {
        histIdx -= 1;
        value = recent[histIdx];
      } else {
        histIdx = -1;
        value = "";
      }
      e.preventDefault();
    }
  }

  function onPaste(e: ClipboardEvent) {
    const text = e.clipboardData?.getData("text") ?? "";
    if (!text.includes("\n")) return; // single line: let the browser handle it
    e.preventDefault();
    const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    if (lines.length <= 1) {
      value += lines[0] ?? "";
      return;
    }
    if (confirm(`Send ${lines.length} pasted lines as separate commands?`)) {
      for (const l of lines) commands.run(l);
      value = "";
    }
  }
</script>

{#if composing}
  <div class="compose">
    <div class="c-head">
      <span class="c-tag glow-text">Compose</span>
      <span class="c-hint">Ctrl-Enter to send, Esc to cancel</span>
      <button class="c-x" onclick={() => (composing = false)} aria-label="close">×</button>
    </div>
    <textarea
      bind:this={composeEl}
      bind:value={composeText}
      onkeydown={onComposeKey}
      placeholder="write a longer pose or message…"
      aria-label="compose"
    ></textarea>
    <button class="c-send" onclick={sendCompose}>Send</button>
  </div>
{/if}

<div class="command-bar">
  {#if session.prompt && !settings.hidePrompt}
    <div class="prompt">{@html session.prompt}</div>
  {/if}
  {#if rSearch}
    <span class="rs-tag" aria-hidden="true">r-search</span>
    <!-- svelte-ignore a11y_autofocus -->
    <input class="rs-input" bind:value={rQuery} onkeydown={onRKey} autofocus placeholder="search history…" aria-label="reverse history search" />
    <span class="rs-match">{rMatch || "(no match)"}</span>
  {:else}
    <span class="chevron glow-text" aria-hidden="true">❯</span>
    <!-- svelte-ignore a11y_autofocus -->
    <input
      class="command-input"
      bind:value
      onkeydown={onKeydown}
      onpaste={onPaste}
      autocomplete="off"
      autocapitalize="off"
      spellcheck="false"
      autofocus
      aria-label="command input"
      placeholder="enter command"
    />
    <button class="compose-btn" onclick={openCompose} title="compose pad" aria-label="compose pad">⤢</button>
  {/if}
</div>

<style>
  .command-bar {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.55rem 0.85rem;
    border-top: 1px solid var(--accent);
    background: var(--bg-elev);
  }
  .prompt {
    white-space: pre-wrap;
    color: var(--gold);
    flex: 0 0 auto;
  }
  .chevron {
    color: var(--accent-bright);
    flex: 0 0 auto;
    font-size: 1.05rem;
    line-height: 1;
  }
  .command-input {
    flex: 1 1 auto;
    background: transparent;
    border: none;
    outline: none;
    color: var(--fg);
    font: inherit;
    letter-spacing: 0.02em;
    caret-color: var(--accent-bright);
  }
  .command-input::placeholder {
    color: var(--fg-faint);
    font-style: italic;
    letter-spacing: 0.14em;
    text-transform: lowercase;
  }
  .compose-btn {
    flex: 0 0 auto; background: none; border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.85rem; padding: 0 7px; cursor: pointer;
  }
  .compose-btn:hover { color: var(--accent-bright); border-color: var(--accent); }
  .rs-tag { color: var(--gold); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; flex: 0 0 auto; }
  .rs-input {
    flex: 1 1 auto; background: transparent; border: none; outline: none;
    color: var(--fg); font: inherit; caret-color: var(--gold);
  }
  .rs-match { color: var(--accent-bright); font-size: 0.82rem; flex: 0 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .compose {
    border-top: 1px solid var(--accent); background: var(--bg-elev);
    display: flex; flex-direction: column; gap: 5px; padding: 6px 10px;
  }
  .c-head { display: flex; align-items: center; gap: 1ch; }
  .c-tag { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.18em; font-size: 0.72rem; }
  .c-hint { color: var(--fg-faint); font-size: 0.66rem; }
  .c-x { margin-left: auto; background: none; border: none; color: var(--fg-dim); font-size: 1.1rem; line-height: 1; cursor: pointer; }
  .c-x:hover { color: var(--accent-bright); }
  .compose textarea {
    background: var(--bg); color: var(--fg); border: 1px solid var(--border-bright);
    font-family: inherit; font-size: 0.9rem; padding: 6px 8px; min-height: 80px; resize: vertical; line-height: 1.5;
  }
  .compose textarea:focus { outline: none; border-color: var(--accent); }
  .c-send {
    align-self: flex-end; background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.12em; padding: 4px 14px; cursor: pointer;
  }
  .c-send:hover { color: var(--accent-bright); border-color: var(--accent); }
</style>
