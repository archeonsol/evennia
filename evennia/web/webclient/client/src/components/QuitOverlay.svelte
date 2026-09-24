<script lang="ts">
  import { connection } from "../lib/evennia.svelte";
  import { modal } from "../lib/modal";

  let reconnectBtn = $state<HTMLButtonElement | null>(null);
</script>

<div class="scrim">
  <!-- No onclose: the session is over, so Escape has nothing to go back to. -->
  <div class="quit framed" role="alertdialog" aria-modal="true" aria-labelledby="quit-title" aria-describedby="quit-sub"
    use:modal={{ initial: reconnectBtn }}>
    <!-- Power mark as SVG: U+23FB sits outside the shell-glyph fallback range,
         so the Latin-only webfont stacks render it as a missing-glyph box. -->
    <div class="mark" aria-hidden="true">
      <svg viewBox="0 0 24 24">
        <path
          d="M13 3h-2v10h2V3zm4.83 2.17l-1.42 1.42C17.99 7.86 19 9.81 19 12c0 3.87-3.13 7-7 7s-7-3.13-7-7c0-2.19 1.01-4.14 2.58-5.42L6.17 5.17C4.23 6.82 3 9.26 3 12c0 4.97 4.03 9 9 9s9-4.03 9-9c0-2.74-1.23-5.18-3.17-6.83z"
        />
      </svg>
    </div>
    <h2 class="glow-text" id="quit-title">DISCONNECTED</h2>
    <p class="sub" id="quit-sub">You have left Underspire.</p>
    <div class="acts">
      <button class="primary" bind:this={reconnectBtn} onclick={() => connection.reconnect()}>Reconnect</button>
      <a class="secondary" href="/">Leave</a>
    </div>
    <p class="hint">Your session ended. Reconnect to return to the game.</p>
  </div>
</div>

<style>
  .scrim {
    position: fixed; inset: 0; z-index: 200;
    display: flex; align-items: center; justify-content: center;
    background: rgba(0, 0, 0, 0.82); backdrop-filter: blur(2px);
  }
  .quit {
    width: min(22rem, 92vw); padding: 2rem 1.6rem 1.6rem;
    background: var(--bg-elev); color: var(--fg); font-family: var(--font-mono);
    text-align: center; display: flex; flex-direction: column; align-items: center; gap: 0.5rem;
  }
  .mark { display: flex; color: var(--accent-bright); line-height: 1; }
  .mark svg { display: block; width: 2.4rem; height: 2.4rem; fill: currentColor; }
  :global(html[data-glow]) .mark svg { filter: drop-shadow(0 0 6px var(--glow)); }
  h2 { margin: 0.3rem 0 0; color: var(--accent-bright); letter-spacing: 0.32em; font-size: 1rem; }
  .sub { margin: 0; color: var(--fg-dim); font-size: 0.8rem; }
  .acts { display: flex; gap: 10px; margin: 1rem 0 0.4rem; }
  .primary {
    background: var(--accent); color: var(--bg-deep); border: 1px solid var(--accent-bright);
    font-family: inherit; text-transform: uppercase; letter-spacing: 0.14em; font-size: 0.74rem;
    padding: 8px 20px; cursor: pointer;
  }
  .primary:hover { background: var(--accent-bright); }
  .secondary {
    display: inline-flex; align-items: center; background: none; border: 1px solid var(--border-bright);
    color: var(--fg-dim); text-decoration: none; font-family: inherit; text-transform: uppercase;
    letter-spacing: 0.14em; font-size: 0.74rem; padding: 8px 20px; cursor: pointer;
  }
  .secondary:hover { color: var(--fg); border-color: var(--accent); }
  .hint { margin: 0.6rem 0 0; color: var(--fg-faint); font-size: 0.68rem; }
</style>
