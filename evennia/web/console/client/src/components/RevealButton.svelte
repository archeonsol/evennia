<script lang="ts">
  import { runAction } from "../lib/load.svelte";
  import { withPresence } from "../lib/presence";

  /* Show one withheld value, and record that it was shown.
   *
   * Looking is not the same act as banning, and the two are gated differently
   * on purpose. Banning a network needs the address capability. Looking needs a
   * reason and a password: nobody is prevented, because an investigation
   * sometimes needs the raw value and refusing only moves the work somewhere
   * with no record. Everybody is recorded, permanently, and the record says
   * why.
   *
   * The value is held here and never written back into the row, so it is gone
   * on the next render rather than sitting on the page for whoever walks past
   * next. */

  interface Props {
    record: "session" | "sanction" | "flag";
    id: number | string;
    field: string;
    /** What is being revealed, for the prompt. */
    label?: string;
  }

  const { record, id, field, label = "this value" }: Props = $props();

  let shown = $state("");

  async function reveal() {
    const reason = prompt(
      `Why do you need ${label}? The console keeps this record permanently.`,
    );
    if (!reason) return;
    await withPresence(async () => {
      const result = await runAction<{ value?: string }>("moderation", "reveal", {
        record,
        record_id: id,
        field,
        reason,
      });
      if (result === null) return false;
      shown = String(result.value ?? "");
      return true;
    });
  }
</script>

{#if shown}
  <span class="revealed" title="Recorded permanently">{shown}</span>
{:else}
  <button type="button" class="linkish" title="Recorded permanently, with your reason" onclick={reveal}>
    withheld
  </button>
{/if}
