<script lang="ts">
  // Hidden YouTube player for room BGM / @music. Always mounted (#yt-player parity
  // with legacy custom-client.js) so playback and fades do not depend on the Media dock.
  import { onMount } from "svelte";
  import { ytBgm } from "../lib/youtube-bgm.svelte";

  onMount(() => {
    void ytBgm.init("yt-player");
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
