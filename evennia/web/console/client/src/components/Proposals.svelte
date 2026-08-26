<script lang="ts">
  import Section from "./Section.svelte";
  import DataTable from "./DataTable.svelte";
  import Cell from "./Cell.svelte";
  import { runAction } from "../lib/load.svelte";
  import { askText } from "../lib/dialog.svelte";

  /* Sanction proposals: what a staff member asked for and cannot issue alone. */

  interface Proposal {
    id: number;
    subject_type: string;
    subject_value: string;
    level: string;
    reason: string;
    collateral?: { account_count?: number };
    proposed_by: string;
    /** Whether this caller made it. Nobody decides their own request. */
    yours: boolean;
  }

  interface Payload {
    rows?: Proposal[];
    may_decide?: boolean;
    note?: string;
  }

  interface Props {
    onDecided: () => void;
  }

  const { onDecided }: Props = $props();

  let data = $state<Payload | null>(null);
  let reload = $state(0);

  $effect(() => {
    void reload;
    runAction<Payload>("moderation", "proposals").then((found) => (data = found));
  });

  const rows = $derived(data?.rows ?? []);

  async function decide(action: string, id: number) {
    const note = await askText({
      title: action === "approve" ? "Accept permanent-ban proposal" : "Reject permanent-ban proposal",
      description: action === "approve"
        ? "This issues the proposed permanent sanction. You cannot approve your own proposal."
        : "The proposal closes without issuing a sanction.",
      label: "Decision reason",
      input: "textarea",
      confirmLabel: action === "approve" ? "ACCEPT PROPOSAL" : "REJECT PROPOSAL",
      danger: action === "approve",
    });
    if (!note) return;
    if ((await runAction("moderation", action, { proposal_id: id, note })) !== null) {
      reload += 1;
      onDecided();
    }
  }
</script>

{#if data && rows.length}
  <Section label="Permanent-ban proposals" />
  <DataTable
    label="Sanction proposals"
    columns={[
      { key: "subject", label: "SUBJECT" },
      { key: "level", label: "LEVEL" },
      { key: "reason", label: "REASON" },
      { key: "reaches", label: "REACHES", numeric: true },
      { key: "proposed_by", label: "ASKED BY" },
      { key: "act", label: "" },
    ]}
    {rows}
    key={(row) => row.id}
  >
    {#snippet row(item)}
      <tr>
        <Cell value="{item.subject_type} {item.subject_value}" />
        <Cell value={item.level} />
        <Cell value={item.reason} />
        <td class={(item.collateral?.account_count ?? 0) > 1 ? "num fail-text" : "num"}>
          {item.collateral?.account_count ?? 0}
        </td>
        <Cell value={item.proposed_by} />
        <td>
          <button
            type="button"
            disabled={!data?.may_decide || item.yours}
            title={item.yours
              ? "You cannot decide your own proposal."
              : data?.may_decide
                ? "Issue the ban this proposal asks for."
                : "This needs the permanent-ban permission."}
            onclick={() => decide("approve", item.id)}
          >
            ACCEPT
          </button>
          <button
            type="button"
            disabled={!data?.may_decide || item.yours}
            onclick={() => decide("decline", item.id)}
          >
            REFUSE
          </button>
          <button
            type="button"
            disabled={!item.yours}
            title="Take back a proposal that you made."
            onclick={() => decide("withdraw", item.id)}
          >
            CANCEL
          </button>
        </td>
      </tr>
    {/snippet}
  </DataTable>
  <p class="empty-hint">{data.note || ""}</p>
{/if}
