<script lang="ts">
  import { chat } from "../lib/chat.svelte";
  import ChannelView from "./ChannelView.svelte";

  const active = $derived(chat.active);
</script>

<div class="chat">
  <div class="rail" role="group" aria-label="Channels">
    {#each chat.channels as c (c.key)}
      {@const extra = [
        chat.unread[c.key] ? `${chat.unread[c.key]} unread` : "",
        chat.mentions[c.key] ? "mentioned" : "",
        chat.online[c.key] ? `${chat.online[c.key]} online` : "",
        chat.muted[c.key] ? "muted" : "",
      ].filter(Boolean)}
      <button
        class="chan"
        class:active={c.key === active}
        class:muted={chat.muted[c.key]}
        class:mention={chat.mentions[c.key]}
        aria-current={c.key === active ? "true" : undefined}
        aria-label={extra.length ? `${c.name}, ${extra.join(", ")}` : c.name}
        onclick={() => chat.setActive(c.key)}
        title={c.name}
      >
        <span class="chan-name" style={chat.channelColor(c.key) && c.key !== active ? `color:${chat.channelColor(c.key)}` : ""}>{c.name}</span>
        {#if chat.mentions[c.key]}<span class="at">@</span>{/if}
        {#if chat.online[c.key]}<span class="online">{chat.online[c.key]}</span>{/if}
        {#if chat.unread[c.key]}<span class="sh-count">{chat.unread[c.key]}</span>{/if}
      </button>
    {/each}
    {#if !chat.channels.length}<span class="rail-empty">No channels</span>{/if}
  </div>

  {#if active}
    <ChannelView channelKey={active} />
  {:else}
    <!-- Alt+C still has somewhere to land before the registry arrives. -->
    <p class="empty" tabindex="-1" data-focus-region="channels">Waiting for channels</p>
  {/if}
</div>

<style>
  .chat { display: flex; flex-direction: column; height: 100%; background: var(--bg-elev); }
  /* The channel rail: names in capitals, the one on screen in inverse video. */
  .rail { display: flex; flex-wrap: wrap; gap: 2px; padding: 4px 6px; border-bottom: 1px solid var(--accent); flex: 0 0 auto; }
  .chan {
    display: flex; align-items: center; gap: 0.6ch; background: none; border: 0; color: var(--fg-dim);
    font-family: inherit; font-size: 0.66rem; letter-spacing: 0.14em; text-transform: uppercase;
    padding: 2px 0.8ch; min-height: 24px; cursor: pointer;
  }
  .chan:hover { color: var(--fg); background: color-mix(in srgb, var(--accent) 16%, transparent); }
  .chan.active { color: var(--bg-deep); background: var(--accent); }
  .chan.muted .chan-name { opacity: 0.5; text-decoration: line-through; }
  .chan.mention:not(.active) .chan-name { color: var(--gold) !important; }
  .at { color: var(--gold); }
  .chan.active .at, .chan.active .online { color: inherit; }
  .online { color: var(--ok); font-size: 0.6rem; letter-spacing: 0; }
  .rail-empty, .empty { color: var(--fg-faint); font-size: 0.64rem; letter-spacing: 0.14em; text-transform: uppercase; }
  .empty { padding: 10px; margin: 0; }
</style>
