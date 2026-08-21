<script lang="ts">
  import { call } from "../lib/api";
  import { report } from "../lib/report";
  import { session } from "../lib/state.svelte";

  /* Save the current address as a named view.
   *
   * Offered on every panel, because the thing worth saving is whatever the
   * operator has just set up, and which panel that happened on is not something
   * the console gets to have an opinion about. */

  async function save() {
    const name = prompt("Enter a name for this view.");
    if (!name) return;
    const description = prompt("Describe the view. Leave empty to skip.") || "";
    const query = location.hash.split("?")[1] || "";
    const done = await call<Record<string, unknown>>("panels/views/actions/save/", {
      body: { name, panel: session.current, query, description },
    });
    report(done);
  }
</script>

<button type="button" title="Give this set of filters a name." onclick={save}>
  SAVE THIS VIEW
</button>
