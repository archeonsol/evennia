<script lang="ts">
  import { macros, comboOf } from "../lib/macros.svelte";
  import { commands } from "../lib/commands.svelte";

  let editing = $state(false);
  let dragId: string | null = null;

  function captureKey(id: string, e: KeyboardEvent) {
    e.preventDefault();
    if (e.key === "Backspace" || e.key === "Delete") {
      macros.update(id, { key: undefined });
      return;
    }
    const c = comboOf(e);
    if (c) macros.update(id, { key: c });
  }
</script>

<div class="hotbar">
  <div class="macros">
    {#each macros.list as m (m.id)}
      {#if editing}
        <div class="edit-cell">
          <input class="e-icon" bind:value={m.icon} onchange={() => macros.save()} placeholder="◆" maxlength="2" />
          <input class="e-label" bind:value={m.label} onchange={() => macros.save()} placeholder="label" />
          <input class="e-cmd" bind:value={m.command} onchange={() => macros.save()} placeholder="command" />
          <input class="e-key" readonly value={m.key ?? ""} onkeydown={(e) => captureKey(m.id, e)} placeholder="bind" />
          <button class="e-del" onclick={() => macros.remove(m.id)} aria-label="remove">×</button>
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
      <button class="add" onclick={() => macros.add()}>+ macro</button>
    {/if}
  </div>
  <button class="edit" onclick={() => (editing = !editing)}>{editing ? "done" : "edit"}</button>
</div>

<style>
  .hotbar {
    display: flex; align-items: center; gap: 8px;
    padding: 4px 10px; border-top: 1px solid var(--border);
    background: var(--bg-elev); flex: 0 0 auto;
  }
  .macros { display: flex; gap: 6px; flex: 1; flex-wrap: wrap; align-items: center; }
  .macro {
    display: flex; align-items: baseline; gap: 6px;
    background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.72rem; letter-spacing: 0.08em; text-transform: uppercase;
    padding: 3px 9px; cursor: pointer;
  }
  .macro:hover { color: var(--accent-bright); border-color: var(--accent); }
  .m-key { color: var(--fg-faint); font-size: 0.62rem; }
  .macro:hover .m-key { color: var(--gold); }
  .m-icon { color: var(--accent); font-size: 0.85rem; }
  .macro[draggable="true"] { cursor: grab; }
  .e-icon { width: 2.4rem; text-align: center; }
  .edit-cell { display: flex; gap: 3px; align-items: center; }
  .edit-cell input {
    background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg);
    font-family: inherit; font-size: 0.7rem; padding: 2px 5px;
  }
  .e-label { width: 5rem; }
  .e-cmd { width: 8rem; }
  .e-key { width: 4.5rem; color: var(--gold); }
  .e-del { background: none; border: none; color: var(--alert); cursor: pointer; font-size: 0.9rem; }
  .add {
    background: none; border: 1px dashed var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.7rem; padding: 3px 9px; cursor: pointer;
  }
  .add:hover { color: var(--accent-bright); border-color: var(--accent); }
  .edit {
    background: none; border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.66rem; letter-spacing: 0.16em; text-transform: uppercase;
    padding: 2px 10px; cursor: pointer;
  }
  .edit:hover { color: var(--accent-bright); border-color: var(--accent); }
</style>
