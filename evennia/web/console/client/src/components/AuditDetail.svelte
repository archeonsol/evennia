<script lang="ts">
  import Section from "./Section.svelte";
  import Empty from "./Empty.svelte";
  import Lamp from "./Lamp.svelte";
  import DataTable from "./DataTable.svelte";
  import Cell from "./Cell.svelte";
  import { loadDetail, runAction } from "../lib/load.svelte";

  interface DiffEntry {
    field: string;
    before: unknown;
    after: unknown;
    changed: boolean;
  }

  interface Event {
    id: number;
    event_id: string;
    created_at?: string;
    actor_name?: string;
    panel?: string;
    operation?: string;
    target_ref?: string;
    outcome?: string;
    outcome_meaning?: string;
    retryable?: boolean;
    retention?: string;
    retention_meaning?: string;
    correlation_id?: string;
    message?: string;
    diff?: { entries?: DiffEntry[]; truncated?: number };
    can_undo?: boolean;
    undo_reason?: string;
  }

  interface AsOf {
    covered?: boolean;
    reason?: string;
    changes_undone?: number;
    fields?: Record<string, unknown>;
    note?: string;
  }

  interface Props {
    id: string;
    onClose: () => void;
    onUndone: () => void;
  }

  const { id, onClose, onUndone }: Props = $props();

  let event = $state<Event | null>(null);
  let when = $state("");
  let asOf = $state<AsOf | null>(null);

  $effect(() => {
    loadDetail<Event>("audit", id).then((found) => (event = found));
  });

  const stamp = (value?: string) => (value || "").replace("T", " ").slice(0, 19);

  const facts = $derived(
    event
      ? ([
          ["event", event.event_id],
          ["when", stamp(event.created_at)],
          ["operator", event.actor_name],
          ["operation", `${event.panel}.${event.operation}`],
          ["target", event.target_ref],
          ["outcome", `${event.outcome} - ${event.outcome_meaning}`],
          ["retryable", event.retryable ? "yes" : "no"],
          ["retention", `${event.retention} - ${event.retention_meaning}`],
          ["correlation", event.correlation_id || "--"],
          ["message", event.message || "--"],
        ] as [string, unknown][])
      : [],
  );

  const entries = $derived(event?.diff?.entries ?? []);

  async function undo() {
    if (!event) return;
    const reason = prompt("Why is this operation being reversed?");
    if (!reason) return;
    if ((await runAction("audit", "undo", { audit_id: event.id, reason })) !== null) onUndone();
  }

  /* State as of a moment. Not event sourcing and no new store: it folds the
   * audit table backwards from the newest recorded values. */
  async function reconstruct() {
    if (!when || !event) return;
    asOf = await runAction<AsOf>("audit", "state_as_of", {
      target: event.target_ref,
      when,
    });
  }
</script>

<div class="detail">
  <div class="toolbar">
    <span class="legend">RECORDED OPERATION</span>
    <span class="spacer"></span>
    <button type="button" onclick={onClose}>CLOSE</button>
  </div>

  {#if event}
    <dl class="rows">
      {#each facts as [label, value] (label)}
        <div class="row-pair"><dt>{label}</dt><dd>{value ?? ""}</dd></div>
      {/each}
    </dl>

    <Section label="Field state" />
    {#if !entries.length}
      <Empty line="THIS OPERATION RECORDED NO FIELD STATE." />
    {:else}
      <DataTable
        label="Field state"
        columns={[
          { key: "field", label: "FIELD" },
          { key: "before", label: "BEFORE" },
          { key: "after", label: "AFTER" },
        ]}
        rows={entries}
        key={(row) => row.field}
      >
        {#snippet row(item)}
          <tr>
            <td class={item.changed ? "fail-text" : ""}>{item.field}</td>
            <Cell value={item.before} />
            <Cell value={item.after} />
          </tr>
        {/snippet}
      </DataTable>
    {/if}

    {#if event.diff?.truncated}
      <p class="empty-hint">
        The record contains {event.diff.truncated} more fields. This page does not show them.
      </p>
    {/if}

    <div class="fault-actions">
      {#if event.can_undo}
        <button type="button" onclick={undo}>UNDO</button>
      {:else}
        <!-- Where undo is unavailable the panel says which of the two applies,
             because an absent control explains nothing. -->
        <p class="empty-hint">{event.undo_reason || ""}</p>
      {/if}
    </div>

    <Section label="Values at a past time" />
    <div class="toolbar">
      <div class="field">
        <label class="legend" for="as-of-when">Time</label>
        <input
          type="datetime-local"
          id="as-of-when"
          aria-label="Date and time to reconstruct"
          bind:value={when}
        />
      </div>
      <button type="button" onclick={reconstruct}>SHOW VALUES AT THIS TIME</button>
    </div>

    {#if asOf}
      <div id="as-of">
        <p>
          <Lamp
            label={asOf.covered ? "COMPLETE" : "INCOMPLETE"}
            state={asOf.covered ? "ok" : "attn"}
          />
          <span class="legend">
            &nbsp;{asOf.reason || `The console undid ${asOf.changes_undone} change(s).`}
          </span>
        </p>

        {#if Object.entries(asOf.fields ?? {}).length}
          <DataTable
            label="Values at a past time"
            columns={[
              { key: "field", label: "FIELD" },
              { key: "value", label: "VALUE AT THIS TIME" },
            ]}
            rows={Object.entries(asOf.fields ?? {})}
            key={(pair) => String(pair[0])}
          >
            {#snippet row(pair)}
              <tr>
                <Cell value={pair[0]} />
                <Cell value={pair[1]} />
              </tr>
            {/snippet}
          </DataTable>
        {:else}
          <Empty line="THE CONSOLE HAS NO RECORDED VALUES FOR THIS RECORD." />
        {/if}

        {#if asOf.note}<p class="empty-hint">{asOf.note}</p>{/if}
      </div>
    {/if}
  {/if}
</div>
