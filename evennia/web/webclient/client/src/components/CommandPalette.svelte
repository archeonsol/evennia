<script lang="ts">
  import { commands, CURATED } from "../lib/commands.svelte";

  let { onclose }: { onclose: () => void } = $props();

  let q = $state("");
  let sel = $state(0);
  let input = $state<HTMLInputElement | null>(null);

  interface Item { cmd: string; label: string; recent: boolean }

  const items = $derived.by<Item[]>(() => {
    const base: Item[] = [
      ...commands.recent.map((c) => ({ cmd: c, label: c, recent: true })),
      ...CURATED.map((c) => ({ cmd: c.cmd, label: c.label, recent: false })),
    ];
    const query = q.trim().toLowerCase();
    const list = query
      ? base.filter((i) => `${i.label} ${i.cmd}`.toLowerCase().includes(query))
      : base;
    return list.slice(0, 40);
  });

  $effect(() => {
    input?.focus();
  });
  $effect(() => {
    void q;
    sel = 0;
  });

  function choose(cmd: string) {
    // Prefix commands (trailing space) keep the palette open to compose args;
    // complete commands run and close.
    if (cmd.endsWith(" ")) {
      q = cmd;
      input?.focus();
    } else {
      commands.run(cmd);
      onclose();
    }
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      sel = Math.min(sel + 1, items.length - 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      sel = Math.max(sel - 1, 0);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const it = items[sel];
      if (it) choose(it.cmd);
      else if (q.trim()) {
        commands.run(q);
        onclose();
      }
    } else if (e.key === "Escape") {
      onclose();
    }
  }
</script>

<div class="scrim" onclick={onclose} role="presentation"></div>
<div class="palette framed" role="dialog" aria-label="Command palette">
  <div class="bar">
    <span class="glyph glow-text" aria-hidden="true">❯</span>
    <input
      bind:this={input}
      bind:value={q}
      onkeydown={onKey}
      placeholder="run a command…"
      aria-label="command"
    />
  </div>
  <ul class="list">
    {#each items as it, i (it.cmd + i)}
      <li>
        <button
          class="item"
          class:sel={i === sel}
          onmouseenter={() => (sel = i)}
          onclick={() => choose(it.cmd)}
        >
          <span class="tag" aria-hidden="true">{it.recent ? "↺" : "›"}</span>
          <span class="lbl">{it.label}</span>
          {#if !it.recent && it.cmd.trim() !== it.label.toLowerCase()}
            <span class="hint">{it.cmd.trim()}</span>
          {/if}
        </button>
      </li>
    {/each}
  </ul>
</div>

<style>
  .scrim { position: fixed; inset: 0; z-index: 95; background: rgba(0, 0, 0, 0.5); }
  .palette {
    position: fixed; z-index: 96; top: 18%; left: 50%; transform: translateX(-50%);
    width: min(34rem, 92vw); max-height: 60vh; display: flex; flex-direction: column;
    background: var(--bg-elev); font-family: var(--font-mono);
  }
  .bar {
    display: flex; align-items: center; gap: 0.7ch;
    padding: 0.6rem 0.9rem; border-bottom: 1px solid var(--accent);
  }
  .glyph { color: var(--accent-bright); font-size: 1.05rem; }
  .bar input {
    flex: 1; background: transparent; border: none; outline: none;
    color: var(--fg); font-family: inherit; font-size: 0.95rem; letter-spacing: 0.02em;
  }
  .list { list-style: none; margin: 0; padding: 0; overflow-y: auto; }
  .item {
    display: flex; align-items: baseline; gap: 0.8ch; width: 100%;
    padding: 6px 12px; background: none; border: none; color: var(--fg);
    font-family: inherit; font-size: 0.85rem; cursor: pointer; text-align: left;
  }
  .item.sel { background: color-mix(in srgb, var(--accent) 22%, transparent); }
  .tag { color: var(--accent); width: 1ch; }
  .lbl { color: var(--fg); }
  .hint { margin-left: auto; color: var(--fg-faint); font-size: 0.75rem; }
</style>
