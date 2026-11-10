<script lang="ts">
  // The terminal of a game cabinet. The program runs on the Server, which sends what it prints; xterm.js
  // draws that here at the program's own size, and the panel scales it to fit. While the screen has
  // focus every key goes to the game (see lib/screens.svelte.ts). The words on it come from the
  // Server, so this panel has almost none of its own.
  import { onDestroy, untrack } from "svelte";
  import { Terminal } from "@xterm/xterm";
  import "@xterm/xterm/css/xterm.css";
  import { screens } from "../lib/screens.svelte";
  import { settings } from "../lib/settings.svelte";

  let { screenId = "" }: { screenId?: string } = $props();

  // Room taken off the space the screen may use: the well's padding on both sides.
  const CHROME = 16;
  const MAX_SCALE = 2.5;
  const MIN_SCALE = 0.3;
  // How long a panel with nothing to show waits for the Server's `ui_component` before it goes.
  const ORPHAN_MS = 4000;

  // The sixteen colours of a terminal that reads well on a dark ground (the VS Code set). A program
  // picks its colours for black, and the game log's dark blue and dark red cannot be read as text here.
  const ANSI = {
    black: "#000000",
    red: "#cd3131",
    green: "#0dbc79",
    yellow: "#e5e510",
    blue: "#2472c8",
    magenta: "#bc3fbc",
    cyan: "#11a8cd",
    white: "#e5e5e5",
    brightBlack: "#666666",
    brightRed: "#f14c4c",
    brightGreen: "#23d18b",
    brightYellow: "#f5f543",
    brightBlue: "#3b8eea",
    brightMagenta: "#d670d6",
    brightCyan: "#29b8db",
    brightWhite: "#ffffff",
  };

  /** The terminal's ground and ink are the shell's, whatever theme is on. */
  function shellTheme() {
    const css = getComputedStyle(document.documentElement);
    const token = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
    const ground = token("--bg-deep", "#060403");
    const ink = token("--fg", "#cdbfa6");
    const accent = token("--accent", "#b21f1a");
    return {
      ...ANSI,
      background: ground,
      foreground: ink,
      cursor: ink,
      cursorAccent: ground,
      selectionBackground: /^#[0-9a-f]{6}$/i.test(accent) ? `${accent}99` : "#ffffff44",
    };
  }

  const spec = $derived(screens.screens[screenId]);
  const end = $derived(screens.ended[screenId]);
  // Primitives, so a replayed `ui_component` with the same size does not rebuild the terminal.
  const cols = $derived(spec?.size[0] ?? 80);
  const rows = $derived(spec?.size[1] ?? 24);
  const known = $derived(!!spec);

  let well = $state<HTMLDivElement>();
  let host = $state<HTMLDivElement>();
  let leave = $state<HTMLButtonElement>();
  let box = $state({ w: 0, h: 0 });
  let natural = $state({ w: 0, h: 0 });
  let focused = $state(false);
  let everVisible = $state(false);
  // The terminal in use, for the effects that act on it without rebuilding it.
  let current: Terminal | null = null;

  const scale = $derived(
    natural.w && box.w > CHROME * 2 && box.h > CHROME * 2
      ? Math.max(MIN_SCALE, Math.min((box.w - CHROME) / natural.w, (box.h - CHROME) / natural.h, MAX_SCALE))
      : 1,
  );

  // Watch the space the screen may use. A terminal built where it cannot be seen measures its letters
  // as nothing, so it waits for the first time the space is real.
  $effect(() => {
    if (!well) return;
    const watch = new ResizeObserver(() => {
      const w = well!.clientWidth;
      const h = well!.clientHeight;
      if (w !== box.w || h !== box.h) box = { w, h };
      if (w > 0 && h > 0) everVisible = true;
    });
    watch.observe(well);
    return () => watch.disconnect();
  });

  $effect(() => {
    if (!host || !known || !everVisible) return;
    const id = screenId;
    const term = new Terminal({
      cols,
      rows,
      fontFamily: getComputedStyle(document.documentElement).getPropertyValue("--font-mono").trim() || "monospace",
      fontSize: 16,
      lineHeight: 1,
      letterSpacing: 0,
      scrollback: 0,
      cursorBlink: false,
      cursorInactiveStyle: "none",
      customGlyphs: true,
      drawBoldTextInBrightColors: true,
      screenReaderMode: settings.screenreader,
      theme: shellTheme(),
    });
    term.open(host);
    current = term;

    // The size of the letters is only known once xterm has drawn: take it from what it made.
    const measure = () => {
      const screen = host?.querySelector<HTMLElement>(".xterm-screen");
      const w = screen?.offsetWidth ?? 0;
      const h = screen?.offsetHeight ?? 0;
      if (w && h && (w !== natural.w || h !== natural.h)) natural = { w, h };
    };
    const rendered = term.onRender(measure);
    const typed = term.onData((data) => screens.key(id, data));
    // A key that leaves the screen, so the keyboard is never stuck in the game.
    term.attachCustomKeyEventHandler((event) => {
      if (event.type === "keydown" && event.key === "Escape" && event.shiftKey) {
        event.preventDefault();
        term.blur();
        leave?.focus();
        return false;
      }
      return true;
    });
    const gained = () => (focused = true);
    const lost = () => (focused = false);
    host.addEventListener("focusin", gained);
    host.addEventListener("focusout", lost);
    const aim = () => term.focus();
    well?.addEventListener("pointerdown", aim);

    // A theme change repaints the shell; the terminal follows it.
    const retheme = new MutationObserver(() => {
      term.options.theme = shellTheme();
    });
    retheme.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "style"] });

    // `attach` reads how the run ended. That is not a reason to rebuild the terminal: when the run
    // ends the screen stays up, dark, with its last picture.
    const detach = untrack(() => screens.attach(id, (data) => term.write(data)));
    untrack(measure);
    term.focus();

    return () => {
      current = null;
      detach();
      retheme.disconnect();
      well?.removeEventListener("pointerdown", aim);
      host?.removeEventListener("focusin", gained);
      host?.removeEventListener("focusout", lost);
      rendered.dispose();
      typed.dispose();
      term.dispose();
      focused = false;
      natural = { w: 0, h: 0 };
    };
  });

  // The run is over: the keys are the page's again.
  $effect(() => {
    if (end) current?.blur();
  });

  // A panel restored from a saved layout has no game behind it unless the Server's replay brings one.
  $effect(() => {
    if (known) return;
    const timer = setTimeout(() => screens.dismiss(screenId), ORPHAN_MS);
    return () => clearTimeout(timer);
  });

  onDestroy(() => screens.released(screenId));
</script>

{#if spec}
  <section class="tile" class:live={focused && !end} class:over={!!end} aria-label={spec.title}>
    <header class="head">
      <span class="name">{spec.title}</span>
      <span class="state" title="Shift+Esc moves the keyboard out of the game">
        {end ? end.headline : focused ? "Playing" : "Click to play"}
      </span>
      <button
        bind:this={leave}
        class="sh-cmd tool"
        class:primary={!!end}
        aria-label="Close {spec.title}"
        onclick={() => screens.dismiss(screenId)}>Close</button
      >
    </header>

    <div class="well" bind:this={well}>
      <div class="stage" style:width="{natural.w * scale}px" style:height="{natural.h * scale}px">
        <div
          class="host"
          bind:this={host}
          style:width="{natural.w || 0}px"
          style:height="{natural.h || 0}px"
          style:transform="scale({scale})"
        ></div>
      </div>
    </div>
  </section>
{:else}
  <div class="none">
    <p>No game on this screen</p>
    <button class="sh-cmd" onclick={() => screens.dismiss(screenId)}>Close</button>
  </div>
{/if}

<style>
  /* The same pieces as a puppet's terminal tile (PuppetTerminal.svelte): a head with its commands on a
     rule, the body, and a foot on a rule. The border lights while the keys are going to the game. */
  .tile {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-width: 0;
    min-height: 0;
    overflow: hidden;
    background: var(--bg-elev);
    border: 1px solid var(--border-bright);
  }
  .tile.live {
    border-color: var(--accent-bright);
  }
  .head {
    flex: none;
    display: flex;
    align-items: center;
    gap: 1.4ch;
    min-width: 0;
    padding: 4px 6px 4px 10px;
    border-bottom: 1px solid var(--accent);
  }
  .name {
    flex: 1 1 0;
    min-width: 6ch;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .state {
    flex: none;
    color: var(--fg-faint);
    font-size: 0.66rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    white-space: nowrap;
  }
  .live .state {
    color: var(--gold);
  }
  .tool {
    flex: none;
  }

  .well {
    flex: 1 1 0;
    min-height: 0;
    min-width: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    padding: 8px;
    background: var(--bg-deep);
  }
  .stage {
    overflow: hidden;
  }
  /* The run is over and the screen goes dark. */
  .over .stage {
    opacity: 0.4;
  }
  .host {
    transform-origin: 0 0;
  }
  /* xterm lays its own helper layers out; keep its blank edge and scrollbar out of the picture, and
     let the well's ground (the shell's, whatever theme is on) show where the program draws nothing. */
  .host :global(.xterm) {
    padding: 0;
  }
  .host :global(.xterm-viewport) {
    overflow: hidden !important;
    background-color: transparent !important;
  }

  .none {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 0.4rem;
    padding: 10px;
  }
  .none p {
    margin: 0;
    color: var(--fg-faint);
    font-size: 0.66rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
  }
</style>
