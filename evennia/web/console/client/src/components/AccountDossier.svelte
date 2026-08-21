<script lang="ts">
  import Section from "./Section.svelte";
  import Empty from "./Empty.svelte";
  import Lamp from "./Lamp.svelte";
  import DataTable from "./DataTable.svelte";
  import Cell from "./Cell.svelte";
  import { runAction } from "../lib/load.svelte";
  import type { DossierKey } from "../lib/types";

  /* Every identity key one account connected with, and who else used it.
   *
   * Exact matches only. Two accounts used the same device token or they did
   * not; the console never reports a likelihood, because the rule this package
   * is built on is that a conclusion has to be showable to the player it is
   * used against, and a percentage cannot be shown to anybody. */

  interface Dossier {
    account?: string;
    first_seen?: string;
    last_seen?: string;
    keys?: DossierKey[];
    sanctions?: { id: number; level: string; reason: string; created: string; expires: string }[];
    flags?: { id: number; kind: string; severity: number; state: string; summary: string }[];
    note?: string;
  }

  interface Props {
    account: string;
    onClose: () => void;
  }

  const { account, onClose }: Props = $props();

  let data = $state<Dossier | null>(null);

  $effect(() => {
    data = null;
    runAction<Dossier>("moderation", "account", { name: account }).then((found) => (data = found));
  });

  const keys = $derived(data?.keys ?? []);
</script>

<div class="editor">
  <div class="editor-head">
    <span class="legend">ACCOUNT {account}</span>
    <span class="spacer"></span>
    <span class="legend">
      {data?.first_seen ? `SEEN ${data.first_seen} TO ${data.last_seen}` : "NEVER SEEN"}
    </span>
    <button type="button" onclick={onClose}>CLOSE</button>
  </div>

  {#if data && !keys.length}
    <Empty line="NO CONNECTION RECORDED." hint="This account has no session history to compare." />
  {:else if data}
    <DataTable
      label="Identity keys"
      columns={[
        { key: "label", label: "KIND" },
        { key: "value", label: "VALUE" },
        { key: "sessions", label: "SESSIONS", numeric: true },
        { key: "last_seen", label: "LAST" },
        { key: "shared_with", label: "ALSO USED BY" },
        { key: "standing", label: "" },
      ]}
      rows={keys}
      key={(row) => `${row.kind}:${row.value}:${row.last_seen}`}
    >
      {#snippet row(item)}
        <tr>
          <Cell value={item.label.toUpperCase()} />
          <Cell value={item.value} />
          <td class="num">{item.sessions}</td>
          <Cell value={item.last_seen} />
          {#if item.shared_with.length}
            <td title={item.shared_with.join(", ")}>
              {item.shared_with.join(", ")}{item.shared_count > item.shared_with.length
                ? ` (+${item.shared_count - item.shared_with.length} more)`
                : ""}
            </td>
          {:else}
            <td class="null">nobody</td>
          {/if}
          <td>
            {#if item.sanctioned}
              <Lamp label="BANNED" state="fail" />
            {:else if !item.bannable}
              <!-- Said, not left to be inferred from a missing button. This
                   value identifies a program, so banning it would ban everybody
                   who uses that program. -->
              <span class="legend" title="This identifies a program, not a person.">
                NOT BANNABLE
              </span>
            {/if}
          </td>
        </tr>
      {/snippet}
    </DataTable>
    <p class="legend">{data.note || ""}</p>

    {#if (data.sanctions ?? []).length}
      <Section label="Sanctions on this account" />
      <DataTable
        label="Sanctions on this account"
        columns={[
          { key: "level", label: "LEVEL" },
          { key: "reason", label: "REASON" },
          { key: "created", label: "FROM" },
          { key: "expires", label: "EXPIRES" },
        ]}
        rows={data.sanctions ?? []}
        key={(row) => row.id}
      >
        {#snippet row(item)}
          <tr>
            <Cell value={item.level} />
            <Cell value={item.reason} />
            <Cell value={item.created} />
            <Cell value={item.expires} />
          </tr>
        {/snippet}
      </DataTable>
    {/if}

    {#if (data.flags ?? []).length}
      <Section label="Flags naming this account" />
      <DataTable
        label="Flags naming this account"
        columns={[
          { key: "severity", label: "SEV", numeric: true },
          { key: "kind", label: "KIND" },
          { key: "state", label: "STATE" },
          { key: "summary", label: "SUMMARY" },
        ]}
        rows={data.flags ?? []}
        key={(row) => row.id}
      >
        {#snippet row(item)}
          <tr>
            <Cell value={item.severity} />
            <Cell value={item.kind} />
            <Cell value={item.state} />
            <Cell value={item.summary} />
          </tr>
        {/snippet}
      </DataTable>
    {/if}
  {/if}
</div>
