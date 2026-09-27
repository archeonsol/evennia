<script lang="ts">
  import { session } from "../lib/session.svelte";
  import { settings } from "../lib/settings.svelte";
  import { commands, CURATED } from "../lib/commands.svelte";
  import { chat } from "../lib/chat.svelte";
  import { scene } from "../lib/scene.svelte";
  import { playKey } from "../lib/audio";
  import { compose } from "../lib/compose.svelte";
  import { COMPOSE_MODES, composeToCommand, specFor } from "../lib/compose-modes";
  import { composePreviewToHtml } from "../lib/compose-preview";
  import { focusOnMount } from "../lib/focus";
  import { announcer } from "../lib/announce.svelte";
  import { lexicon } from "../lib/lexicon.svelte";
  import { HistoryWalk, shouldRecall } from "../lib/history";
  import { caretOnEdge, fitHeight } from "../lib/textarea";

  let value = $state("");
  // History recall over shared recents (newest-first). The line being typed
  // is the walk's bottom slot, so Up and Down never lose it (lib/history.ts).
  const walk = new HistoryWalk();

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

  // Multi-line compose pad. State (draft, mode, preview) lives in the compose
  // store so it survives closing the pad and reloading the page.
  let composeEl = $state<HTMLTextAreaElement | null>(null);

  // Warm the scope cache while a target word is being typed, so Tab answers
  // from cache instead of a round trip after the keypress.
  $effect(() => {
    const parts = value.split(" ");
    const word = parts.length > 1 ? (parts[parts.length - 1] ?? "").toLowerCase() : "";
    if (!word) return;
    const t = setTimeout(() => {
      if (lexicon.scopeMatches(word) === undefined) void lexicon.fetchScope(word);
    }, 250);
    return () => clearTimeout(t);
  });

  // Another room is another set of targets. (Same-name rooms and arrivals are
  // covered by the store's own cache age.)
  $effect(() => {
    scene.room.name;
    lexicon.reset();
  });

  let inputEl = $state<HTMLTextAreaElement | null>(null);

  // The command line grows with its text, so a long pose can be read whole
  // while it is written; a max-height in the styles caps it. Wrapping changes
  // with the width and the type, so those refit it too.
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
    const ro = new ResizeObserver(() => {
      // Its own height changes land here too; only a new width rewraps.
      if (el.clientWidth === width) return;
      width = el.clientWidth;
      fitHeight(el);
    });
    ro.observe(el);
    return () => ro.disconnect();
  });

  function submit() {
    commands.run(value);
    // A recalled command went instead of the line being typed: put that back.
    const typed = walk.finish();
    if (typed) {
      value = typed;
      announcer.now(`Restored: ${typed}`);
    } else if (settings.keepCommand && value.trim()) {
      // Kept and selected: Enter sends it again, typing replaces it.
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
    for (const ch of chat.channels) if (ch.name) set.add(ch.name);
    return [...set].filter(Boolean);
  }

  function openCompose() {
    // Carry a half-typed command line into the pad. A saved draft is never
    // overwritten: the line then stays where it is.
    if (value.trim() && !compose.hasDraft) {
      compose.setText(value);
      // The line moved; one the history walk had put aside comes back.
      value = walk.finish();
    }
    compose.show();
    queueMicrotask(() => composeEl?.focus());
  }
  function closeCompose() {
    compose.hide();
    // The pad's field is gone, so the keys go back to the command line.
    queueMicrotask(() => inputEl?.focus());
  }
  function sendCompose() {
    const text = compose.take();
    if (!text) return;
    commands.run(composeToCommand(compose.mode, text));
    // Kept open, the pad is ready for the next pose in the same mode.
    if (settings.composeStaysOpen) composeEl?.focus();
    else closeCompose();
  }
  function onComposeKey(e: KeyboardEvent) {
    if (e.isComposing) return;
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      sendCompose();
    } else if (e.key === "Escape") {
      // Esc closes; the draft is kept, which is the whole point of persisting it.
      closeCompose();
    }
  }

  function onRKey(e: KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      // Accepting a match is a jump in the history walk, so the line being
      // typed is put aside rather than overwritten.
      if (rMatch) value = walk.jump(rMatch, value, commands.recent);
      rSearch = false;
      rQuery = "";
    } else if (e.key === "Escape") {
      rSearch = false;
      rQuery = "";
    }
  }

  /**
   * Tab a target word whose scope answer is not cached yet; complete it when
   * the reply lands, unless the line moved on in the meantime.
   */
  function startScopeComplete(word: string) {
    const lineAtAsk = value;
    void lexicon.fetchScope(word).then((names) => {
      if (!names.length || value !== lineAtAsk) return;
      compMatches = names;
      compActive = true;
      compIdx = 0;
      const parts = value.split(" ");
      parts[compWordIdx] = names[0];
      value = parts.join(" ");
      announcer.now(`${names[0]}, 1 of ${names.length}`);
    });
  }

  /** Complete the word before the caret; false when there is nothing to complete. */
  function tabComplete(shift: boolean): boolean {
    if (!compActive) {
      const parts = value.split(" ");
      compWordIdx = parts.length - 1;
      const base = parts[compWordIdx] ?? "";
      if (!base) return false;
      const b = base.toLowerCase();
      if (compWordIdx === 0) {
        compMatches = candidates().filter((c) => c.toLowerCase().startsWith(b));
      } else {
        const scope = lexicon.scopeMatches(b);
        if (scope === undefined) {
          startScopeComplete(b);
          return true;
        }
        compMatches = scope;
      }
      if (!compMatches.length) return false;
      compActive = true;
      compIdx = -1;
    }
    compIdx = (compIdx + (shift ? -1 : 1) + compMatches.length) % compMatches.length;
    const parts = value.split(" ");
    parts[compWordIdx] = compMatches[compIdx];
    value = parts.join(" ");
    // The field's new value is not reliably re-read; say the completion.
    announcer.now(`${compMatches[compIdx]}, ${compIdx + 1} of ${compMatches.length}`);
    return true;
  }

  // Page Up/Down page the game output from the command line, so reading back
  // does not mean leaving the line you are typing.
  function pageLog(dir: 1 | -1) {
    const log = document.querySelector<HTMLElement>('[data-focus-region="output"]');
    if (log) log.scrollBy({ top: dir * log.clientHeight * 0.9 });
  }

  /**
   * Up or Down: walk the history, or leave the key to move the caret through
   * a long line. Returns whether the key was taken.
   */
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
    // The field's new value is not reliably re-read; say it.
    announcer.now(next.trim() ? next : "Blank line");
    return true;
  }

  // Set by Shift+Enter, so the line break it asks for is let through.
  let breakWanted = false;

  function onKeydown(e: KeyboardEvent) {
    if (settings.keyboardSfx && e.key.length === 1) playKey(settings.keyboardVolume);
    breakWanted = false;
    // An IME building a character owns Enter and the arrows until it is done.
    // Safari ends the composition before the keydown that confirms it, and
    // marks only its keyCode.
    if (e.isComposing || e.keyCode === 229) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "r") {
      e.preventDefault();
      rSearch = true;
      rQuery = "";
      return;
    }
    // An empty line has nothing to complete, so Tab moves focus as it does
    // everywhere else; holding it there trapped keyboard users in the field
    // focused on load. Once the line has text Tab belongs to the command:
    // releasing it on a miss walked focus to the Compose button mid-word.
    if (e.key === "Tab") {
      if (tabComplete(e.shiftKey) || value.trim()) e.preventDefault();
      return;
    }
    if (e.key === "PageUp" || e.key === "PageDown") {
      e.preventDefault();
      pageLog(e.key === "PageUp" ? -1 : 1);
      return;
    }
    compActive = false; // any other key ends a completion cycle
    // Enter sends; Shift+Enter is a new line, as in the compose pad.
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

  // A line break nobody asked for with Shift+Enter is an Enter the keydown
  // did not see: some phone keyboards send Enter as an unidentified key. The
  // field is a textarea, so without this it would take a new line, not send.
  function onBeforeInput(e: InputEvent) {
    if (e.inputType !== "insertLineBreak" && e.inputType !== "insertParagraph") return;
    if (breakWanted) {
      breakWanted = false;
      return;
    }
    e.preventDefault();
    submit();
  }

  /** Put text in at the caret, over any selection, as typing it would. */
  function insertText(text: string) {
    const el = inputEl;
    if (!el) return;
    el.focus();
    // execCommand keeps the edit on the field's undo stack; setRangeText,
    // the fallback, does not.
    if (!document.execCommand("insertText", false, text)) {
      el.setRangeText(text, el.selectionStart, el.selectionEnd, "end");
    }
    value = el.value;
  }

  function onPaste(e: ClipboardEvent) {
    const text = e.clipboardData?.getData("text") ?? "";
    if (!text.includes("\n")) return; // one line: the browser pastes it
    e.preventDefault();
    const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    if (lines.length <= 1) {
      // A line copied along with its line break: paste the line alone.
      insertText(lines[0] ?? "");
      return;
    }
    if (confirm(`Send ${lines.length} pasted lines as separate commands?\n\nCancel pastes them into the command line instead.`)) {
      // Sent as they are; whatever was being typed stays on the line.
      for (const l of lines) commands.run(l);
    } else {
      insertText(text.replace(/\r\n?/g, "\n").replace(/\n+$/, ""));
    }
  }
</script>

{#if compose.open}
  <div class="compose">
    <div class="c-head">
      <span class="c-tag glow-text">Compose</span>
      <div class="c-modes" role="group" aria-label="compose mode">
        {#each COMPOSE_MODES as m (m.id)}
          <button
            class="sh-toggle"
            aria-pressed={compose.mode === m.id}
            onclick={() => compose.setMode(m.id)}>{m.label}</button
          >
        {/each}
      </div>
      <span class="c-hint">Ctrl+Enter send &middot; Esc close</span>
      <button class="sh-cmd c-x" onclick={closeCompose} aria-label="close">Close</button>
    </div>
    <textarea
      bind:this={composeEl}
      value={compose.text}
      oninput={(e) => compose.setText(e.currentTarget.value)}
      onkeydown={onComposeKey}
      aria-label="compose"
    ></textarea>
    <!-- Server-rendered preview of what you and the room will actually see. -->
    <div class="c-preview" role="region" aria-label="Preview">
      {#if compose.preview.error}
        <div class="c-err">{compose.preview.error}</div>
      {:else if compose.preview.you || compose.preview.room}
        {#if compose.preview.you}
          <div class="c-line {specFor(compose.mode).msgClass}">
            {@html composePreviewToHtml(compose.preview.you)}
          </div>
        {/if}
        {#if compose.preview.room}
          <div class="c-line c-room {specFor(compose.mode).msgClass}">
            {@html composePreviewToHtml(compose.preview.room)}
          </div>
        {/if}
      {:else}
        <div class="c-empty">Preview</div>
      {/if}
    </div>
    <div class="c-foot">
      <!-- The same setting as Settings > Text, where players look for it. -->
      <button
        class="sh-toggle"
        aria-pressed={settings.composeStaysOpen}
        onclick={() => (settings.composeStaysOpen = !settings.composeStaysOpen)}>Keep open</button
      >
      <button class="sh-cmd primary c-send" onclick={sendCompose}>Send</button>
    </div>
  </div>
{/if}

<div class="command-bar" role="region" aria-label="Command line">
  {#if session.prompt && !settings.hidePrompt}
    <div class="prompt">{@html session.prompt}</div>
  {/if}
  {#if rSearch}
    <span class="rs-tag" aria-hidden="true">r-search</span>
    <input class="rs-input" bind:value={rQuery} onkeydown={onRKey} use:focusOnMount class:sh-placeholder={true} placeholder="Search history"
      aria-label="Search command history" aria-describedby="rs-match" />
    <span class="rs-match" id="rs-match" aria-live="polite">{rMatch || "(no match)"}</span>
  {:else}
    <span class="chevron glow-text" aria-hidden="true">&gt;</span>
    <!-- A textarea, so a long pose wraps where it can be read. Enter still
         sends; a reader announces "multi-line", hence the key hint. -->
    <textarea
      class="command-input"
      rows="1"
      bind:this={inputEl}
      bind:value
      onkeydown={onKeydown}
      onbeforeinput={onBeforeInput}
      onpaste={onPaste}
      autocomplete="off"
      autocapitalize="off"
      spellcheck="false"
      enterkeyhint="send"
      use:focusOnMount
      aria-label="Command"
      aria-describedby="command-keys"
      data-focus-region="input"
    ></textarea>
    <span class="sr-only" id="command-keys">Enter sends. Shift+Enter starts a new line.</span>
    <button
      class="sh-cmd compose-btn"
      class:has-draft={compose.hasDraft}
      onclick={openCompose}
      title={compose.hasDraft ? "compose pad (draft saved)" : "compose pad"}
      aria-label="compose pad">Compose</button
    >
  {/if}
</div>

<style>
  .command-bar {
    /* One row is a line of text or a command, whichever is taller. Everything
       lines up on the first row, and a long line grows down past it. */
    --cmd-line: calc(1rem * var(--shell-line-height, 1.5));
    --cmd-row: max(24px, var(--cmd-line));
    display: flex;
    align-items: flex-start;
    gap: 0.6rem;
    padding: 0.55rem 0.85rem;
    border-top: 1px solid var(--accent);
    background: var(--bg-elev);
  }
  /* The command line's focus indicator; see shell.css. */
  .command-bar:focus-within { box-shadow: inset 0 0 0 2px var(--accent-bright); }
  .prompt {
    white-space: pre-wrap;
    color: var(--gold);
    flex: 0 0 auto;
    line-height: var(--shell-line-height, 1.5);
    padding-block: calc((var(--cmd-row) - var(--cmd-line)) / 2);
  }
  .chevron {
    color: var(--accent-bright);
    flex: 0 0 auto;
    line-height: var(--cmd-row);
  }
  .command-input {
    flex: 1 1 auto;
    min-width: 0;
    display: block;
    margin: 0;
    /* 2px sides: where the text sat when the command line was an <input>. */
    padding: calc((var(--cmd-row) - var(--cmd-line)) / 2) 2px;
    background: transparent;
    border: none;
    outline: none;
    resize: none;
    /* A long line grows the field to here, then scrolls. */
    max-height: 40vh;
    overflow-y: auto;
    color: var(--fg);
    font: inherit;
    line-height: var(--shell-line-height, 1.5);
    letter-spacing: 0.02em;
    caret-color: var(--accent-bright);
  }
  /* A saved draft is invisible once the pad is closed, so mark the button. */
  .compose-btn { flex: 0 0 auto; min-height: var(--cmd-row); }
  .compose-btn.has-draft { color: var(--gold); }
  .rs-tag { color: var(--gold); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; flex: 0 0 auto; line-height: var(--cmd-row); }
  .rs-input {
    flex: 1 1 auto; background: transparent; border: none; outline: none; height: var(--cmd-row);
    color: var(--fg); font: inherit; caret-color: var(--gold);
  }
  .rs-match {
    color: var(--accent-bright); font-size: 0.82rem; flex: 0 1 auto; line-height: var(--cmd-row);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .compose {
    border-top: 1px solid var(--accent); background: var(--bg-elev);
    display: flex; flex-direction: column; gap: 5px; padding: 6px 10px;
  }
  .c-head { display: flex; align-items: center; gap: 1ch; }
  .c-tag { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.18em; font-size: 0.72rem; }
  .c-hint { color: var(--fg-faint); font-size: 0.6rem; letter-spacing: 0.14em; text-transform: uppercase; }
  .c-x { margin-left: auto; }
  .c-modes { display: flex; gap: 2px 6px; flex-wrap: wrap; }
  .c-preview {
    min-height: 2.4em; border-left: 2px solid var(--border-bright);
    padding: 2px 0 2px 8px; font-size: 0.84rem; line-height: 1.45;
  }
  .c-line { color: var(--fg); }
  .c-room { color: var(--fg-dim); }
  .c-empty, .c-err { color: var(--fg-faint); font-size: 0.62rem; letter-spacing: 0.14em; text-transform: uppercase; }
  .c-err { color: var(--accent-ember, var(--fg-dim)); }
  .compose textarea {
    background: var(--bg); color: var(--fg); border: 1px solid var(--border-bright);
    font-family: inherit; font-size: 0.9rem; padding: 6px 8px; min-height: 80px; resize: vertical; line-height: 1.5;
  }
  .compose textarea:focus { outline: none; border-color: var(--accent); }
  .c-foot { display: flex; align-items: center; justify-content: space-between; gap: 1ch; }
</style>
