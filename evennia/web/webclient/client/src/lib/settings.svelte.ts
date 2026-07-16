// Reactive, persisted shell settings. The store writes every value onto <html>
// as data-* attributes and CSS vars; all styling and effects key off those, so a
// change is a single attribute/var flip. Grouped conceptually into Visual, CRT,
// Audio and Text (see SettingsPanel).

export type ThemeName = "haemal" | "amber" | "abyssal" | "sanctum" | "matrix" | "custom";

export const THEMES: { id: ThemeName; label: string }[] = [
  { id: "haemal", label: "Haemal - Blood" },
  { id: "amber", label: "Amber - Phosphor" },
  { id: "abyssal", label: "Abyssal - Deep Blue" },
  { id: "sanctum", label: "Sanctum - Gold" },
  { id: "matrix", label: "Matrix - Green" },
  { id: "custom", label: "Custom - your colours" },
];

// CSS vars the custom-theme editor exposes. Keys map to --var names.
export const CUSTOM_VARS: { key: string; var: string; label: string }[] = [
  { key: "bg", var: "--bg", label: "Background" },
  { key: "bgElev", var: "--bg-elev", label: "Panel" },
  { key: "fg", var: "--fg", label: "Text" },
  { key: "accent", var: "--accent", label: "Accent" },
  { key: "accentBright", var: "--accent-bright", label: "Accent bright" },
  { key: "gold", var: "--gold", label: "Gold" },
  { key: "alert", var: "--alert", label: "Alert" },
];

const CUSTOM_DEFAULTS: Record<string, string> = {
  bg: "#0d0b0f",
  bgElev: "#15121a",
  fg: "#d6cbb8",
  accent: "#7a2233",
  accentBright: "#c8324a",
  gold: "#c9a44c",
  alert: "#e5484d",
};

export const FONTS: { id: string; label: string; stack: string }[] = [
  { id: "plex", label: "IBM Plex Mono", stack: '"IBM Plex Mono", ui-monospace, monospace' },
  { id: "jetbrains", label: "JetBrains Mono", stack: '"JetBrains Mono", ui-monospace, monospace' },
  { id: "fira", label: "Fira Code", stack: '"Fira Code", ui-monospace, monospace' },
  { id: "space", label: "Space Mono", stack: '"Space Mono", ui-monospace, monospace' },
  { id: "sharetech", label: "Share Tech Mono", stack: '"Share Tech Mono", ui-monospace, monospace' },
  { id: "vt323", label: "VT323 - Retro", stack: '"VT323", ui-monospace, monospace' },
  { id: "system", label: "System Mono", stack: 'ui-monospace, "Cascadia Mono", Menlo, Consolas, monospace' },
];

export const EMBER_THEMES: { id: string; label: string; colors: string[] }[] = [
  { id: "ash", label: "Ash & Ember", colors: ["#e0602a", "#c9a44c", "#8a3a1a"] },
  { id: "plasma", label: "Plasma Storm - Cyan/Purple", colors: ["#39d0ff", "#9b6bff", "#6fe0ff"] },
  { id: "warp", label: "Warp - Green", colors: ["#39ff88", "#8affb0", "#2f9a5a"] },
  { id: "sanguine", label: "Sanguine - Blood", colors: ["#e0362b", "#b21f1a", "#ff6a5a"] },
  { id: "reliquary", label: "Reliquary - Gold", colors: ["#ecc972", "#c9a44c", "#fff0b8"] },
];

const KEY = "underspire.settings.v2";

interface Persisted {
  theme: ThemeName;
  font: string;
  fontSize: number;
  lineHeight: number;
  scanlines: boolean;
  scanlineOpacity: number; // 0..30 (%)
  flicker: boolean;
  vignette: boolean;
  vignetteIntensity: number; // 0..100 (%)
  glow: boolean;
  embers: boolean;
  emberTheme: string;
  emberIntensity: number; // 0..100
  keyboardSfx: boolean;
  music: boolean;
  typewriter: boolean;
  sceneStrip: boolean;
  notifyDesktop: boolean;
  notifySound: boolean;
  screenreader: boolean;
  reduceMotion: boolean;
  hidePrompt: boolean;
  customColors: Record<string, string>;
}

const DEFAULTS: Persisted = {
  theme: "haemal",
  font: "plex",
  fontSize: 15,
  lineHeight: 1.5,
  scanlines: true,
  scanlineOpacity: 14,
  flicker: true,
  vignette: true,
  vignetteIntensity: 70,
  glow: true,
  embers: true,
  emberTheme: "ash",
  emberIntensity: 45,
  keyboardSfx: false,
  music: true,
  typewriter: true,
  sceneStrip: true,
  notifyDesktop: false,
  notifySound: true,
  screenreader: false,
  reduceMotion:
    typeof matchMedia === "function" &&
    matchMedia("(prefers-reduced-motion: reduce)").matches,
  hidePrompt: false,
  customColors: { ...CUSTOM_DEFAULTS },
};

const RANGES = {
  fontSize: [12, 22],
  lineHeight: [1.2, 1.9],
  scanlineOpacity: [0, 30],
  vignetteIntensity: [0, 100],
  emberIntensity: [0, 100],
} as const;

function load(): Persisted {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch {
    /* ignore */
  }
  return { ...DEFAULTS };
}

function fontStack(id: string): string {
  return (FONTS.find((f) => f.id === id) ?? FONTS[0]).stack;
}

class Settings {
  theme = $state<ThemeName>(DEFAULTS.theme);
  font = $state(DEFAULTS.font);
  fontSize = $state(DEFAULTS.fontSize);
  lineHeight = $state(DEFAULTS.lineHeight);
  scanlines = $state(DEFAULTS.scanlines);
  scanlineOpacity = $state(DEFAULTS.scanlineOpacity);
  flicker = $state(DEFAULTS.flicker);
  vignette = $state(DEFAULTS.vignette);
  vignetteIntensity = $state(DEFAULTS.vignetteIntensity);
  glow = $state(DEFAULTS.glow);
  embers = $state(DEFAULTS.embers);
  emberTheme = $state(DEFAULTS.emberTheme);
  emberIntensity = $state(DEFAULTS.emberIntensity);
  keyboardSfx = $state(DEFAULTS.keyboardSfx);
  music = $state(DEFAULTS.music);
  typewriter = $state(DEFAULTS.typewriter);
  sceneStrip = $state(DEFAULTS.sceneStrip);
  notifyDesktop = $state(DEFAULTS.notifyDesktop);
  notifySound = $state(DEFAULTS.notifySound);
  screenreader = $state(DEFAULTS.screenreader);
  reduceMotion = $state(DEFAULTS.reduceMotion);
  hidePrompt = $state(DEFAULTS.hidePrompt);
  customColors = $state<Record<string, string>>({ ...CUSTOM_DEFAULTS });
  private _lastSR: boolean | null = null;

  init(): void {
    const p = load();
    for (const k of Object.keys(DEFAULTS) as (keyof Persisted)[]) {
      (this as any)[k] = p[k];
    }
    $effect.root(() => {
      $effect(() => this.apply());
    });
  }

  private apply(): void {
    const root = document.documentElement;
    // Reduced motion / screenreader force the heavy visual effects off.
    const calm = this.reduceMotion || this.screenreader;
    root.setAttribute("data-theme", this.theme);
    // Custom theme: paint the picked colours as inline CSS vars (else clear them).
    for (const cv of CUSTOM_VARS) {
      if (this.theme === "custom") {
        root.style.setProperty(cv.var, this.customColors[cv.key] ?? CUSTOM_DEFAULTS[cv.key]);
      } else {
        root.style.removeProperty(cv.var);
      }
    }
    root.style.setProperty("--font-mono", fontStack(this.font));
    root.style.setProperty("--shell-font-size", `${this.fontSize}px`);
    root.style.setProperty("--shell-line-height", String(this.lineHeight));
    root.style.setProperty("--scanline-opacity", String(this.scanlineOpacity / 100));
    root.style.setProperty("--vignette-intensity", String(this.vignetteIntensity / 100));
    // Shared hook so CSS-only effects (e.g. ANSI blink) can suppress motion.
    root.toggleAttribute("data-calm", calm);
    root.toggleAttribute("data-scanlines", this.scanlines && !calm);
    root.toggleAttribute("data-flicker", this.flicker && !calm);
    root.toggleAttribute("data-vignette", this.vignette && !calm);
    root.toggleAttribute("data-glow", this.glow && !calm);
    root.toggleAttribute("data-embers", this.embers && !calm);
    root.toggleAttribute("data-scene-strip", this.sceneStrip);
    root.toggleAttribute("data-kbd-sfx", this.keyboardSfx);
    root.toggleAttribute("data-screenreader", this.screenreader);
    // Mirror screenreader to the server so its rendering matches (telnet parity).
    if (this._lastSR !== this.screenreader) {
      this._lastSR = this.screenreader;
      import("./evennia.svelte").then(({ connection }) => {
        connection.sendOobRaw("webclient_options", [], { SCREENREADER: this.screenreader });
      });
    }
    this.save();
  }

  private save(): void {
    try {
      const out: any = {};
      for (const k of Object.keys(DEFAULTS)) out[k] = (this as any)[k];
      localStorage.setItem(KEY, JSON.stringify(out));
    } catch {
      /* ignore */
    }
  }
}

export const settings = new Settings();
export { RANGES };
