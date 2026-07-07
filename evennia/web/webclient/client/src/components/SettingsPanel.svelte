<script lang="ts">
  import { settings, THEMES, FONTS, EMBER_THEMES, CUSTOM_VARS } from "../lib/settings.svelte";
  import { notify } from "../lib/notify.svelte";
  import { triggers } from "../lib/triggers.svelte";
  import { routing } from "../lib/routing.svelte";
  import { macros } from "../lib/macros.svelte";
  import { keybinds, comboFromEvent } from "../lib/keybinds.svelte";
  import { dock } from "../lib/dock.svelte";
  import { panelPrefs } from "../lib/panelPrefs.svelte";
  import { exportConfig, importConfig } from "../lib/backup";

  let { onclose }: { onclose: () => void } = $props();
  const s = settings as any;

  type View =
    | "hub" | "visual" | "crt" | "audio" | "text"
    | "notify" | "access" | "triggers" | "macros" | "keys" | "panels" | "data";
  let view = $state<View>("hub");

  const groups: { id: View; glyph: string; label: string }[] = [
    { id: "visual", glyph: "▦", label: "Visual" },
    { id: "crt", glyph: "⌁", label: "Effects" },
    { id: "text", glyph: "≡", label: "Text" },
    { id: "audio", glyph: "◊", label: "Audio" },
    { id: "notify", glyph: "◔", label: "Alerts" },
    { id: "access", glyph: "✵", label: "Access" },
    { id: "panels", glyph: "▤", label: "Panels" },
    { id: "macros", glyph: "⌘", label: "Macros" },
    { id: "keys", glyph: "⌨", label: "Keys" },
    { id: "triggers", glyph: "⌥", label: "Triggers" },
    { id: "data", glyph: "⤓", label: "Backup" },
  ];

  const openPanels = $derived(view === "panels" ? (dock.api?.panels ?? []).map((p) => ({ id: p.id, title: p.title || p.id })) : []);

  let capKey = $state<string | null>(null);
  function onKbKey(e: KeyboardEvent, id: string) {
    e.preventDefault();
    const c = comboFromEvent(e);
    if (c) {
      keybinds.set(id, c);
      capKey = null;
    }
  }

  let fileInput = $state<HTMLInputElement | null>(null);
  function onImport(e: Event) {
    const f = (e.target as HTMLInputElement).files?.[0];
    if (f) importConfig(f);
  }

  function onDesktopToggle() {
    s.notifyDesktop = !s.notifyDesktop;
    if (s.notifyDesktop) notify.requestPermission();
  }

  let capturing = $state<string | null>(null);
  function onMacroKey(e: KeyboardEvent, id: string) {
    e.preventDefault();
    if (e.key === "Escape") {
      macros.update(id, { key: undefined });
      capturing = null;
      return;
    }
    const isFn = /^F\d{1,2}$/.test(e.key);
    const mod = e.ctrlKey || e.metaKey || e.altKey;
    if (!isFn && !mod) return;
    let combo = "";
    if (e.ctrlKey || e.metaKey) combo += "Ctrl+";
    if (e.altKey) combo += "Alt+";
    if (e.shiftKey && !isFn) combo += "Shift+";
    combo += isFn ? e.key : e.key.length === 1 ? e.key.toUpperCase() : e.key;
    macros.update(id, { key: combo });
    capturing = null;
  }

  const current = $derived(groups.find((g) => g.id === view));
  const fontStack = $derived(
    (FONTS.find((f) => f.id === settings.font) ?? FONTS[0]).stack,
  );
</script>

{#snippet toggle(label: string, key: string)}
  <button class="row toggle" onclick={() => (s[key] = !s[key])}>
    <span>{label}</span>
    <span class="ind" class:on={s[key]}>{s[key] ? "ON" : "OFF"}</span>
  </button>
{/snippet}

{#snippet slider(label: string, key: string, min: number, max: number, step: number, fmt: (v: number) => string)}
  <label class="row range">
    <span>{label}</span>
    <input type="range" {min} {max} {step} value={s[key]}
      oninput={(e) => (s[key] = +e.currentTarget.value)} />
    <span class="val">{fmt(s[key])}</span>
  </label>
{/snippet}

<div class="scrim" onclick={onclose} role="presentation"></div>
<div class="panel framed" role="dialog" aria-label="Settings" aria-modal="true">
  <header>
    {#if view !== "hub"}
      <button class="back" onclick={() => (view = "hub")} aria-label="back">‹</button>
    {/if}
    <span class="title glow-text">UNDERSPIRE</span>
    <span class="sub">{view === "hub" ? "settings" : current?.label}</span>
    <button class="x" onclick={onclose} aria-label="close">×</button>
  </header>

  <div class="body">
    {#if view === "hub"}
      <div class="hub">
        {#each groups as g}
          <button class="tile" onclick={() => (view = g.id)}>
            <span class="glyph" aria-hidden="true">{g.glyph}</span>
            <span class="tile-label">{g.label}</span>
          </button>
        {/each}
      </div>
    {:else if view === "visual"}
      <label class="row select">
        <span>Colour theme</span>
        <select bind:value={settings.theme}>
          {#each THEMES as t}<option value={t.id}>{t.label}</option>{/each}
        </select>
      </label>
      <label class="row select">
        <span>Output font</span>
        <select bind:value={settings.font}>
          {#each FONTS as f}<option value={f.id}>{f.label}</option>{/each}
        </select>
      </label>
      <div class="preview" style="font-family: {fontStack}">
        The quick brown fox jumps over the lazy dog. 0123456789
      </div>
      {@render slider("Font size", "fontSize", 12, 22, 1, (v) => `${v}px`)}
      {@render slider("Line height", "lineHeight", 1.2, 1.9, 0.05, (v) => v.toFixed(2))}
      {#if settings.theme === "custom"}
        <div class="grp">Custom colours</div>
        <div class="rules">
          {#each CUSTOM_VARS as cv}
            <label class="row swatch">
              <span>{cv.label}</span>
              <input type="color" value={settings.customColors[cv.key]}
                oninput={(e) => (settings.customColors = { ...settings.customColors, [cv.key]: e.currentTarget.value })} />
            </label>
          {/each}
        </div>
      {/if}
    {:else if view === "crt"}
      {@render toggle("Scanlines", "scanlines")}
      {@render slider("Scanline opacity", "scanlineOpacity", 0, 30, 1, (v) => `${v}%`)}
      {@render toggle("Screen flicker", "flicker")}
      {@render toggle("Vignette", "vignette")}
      {@render slider("Vignette intensity", "vignetteIntensity", 0, 100, 5, (v) => `${v}%`)}
      {@render toggle("Phosphor glow", "glow")}
      {@render toggle("Ember particles", "embers")}
      <label class="row select">
        <span>Particle theme</span>
        <select bind:value={settings.emberTheme}>
          {#each EMBER_THEMES as e}<option value={e.id}>{e.label}</option>{/each}
        </select>
      </label>
      {@render slider("Ember intensity", "emberIntensity", 0, 100, 5, (v) => `${v}%`)}
    {:else if view === "audio"}
      {@render toggle("Keyboard sound FX", "keyboardSfx")}
    {:else if view === "notify"}
      <button class="row toggle" onclick={onDesktopToggle}>
        <span>Desktop notifications</span>
        <span class="ind" class:on={s.notifyDesktop}>{s.notifyDesktop ? "ON" : "OFF"}</span>
      </button>
      {@render toggle("Notification sound", "notifySound")}
    {:else if view === "access"}
      {@render toggle("Screenreader mode", "screenreader")}
      {@render toggle("Reduce motion", "reduceMotion")}
    {:else if view === "text"}
      {@render toggle("Typewriter animations", "typewriter")}
      {@render toggle("Show scene panel", "sceneStrip")}
      {@render toggle("Hide prompt line", "hidePrompt")}
    {:else if view === "panels"}
      {#if openPanels.length}
        {#each openPanels as p (p.id)}
          <div class="grp">{p.title}
            <button class="pop" onclick={() => dock.popout(p.id)}>pop out ⇱</button>
          </div>
          <label class="row range">
            <span>Font</span>
            <input type="range" min="10" max="24" step="1"
              value={panelPrefs.get(p.id).fontPx ?? settings.fontSize}
              oninput={(e) => panelPrefs.set(p.id, { fontPx: +e.currentTarget.value })} />
            <span class="val">{panelPrefs.get(p.id).fontPx ?? settings.fontSize}px</span>
          </label>
          <label class="row range">
            <span>Opacity</span>
            <input type="range" min="30" max="100" step="5"
              value={panelPrefs.get(p.id).opacity ?? 100}
              oninput={(e) => panelPrefs.set(p.id, { opacity: +e.currentTarget.value })} />
            <span class="val">{panelPrefs.get(p.id).opacity ?? 100}%</span>
          </label>
        {/each}
      {:else}
        <p class="note">No panels open.</p>
      {/if}
    {:else if view === "keys"}
      <div class="rules">
        {#each keybinds.list as b (b.id)}
          <div class="row">
            <span>{b.label}</span>
            <button class="r-key" class:cap={capKey === b.id}
              onclick={() => (capKey = capKey === b.id ? null : b.id)}
              onkeydown={(e) => capKey === b.id && onKbKey(e, b.id)}>
              {capKey === b.id ? "press…" : b.combo}
            </button>
          </div>
        {/each}
      </div>
    {:else if view === "data"}
      <button class="add-rule" onclick={exportConfig}>⤓ Export config</button>
      <button class="add-rule" onclick={() => fileInput?.click()}>⤒ Import config</button>
      <input bind:this={fileInput} type="file" accept="application/json" style="display:none" onchange={onImport} />
    {:else if view === "macros"}
      <div class="rules">
        {#each macros.list as m (m.id)}
          <div class="rule macro">
            <input class="r-icon" placeholder="◆" maxlength="2" value={m.icon ?? ""}
              oninput={(e) => macros.update(m.id, { icon: e.currentTarget.value })} />
            <input class="r-label" placeholder="label" value={m.label}
              oninput={(e) => macros.update(m.id, { label: e.currentTarget.value })} />
            <input class="r-cmd" placeholder="command" value={m.command}
              oninput={(e) => macros.update(m.id, { command: e.currentTarget.value })} />
            <button class="r-key" class:cap={capturing === m.id}
              onclick={() => (capturing = capturing === m.id ? null : m.id)}
              onkeydown={(e) => capturing === m.id && onMacroKey(e, m.id)}>
              {capturing === m.id ? "press…" : m.key || "key"}
            </button>
            <button class="r-del" onclick={() => macros.remove(m.id)} aria-label="remove">×</button>
          </div>
        {/each}
      </div>
      <button class="add-rule" onclick={() => macros.add()}>+ macro</button>
    {:else if view === "triggers"}
      <div class="grp">Highlights</div>
      <div class="rules">
        {#each triggers.highlights as h, i}
          <div class="rule">
            <input class="r-cmd" placeholder="text or /regex/" bind:value={h.pattern} oninput={() => triggers.sync()} />
            <input class="r-color" type="color" bind:value={h.color} oninput={() => triggers.sync()} aria-label="colour" />
            <button class="r-del" onclick={() => triggers.removeHighlight(i)} aria-label="remove">×</button>
          </div>
        {/each}
      </div>
      <button class="add-rule" onclick={() => triggers.addHighlight()}>+ highlight</button>

      <div class="grp">Gags <span class="hint">hide matching lines</span></div>
      <div class="rules">
        {#each triggers.gags as g, i}
          <div class="rule">
            <input class="r-cmd" placeholder="text or /regex/" bind:value={g.pattern} oninput={() => triggers.sync()} />
            <button class="r-del" onclick={() => triggers.removeGag(i)} aria-label="remove">×</button>
          </div>
        {/each}
      </div>
      <button class="add-rule" onclick={() => triggers.addGag()}>+ gag</button>

      <div class="grp">Aliases <span class="hint">name → command</span></div>
      <div class="rules">
        {#each triggers.aliases as a, i}
          <div class="rule">
            <input class="r-label" placeholder="name" bind:value={a.name} oninput={() => triggers.sync()} />
            <input class="r-cmd" placeholder="command" bind:value={a.command} oninput={() => triggers.sync()} />
            <button class="r-del" onclick={() => triggers.removeAlias(i)} aria-label="remove">×</button>
          </div>
        {/each}
      </div>
      <button class="add-rule" onclick={() => triggers.addAlias()}>+ alias</button>

      <div class="grp">Actions <span class="hint">on match: sound / command / notify</span></div>
      <div class="rules">
        {#each triggers.actions as act, i}
          <div class="rule">
            <input class="r-cmd" placeholder="text or /regex/" bind:value={act.pattern} oninput={() => triggers.sync()} />
            <select class="r-kind" bind:value={act.kind} onchange={() => triggers.sync()}>
              <option value="sound">sound</option>
              <option value="command">command</option>
              <option value="notify">notify</option>
            </select>
            {#if act.kind !== "sound"}
              <input class="r-cmd" placeholder={act.kind === "command" ? "command" : "message"} bind:value={act.arg} oninput={() => triggers.sync()} />
            {/if}
            <button class="r-del" onclick={() => triggers.removeAction(i)} aria-label="remove">×</button>
          </div>
        {/each}
      </div>
      <button class="add-rule" onclick={() => triggers.addAction()}>+ action</button>

      <div class="grp">Routing <span class="hint">copy matching lines to a Feeds tab</span></div>
      <div class="rules">
        {#each routing.routes as r, i}
          <div class="rule">
            <input class="r-cmd" placeholder="text or /regex/" bind:value={r.pattern} oninput={() => routing.sync()} />
            <input class="r-label" placeholder="tab" bind:value={r.label} oninput={() => routing.sync()} />
            <button class="r-del" onclick={() => routing.remove(i)} aria-label="remove">×</button>
          </div>
        {/each}
      </div>
      <button class="add-rule" onclick={() => routing.add()}>+ route</button>
    {/if}
  </div>

  {#if view === "hub"}
    <button class="close-btn" onclick={onclose}>Close</button>
  {/if}
</div>

<style>
  .scrim { position: fixed; inset: 0; z-index: 90; background: rgba(0, 0, 0, 0.6); }
  .panel {
    position: fixed; z-index: 91; top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    width: min(25rem, 94vw); max-height: 88vh;
    display: flex; flex-direction: column;
    background: var(--bg-elev); color: var(--fg); font-family: var(--font-mono);
  }
  header {
    display: flex; align-items: baseline; gap: 0.6ch;
    padding: 0.7rem 1rem 0.5rem; border-bottom: 1px solid var(--accent);
  }
  .back {
    background: none; border: none; color: var(--accent-bright);
    font-size: 1.2rem; line-height: 1; cursor: pointer; padding: 0; align-self: center;
  }
  .title { color: var(--accent-bright); letter-spacing: 0.3em; font-size: 0.85rem; }
  .sub { color: var(--fg-dim); text-transform: uppercase; letter-spacing: 0.2em; font-size: 0.65rem; }
  .x { margin-left: auto; background: none; border: none; color: var(--fg-dim); font-size: 1.2rem; line-height: 1; cursor: pointer; }
  .x:hover { color: var(--accent-bright); }
  .body { overflow-y: auto; padding: 0.4rem 1rem 0.5rem; }

  .hub {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; padding: 0.4rem 0;
  }
  .tile {
    display: flex; flex-direction: column; align-items: center; gap: 6px;
    padding: 12px 6px; background: var(--bg); border: 1px solid var(--border);
    color: var(--fg); font-family: inherit; cursor: pointer;
  }
  .tile:hover { border-color: var(--accent); background: var(--bg-elev); }
  .glyph { color: var(--accent); font-size: 1.4rem; line-height: 1; }
  .tile-label { text-transform: uppercase; letter-spacing: 0.1em; font-size: 0.62rem; color: var(--fg-dim); }
  .tile:hover .tile-label { color: var(--fg); }

  .row {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    width: 100%; padding: 6px 0; font-size: 0.82rem; color: var(--fg);
    border-bottom: 1px solid var(--border);
  }
  .select select {
    flex: 1; max-width: 60%; background: var(--bg); color: var(--fg);
    border: 1px solid var(--border-bright); font-family: inherit; font-size: 0.8rem; padding: 4px 6px;
  }
  .select select:focus { outline: none; border-color: var(--accent); }
  .preview {
    padding: 8px 10px; margin: 6px 0; border: 1px solid var(--border-bright);
    background: var(--bg); color: var(--fg-dim); font-size: 0.85rem; line-height: 1.5;
  }
  .range input { flex: 1; max-width: 55%; accent-color: var(--accent); }
  .val { color: var(--fg-dim); min-width: 3.6em; text-align: right; }
  .toggle { background: none; border: none; border-bottom: 1px solid var(--border); color: inherit; font-family: inherit; cursor: pointer; }
  .toggle:hover .ind { color: var(--accent-bright); }
  .ind { letter-spacing: 0.15em; font-size: 0.7rem; color: var(--fg-faint); border: 1px solid var(--border-bright); padding: 1px 9px; }
  .ind.on { color: var(--accent-bright); border-color: var(--accent); }
  .note { color: var(--fg-faint); font-size: 0.72rem; font-style: italic; padding: 8px 0; }
  .pop {
    margin-left: auto; background: none; border: none; color: var(--accent-bright);
    font-family: inherit; font-size: 0.62rem; text-transform: none; letter-spacing: 0; cursor: pointer;
  }
  .pop:hover { color: var(--gold); }
  /* Structured rule rows (macros / triggers) */
  .grp {
    display: flex; align-items: baseline;
    margin: 12px 0 5px; font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.16em;
    color: var(--accent-bright); border-bottom: 1px solid var(--border); padding-bottom: 3px;
  }
  .grp .hint { color: var(--fg-faint); letter-spacing: 0.04em; text-transform: none; margin-left: 0.6ch; }
  .rules { display: flex; flex-direction: column; gap: 5px; }
  .rule { display: flex; align-items: center; gap: 5px; }
  .rule input {
    background: var(--bg); color: var(--fg); border: 1px solid var(--border-bright);
    font-family: inherit; font-size: 0.78rem; padding: 4px 6px; min-width: 0;
  }
  .rule input:focus { outline: none; border-color: var(--accent); }
  .r-icon { flex: 0 0 2.4em; text-align: center; }
  .r-label { flex: 0 0 6.5em; }
  .r-cmd { flex: 1 1 auto; }
  .r-color { flex: 0 0 2.2em; padding: 1px; height: 1.7em; cursor: pointer; }
  .r-kind {
    flex: 0 0 6em; background: var(--bg); color: var(--fg); border: 1px solid var(--border-bright);
    font-family: inherit; font-size: 0.74rem; padding: 3px 4px;
  }
  .swatch { justify-content: space-between; }
  .swatch input[type="color"] { width: 2.4em; height: 1.6em; padding: 1px; border: 1px solid var(--border-bright); cursor: pointer; background: var(--bg); }
  .r-key {
    flex: 0 0 4.5em; background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.06em;
    padding: 4px 4px; cursor: pointer;
  }
  .r-key.cap { border-color: var(--accent); color: var(--accent-bright); }
  .r-del {
    flex: 0 0 auto; background: none; border: 1px solid var(--border-bright); color: var(--fg-faint);
    font-family: inherit; cursor: pointer; padding: 2px 7px; line-height: 1;
  }
  .r-del:hover { border-color: var(--alert); color: var(--alert); }
  .add-rule {
    margin: 6px 0 2px; background: none; border: 1px dashed var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.7rem; letter-spacing: 0.08em; padding: 5px 10px; cursor: pointer;
  }
  .add-rule:hover { border-color: var(--accent); color: var(--accent-bright); }
  .close-btn {
    margin: 0.6rem 1rem 0.9rem; padding: 0.5rem; background: var(--bg);
    color: var(--fg-dim); border: 1px solid var(--border-bright); font-family: inherit;
    letter-spacing: 0.24em; text-transform: uppercase; font-size: 0.72rem; cursor: pointer;
  }
  .close-btn:hover { color: var(--accent-bright); border-color: var(--accent); }
</style>
