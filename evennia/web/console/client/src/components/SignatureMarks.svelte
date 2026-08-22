<script lang="ts">
  import type { Signature } from "../lib/types";

  /* Which signatures one connection carried, in three characters each.
   *
   * Abbreviated rather than named, because this sits at the end of a row that
   * already carries an account, a network, and a host. The full name is on the
   * marker for anybody who does not recognise it, and the coverage table above
   * spells all four out. */

  const MARKS: Record<string, string> = {
    client_fp: "CAP",
    telnet_sig: "NEG",
    device_token: "DEV",
    http_fp: "HTTP",
    tls_sig: "TLS",
    http_order_fp: "HDR",
    csessid: "SES",
  };

  interface Props {
    signatures?: Signature[];
  }

  const { signatures = [] }: Props = $props();
  const present = $derived(signatures.filter((item) => item.present));
</script>

{#if present.length}
  <span class="legend sig-marks" title={present.map((item) => item.label).join(", ")}>
    {present.map((item) => MARKS[item.field] || item.field).join(" ")}
  </span>
{:else}
  <!-- Nothing was recorded, which is not the same as nothing being shown. -->
  <span class="legend sig-marks none">NO SIGNATURE</span>
{/if}

<style>
  .sig-marks {
    min-width: 0;
    text-align: start;
    white-space: normal;
  }
</style>
