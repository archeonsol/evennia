<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Empty from "../components/Empty.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { call } from "../lib/api";
  import { report } from "../lib/report";
  import { withPresence } from "../lib/presence";

  interface Row {
    sessid: number;
    account: string;
    puppet: string;
    protocol: string;
    commands: number;
  }

  interface Payload {
    rows?: {
      rows?: Row[];
      available?: boolean;
      reason?: string;
      count?: number;
      note?: string;
    };
  }

  const data = new Loader<Payload>();
  let reload = $state(0);

  $effect(() => {
    void reload;
    data.load(rowsPath("sessions"));
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);

  async function act(action: string, sessid: number, question: string) {
    const reason = prompt(question);
    if (!reason) return;

    const run = async () => {
      const done = await call<Record<string, unknown>>(`panels/sessions/actions/${action}/`, {
        body: { sessid, reason },
      });
      return report(done);
    };

    // Watching another person's session is the one read here that needs proof
    // of presence. Disconnecting is disruptive and reversible; watching is not
    // reversible, because it cannot be un-seen.
    const ok = action === "watch" ? await withPresence(run) : await run();
    if (ok) reload += 1;
  }
</script>

<PanelHead title="LIVE SESSIONS" count={body.count ? `${body.count} CONNECTED` : ""}>
  {#snippet toolbar()}
    <span class="legend">{body.note || ""}</span>
  {/snippet}
</PanelHead>

<div class="panel-body">
  {#if body.available === false}
    <Empty line="THE SESSION HANDLER IS NOT LOADED." hint={body.reason || ""} />
  {:else if rows.length === 0}
    <Empty line="NOBODY IS CONNECTED." />
  {:else}
    <DataTable
      label="Connected sessions"
      columns={[
        { key: "account", label: "ACCOUNT" },
        { key: "puppet", label: "PUPPET" },
        { key: "protocol", label: "PROTOCOL" },
        { key: "commands", label: "COMMANDS", numeric: true },
        { key: "act", label: "" },
      ]}
      {rows}
      key={(row) => row.sessid}
    >
      {#snippet row(item)}
        <tr>
          <Cell value={item.account} />
          <Cell value={item.puppet} />
          <Cell value={item.protocol} />
          <Cell value={item.commands} />
          <td>
            <button
              type="button"
              title="The console records this action permanently. The account sees it in its own timeline."
              onclick={() => act("watch", item.sessid, "Why is this session being watched?")}
            >
              WATCH
            </button>
            <button
              type="button"
              onclick={() =>
                act("disconnect", item.sessid, "Why is this session being disconnected?")}
            >
              DISCONNECT
            </button>
          </td>
        </tr>
      {/snippet}
    </DataTable>
  {/if}
</div>
