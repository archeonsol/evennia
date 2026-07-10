<script lang="ts">
  // Hidden YouTube player for room BGM / @music. The #yt-player host stays mounted
  // (parity with legacy custom-client.js) so playback and fades do not depend on the
  // Media dock. The YT IFrame API (a youtube.com request) only loads once room music
  // is enabled; disabling it stops playback and blocks all loads.
  import { ytBgm } from "../lib/youtube-bgm.svelte";
  import { settings } from "../lib/settings.svelte";

  $effect(() => {
    ytBgm.setEnabled(settings.music);
    if (settings.music) void ytBgm.init("yt-player");
  });
</script>

<div class="yt-bgm" aria-hidden="true">
  <div id="yt-player"></div>
</div>

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
  #yt-player {
    width: 1px;
    height: 1px;
  }
</style>
