<script lang="ts">
  import { settings, EMBER_THEMES } from "../lib/settings.svelte";

  let canvas = $state<HTMLCanvasElement | null>(null);

  interface P {
    x: number;
    y: number;
    vx: number;
    vy: number;
    life: number;
    max: number;
    size: number;
    color: string;
  }

  $effect(() => {
    // Re-run when embers/theme/intensity change.
    const on = settings.embers;
    const themeId = settings.emberTheme;
    const intensity = settings.emberIntensity;
    const el = canvas;
    if (!el || !on || intensity <= 0) {
      const ctx = el?.getContext("2d");
      if (ctx && el) ctx.clearRect(0, 0, el.width, el.height);
      return;
    }

    const ctx = el.getContext("2d");
    if (!ctx) return;
    const colors = (EMBER_THEMES.find((t) => t.id === themeId) ?? EMBER_THEMES[0]).colors;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let raf = 0;
    let particles: P[] = [];
    const cap = Math.round(20 + intensity * 1.4); // ~20..160

    function resize() {
      if (!el) return;
      el.width = window.innerWidth * dpr;
      el.height = window.innerHeight * dpr;
    }
    resize();
    window.addEventListener("resize", resize);

    function spawn(): P {
      const w = el!.width;
      const h = el!.height;
      return {
        x: Math.random() * w,
        y: h + Math.random() * 40 * dpr,
        vx: (Math.random() - 0.5) * 0.25 * dpr,
        vy: -(0.2 + Math.random() * 0.6) * dpr,
        life: 0,
        max: 240 + Math.random() * 360,
        size: (0.6 + Math.random() * 1.6) * dpr,
        color: colors[(Math.random() * colors.length) | 0],
      };
    }

    function frame() {
      if (!ctx || !el) return;
      ctx.clearRect(0, 0, el.width, el.height);
      const spawnRate = intensity / 60;
      if (particles.length < cap && Math.random() < spawnRate) particles.push(spawn());
      ctx.globalCompositeOperation = "lighter";
      for (const p of particles) {
        p.life += 1;
        p.x += p.vx;
        p.y += p.vy;
        p.vx += (Math.random() - 0.5) * 0.03 * dpr; // faint drift
        const t = p.life / p.max;
        const alpha = Math.max(0, Math.sin(t * Math.PI)) * 0.7;
        ctx.globalAlpha = alpha;
        ctx.fillStyle = p.color;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
      particles = particles.filter((p) => p.life < p.max && p.y > -20 * dpr);
      raf = requestAnimationFrame(frame);
    }
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      ctx.clearRect(0, 0, el.width, el.height);
    };
  });
</script>

<canvas bind:this={canvas} class="embers" aria-hidden="true"></canvas>

<style>
  .embers {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
    z-index: 55;
    opacity: 0.8;
  }
</style>
