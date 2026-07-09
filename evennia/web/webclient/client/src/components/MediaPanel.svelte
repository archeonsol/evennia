<script lang="ts">
  import { media } from "../lib/media.svelte";
  import { youtubeId } from "../lib/media";

  let videoEl = $state<HTMLVideoElement | null>(null);

  const yt = $derived(media.nowPlaying ? youtubeId(media.nowPlaying.url) : null);

  function tryPlay(el: HTMLMediaElement | null): void {
    if (!el) return;
    el.play().catch(() => {
      const resume = () => {
        el.play().catch(() => {});
        document.removeEventListener("click", resume);
        document.removeEventListener("keydown", resume);
      };
      document.addEventListener("click", resume, { once: true });
      document.addEventListener("keydown", resume, { once: true });
    });
  }

  $effect(() => {
    const v = media.volume / 100;
    if (videoEl && !media.isFadeActive) videoEl.volume = v;
  });

  $effect(() => {
    if (media.nowPlaying?.type === "video") tryPlay(videoEl);
  });
</script>

<div class="media-panel">
  <div class="np">
    <div class="hd">
      <span class="tag glow-text">Now playing</span>
      {#if media.nowPlaying}<button class="x" onclick={() => media.stop()} aria-label="stop">×</button>{/if}
    </div>

    {#if media.nowPlaying}
      <div class="player">
        {#if yt || media.nowPlaying.type === "audio"}
          <p class="yt-note">
            <a href={media.nowPlaying.url} target="_blank" rel="noopener">{media.nowPlaying.url}</a>
          </p>
        {:else if media.nowPlaying.type === "video"}
          <video bind:this={videoEl} src={media.nowPlaying.url} controls>
            <track kind="captions" />
          </video>
        {/if}
      </div>
    {:else}
      <p class="empty">Nothing playing. The game can push audio or a YouTube link here.</p>
    {/if}
    <div class="vol">
      <span class="vglyph" aria-hidden="true">{media.volume === 0 ? "🔇" : "🔊"}</span>
      <input
        type="range" min="0" max="100" step="1" value={media.volume}
        oninput={(e) => media.setVolume(+e.currentTarget.value)}
        aria-label="volume"
      />
      <span class="vval">{media.volume}</span>
    </div>
  </div>

  <div class="gallery">
    <div class="hd"><span class="tag glow-text">Images</span>
      {#if media.images.length}<button class="x" onclick={() => media.clearImages()} aria-label="clear">clear</button>{/if}
    </div>
    {#if media.images.length}
      <div class="grid">
        {#each media.images as im (im.url)}
          <a href={im.url} target="_blank" rel="noopener" class="thumb">
            <img src={im.url} alt="" loading="lazy" />
          </a>
        {/each}
      </div>
    {:else}
      <p class="empty">No images yet.</p>
    {/if}
  </div>
</div>

<style>
  .media-panel { display: flex; flex-direction: column; height: 100%; background: var(--bg-elev); overflow-y: auto; }
  .hd { display: flex; align-items: center; padding: 6px 10px; border-bottom: 1px solid var(--border); }
  .tag { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.2em; font-size: 0.72rem; }
  .x { margin-left: auto; background: none; border: none; color: var(--fg-dim); font-family: inherit; font-size: 0.7rem; cursor: pointer; }
  .x:hover { color: var(--accent-bright); }
  .np { flex: 0 0 auto; border-bottom: 1px solid var(--accent); }
  .player { padding: 8px 10px 0; }
  .player video { width: 100%; max-height: 240px; border: 1px solid var(--border-bright); }
  .yt-note { margin: 0; padding: 8px 10px; font-size: 0.78rem; color: var(--fg-dim); }
  .yt-note a { color: var(--accent-bright); word-break: break-all; }
  .vol { display: flex; align-items: center; gap: 8px; padding: 6px 10px 9px; }
  .vol input { flex: 1; accent-color: var(--accent); }
  .vglyph { font-size: 0.9rem; }
  .vval { color: var(--fg-dim); font-size: 0.72rem; min-width: 2em; text-align: right; }
  .empty { color: var(--fg-faint); font-style: italic; padding: 10px; font-size: 0.78rem; }
  .gallery { flex: 1; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(72px, 1fr)); gap: 5px; padding: 8px 10px; }
  .thumb { display: block; aspect-ratio: 1; overflow: hidden; border: 1px solid var(--border-bright); }
  .thumb img { width: 100%; height: 100%; object-fit: cover; }
  .thumb:hover { border-color: var(--accent); }
</style>
