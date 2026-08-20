<script lang="ts">
  import Empty from "./Empty.svelte";

  /* A panel a deployment has not switched on.
   *
   * Deliberately distinct from a refusal about who you are. The message names
   * the setting, so an operator reads "this game has not enabled it" rather
   * than "you are not trusted with it" -- two different problems with two
   * different people to talk to. */

  interface Props {
    setting?: string;
  }

  const { setting = "" }: Props = $props();
</script>

<Empty
  line="THIS PANEL IS SWITCHED OFF."
  hint={setting ? `To use this panel, set ${setting} to True in the game settings.` : ""}
/>
