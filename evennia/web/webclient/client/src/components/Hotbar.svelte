<script lang="ts">
  import { macros, comboOf } from "../lib/macros.svelte";
  import { commands } from "../lib/commands.svelte";

  let editing = $state(false);
  let dragId: string | null = null;

  function captureKey(id: string, e: KeyboardEvent) {
    // Tab and Escape leave the field: capturing them trapped keyboard users.
    if (e.key === "Tab" || e.key === "Escape") return;
    e.preventDefault();
    if (e.key === "Backspace" || e.key === "Delete") {
      macros.update(id, { key: undefined });
      return;
    }
    const c = comboOf(e);
    if (c) macros.update(id, { key: c });
  }
</script>

<div class="hotbar" role="region" aria-label="Macros">
  <div class="macros">
    {#each macros.list as m, i (m.id)}
      {#if editing}
        {@const n = `Macro ${i + 1}`}
        <div class="edit-cell" role="group" aria-label={n}>
          <input class="e-icon" bind:value={m.icon} onchange={() => macros.save()} maxlength="2" aria-label="{n} icon" />
          <input class="e-label" bind:value={m.label} onchange={() => macros.save()} placeholder="label" aria-label="{n} label" />
          <input class="e-cmd" bind:value={m.command} onchange={() => macros.save()} placeholder="command" aria-label="{n} command" />
          <input class="e-key" readonly value={m.key ?? ""} onkeydown={(e) => captureKey(m.id, e)} placeholder="bind"
            aria-label="{n} key: press a key combination, Backspace to clear" />
          <!-- Reordering had been drag-only. -->
          <button class="e-mv" disabled={i === 0} onclick={() => macros.move(m.id, macros.list[i - 1].id)} aria-label="Move {n} left">‹</button>
          <button class="e-mv" disabled={i === macros.list.length - 1} onclick={() => macros.move(m.id, macros.list[i + 1].id)} aria-label="Move {n} right">›</button>
          <button class="e-del" onclick={() => macros.remove(m.id)} aria-label="Remove {n}">&times;</button>
        </div>
      {:else}
        <button
          class="macro"
          draggable="true"
          ondragstart={() => (dragId = m.id)}
          ondragover={(e) => e.preventDefault()}
          ondrop={() => { if (dragId) macros.move(dragId, m.id); dragId = null; }}
          onclick={() => commands.run(m.command)}
          title={m.command}
        >
          {#if m.icon}<span class="m-icon">{m.icon}</span>{/if}
          <span class="m-label">{m.label}</span>
          {#if m.key}<span class="m-key">{m.key}</span>{/if}
        </button>
      {/if}
    {/each}
    {#if editing}
      <button class="sh-cmd" onclick={() => macros.add()}>Add</button>
    {/if}
  </div>
  <button class="sh-cmd edit" aria-pressed={editing} onclick={() => (editing = !editing)}>{editing ? "Done" : "Macros"}</button>
</div>

<style>
  .hotbar {
    display: flex; align-items: center; gap: 8px;
    padding: 4px 10px; border-top: 1px solid var(--border);
    background: var(--bg-elev); flex: 0 0 auto;
  }
  .macros { display: flex; gap: 6px; flex: 1; flex-wrap: wrap; align-items: center; }
  /* A macro is a key on the deck: its binding stamped beside the label. */
  .macro {
    display: flex; align-items: baseline; gap: 0.8ch;
    background: none; border: 0; border-bottom: 1px solid var(--border-bright); color: var(--fg);
    font-family: inherit; font-size: 0.68rem; letter-spacing: 0.12em; text-transform: uppercase;
    padding: 3px 0.8ch; cursor: pointer; min-height: 24px;
  }
  .macro:hover, .macro:focus-visible { color: var(--bg-deep); background: var(--accent); border-bottom-color: var(--accent); }
  .m-key { order: -1; color: var(--gold); font-size: 0.6rem; letter-spacing: 0.06em; }
  .macro:hover .m-key { color: inherit; }
  .m-icon { color: var(--accent-bright); }
  .macro:hover .m-icon { color: inherit; }
  .macro[draggable="true"] { cursor: grab; }
  .e-icon { width: 2.4rem; text-align: center; }
  .edit-cell { display: flex; gap: 3px; align-items: center; }
  .edit-cell input {
    background: transparent; border: 0; border-bottom: 1px solid var(--border-bright); color: var(--fg);
    font-family: inherit; font-size: 0.7rem; padding: 2px 4px;
  }
  .edit-cell input:focus { border-bottom-color: var(--accent-bright); }
  .e-label { width: 5rem; }
  .e-cmd { width: 8rem; }
  .e-key { width: 4.5rem; color: var(--gold); }
  .e-del { background: none; border: none; color: var(--alert); cursor: pointer; font-size: 0.9rem; }
  .e-mv { background: none; border: none; color: var(--fg-dim); cursor: pointer; font-size: 0.9rem; min-width: 18px; }
  .e-mv:disabled { color: var(--fg-faint); cursor: default; }
  .edit { flex: 0 0 auto; }
</style>
