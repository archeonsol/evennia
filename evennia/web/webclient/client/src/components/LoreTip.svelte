<script lang="ts">
  import { lore } from "../lib/lore.svelte";

  let box = $state<HTMLElement | null>(null);
  let left = $state(0);
  let top = $state(0);
  let placed = $state(false);

  // Place the note once it is on screen and its size is known: over the word when
  // there is room, under it when there is not, and never past the window's edge.
  $effect(() => {
    const a = lore.active;
    const el = box;
    if (!a || !el) {
      placed = false;
      return;
    }
    const margin = 8;
    const gap = 6;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    left = Math.min(Math.max(margin, a.rect.left), Math.max(margin, window.innerWidth - w - margin));
    const above = a.rect.top - h - gap;
    top = above >= margin ? above : Math.min(a.rect.bottom + gap, Math.max(margin, window.innerHeight - h - margin));
    placed = true;
  });
</script>

{#if lore.active}
  {@const a = lore.active}
  <div
    class="lore-tip framed"
    role="tooltip"
    bind:this={box}
    style="left: {left}px; top: {top}px; visibility: {placed ? 'visible' : 'hidden'}"
    onpointerenter={() => lore.hold()}
    onpointerleave={() => lore.hide()}
  >
    <div class="title">{a.tip.title}</div>
    <p class="blurb">{a.tip.blurb}</p>
    <button class="sh-cmd more" onclick={() => void lore.open()}>Read more</button>
  </div>
{/if}

<style>
  /* Over the floating panels (40), under the shell's own dialogs (Settings 90,
     palette 95, toasts 120): a note never covers a dialog. */
  .lore-tip {
    position: fixed;
    z-index: 80;
    box-sizing: border-box;
    max-width: min(24rem, calc(100vw - 16px));
    padding: 8px 11px 9px;
    background: var(--bg-deep);
    color: var(--fg);
    border: 1px solid var(--accent);
    font-family: var(--font-mono);
    font-size: calc(var(--shell-font-size, 15px) * 0.88);
    line-height: 1.45;
    box-shadow: 0 6px 22px rgb(0 0 0 / 55%);
  }
  .title {
    margin-bottom: 3px;
    color: var(--gold);
    font-size: 0.78em;
    letter-spacing: 0.14em;
    text-transform: uppercase;
  }
  .blurb {
    margin: 0;
  }
  .more {
    margin-top: 7px;
  }
</style>
