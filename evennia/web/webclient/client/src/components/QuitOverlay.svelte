<script lang="ts">
  import { connection } from "../lib/evennia.svelte";
  import { modal } from "../lib/modal";

  // Reconnect reloads the page: every store starts empty, so the next login
  // cannot read what the last account had on screen. The scrim is opaque for
  // the same reason.

  let reconnectBtn = $state<HTMLButtonElement | null>(null);
  // The portal handed this session to another window (a copied tab).
  const moved = $derived(connection.logoutReason === "superseded");
</script>

<div class="scrim">
  <!-- No onclose: the session is over, so Escape has nothing to go back to. -->
  <div class="quit framed" role="alertdialog" aria-modal="true" aria-labelledby="quit-title" aria-describedby="quit-sub"
    use:modal={{ initial: reconnectBtn }}>
    <h2 class="glow-text" id="quit-title">Disconnected</h2>
    <p class="sub" id="quit-sub">
      {moved ? "Your session has moved to another window." : "You have left Underspire."}
    </p>
    <div class="acts">
      <button class="sh-cmd primary" bind:this={reconnectBtn} onclick={() => location.reload()}>Reconnect</button>
      <a class="sh-cmd" href="/">Leave</a>
    </div>
  </div>
</div>

<style>
  .scrim {
    position: fixed; inset: 0; z-index: 200;
    display: flex; align-items: center; justify-content: center;
    background: var(--bg);
  }
  .quit {
    width: min(22rem, 92vw); padding: 1.6rem 1.6rem 1.3rem;
    background: var(--bg-elev); color: var(--fg); font-family: var(--font-mono);
    display: flex; flex-direction: column; gap: 0.5rem;
  }
  h2 { margin: 0; color: var(--accent-bright); letter-spacing: 0.32em; font-size: 1rem; font-weight: normal; text-transform: uppercase; }
  .sub { margin: 0; color: var(--fg-dim); font-size: 0.8rem; }
  .acts { display: flex; gap: 6px; margin: 1rem 0 0 -0.5ch; }
  .acts a { text-decoration: none; }
</style>
