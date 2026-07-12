<script lang="ts">
  import { scene } from "../lib/scene.svelte";
</script>

<aside class="room-panel">
  {#if scene.present}
    {#if scene.room.name}
      <div class="panel-title glow-text">{@html scene.room.name}</div>
    {/if}
    {#if scene.room.atmosphere}
      <div class="atmo">{@html scene.room.atmosphere}</div>
    {/if}
    {#if scene.room.desc}
      <div class="desc">{@html scene.room.desc}</div>
    {/if}

    <div class="section-head">
      <span class="br">┤</span>Present<span class="br">├</span><span class="rule"></span>
    </div>
    {#if scene.occupants.length}
      <ul>
        {#each scene.occupants as o (o.handle)}
          <li class="entity-ref" data-entity-handle={o.handle}>
            <span class="sigil" aria-hidden="true">▸</span>{@html o.name}
          </li>
        {/each}
      </ul>
    {:else}
      <p class="empty">none</p>
    {/if}

    {#if scene.exits.length}
      <div class="section-head">
        <span class="br">┤</span>Exits<span class="br">├</span><span class="rule"></span>
      </div>
      <ul>
        {#each scene.exits as e (e.key)}
          <li><span class="sigil" aria-hidden="true">▸</span>{e.name}</li>
        {/each}
      </ul>
    {/if}
  {:else}
    <p class="awaiting">Awaiting signal…</p>
  {/if}
</aside>

<style>
  .room-panel {
    width: 100%;
    height: 100%;
    overflow-y: auto;
    padding: 0.7rem 0.85rem 1rem;
    background: var(--bg-elev);
    font-size: 0.9rem;
  }
  .awaiting { color: var(--fg-faint); font-style: italic; }
  .panel-title {
    color: var(--accent-bright);
    text-transform: uppercase;
    letter-spacing: 0.16em;
    font-size: 0.9rem;
    padding-bottom: 0.4rem;
    margin-bottom: 0.5rem;
    border-bottom: 1px solid var(--border-bright);
  }
  .atmo {
    color: var(--fg-dim);
    font-style: italic;
    margin-bottom: 0.4rem;
  }
  .desc {
    color: var(--fg);
    margin-bottom: 0.4rem;
    white-space: pre-wrap;
  }
  ul { list-style: none; margin: 0; padding: 0; }
  li { padding: 0.08rem 0; color: var(--fg); }
  .empty { margin: 0.1rem 0; color: var(--fg-faint); }
</style>
