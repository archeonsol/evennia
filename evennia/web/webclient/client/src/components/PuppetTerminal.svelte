<script lang="ts">
  // One puppet's terminal: its feed and a command line of its own. The command
  // line keeps the main one's habits (history on the arrows, Ctrl-R, Tab on the
  // verb, paging the feed, Shift+Enter, a pasted block) and shares its history.
  import { connection } from "../lib/evennia.svelte";
  import { commands, CURATED } from "../lib/commands.svelte";
  import { settings } from "../lib/settings.svelte";
  import { triggers } from "../lib/triggers.svelte";
  import { lexicon } from "../lib/lexicon.svelte";
  import { announcer } from "../lib/announce.svelte";
  import { playKey } from "../lib/audio";
  import { looksLikeHtml, pipeToHtml } from "../lib/markup";
  import { puppets } from "../lib/puppets.svelte";
  import { HistoryWalk, shouldRecall } from "../lib/history";
  import { caretOnEdge, fitHeight } from "../lib/textarea";

  interface Props {
    npcId: number;
    /** This terminal has the whole panel to itself. */
    solo?: boolean;
    onsolo?: () => void;
    onclose?: () => void;
  }
  let { npcId, solo = false, onsolo, onclose }: Props = $props();

  const toHtml = (body: string) => (looksLikeHtml(body) ? body : pipeToHtml(body));
  const escape = (text: string) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const puppet = $derived(puppets.feeds.get(String(npcId)) ?? null);

  let value = $state("");
  let feedEl = $state<HTMLDivElement | null>(null);
  let inputEl = $state<HTMLTextAreaElement | null>(null);
  // Following the newest line. Scrolling up to read stops it; reaching the end starts it again.
  let following = true;

  // History recall over the shared recents (newest first). The line being
  // typed is the walk's bottom slot, so Up and Down never lose it.
  const walk = new HistoryWalk();

  // Tab-completion cycle on the verb.
  let compActive = false;
  let compMatches: string[] = [];
  let compIdx = -1;

  // Ctrl-R reverse history search.
  let rSearch = $state(false);
  let rQuery = $state("");
  const rMatch = $derived(
    rQuery ? commands.recent.find((c) => c.toLowerCase().includes(rQuery.toLowerCase())) ?? "" : "",
  );

  function onFeedScroll() {
    if (!feedEl) return;
    following = feedEl.scrollHeight - feedEl.scrollTop - feedEl.clientHeight < 24;
  }

  $effect(() => {
    void puppet?.feed.length;
    const el = feedEl;
    if (!el || !following) return;
    queueMicrotask(() => {
      el.scrollTop = el.scrollHeight;
    });
  });

  // The command line grows with its text, as the main one does.
  $effect(() => {
    const el = inputEl;
    if (!el) return;
    value;
    settings.fontSize;
    settings.lineHeight;
    settings.font;
    fitHeight(el);
  });
  $effect(() => {
    const el = inputEl;
    if (!el) return;
    let width = el.clientWidth;
    let frame = 0;
    const ro = new ResizeObserver(() => {
      // Its own height changes land here too; only a new width rewraps. The
      // refit waits a frame so it is not part of the observer's own turn.
      if (el.clientWidth === width) return;
      width = el.clientWidth;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => fitHeight(el));
    });
    ro.observe(el);
    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
    };
  });

  function toPlain(body: string): string {
    const el = document.createElement("div");
    el.innerHTML = toHtml(body);
    return el.textContent ?? "";
  }
  function clearFeed() {
    if (puppet && puppet.feed.length && confirm(`Clear ${puppet.name}'s buffer?`)) {
      puppets.clearFeed(npcId);
    }
  }
  function downloadFeed() {
    if (!puppet) return;
    const text = puppet.feed.map((line) => toPlain(line.body)).join("\n");
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    a.href = url;
    a.download = `puppet-${npcId}-${stamp}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }

  /** Send one line as this puppet, through the puppet endpoint (never a p<n> prefix). */
  function dispatch(line: string) {
    const cmd = line.trim();
    if (!cmd) return;
    commands.record(cmd);
    if (settings.echoCommands) puppets.addLocal(npcId, `<span class="cmd-echo">&gt; ${escape(cmd)}</span>`);
    following = true;
    connection
      .request("puppets", "puppet_cmd", { npc_id: npcId, cmd: triggers.expand(cmd) })
      .catch(() => puppets.addLocal(npcId, `<span class="conn-note">Not sent: could not reach ${escape(puppet?.name ?? `#${npcId}`)}.</span>`));
  }

  function submit() {
    dispatch(value);
    // A recalled command went instead of the line being typed: put that back.
    const typed = walk.finish();
    if (typed) {
      value = typed;
      announcer.now(`Restored: ${typed}`);
    } else if (settings.keepCommand && value.trim()) {
      queueMicrotask(() => inputEl?.select());
    } else {
      value = "";
    }
  }

  function candidates(): string[] {
    const set = new Set<string>();
    for (const v of lexicon.verbs) set.add(v);
    for (const c of commands.recent) set.add(c.split(" ")[0]);
    for (const c of CURATED) set.add(c.cmd.trim());
    return [...set].filter(Boolean);
  }

  /** Complete the verb; false when the caret is past it or nothing matches. */
  function tabComplete(shift: boolean): boolean {
    if (!compActive) {
      if (value.includes(" ")) return false;
      const base = value.toLowerCase();
      if (!base) return false;
      compMatches = candidates().filter((c) => c.toLowerCase().startsWith(base));
      if (!compMatches.length) return false;
      compActive = true;
      compIdx = -1;
    }
    compIdx = (compIdx + (shift ? -1 : 1) + compMatches.length) % compMatches.length;
    value = compMatches[compIdx];
    announcer.now(`${compMatches[compIdx]}, ${compIdx + 1} of ${compMatches.length}`);
    return true;
  }

  // Page Up/Down page this terminal's feed from its command line.
  function pageFeed(dir: 1 | -1) {
    if (feedEl) feedEl.scrollBy({ top: dir * feedEl.clientHeight * 0.9 });
  }

  /** Up or Down: walk the history, or leave the key to move the caret through a long line. */
  function recall(dir: 1 | -1): boolean {
    const el = inputEl;
    if (!el) return false;
    const walkOn = shouldRecall({
      walking: walk.walking,
      untouched: value === walk.shown(),
      line: value,
      atEdge: caretOnEdge(el, dir === 1 ? "first" : "last"),
      keys: settings.historyKeys,
    });
    if (!walkOn) return false;
    const next = walk.step(dir, value, commands.recent);
    if (next === null) return false;
    value = next;
    announcer.now(next.trim() ? next : "Blank line");
    return true;
  }

  function onRKey(e: KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      if (rMatch) value = walk.jump(rMatch, value, commands.recent);
      rSearch = false;
      rQuery = "";
      queueMicrotask(() => inputEl?.focus());
    } else if (e.key === "Escape") {
      rSearch = false;
      rQuery = "";
      queueMicrotask(() => inputEl?.focus());
    }
  }

  // Set by Shift+Enter, so the line break it asks for is let through.
  let breakWanted = false;

  function onKeydown(e: KeyboardEvent) {
    if (settings.keyboardSfx && e.key.length === 1) playKey(settings.keyboardVolume);
    breakWanted = false;
    if (e.isComposing || e.keyCode === 229) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "r") {
      e.preventDefault();
      rSearch = true;
      rQuery = "";
      return;
    }
    if (e.key === "Tab") {
      if (tabComplete(e.shiftKey) || value.trim()) e.preventDefault();
      return;
    }
    if (e.key === "PageUp" || e.key === "PageDown") {
      e.preventDefault();
      pageFeed(e.key === "PageUp" ? -1 : 1);
      return;
    }
    compActive = false;
    if (e.key === "Enter") {
      if (e.shiftKey) {
        breakWanted = true;
        return;
      }
      e.preventDefault();
      submit();
    } else if (
      (e.key === "ArrowUp" || e.key === "ArrowDown") &&
      !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey
    ) {
      if (recall(e.key === "ArrowUp" ? 1 : -1)) e.preventDefault();
    }
  }

  // Some phone keyboards send Enter as an unidentified key; the field is a
  // textarea, so without this it would take a new line instead of sending.
  function onBeforeInput(e: InputEvent) {
    if (e.inputType !== "insertLineBreak" && e.inputType !== "insertParagraph") return;
    if (breakWanted) {
      breakWanted = false;
      return;
    }
    e.preventDefault();
    submit();
  }

  function insertText(text: string) {
    const el = inputEl;
    if (!el) return;
    el.focus();
    if (!document.execCommand("insertText", false, text)) {
      el.setRangeText(text, el.selectionStart, el.selectionEnd, "end");
    }
    value = el.value;
  }

  function onPaste(e: ClipboardEvent) {
    const text = e.clipboardData?.getData("text") ?? "";
    if (!text.includes("\n")) return;
    e.preventDefault();
    const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    if (lines.length <= 1) {
      insertText(lines[0] ?? "");
      return;
    }
    if (confirm(`Send ${lines.length} pasted lines to ${puppet?.name ?? "this puppet"} as separate commands?\n\nCancel pastes them into the command line instead.`)) {
      for (const l of lines) dispatch(l);
    } else {
      insertText(text.replace(/\r\n?/g, "\n").replace(/\n+$/, ""));
    }
  }
</script>

{#if puppet}
  <section class="tile" data-npc={npcId} aria-label="Puppet {puppet.name}">
    <header class="term-head">
      <div class="head-row">
        <strong>P{puppet.slot}</strong>
        <span class="name">{puppet.name}</span>
        <small>#{puppet.npcId}</small>
        {#if onsolo}
          <button class="sh-cmd tool" aria-pressed={solo} onclick={onsolo} title={solo ? "show every terminal" : "give this terminal the whole panel"}>{solo ? "Restore" : "Expand"}</button>
        {/if}
        {#if onclose}
          <button class="sh-cmd tool" onclick={onclose} title="hide this terminal" aria-label="hide {puppet.name}'s terminal">Hide</button>
        {/if}
      </div>
      <!-- The room has a line of its own, so a narrow tile still says where its puppet is. -->
      <div class="head-row">
        <span class="where">{@html puppet.scene.room.name ?? ""}</span>
        <button class="sh-cmd tool" onclick={downloadFeed} title="download buffer" aria-label="download {puppet.name}'s buffer" disabled={!puppet.feed.length}>Save</button>
        <button class="sh-cmd tool" onclick={clearFeed} title="clear buffer" aria-label="clear {puppet.name}'s buffer" disabled={!puppet.feed.length}>Clear</button>
      </div>
    </header>

    <div class="term-feed" bind:this={feedEl} onscroll={onFeedScroll} role="log" aria-label="{puppet.name} output" data-focus-region="puppet-output">
      {#if puppet.resyncing && !puppet.feed.length}
        <div class="sync">Loading viewpoint</div>
      {/if}
      {#each puppet.feed as line, index (index)}
        <div class="term-line">{@html toHtml(line.body)}</div>
      {/each}
    </div>

    <div class="term-bar" role="region" aria-label="Command line for {puppet.name}">
      {#if rSearch}
        <span class="rs-tag" aria-hidden="true">r-search</span>
        <input class="rs-input" bind:value={rQuery} onkeydown={onRKey} class:sh-placeholder={true} placeholder="Search history"
          aria-label="Search command history" aria-describedby="rs-match-{npcId}" />
        <span class="rs-match" id="rs-match-{npcId}" aria-live="polite">{rMatch || "(no match)"}</span>
      {:else}
        <span class="prompt" aria-hidden="true">P{puppet.slot}&gt;</span>
        <textarea
          class="term-input"
          rows="1"
          bind:this={inputEl}
          bind:value
          onkeydown={onKeydown}
          onbeforeinput={onBeforeInput}
          onpaste={onPaste}
          autocomplete="off"
          autocapitalize="off"
          spellcheck={settings.spellcheck}
          enterkeyhint="send"
          placeholder="Act as {puppet.name}"
          aria-label="Command for {puppet.name}"
          data-focus-region="puppet-input"
        ></textarea>
      {/if}
    </div>
  </section>
{/if}

<style>
  .tile {
    display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden;
    background: var(--bg-elev); border: 1px solid var(--border-bright);
  }
  .tile:focus-within { border-color: var(--accent-bright); }
  .term-head { display: flex; flex-direction: column; padding: 4px 6px 4px 10px; border-bottom: 1px solid var(--accent); min-width: 0; }
  .head-row { display: flex; gap: 1ch; align-items: center; min-width: 0; }
  .term-head strong, .prompt { color: var(--gold); font-weight: normal; }
  .term-head .name { flex: 1 1 0; min-width: 6ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .term-head small { flex: none; }
  .term-head .where { flex: 1 1 0; min-width: 0; color: var(--fg-dim); font-size: 0.8em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .term-head .tool { flex: none; }
  .term-feed { flex: 1 1 0; min-height: 0; overflow-y: auto; padding: 8px 10px; display: flex; flex-direction: column; gap: 2px; }
  .term-line { color: var(--fg); font-size: 0.9em; white-space: pre-wrap; word-break: break-word; }
  .term-bar {
    display: flex; gap: 0.8ch; align-items: flex-start; padding: 6px 10px;
    border-top: 1px solid var(--accent); --row: calc(1rem * var(--shell-line-height, 1.5));
  }
  .prompt { flex: none; line-height: var(--row); }
  .term-input {
    flex: 1 1 auto; min-width: 0; display: block; margin: 0; padding: 0 2px;
    background: transparent; border: none; outline: none; resize: none;
    max-height: 30vh; overflow-y: auto;
    color: var(--fg); font: inherit; line-height: var(--shell-line-height, 1.5);
    caret-color: var(--accent-bright);
  }
  .rs-tag { color: var(--gold); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; flex: none; line-height: var(--row); }
  .rs-input { flex: 1 1 auto; min-width: 0; background: transparent; border: none; outline: none; height: var(--row); color: var(--fg); font: inherit; caret-color: var(--gold); }
  .rs-match { color: var(--accent-bright); font-size: 0.82rem; flex: 0 1 auto; line-height: var(--row); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  small { color: var(--fg-faint); }
  .sync { color: var(--fg-faint); font-size: 0.64rem; letter-spacing: 0.14em; text-transform: uppercase; padding: 10px; }
</style>
