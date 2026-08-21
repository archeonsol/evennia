<script lang="ts">
  import Lamp from "./Lamp.svelte";
  import { session } from "../lib/state.svelte";
  import { live } from "../lib/feed.svelte";

  /* The status strip. Fixed across the top, never scrolls, never rearranges.
   *
   * The three optional lamps report a *setting*, not a fault: a REPL that is
   * switched off is the correct state for almost every deployment, so it is
   * unlit rather than absent. An operator who cannot see that the REPL exists
   * cannot tell a disabled one from a console that never had one. */

  const DANGEROUS: [string, string][] = [
    ["repl_enabled", "REPL"],
    ["sql_enabled", "SQL"],
    ["server_control_enabled", "SERVER CONTROL"],
  ];
</script>

<header class="strip" aria-label="Server status">
  <div class="strip-id">
    <span class="strip-mark" aria-hidden="true"></span>
    <span class="strip-name">CONSOLE</span>
    <span class="strip-version" data-empty="--">{session.version}</span>
  </div>

  <div class="strip-lamps" role="status" aria-live="polite">
    <Lamp label="DATABASE" state={live.health?.database ?? "ok"} check="database" />
    <Lamp
      label="GAME SERVER"
      state={live.health?.io_owner ?? (session.degraded ? "attn" : "ok")}
      check="io_owner"
    />
    <Lamp label="LIVE" state={live.connected ? "ok" : "attn"} title="The live feed" live />
    {#each DANGEROUS as [key, label] (key)}
      <Lamp {label} state={session.settings[key] ? "attn" : "off"} />
    {/each}
  </div>

  <div class="strip-actor">{session.actor ? session.actor.name : ""}</div>
</header>
