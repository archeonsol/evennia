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
  import QuitOverlay from "./components/QuitOverlay.svelte";
  import UIHost from "./components/UIHost.svelte";
  import { macros, comboOf } from "./lib/macros.svelte";
  import { keybinds } from "./lib/keybinds.svelte";
  import { connection } from "./lib/evennia.svelte";
  import { session } from "./lib/session.svelte";
  import { chat } from "./lib/chat.svelte";

  let booted = $state(false);
  let settingsOpen = $state(false);
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

  function onKey(e: KeyboardEvent) {
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
    if (keybinds.match(e, "clear")) {
      e.preventDefault();
      session.clear();
      return;
    }
    const combo = comboOf(e);
    if (combo && macros.handleKey(combo)) e.preventDefault();
  }
</script>

<svelte:window onkeydown={onKey} />

<CrtShell>
  <div class="bezel framed">
    <div class="shell">
      <StatusBar onsettings={() => (settingsOpen = true)} />
      <div class="main">
        <Workspace />
      </div>
      <Hotbar />
      <CommandInput />
    </div>
  </div>

  {#if !booted}
    <Boot ondone={() => (booted = true)} />
  {/if}
  {#if settingsOpen}
    <SettingsPanel onclose={() => (settingsOpen = false)} />
  {/if}
  {#if paletteOpen}
    <CommandPalette onclose={() => (paletteOpen = false)} />
  {/if}
  {#if connection.loggedOut}
    <QuitOverlay />
  {/if}
  <UIHost />
  <Toasts />
</CrtShell>

<style>
  .bezel {
    position: absolute; inset: 10px;
    border: 1px solid var(--border-bright); background: var(--bg); overflow: hidden;
  }
  .shell {
    display: grid;
    grid-template-rows: auto 1fr auto auto;
    height: 100%; width: 100%;
    color: var(--fg); font-family: var(--font-mono);
  }
  .main {
    min-height: 0;
    overflow: hidden;
  }
</style>
