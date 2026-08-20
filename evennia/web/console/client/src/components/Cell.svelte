<script lang="ts">
  import { cellShape } from "../lib/format";

  interface Props {
    value?: unknown;
    /** Overrides the derived class, for a cell that is neither text nor number. */
    kind?: "" | "null" | "num";
  }

  const { value, kind }: Props = $props();
  const shape = $derived(cellShape(value));
</script>

<td class={kind ?? shape.kind} title={shape.title || undefined}>{shape.text}</td>
