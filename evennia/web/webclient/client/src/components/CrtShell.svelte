<script lang="ts">
  import Embers from "./Embers.svelte";
  let { children } = $props();
</script>

<div class="crt">
  {@render children()}
  <Embers />
  <div class="crt-scan" aria-hidden="true"></div>
  <div class="crt-vig" aria-hidden="true"></div>
</div>

<style>
  .crt {
    position: relative;
    height: 100dvh;
    width: 100%;
    overflow: hidden;
    background: var(--bg-deep);
  }
  .crt-scan,
  .crt-vig {
    position: absolute;
    inset: 0;
    pointer-events: none;
    z-index: 60;
  }
  /* Scanlines - opacity driven by the setting. */
  :global(html[data-scanlines]) .crt-scan {
    background-image: repeating-linear-gradient(
      to bottom,
      rgba(0, 0, 0, var(--scanline-opacity, 0.14)) 0 1px,
      transparent 1px 3px
    );
  }
  /* Vignette - darkness scaled by intensity. */
  :global(html[data-vignette]) .crt-vig {
    background: radial-gradient(
      120% 120% at 50% 45%,
      transparent 52%,
      rgba(0, 0, 0, calc(var(--vignette-intensity, 0.7) * 0.8)) 100%
    );
    box-shadow: inset 0 0 240px 50px
      rgba(0, 0, 0, calc(var(--vignette-intensity, 0.7) * 0.85));
  }
  :global(html[data-flicker]) .crt-scan {
    animation: crt-flicker 7s steps(60) infinite;
  }
  @keyframes crt-flicker {
    0%, 100% { opacity: 1; }
    2% { opacity: 0.86; }
    4% { opacity: 1; }
    48% { opacity: 0.94; }
    52% { opacity: 0.88; }
    54% { opacity: 1; }
  }
</style>
