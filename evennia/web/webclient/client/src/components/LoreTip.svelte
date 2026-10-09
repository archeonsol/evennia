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
    <div class="head">
      <span class="title">{a.tip.title}</span>
      <button class="sh-cmd more" onclick={() => void lore.open()}>Read more</button>
    </div>
    <p class="blurb">{a.tip.blurb}</p>
  </div>
{/if}

<style>
  /* Over the floating panels (40), under the shell's own dialogs (Settings 90,
     palette 95, toasts 120): a note never covers a dialog. Small on purpose: it
     sits over the text being read. Sized in em, so it follows the font setting,
     with a floor so a small font setting does not make it unreadable. */
  .lore-tip {
    position: fixed;
    z-index: 80;
    box-sizing: border-box;
    /* max-content, not shrink-to-fit: the width must not depend on where the
       note is placed, or one placed near the right edge would be squeezed. */
    width: max-content;
    max-width: min(24em, calc(100vw - 16px));
    padding: 4px 8px 6px;
    background: var(--bg-deep);
    color: var(--fg);
    border: 1px solid var(--accent);
    font-family: var(--font-mono);
    font-size: max(10px, calc(var(--shell-font-size, 15px) * 0.72));
    line-height: 1.36;
    box-shadow: 0 4px 16px rgb(0 0 0 / 55%);
  }
  .head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 1em;
    margin-bottom: 1px;
  }
  .title {
    color: var(--gold);
    font-size: 0.86em;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .blurb {
    margin: 0;
  }
  .more {
    flex: none;
    min-height: 0;
    padding: 0;
    font-size: 0.9em;
  }
</style>
