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
        <span class="dot" aria-hidden="true" style={chat.channelColor(c.key) ? `background:${chat.channelColor(c.key)}` : ""}></span>
        <span class="chan-name" style={chat.channelColor(c.key) ? `color:${chat.channelColor(c.key)}` : ""}>{c.name}</span>
        {#if chat.mentions[c.key]}<span class="at">@</span>{/if}
        {#if chat.online[c.key]}<span class="online">{chat.online[c.key]}</span>{/if}
        {#if chat.unread[c.key]}<span class="badge">{chat.unread[c.key]}</span>{/if}
      </button>
    {/each}
    {#if !chat.channels.length}<span class="rail-empty">no channels</span>{/if}
  </div>

  {#if active}
    <ChannelView channelKey={active} />
  {:else}
    <!-- Alt+C still has somewhere to land before the registry arrives. -->
    <p class="empty" tabindex="-1" data-focus-region="channels">Awaiting channel registry…</p>
  {/if}
</div>

<style>
  .chat { display: flex; flex-direction: column; height: 100%; background: var(--bg-elev); }
  .rail { display: flex; flex-wrap: wrap; gap: 4px; padding: 5px 8px; border-bottom: 1px solid var(--accent); flex: 0 0 auto; }
  .chan { display: flex; align-items: center; gap: 5px; background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg-dim); font-family: inherit; font-size: 0.74rem; padding: 2px 9px; min-height: 24px; cursor: pointer; }
  .chan:hover { color: var(--fg); }
  .chan.active { color: var(--accent-bright); border-color: var(--accent); }
  .chan.muted .chan-name { opacity: 0.5; text-decoration: line-through; }
  .chan.mention { border-color: var(--gold); box-shadow: 0 0 6px color-mix(in srgb, var(--gold) 40%, transparent); }
  .at { color: var(--gold); font-weight: 500; }
  .chan .dot { width: 5px; height: 5px; background: var(--border-bright); }
  .chan.active .dot { background: var(--accent); box-shadow: 0 0 5px var(--glow); }
  .online { color: var(--ok); font-size: 0.6rem; }
  .badge { background: var(--accent); color: var(--bg-deep); font-size: 0.6rem; padding: 0 4px; min-width: 1.1em; text-align: center; }
  .rail-empty { color: var(--fg-faint); font-size: 0.7rem; font-style: italic; }
  .empty { color: var(--fg-faint); font-style: italic; padding: 8px 10px; }
</style>
