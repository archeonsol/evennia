<script lang="ts">
  import { describeVista, paintVista, type Vista } from "../lib/vista";
  import { settings } from "../lib/settings.svelte";

  // The picture of what lies ahead, drawn from the pixels the game sent. It is a still
  // picture, with a little life in it (stars that twinkle, lamps that pulse) unless the
  // viewer asked for less motion or reads by screen reader.
  let { vista }: { vista: Vista } = $props();

  let canvas = $state<HTMLCanvasElement | null>(null);
  let beat = $state(0);
  const live = $derived(!(settings.reduceMotion || settings.screenreader));

  $effect(() => {
    if (!live) return;
    const timer = setInterval(() => (beat += 1), 650);
    return () => clearInterval(timer);
  });

  $effect(() => {
    const el = canvas;
    if (!el) return;
    const ctx = el.getContext("2d");
    if (!ctx) return;
    const image = ctx.createImageData(vista.width, vista.height);
    paintVista(image.data, vista, beat, live);
    ctx.putImageData(image, 0, 0);
  });
</script>

<figure class="vista" aria-label={describeVista(vista)}>
  <canvas bind:this={canvas} width={vista.width} height={vista.height} aria-hidden="true"></canvas>
  {#if vista.ruler.length}
    <div class="ruler" aria-hidden="true">
      {#each vista.ruler as mark (mark.label)}
        <span style="left: {((mark.column + 0.5) / vista.width) * 100}%">{mark.label}</span>
      {/each}
    </div>
  {/if}
</figure>

<style>
  .vista {
    margin: 0 0 0.5rem;
    border: 1px solid var(--border-bright);
    background: #000;
  }
  canvas {
    display: block;
    width: 100%;
    height: auto;
    image-rendering: pixelated;
  }
  .ruler {
    position: relative;
    height: 1.1rem;
    border-top: 1px solid var(--border);
    background: var(--bg-elev);
  }
  .ruler span {
    position: absolute;
    top: 0.1rem;
    transform: translateX(-50%);
    color: var(--fg-faint);
    font-size: 0.6rem;
    letter-spacing: 0.12em;
  }
</style>
