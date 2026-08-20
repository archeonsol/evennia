<script lang="ts">
  import Strip from "./components/Strip.svelte";
  import Rail from "./components/Rail.svelte";
  import Notice from "./components/Notice.svelte";
  import Palette from "./components/Palette.svelte";
  import Station from "./components/Station.svelte";
  import { call } from "./lib/api";
  import { openFeed } from "./lib/feed.svelte";
  import { session, readUrl, writeUrl, select } from "./lib/state.svelte";

  /* The shell. Three regions that never move, so the operator learns positions
   * instead of navigation. */

  let paletteOpen = $state(false);
  let bootFault = $state("");

  interface Root {
    version?: string;
    actor?: { name: string };
    degraded?: boolean;
    panels?: { key: string; label: string; description?: string }[];
    settings?: Record<string, boolean>;
  }

  async function boot() {
    const result = await call<Root>("");
    if (!result.ok) {
      bootFault =
        result.status === 403
          ? "YOU DO NOT HAVE ACCESS TO THE CONSOLE."
          : "THE CONSOLE API DID NOT ANSWER.";
      return;
    }
    const root = result.payload;
    session.version = root.version || "";
    session.actor = root.actor || null;
    session.degraded = Boolean(root.degraded);
    session.panels = root.panels || [];
    session.settings = root.settings || {};
    session.booted = true;

    // The address bar is read before a panel is chosen, so a pasted deep link
    // lands where it points instead of on whatever happens to be first.
    const wanted = readUrl();
    const known = session.panels.some((panel) => panel.key === wanted);
    session.current = known ? wanted : (session.panels[0]?.key ?? "");
    writeUrl();

    openFeed((degraded) => {
      session.degraded = degraded;
    });
  }

  function onKeydown(event: KeyboardEvent) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      paletteOpen = true;
    }
  }

  $effect(() => {
    boot();
  });

  $effect(() => {
    const onHash = () => {
      const wanted = readUrl();
      if (wanted && wanted !== session.current) select(wanted);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  });
</script>

<svelte:window onkeydown={onKeydown} />

<a class="skip" href="#station">Go to the station</a>

<Strip />

<div class="frame">
  <Rail onPalette={() => (paletteOpen = true)} />

  <main class="station" id="station" tabindex="-1">
    {#if bootFault}
      <div class="boot"><p class="boot-line">{bootFault}</p></div>
    {:else if !session.booted}
      <div class="boot"><p class="boot-line">CONNECT TO THE CONSOLE API.</p></div>
    {:else}
      <Station />
    {/if}
  </main>
</div>

<Notice />

{#if paletteOpen}
  <Palette onClose={() => (paletteOpen = false)} />
{/if}
