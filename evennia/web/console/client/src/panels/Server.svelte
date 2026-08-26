<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Section from "../components/Section.svelte";
  import Lamp from "../components/Lamp.svelte";
  import DisabledNotice from "../components/DisabledNotice.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { call } from "../lib/api";
  import { report } from "../lib/report";
  import { withPresence } from "../lib/presence";
  import { askText } from "../lib/dialog.svelte";

  interface Payload {
    rows?: {
      checks?: Record<string, unknown>;
      version?: string;
      enabled?: boolean;
      setting?: string;
      actions?: string[];
      degraded?: boolean;
      note?: string;
    };
  }

  const data = new Loader<Payload>();
  $effect(() => {
    data.load(rowsPath("server"));
  });

  const body = $derived(data.value?.rows ?? {});
  const checks = $derived(Object.entries(body.checks ?? {}));

  async function control(action: string) {
    const reason = await askText({
      title: `${action} the server`,
      description: "This changes the running service. The console records your reason and the outcome.",
      label: "Reason",
      input: "textarea",
      confirmLabel: action.toUpperCase(),
      danger: true,
    });
    if (!reason) return;
    await withPresence(async () => {
      const done = await call<Record<string, unknown>>("panels/server/actions/control/", {
        body: { action, reason },
      });
      return report(done);
    });
  }
</script>

<PanelHead title="SERVER CONTROL">
  {#snippet toolbar()}
    <Lamp label={body.degraded ? "DEGRADED" : "RUNNING"} state={body.degraded ? "attn" : "ok"} />
    <Lamp
      label={body.enabled ? "CONTROL ENABLED" : "CONTROL DISABLED"}
      state={body.enabled ? "attn" : "off"}
    />
  {/snippet}
</PanelHead>

<div class="panel-body">
  <Section label="Status" />
  <dl class="rows">
    {#each checks as [name, ok] (name)}
      <div class="row-pair">
        <dt>{name.replace(/_/g, " ")}</dt>
        <dd><Lamp label={ok ? "OK" : "FAILED"} state={ok ? "ok" : "fail"} /></dd>
      </div>
    {/each}
    <div class="row-pair">
      <dt>version</dt>
      <dd>{body.version || "--"}</dd>
    </div>
  </dl>

  <Section label="Control" />
  {#if !body.enabled}
    <DisabledNotice setting={body.setting} />
  {:else}
    <div class="fault-actions">
      {#each body.actions ?? [] as action (action)}
        <button type="button" onclick={() => control(action)}>{action.toUpperCase()}</button>
      {/each}
    </div>
    <p class="empty-hint">{body.note || ""}</p>
  {/if}
</div>
