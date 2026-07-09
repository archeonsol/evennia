<script lang="ts">
  // Hidden YouTube player for room BGM / @music. Always mounted so playback
  // does not depend on the Media dock panel being open (legacy client parity).
  import { media } from "../lib/media.svelte";
  import { youtubeId, youtubeEmbedUrl } from "../lib/media";

  let ytEl = $state<HTMLIFrameElement | null>(null);

  const yt = $derived(
    media.nowPlaying?.type === "youtube" ? youtubeId(media.nowPlaying.url) : null,
  );
  const src = $derived(
    yt
      ? youtubeEmbedUrl(yt, {
          start: media.ytStart,
          loop: media.ytLoop,
          autoplay: true,
        })
      : "",
  );

  $effect(() => {
    if (!ytEl || !yt) return;
    try {
      ytEl.contentWindow?.postMessage(
        JSON.stringify({ event: "command", func: "setVolume", args: [media.volume] }),
        "*",
      );
    } catch {
      /* ignore */
    }
  });
</script>

{#if yt && src}
  <div class="yt-bgm" aria-hidden="true">
    <iframe
      bind:this={ytEl}
      {src}
      title="Background music"
      referrerpolicy="strict-origin-when-cross-origin"
      allow="autoplay; encrypted-media"
    ></iframe>
  </div>
{/if}

<style>
  .yt-bgm {
    position: fixed;
    width: 0;
    height: 0;
    overflow: hidden;
    opacity: 0;
    pointer-events: none;
    z-index: -1;
  }
  iframe {
    width: 1px;
    height: 1px;
    border: 0;
  }
</style>
