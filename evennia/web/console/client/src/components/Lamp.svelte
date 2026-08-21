<script lang="ts">
  /* The console's signature element: a filled legend block, lit or unlit.
   *
   * The legend stays readable when the lamp is dark, exactly as a mission
   * control event indicator does, so an operator reads what is *not* lit as
   * easily as what is. A lamp that disappears when inactive tells you nothing
   * about the state it is reporting. */

  interface Props {
    label: string;
    /** ok, attn, fail, or off. Unknown values render unlit. */
    state?: string;
    title?: string;
    check?: string;
    live?: boolean;
  }

  const { label, state = "off", title = "", check = "", live = false }: Props = $props();
</script>

<span
  class="lamp"
  data-state={state}
  data-check={check || undefined}
  data-live={live ? "1" : undefined}
  {title}
>{label}</span>
