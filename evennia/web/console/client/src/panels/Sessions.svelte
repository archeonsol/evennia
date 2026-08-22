<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Empty from "../components/Empty.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import WatchFeed from "../components/WatchFeed.svelte";
  import { Loader, rowsPath } from "../lib/load.svelte";
  import { call } from "../lib/api";
  import { retainWatches } from "../lib/feed.svelte";
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

  interface WatchRow {
    watch_id: string;
    sessid: number;
    account: string;
    watcher: string;
    reason: string;
    seconds_left: number;
    mine: boolean;
  }

  const data = new Loader<Payload>();
  let reload = $state(0);
  let watching = $state<WatchRow[]>([]);

  /** Refresh the list of running watches, whoever started them. */
  async function refreshWatches() {
    const found = await call<{
      result?: { rows?: WatchRow[] };
      rows?: WatchRow[];
    }>("panels/sessions/actions/watches/", { body: {} });
    const payload = found.payload?.result ?? found.payload;
    if (!found.ok) return;
    watching = Array.isArray(payload?.rows) ? payload.rows : [];
    retainWatches(watching.filter((entry) => entry.mine).map((entry) => entry.watch_id));
  }

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

    // Watching another person is the one act here that needs proof of
    // presence. Disconnecting is disruptive and reversible; being seen is not,
    // because it cannot be un-seen.
    const ok = action === "watch" ? await withPresence(run) : await run();
    if (ok) {
      reload += 1;
      await refreshWatches();
    }
  }

  /** Stop a watch. No reason needed: stopping surveillance is never the act
   *  that wants justifying. */
  async function stopWatch(sessid: number) {
    const ok = await report(
      await call("panels/sessions/actions/unwatch/", { body: { sessid } }),
    );
    if (ok) await refreshWatches();
  }

  $effect(() => {
    void reload;
    void refreshWatches();
    const timer = window.setInterval(() => void refreshWatches(), 5000);
    return () => window.clearInterval(timer);
  });

  const mine = $derived(watching.filter((entry) => entry.mine));
  const watchedIds = $derived(new Set(mine.map((entry) => entry.sessid)));
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
            {#if watchedIds.has(item.sessid)}
              <button type="button" onclick={() => stopWatch(item.sessid)}>STOP MY WATCH</button>
            {:else}
              <button
                type="button"
                title="A private shadow terminal of player-visible output, prompts, and locally echoed submitted lines. Secrets, partially typed text, aliases, triggers, and local UI are not visible. Recorded permanently once it delivers anything. The player is not told."
                onclick={() => act("watch", item.sessid, "Why is this session being watched?")}
              >
                WATCH
              </button>
            {/if}
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

  <WatchFeed watches={watching} />
</div>
