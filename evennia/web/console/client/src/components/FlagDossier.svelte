<script lang="ts">
  import Lamp from "./Lamp.svelte";
  import { loadDetail, runAction } from "../lib/load.svelte";

  interface Evidence {
    key: string;
    value: unknown;
  }

  interface Flag {
    id: number;
    kind?: string;
    summary?: string;
    account?: string;
    evidence?: Evidence[];
  }

  interface Collateral {
    account_count?: number;
    sessions?: number;
    accounts?: string[];
    note?: string;
  }

  interface Props {
    id: string;
    levels: string[];
    subjectTypes: string[];
    onClose: () => void;
    onResolved: () => void;
  }

  const { id, levels, subjectTypes, onClose, onResolved }: Props = $props();

  let flag = $state<Flag | null>(null);
  let note = $state("");
  let subjectType = $state("");
  let subjectValue = $state("");
  let level = $state("");
  /* A duration is required. The old banlist had no expiry column, so every
   * entry in it was permanent by default; that is the mistake this field exists
   * to stop repeating. */
  let duration = $state("7d");
  let reach = $state<Collateral | null>(null);
  let proposed = $state("");

  $effect(() => {
    loadDetail<Flag>("moderation", id).then((found) => {
      flag = found;
      subjectValue = found?.account || "";
    });
  });

  $effect(() => {
    if (!subjectType && subjectTypes.length) subjectType = subjectTypes[0];
    if (!level && levels.length) level = levels[0];
  });

  async function resolve(wanted: string) {
    if (!flag) return;
    const done = await runAction("moderation", "resolve", {
      flag_id: flag.id,
      state: wanted,
      note,
    });
    if (done !== null) onResolved();
  }

  async function checkReach() {
    reach = null;
    if (!subjectValue) return;
    reach = await runAction<Collateral>("moderation", "collateral", {
      subject_type: subjectType,
      subject_value: subjectValue,
    });
  }

  async function sanction() {
    if (!flag) return;
    const result = await runAction<{ proposed?: boolean; message?: string }>(
      "moderation",
      "sanction",
      {
        subject_type: subjectType,
        subject_value: subjectValue,
        level,
        reason: note,
        expires_at: duration,
        flag_id: flag.id,
      },
    );
    if (result === null) return;
    if (result.proposed) {
      // Not a failure. The console recorded a proposal instead, and the operator
      // has to be told that plainly or they will assume the ban is in place.
      proposed = result.message || "";
      return;
    }
    onResolved();
  }
</script>

<div class="editor">
  {#if flag}
    <div class="editor-head">
      <span class="legend">FLAG {flag.id}&nbsp;&nbsp;{flag.kind}</span>
      <span class="spacer"></span>
      <button type="button" onclick={() => resolve("dismissed")}>DISMISS</button>
      <button type="button" onclick={() => resolve("acknowledged")}>ACKNOWLEDGE</button>
      <button type="button" onclick={onClose}>CLOSE</button>
    </div>

    <p class="empty-hint">{flag.summary || ""}</p>

    <dl class="rows">
      {#each flag.evidence ?? [] as row (row.key)}
        <div class="row-pair"><dt>{row.key}</dt><dd>{String(row.value)}</dd></div>
      {/each}
    </dl>

    <div class="fault-actions">
      <input
        type="text"
        placeholder="WHY, FOR WHOEVER READS THIS NEXT"
        aria-label="Resolution note"
        bind:value={note}
      />
    </div>

    <p class="section-legend">Issue a sanction from this flag</p>
    <div class="fault-actions">
      <select aria-label="Sanction subject type" bind:value={subjectType}>
        {#each subjectTypes as option (option)}
          <option value={option}>{option}</option>
        {/each}
      </select>
      <input
        type="text"
        placeholder="SUBJECT VALUE"
        aria-label="Sanction subject value"
        bind:value={subjectValue}
      />
      <select aria-label="Sanction level" bind:value={level}>
        {#each levels as option (option)}
          <option value={option}>{option}</option>
        {/each}
      </select>
      <input
        type="text"
        size="6"
        aria-label="How long the sanction lasts"
        title="30m, 12h, 7d, 2w, or perm"
        placeholder="7d"
        bind:value={duration}
      />
      <button
        type="button"
        title="Count the accounts that have connected from this subject."
        onclick={checkReach}
      >
        CHECK WHO THIS REACHES
      </button>
      <button type="button" onclick={sanction}>SANCTION</button>
    </div>

    {#if proposed}
      <div class="empty">
        <p class="empty-line">YOUR PROPOSAL IS SAVED.</p>
        <p class="empty-hint">{proposed}</p>
      </div>
    {/if}

    <!-- What a ban on this subject would reach, before it is issued. A /24 can
         be one household or a whole campus, and the two look identical in a
         form field. -->
    {#if reach}
      <div class="collateral">
        <p>
          <Lamp
            label="{reach.account_count} ACCOUNT(S)"
            state={(reach.account_count ?? 0) > 1 ? "attn" : "ok"}
          />
          <span class="legend">
            &nbsp;have connected from this subject, over {reach.sessions} connection(s).
          </span>
        </p>
        {#if (reach.accounts ?? []).length}
          <p class="empty-hint">{(reach.accounts ?? []).join(", ")}</p>
        {/if}
        <p class="empty-hint">{reach.note || ""}</p>
      </div>
    {/if}
  {/if}
</div>
