<script lang="ts">
  // Off-screen live regions for screen-reader speech; see lib/announce.svelte.ts.
  // Each utterance is its own element so an identical repeat is still new text.
  import { announcer } from "../lib/announce.svelte";
</script>

<div class="sr-only" role="status" aria-live="polite" aria-atomic="false">
  {#each announcer.polite as u (u.id)}<div>{#each u.lines as line}<p>{line}</p>{/each}</div>{/each}
</div>
<div class="sr-only" role="alert" aria-live="assertive" aria-atomic="false">
  {#each announcer.assertive as u (u.id)}<div>{#each u.lines as line}<p>{line}</p>{/each}</div>{/each}
</div>
