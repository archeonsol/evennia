<script lang="ts">
  import { commands } from "../lib/commands.svelte";
  import { puppets } from "../lib/puppets.svelte";
</script>

<section class="puppets" aria-label="Remote puppet viewpoints">
  {#if puppets.list.length}
    {#each puppets.list as feed (feed.npcId)}
      <article class="feed framed" class:resyncing={feed.resyncing}>
        <button class="heading" onclick={() => commands.run(`p${feed.slot} look`)}>
          <strong>P{feed.slot}</strong>
          <span>{feed.name}</span>
          <small>#{feed.npcId} · r{feed.revision}</small>
        </button>
        {#if feed.scene.room.name}
          <div class="room glow-text">{@html feed.scene.room.name}</div>
        {/if}
        {#if feed.scene.room.atmosphere}
          <div class="atmo">{@html feed.scene.room.atmosphere}</div>
        {/if}
        {#if feed.scene.occupants.length}
          <div class="line">
            <span class="label">HERE</span>
            {#each feed.scene.occupants as occupant, index (occupant.handle)}
              {@html occupant.name}{index < feed.scene.occupants.length - 1 ? ", " : ""}
            {/each}
          </div>
        {/if}
        {#if feed.scene.exits.length}
          <div class="line">
            <span class="label">EXITS</span>
            {feed.scene.exits.map((exit) => exit.name).join(", ")}
          </div>
        {/if}
        {#if feed.resyncing}<div class="sync">resyncing viewpoint…</div>{/if}
      </article>
    {/each}
  {:else}
    <p class="empty">No remote puppet viewpoints.</p>
  {/if}
</section>

<style>
  .puppets { display: grid; gap: 8px; padding: 8px; overflow: auto; }
  .feed { padding: 8px; background: color-mix(in srgb, var(--panel-bg, #111) 92%, var(--gold)); }
  .feed.resyncing { opacity: 0.72; }
  .heading { width: 100%; display: flex; gap: 7px; align-items: baseline; color: inherit; background: none; border: 0; padding: 0 0 6px; text-align: left; cursor: pointer; }
  .heading strong, .label { color: var(--gold); }
  .heading small { margin-left: auto; color: var(--muted, #889); }
  .room { font-weight: 600; margin-bottom: 3px; }
  .atmo, .line, .sync, .empty { color: var(--muted, #aab); font-size: 0.88em; }
  .label { margin-right: 6px; font-size: 0.78em; letter-spacing: 0.08em; }
  .sync { margin-top: 4px; }
</style>
