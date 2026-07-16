<script lang="ts">
  import { settings, EMBER_THEMES } from "../lib/settings.svelte";

  let canvas = $state<HTMLCanvasElement | null>(null);

  interface P {
    x: number;
    y: number;
    vx: number;
    vy: number;
    phase: number;
    drift: number;
    wobble: number;
    life: number;
    max: number;
    size: number;
    color: string;
    bright: number;
    pulse: number;
    ember: boolean;
  }

  $effect(() => {
    // Re-run when embers/theme/intensity change. Reduce motion / screenreader
    // stop the particle loop outright, not just hide the canvas.
    const on = settings.embers && !settings.reduceMotion && !settings.screenreader;
    const themeId = settings.emberTheme;
    const intensity = settings.emberIntensity;
    const el = canvas;
    if (!el || !on || intensity <= 0) {
      const ctx = el?.getContext("2d");
      if (ctx && el) ctx.clearRect(0, 0, el.width, el.height);
      if (el) el.style.opacity = "0";
      return;
    }

    const ctx = el.getContext("2d");
    if (!ctx) return;
    const colors = (EMBER_THEMES.find((t) => t.id === themeId) ?? EMBER_THEMES[0]).colors;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let raf = 0;

    // Intensity (0..100) drives a few aspects at once: how many particles,
    // and how strongly the whole layer reads. Count ranges wider than the
    // legacy fixed-40 swarm so the high end can get genuinely dense.
    const cap = Math.round(10 + intensity * 1.3); // ~10..140
    el.style.opacity = String(0.4 + (intensity / 100) * 0.4); // ~0.4..0.8

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
      const ember = Math.random() < 0.15; // most are faint dust; a few are brighter embers
      return {
        x: Math.random() * w,
        y: h + (10 + Math.random() * 40) * dpr,
        vx: (Math.random() - 0.5) * 0.3 * dpr,
        vy: -(0.15 + Math.random() * 0.45) * dpr,
        phase: Math.random() * Math.PI * 2,
        drift: (Math.random() - 0.5) * 0.008,
        wobble: (8 + Math.random() * 20) * dpr,
        life: 0,
        max: 400 + Math.random() * 600,
        size: (ember ? 1.2 + Math.random() * 1.8 : 0.4 + Math.random() * 1.0) * dpr,
        color: colors[(Math.random() * colors.length) | 0],
        bright: ember ? 0.6 + Math.random() * 0.4 : 0.2 + Math.random() * 0.4,
        pulse: ember ? 0.02 + Math.random() * 0.03 : 0,
        ember,
      };
    }

    // Prefill a steady swarm with staggered lives so they don't fade in unison.
    let particles: P[] = Array.from({ length: cap }, () => {
      const p = spawn();
      p.y = Math.random() * el!.height;
      p.life = Math.random() * p.max * 0.6;
      return p;
    });

    function frame() {
      if (!ctx || !el) return;
      ctx.clearRect(0, 0, el.width, el.height);
      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];
        p.life += 1;
        p.phase += p.drift;
        p.x += p.vx + Math.sin(p.phase) * (p.wobble * 0.01);
        p.y += p.vy;

        const fadeIn = Math.min(1, p.life / 60);
        const fadeOut = Math.min(1, (p.max - p.life) / 80);
        let alpha = p.bright * fadeIn * fadeOut;
        if (p.pulse > 0) alpha += Math.sin(p.life * p.pulse) * 0.15;
        alpha = Math.max(0, Math.min(1, alpha));

        ctx.globalAlpha = alpha;
        ctx.fillStyle = p.color;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fill();

        if (p.ember && alpha > 0.3) {
          ctx.globalAlpha = alpha * 0.12;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size * 3, 0, Math.PI * 2);
          ctx.fill();
        }

        if (p.life >= p.max || p.y < -20 * dpr || p.x < -30 * dpr || p.x > el.width + 30 * dpr) {
          particles[i] = spawn();
        }
      }
      ctx.globalAlpha = 1;
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
  }
</style>
