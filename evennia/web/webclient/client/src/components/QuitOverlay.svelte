<script lang="ts">
  import { connection } from "../lib/evennia.svelte";
</script>

<div class="scrim">
  <div class="quit framed">
    <div class="mark glow-text" aria-hidden="true">⏻</div>
    <h2 class="glow-text">DISCONNECTED</h2>
    <p class="sub">You have left Underspire.</p>
    <div class="acts">
      <button class="primary" onclick={() => connection.reconnect()}>Reconnect</button>
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
  .mark { font-size: 2.4rem; color: var(--accent-bright); line-height: 1; }
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
