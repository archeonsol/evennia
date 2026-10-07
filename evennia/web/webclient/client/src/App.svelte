<script lang="ts">
  import Workspace from "./components/Workspace.svelte";
  import CommandInput from "./components/CommandInput.svelte";
  import StatusBar from "./components/StatusBar.svelte";
  import CrtShell from "./components/CrtShell.svelte";
  import Boot from "./components/Boot.svelte";
  import SettingsPanel from "./components/SettingsPanel.svelte";
  import CommandPalette from "./components/CommandPalette.svelte";
  import Hotbar from "./components/Hotbar.svelte";
  import Toasts from "./components/Toasts.svelte";
  import LoreTip from "./components/LoreTip.svelte";
  import QuitOverlay from "./components/QuitOverlay.svelte";
  import UIHost from "./components/UIHost.svelte";
  import YoutubeBgm from "./components/YoutubeBgm.svelte";
  import SimpleWorkspace from "./components/SimpleWorkspace.svelte";
  import Announcer from "./components/Announcer.svelte";
  import { macros, comboOf } from "./lib/macros.svelte";
  import { keybinds, reviewIndex } from "./lib/keybinds.svelte";
  import { connection } from "./lib/evennia.svelte";
  import { session } from "./lib/session.svelte";
  import { chat } from "./lib/chat.svelte";
  import { settings } from "./lib/settings.svelte";
  import { dock } from "./lib/dock.svelte";
  import { logview } from "./lib/logview.svelte";
  import { announcer } from "./lib/announce.svelte";
  import { focusRegion, type Region } from "./lib/regions";
  import { commandInput, shouldTypeCommand } from "./lib/focus";
  import { commands } from "./lib/commands.svelte";
  import { reviewCursor, type ReviewMove } from "./lib/review";
  import type { LogLine } from "./lib/session.svelte";

  // The intro is a visual flourish; a screen reader user would only have to
  // find and dismiss it.
  let booted = $state(settings.screenreader);
  let settingsOpen = $state(false);
  // Other panels ask for a settings view (the Feeds panel's rules button).
  let settingsView = $state("hub");
  function onSettingsRequest(e: Event) {
    settingsView = (e as CustomEvent).detail?.view ?? "hub";
    settingsOpen = true;
  }
  $effect(() => {
    window.addEventListener("underspire:settings", onSettingsRequest);
    return () => window.removeEventListener("underspire:settings", onSettingsRequest);
  });
  let paletteOpen = $state(false);

  // On (re)connect, ask the server to push the channel registry + assist inbox.
  let synced = false;
  $effect(() => {
    if (connection.state === "open" && !synced) {
      synced = true;
      chat.syncChannels();
    } else if (connection.state !== "open") {
      synced = false;
    }
  });

  // Say when the link drops and comes back. The status dot is the only other
  // sign, and a reader typing into a dead socket hears nothing at all.
  let lastState = connection.state;
  let everOpen = false;
  $effect(() => {
    const state = connection.state;
    if (state === lastState) return;
    const was = lastState;
    lastState = state;
    if (state === "open") {
      if (everOpen) announcer.alert("Connection restored.");
      everOpen = true;
    } else if (was === "open" && !connection.loggedOut) {
      announcer.alert("Connection lost. Reconnecting.");
    }
  });

  const JUMPS: [string, Region][] = [
    ["focusInput", "input"],
    ["focusOutput", "output"],
    ["focusChannels", "channels"],
    ["focusScene", "scene"],
  ];

  // The review cursor reads what the log is showing: its filters apply, and
  // media rows and blank lines have nothing to say.
  const readable = (line: LogLine) =>
    line.type !== "media" && !!logview.filters[line.cat] && !!line.text.trim();

  const REVIEW_MOVES: [string, ReviewMove][] = [
    ["reviewOlder", "older"],
    ["reviewNewer", "newer"],
    ["reviewOldest", "oldest"],
    ["reviewNewest", "newest"],
  ];

  // Acting starts a fresh read: after a command, Alt+Up reads the newest line.
  commands.onRun(() => reviewCursor.reset());

  function onKey(e: KeyboardEvent) {
    // An open dialog owns the keyboard; a jump would land behind it.
    const modalOpen = !!document.querySelector('[aria-modal="true"]');
    for (const [id, region] of JUMPS) {
      if (modalOpen) break;
      if (keybinds.match(e, id)) {
        e.preventDefault();
        void focusRegion(region);
        return;
      }
    }
    const back = reviewIndex(e);
    if (back) {
      e.preventDefault();
      announcer.now(reviewCursor.recent(session.lines, readable, back));
      return;
    }
    for (const [id, move] of REVIEW_MOVES) {
      if (modalOpen) break;
      if (keybinds.match(e, id)) {
        e.preventDefault();
        announcer.now(reviewCursor[move](session.lines, readable));
        return;
      }
    }
    if (keybinds.match(e, "palette")) {
      e.preventDefault();
      paletteOpen = true;
      return;
    }
    if (keybinds.match(e, "settings")) {
      e.preventDefault();
      settingsOpen = true;
      return;
    }
    if (keybinds.match(e, "help")) {
      e.preventDefault();
      dock.openHelp();
      return;
    }
    if (keybinds.match(e, "clear")) {
      e.preventDefault();
      // Same question the clear button asks: one stray chord wiped the log.
      if (session.lines.length && confirm("Clear the scrollback buffer?")) session.clear();
      return;
    }
    const combo = comboOf(e);
    if (combo && macros.handleKey(combo)) e.preventDefault();
    // A plain character typed anywhere that is not a field goes to the command
    // line, so clicking the log, a filter chip or a channel does not cost a
    // second click before typing. The character still lands: focus moves
    // during keydown, before the browser inserts it.
    if (shouldTypeCommand(e, e.target, modalOpen)) {
      const input = commandInput();
      if (input && document.activeElement !== input) input.focus({ preventScroll: true });
    }
  }
</script>

<svelte:window onkeydown={onKey} />

{#snippet skip(label: string, region: Region)}
  <a
    href="#{region}"
    onclick={(e) => {
      e.preventDefault();
      void focusRegion(region);
    }}>{label}</a
  >
{/snippet}

<CrtShell>
  <nav class="skip-links" aria-label="Skip links">
    <!-- The first Tab stop, so a blind player finds the mode without
         hunting through Settings for it. -->
    {#if !settings.screenreader}
      <a href="#screenreader" onclick={(e) => {
        e.preventDefault();
        settings.screenreader = true;
        settings.channelEcho = true;
        settings.music = false;
        announcer.now("Screen reader mode on. One view at a time. Alt+O goes to the game output, Alt+I to the command line.");
        void focusRegion("input");
      }}>Turn on screen reader mode</a>
    {/if}
    {@render skip("Skip to command line", "input")}
    {@render skip("Skip to game output", "output")}
  </nav>
  <div class="bezel framed">
    <div class="shell">
      <StatusBar onsettings={() => (settingsOpen = true)} />
      <main class="main" aria-label="Game">
        {#if settings.screenreader}
          <SimpleWorkspace />
        {:else}
          <Workspace />
        {/if}
      </main>
      <Hotbar />
      <CommandInput />
    </div>
  </div>
  <Announcer />

  {#if !booted}
    <Boot ondone={() => (booted = true)} />
  {/if}
  {#if settingsOpen}
    <SettingsPanel initial={settingsView} onclose={() => {
      settingsOpen = false;
      settingsView = "hub";
    }} />
  {/if}
  {#if paletteOpen}
    <CommandPalette onclose={() => (paletteOpen = false)} />
  {/if}
  {#if connection.loggedOut}
    <QuitOverlay />
  {/if}
  <UIHost />
  <YoutubeBgm />
  <Toasts />
  <LoreTip />
</CrtShell>

<style>
  .bezel {
    position: absolute; inset: 10px;
    border: 1px solid var(--border-bright); background: var(--bg); overflow: hidden;
  }
  .shell {
    display: grid;
    grid-template-rows: auto 1fr auto auto;
    grid-template-columns: minmax(0, 1fr);
    height: 100%; width: 100%;
    color: var(--fg); font-family: var(--font-mono);
  }
  .main {
    min-height: 0;
    overflow: hidden;
  }
</style>
