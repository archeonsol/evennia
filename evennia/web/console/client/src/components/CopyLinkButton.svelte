<script lang="ts">
  /* A link to this exact view.
   *
   * The label reports what happened rather than assuming it worked: clipboard
   * access is refused often enough that a button which always says COPIED is a
   * button that lies. */

  let label = $state("COPY LINK");

  async function copy() {
    try {
      await navigator.clipboard.writeText(location.href);
      label = "COPIED";
    } catch {
      label = "COPY FAILED";
    }
    setTimeout(() => (label = "COPY LINK"), 1500);
  }
</script>

<button type="button" title="Copy a link to this exact view" onclick={copy}>{label}</button>
