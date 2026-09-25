<script lang="ts">
  import { settings, THEMES, FONTS, EMBER_THEMES, CUSTOM_VARS } from "../lib/settings.svelte";
  import { notify } from "../lib/notify.svelte";
  import { triggers } from "../lib/triggers.svelte";
  import { routing, newRouteId, type Route } from "../lib/routing.svelte";
  import { parsePattern, matches } from "../lib/pattern";
  import { session } from "../lib/session.svelte";
  import { macros } from "../lib/macros.svelte";
  import { keybinds, comboFromEvent } from "../lib/keybinds.svelte";
  import { dock } from "../lib/dock.svelte";
  import { panelPrefs } from "../lib/panelPrefs.svelte";
  import { exportConfig, importConfig } from "../lib/backup";
  import { modal } from "../lib/modal";
  import { tick, untrack } from "svelte";

  let { onclose, initial = "hub" }: { onclose: () => void; initial?: string } = $props();
  const s = settings as any;

  type View =
    | "hub" | "visual" | "crt" | "audio" | "text"
    | "notify" | "access" | "triggers" | "feeds" | "macros" | "keys" | "panels" | "data";
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
    { id: "feeds", glyph: "⇶", label: "Feeds" },
    { id: "triggers", glyph: "⌥", label: "Triggers" },
    { id: "data", glyph: "⤓", label: "Backup" },
  ];

  const openPanels = $derived(view === "panels" ? (dock.api?.panels ?? []).map((p) => ({ id: p.id, title: p.title || p.id })) : []);

  let capKey = $state<string | null>(null);
  function onKbKey(e: KeyboardEvent, id: string) {
    // Tab moves on and Escape cancels, leaving the binding as it was.
    if (e.key === "Tab") {
      capKey = null;
      return;
    }
    e.preventDefault();
    if (e.key === "Escape") {
      capKey = null;
      return;
    }
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
    if (e.key === "Tab") {
      capturing = null;
      return;
    }
    e.preventDefault();
    // Escape cancels; it used to erase the binding. Backspace/Delete clear it.
    if (e.key === "Escape") {
      capturing = null;
      return;
    }
    if (e.key === "Backspace" || e.key === "Delete") {
      macros.update(id, { key: undefined });
      capturing = null;
      return;
    }
    // The shared builder: a bare Ctrl/Alt/Shift press is null, so capture
    // waits for the real key instead of saving "Ctrl+Control".
    const combo = comboFromEvent(e);
    if (!combo) return;
    macros.update(id, { key: combo });
    capturing = null;
  }

  const current = $derived(groups.find((g) => g.id === view));
  const fontStack = $derived(
    (FONTS.find((f) => f.id === settings.font) ?? FONTS[0]).stack,
  );

  // Routing edits land on a draft, not on routing.routes: sync() purges the
  // buffers of labels no live route owns, so syncing a mid-typo label would
  // destroy a move feed irrecoverably. The draft reaches routing only on a
  // commit (field blur, MOVE, delete, add).
  let rdraft = $state<Route[]>([]);
  // Each view replaces the one before it, destroying the focused button, so
  // focus goes to the heading or a keyboard user is dropped onto the page.
  let heading = $state<HTMLElement | null>(null);
  let cameFrom: View | null = null;
  async function openView(id: View) {
    if (id === "feeds") rdraft = routing.routes.map((r) => ({ ...r }));
    if (id !== "hub") cameFrom = id;
    view = id;
    await tick();
    const tile = id === "hub" && cameFrom ? document.getElementById(`settings-tile-${cameFrom}`) : null;
    (tile ?? heading)?.focus();
  }
  function commitRoutes() {
    routing.routes = rdraft.map((r) => ({ ...r }));
    routing.sync();
  }
  function addRoute() {
    // The id is given here, not at sync: the draft row must carry the same
    // id as the committed rule, or renaming it would lose the feed's lines.
    rdraft.push({ id: newRouteId(), pattern: "", label: "", move: false, enabled: true });
    commitRoutes();
  }
  function moveRoute(i: number, dir: -1 | 1) {
    const j = i + dir;
    if (j < 0 || j >= rdraft.length) return;
    [rdraft[i], rdraft[j]] = [rdraft[j], rdraft[i]];
    commitRoutes();
  }

  // Live check of a rule while it is being typed: why it cannot work, or
  // what it would catch in the terminal right now. Nothing is committed.
  // What is wrong with a rule, or how many terminal lines it matches now.
  function routeStatus(r: Route): { tone: "err" | "warn" | "ok"; text: string } {
    if (!r.pattern.trim()) return { tone: "ok", text: "" };
    const p = parsePattern(r.pattern);
    if (p.error) return { tone: "err", text: p.error };
    if (!(r.label || "").trim()) return { tone: "warn", text: "Needs a feed name." };
    let n = 0;
    for (const l of session.lines) {
      if (l.type !== "media" && matches(p.re!, l.text)) n += 1;
    }
    return { tone: "ok", text: n ? `${n} match${n === 1 ? "" : "es"}` : "" };
  }
  const feedNames = $derived(routing.allLabels());

  // Opened at a view (the Feeds panel's rules button); the initial hub is the default.
  // untrack: openView reads the routes, and re-running this on every rule
  // commit would reset the draft and yank focus to the heading.
  $effect(() => {
    const start = initial;
    if (start !== "hub") untrack(() => void openView(start as View));
  });
</script>

{#snippet toggle(label: string, key: string, onflip?: (on: boolean) => void)}
  <button class="row toggle" role="switch" aria-checked={!!s[key]} onclick={() => {
    s[key] = !s[key];
    onflip?.(s[key]);
  }}>
    <span>{label}</span>
    <span class="ind" class:on={s[key]} aria-hidden="true">{s[key] ? "ON" : "OFF"}</span>
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
<div class="panel framed" role="dialog" aria-labelledby="settings-heading" aria-modal="true"
  use:modal={{ onclose: () => (view === "hub" ? onclose() : openView("hub")), initial: heading }}>
  <header>
    {#if view !== "hub"}
      <button class="back" onclick={() => openView("hub")} aria-label="Back to all settings">‹</button>
    {/if}
    <span class="title glow-text" aria-hidden="true">UNDERSPIRE</span>
    <h2 class="sub" id="settings-heading" tabindex="-1" bind:this={heading}>
      {view === "hub" ? "Settings" : `Settings: ${current?.label}`}
    </h2>
    <button class="x" onclick={onclose} aria-label="Close settings">×</button>
  </header>

  <div class="body">
    {#if view === "hub"}
      <div class="hub">
        {#each groups as g}
          <button class="tile" id="settings-tile-{g.id}" onclick={() => openView(g.id)}>
            <span class="tile-label">{g.label}</span>
            <span class="go" aria-hidden="true">›</span>
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
      {@render toggle("Room music", "music")}
      {@render toggle("Keyboard sound FX", "keyboardSfx")}
      {@render slider("Keyboard FX volume", "keyboardVolume", 0, 100, 5, (v) => `${v}%`)}
    {:else if view === "notify"}
      <button class="row toggle" onclick={onDesktopToggle}>
        <span>Desktop notifications</span>
        <span class="ind" class:on={s.notifyDesktop}>{s.notifyDesktop ? "ON" : "OFF"}</span>
      </button>
      {@render toggle("Notification sound", "notifySound")}
      <!-- While you are in another tab or window. -->
      <label class="row select">
        <span>Flash the browser tab</span>
        <select bind:value={settings.tabAlert}>
          <option value="any">on anything new</option>
          <option value="direct">on messages to me</option>
          <option value="off">never</option>
        </select>
      </label>
    {:else if view === "access"}
      <!-- Turning screen reader mode on also turns channel echo on: channels
           are otherwise only in a view the player is not reading. -->
      {@render toggle("Screen reader mode", "screenreader", (on) => {
        if (on) {
          s.channelEcho = true;
          s.music = false;
        }
      })}
      {@render toggle("Speak new output", "speakOutput")}
      {@render toggle("Channel messages in terminal", "channelEcho")}
      {@render toggle("Reduce motion", "reduceMotion")}
      <label class="row select">
        <span>Open web pages</span>
        <select bind:value={settings.webPages}>
          <option value="panel">in the client</option>
          <option value="window">in a new window</option>
        </select>
      </label>

    {:else if view === "text"}
      {@render slider("Typewriter reveal", "typewriterMs", 0, 1500, 50, (v) => (v ? `${v}ms` : "off"))}
      {@render toggle("Show scene panel", "sceneStrip")}
      {@render toggle("Hide prompt line", "hidePrompt")}
      {@render toggle("Echo commands in terminal", "echoCommands")}
      {@render toggle("Keep command after sending", "keepCommand")}
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
              aria-label="{b.label}: {capKey === b.id ? 'press the new key, Escape to cancel' : b.combo}"
              onclick={() => (capKey = capKey === b.id ? null : b.id)}
              onkeydown={(e) => capKey === b.id && onKbKey(e, b.id)}>
              {capKey === b.id ? "press…" : b.combo}
            </button>
          </div>
        {/each}
        <!-- Fixed keys, listed so they can be found. -->
        <div class="row"><span>Read a recent line again</span><span class="r-fixed">Alt+1 to Alt+9</span></div>
        <div class="row"><span>Scroll output from the command line</span><span class="r-fixed">Page Up / Page Down</span></div>
      </div>
    {:else if view === "data"}
      <button class="add-rule" onclick={exportConfig}>⤓ Export config</button>
      <button class="add-rule" onclick={() => fileInput?.click()}>⤒ Import config</button>
      <input bind:this={fileInput} type="file" accept="application/json" style="display:none" onchange={onImport} />
    {:else if view === "macros"}
      <div class="rules">
        {#each macros.list as m, i (m.id)}
          {@const n = `Macro ${i + 1}`}
          <div class="rule macro" role="group" aria-label={n}>
            <input class="r-icon" placeholder="◆" maxlength="2" value={m.icon ?? ""} aria-label="{n} icon"
              oninput={(e) => macros.update(m.id, { icon: e.currentTarget.value })} />
            <input class="r-label" placeholder="label" value={m.label} aria-label="{n} label"
              oninput={(e) => macros.update(m.id, { label: e.currentTarget.value })} />
            <input class="r-cmd" placeholder="command" value={m.command} aria-label="{n} command"
              oninput={(e) => macros.update(m.id, { command: e.currentTarget.value })} />
            <button class="r-key" class:cap={capturing === m.id}
              aria-label="{n} key: {capturing === m.id ? 'press a combination, Escape to cancel, Backspace to clear' : m.key || 'none'}"
              onclick={() => (capturing = capturing === m.id ? null : m.id)}
              onkeydown={(e) => capturing === m.id && onMacroKey(e, m.id)}>
              {capturing === m.id ? "press…" : m.key || "key"}
            </button>
            <button class="r-mv" disabled={i === 0} onclick={() => macros.move(m.id, macros.list[i - 1].id)} aria-label="Move {n} up">↑</button>
            <button class="r-mv" disabled={i === macros.list.length - 1} onclick={() => macros.move(m.id, macros.list[i + 1].id)} aria-label="Move {n} down">↓</button>
            <button class="r-del" onclick={() => macros.remove(m.id)} aria-label="Remove {n}">×</button>
          </div>
        {/each}
      </div>
      <button class="add-rule" onclick={() => macros.add()}>+ macro</button>
    {:else if view === "triggers"}
      <div class="grp">Highlights</div>
      <div class="rules">
        {#each triggers.highlights as h, i}
          <div class="rule">
            <input class="r-cmd" placeholder="text or /regex/" bind:value={h.pattern} oninput={() => triggers.sync()} aria-label="Highlight {i + 1} pattern"
              aria-invalid={!!parsePattern(h.pattern).error} title={parsePattern(h.pattern).error || undefined} />
            <input class="r-color" type="color" bind:value={h.color} oninput={() => triggers.sync()} aria-label="Highlight {i + 1} colour" />
            <button class="r-del" onclick={() => triggers.removeHighlight(i)} aria-label="Remove highlight {i + 1}">×</button>
          </div>
        {/each}
      </div>
      <button class="add-rule" onclick={() => triggers.addHighlight()}>+ highlight</button>

      <div class="grp">Gags <span class="hint">hide from the terminal</span></div>
      <div class="rules">
        {#each triggers.gags as g, i}
          <div class="rule">
            <input class="r-cmd" placeholder="text or /regex/" bind:value={g.pattern} oninput={() => triggers.sync()} aria-label="Gag {i + 1} pattern"
              aria-invalid={!!parsePattern(g.pattern).error} title={parsePattern(g.pattern).error || undefined} />
            <button class="r-del" onclick={() => triggers.removeGag(i)} aria-label="Remove gag {i + 1}">×</button>
          </div>
        {/each}
      </div>
      <button class="add-rule" onclick={() => triggers.addGag()}>+ gag</button>

      <div class="grp">Aliases <span class="hint">name → command</span></div>
      <div class="rules">
        {#each triggers.aliases as a, i}
          <div class="rule">
            <input class="r-label" placeholder="name" bind:value={a.name} oninput={() => triggers.sync()} aria-label="Alias {i + 1} name" />
            <input class="r-cmd" placeholder="command" bind:value={a.command} oninput={() => triggers.sync()} aria-label="Alias {i + 1} command" />
            <button class="r-del" onclick={() => triggers.removeAlias(i)} aria-label="Remove alias {i + 1}">×</button>
          </div>
        {/each}
      </div>
      <button class="add-rule" onclick={() => triggers.addAlias()}>+ alias</button>

      <div class="grp">Actions <span class="hint">on match: sound / command / notify</span></div>
      <div class="rules">
        {#each triggers.actions as act, i}
          <div class="rule">
            <input class="r-cmd" placeholder="text or /regex/" bind:value={act.pattern} oninput={() => triggers.sync()} aria-label="Action {i + 1} pattern"
              aria-invalid={!!parsePattern(act.pattern).error} title={parsePattern(act.pattern).error || undefined} />
            <select class="r-kind" bind:value={act.kind} onchange={() => triggers.sync()} aria-label="Action {i + 1} does">
              <option value="sound">sound</option>
              <option value="command">command</option>
              <option value="notify">notify</option>
            </select>
            {#if act.kind !== "sound"}
              <input class="r-cmd" placeholder={act.kind === "command" ? "command" : "message"} bind:value={act.arg} oninput={() => triggers.sync()}
                aria-label="Action {i + 1} {act.kind === 'command' ? 'command' : 'message'}" />
            {/if}
            <button class="r-del" onclick={() => triggers.removeAction(i)} aria-label="Remove action {i + 1}">×</button>
          </div>
        {/each}
      </div>
      <button class="add-rule" onclick={() => triggers.addAction()}>+ action</button>

    {:else if view === "feeds"}
      <datalist id="feed-names">
        {#each feedNames as name}<option value={name}></option>{/each}
      </datalist>
      <div class="rules">
        {#each rdraft as r, i (r.id ?? i)}
          {@const n = `Rule ${i + 1}`}
          {@const st = routeStatus(r)}
          <div class="route" class:off={r.enabled === false} role="group" aria-label={n}>
            <div class="rule">
              <button class="r-on" role="switch" aria-checked={r.enabled !== false} aria-label="{n} on"
                onclick={() => {
                  r.enabled = r.enabled === false;
                  commitRoutes();
                }}>{r.enabled === false ? "OFF" : "ON"}</button>
              <input class="r-cmd" placeholder="text or /regex/" bind:value={r.pattern}
                onblur={commitRoutes} onkeydown={(e) => e.key === "Enter" && commitRoutes()}
                aria-label="{n} pattern" aria-invalid={st.tone === "err"} aria-describedby="route-st-{i}" />
              <span class="arrow" aria-hidden="true">→</span>
              <input class="r-label" placeholder="feed" list="feed-names" bind:value={r.label}
                onblur={commitRoutes} onkeydown={(e) => e.key === "Enter" && commitRoutes()}
                aria-label="{n} feed" aria-describedby="route-st-{i}" />
              <button
                class="r-hide"
                role="switch"
                aria-checked={!!r.move}
                aria-label="{n}: hide in terminal"
                onclick={() => {
                  r.move = !r.move;
                  commitRoutes();
                }}
              >Hide in terminal</button>
              <button class="r-mv" disabled={i === 0} onclick={() => moveRoute(i, -1)} aria-label="Move {n} up">↑</button>
              <button class="r-mv" disabled={i === rdraft.length - 1} onclick={() => moveRoute(i, 1)} aria-label="Move {n} down">↓</button>
              <button
                class="r-del"
                onclick={() => {
                  rdraft.splice(i, 1);
                  commitRoutes();
                }}
                aria-label="Remove {n}">×</button
              >
            </div>
            {#if st.text}<div class="r-status t-{st.tone}" id="route-st-{i}">{st.text}</div>{/if}
          </div>
        {/each}
      </div>
      <button class="add-rule" onclick={addRoute}>+ rule</button>
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
  .sub { margin: 0; font-weight: normal; color: var(--fg-dim); text-transform: uppercase; letter-spacing: 0.2em; font-size: 0.65rem; }
  .x { margin-left: auto; background: none; border: none; color: var(--fg-dim); font-size: 1.2rem; line-height: 1; cursor: pointer; }
  .x:hover { color: var(--accent-bright); }
  .body { overflow-y: auto; padding: 0.4rem 1rem 0.5rem; }

  .hub {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; padding: 0.4rem 0;
  }
  .tile {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    padding: 8px 10px; background: var(--bg); border: 1px solid var(--border);
    color: var(--fg); font-family: inherit; cursor: pointer; text-align: left; min-height: 36px;
  }
  .tile:hover { border-color: var(--accent); }
  .tile-label { font-size: 0.82rem; color: var(--fg); }
  .go { color: var(--fg-dim); }
  .tile:hover .tile-label, .tile:hover .go { color: var(--accent-bright); }

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
  .r-mode {
    background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg-faint);
    font-family: inherit; font-size: 0.6rem; letter-spacing: 0.1em; padding: 0 6px;
    cursor: pointer; flex: 0 0 auto;
  }
  .r-mode.on { color: var(--accent-bright); border-color: var(--accent); }
  .r-del {
    flex: 0 0 auto; background: none; border: 1px solid var(--border-bright); color: var(--fg-faint);
    font-family: inherit; cursor: pointer; padding: 2px 7px; line-height: 1;
  }
  .r-del:hover { border-color: var(--alert); color: var(--alert); }
  .r-mv {
    flex: 0 0 auto; background: none; border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; cursor: pointer; padding: 2px 6px; line-height: 1; min-width: 24px; min-height: 24px;
  }
  .r-mv:disabled { color: var(--fg-faint); cursor: default; }
  .route { display: flex; flex-direction: column; gap: 2px; padding-bottom: 6px; border-bottom: 1px dashed var(--border); }
  .route.off .r-cmd, .route.off .r-label { opacity: 0.6; }
  /* The pattern is the part people read and edit; give it the room. */
  .route .rule { flex-wrap: wrap; row-gap: 4px; }
  .route .r-cmd { flex: 3 1 16ch; min-width: 12ch; }
  .route .r-label { flex: 1 1 9ch; min-width: 8ch; }
  .r-on {
    flex: 0 0 auto; background: none; border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.62rem; letter-spacing: 0.08em; min-width: 34px; min-height: 24px; cursor: pointer;
  }
  .r-on[aria-checked="true"] { color: var(--ok); border-color: var(--ok); }
  .r-fixed { color: var(--fg-dim); font-size: 0.72rem; }
  .r-hide {
    flex: 0 0 auto; background: none; border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.62rem; letter-spacing: 0.06em; text-transform: uppercase;
    padding: 2px 6px; min-height: 24px; cursor: pointer;
  }
  .r-hide[aria-checked="true"] { color: var(--accent-bright); border-color: var(--accent); }
  .arrow { color: var(--fg-dim); flex: 0 0 auto; }
  .r-status { font-size: 0.7rem; padding-left: 40px; line-height: 1.35; }
  .t-err { color: var(--alert); }
  .t-warn { color: var(--gold); }
  .t-ok { color: var(--fg-dim); }
  .rules input[aria-invalid="true"] { border-color: var(--alert) !important; }
  .link { background: none; border: none; padding: 0; color: var(--accent-bright); font: inherit; text-decoration: underline; cursor: pointer; }
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
