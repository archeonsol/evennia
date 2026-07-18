<script lang="ts">
  import { settings } from "../lib/settings.svelte";

  let { ondone }: { ondone: () => void } = $props();

  const LINES = [
    "U N D E R S P I R E",
    "> establishing sanctioned uplink . . .",
    "> rousing the machine-spirit . . .",
    "> purging corrupted sectors . . .",
    "> rites of activation observed",
    "// access granted",
  ];

  let shown = $state(0);
  let closing = $state(false);

  // Reduce motion / screenreader render the boot instantly, like the log typewriter.
  const animate = $derived(
    settings.typewriterMs > 0 && !settings.reduceMotion && !settings.screenreader,
  );

  function finish() {
    if (closing) return;
    closing = true;
    setTimeout(ondone, animate ? 420 : 0);
  }

  function skip() {
    shown = LINES.length;
    finish();
  }

  $effect(() => {
    if (!animate) {
      shown = LINES.length;
      // brief hold so it reads as a boot, not a flash
      const t = setTimeout(finish, 350);
      return () => clearTimeout(t);
    }
    const iv = setInterval(() => {
      shown += 1;
      if (shown >= LINES.length) {
        clearInterval(iv);
        setTimeout(finish, 700);
      }
    }, 260);
    return () => clearInterval(iv);
  });
</script>

<svelte:window onkeydown={skip} />

<button class="boot" class:closing onclick={skip} aria-label="skip intro">
  <div class="boot-inner">
    <div class="sigil" aria-hidden="true">┼</div>
    {#each LINES.slice(0, shown) as line, i}
      <div class="line" class:head={i === 0 || i === LINES.length - 1}>{line}</div>
    {/each}
    <div class="hint">[ press any key ]</div>
  </div>
</button>

<style>
  .boot {
    position: absolute;
    inset: 0;
    z-index: 100;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--bg-deep, #060403);
    border: none;
    cursor: pointer;
    font-family: var(--font-mono);
    transition: opacity 0.4s ease;
  }
  .boot.closing {
    opacity: 0;
  }
  .boot-inner {
    text-align: left;
    min-width: 20rem;
    color: var(--fg-dim);
    font-size: 0.95rem;
    line-height: 1.9;
  }
  .sigil {
    color: var(--accent);
    font-size: 1.6rem;
    margin-bottom: 0.6rem;
    text-shadow: 0 0 10px var(--glow);
  }
  .line {
    white-space: pre;
  }
  .line.head {
    color: var(--accent-bright);
    letter-spacing: 0.14em;
    text-transform: uppercase;
    text-shadow: 0 0 8px var(--glow);
  }
  .hint {
    margin-top: 1.2rem;
    color: var(--fg-faint);
    font-size: 0.75rem;
    letter-spacing: 0.2em;
  }
</style>
