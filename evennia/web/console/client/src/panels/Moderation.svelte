<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Section from "../components/Section.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import Annunciator from "../components/Annunciator.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import FlagDossier from "../components/FlagDossier.svelte";
  import Proposals from "../components/Proposals.svelte";
  import SignalCoverage from "../components/SignalCoverage.svelte";
  import AccountDossier from "../components/AccountDossier.svelte";
  import SignatureMarks from "../components/SignatureMarks.svelte";
  import RevealButton from "../components/RevealButton.svelte";
  import { Loader, rowsPath, runAction } from "../lib/load.svelte";
  import { view } from "../lib/state.svelte";
  import type { FlagRow, SanctionRow, SessionRow } from "../lib/types";

  /* The flag queue leads, because it is the only list here that is asking for
   * somebody's attention. Sanctions and sessions are reference material for
   * deciding what to do about a flag. */

  interface Payload {
    rows?: {
      flags?: FlagRow[];
      sanctions?: SanctionRow[];
      sessions?: SessionRow[];
      open_flags?: number;
      flag_states?: string[];
      levels?: string[];
      subject_types?: string[];
      note?: string;
    };
  }

  const data = new Loader<Payload>();
  let reload = $state(0);

  $effect(() => {
    void reload;
    data.load(rowsPath("moderation", { state: view.modState }));
  });

  const body = $derived(data.value?.rows ?? {});
  const flags = $derived(body.flags ?? []);
  const sanctions = $derived(body.sanctions ?? []);
  const sessions = $derived(body.sessions ?? []);
  const severe = $derived(flags.filter((row) => (row.severity || 0) >= 2));

  let chainLabel = $state("VERIFY CHAIN");

  async function verifyChain() {
    chainLabel = "CHECKING";
    const result = await runAction<{ ok?: boolean }>("moderation", "verify_chain");
    chainLabel = result?.ok === false ? "CHAIN BROKEN" : "CHAIN INTACT";
  }

  async function lift(id: number) {
    const reason = prompt("Why is this sanction being lifted?");
    if (!reason) return;
    if ((await runAction("moderation", "revoke", { sanction_id: id, reason })) !== null) {
      reload += 1;
    }
  }
</script>

<PanelHead title="MODERATION" count={body.open_flags ? `${body.open_flags} OPEN` : "QUEUE CLEAR"}>
  {#snippet toolbar()}
    <div class="field">
      <label class="legend" for="mod-state">Show</label>
      <select
        id="mod-state"
        value={view.modState}
        onchange={(event) => {
          view.modState = event.currentTarget.value;
          view.modFlag = "";
        }}
      >
        <option value="">OPEN ONLY</option>
        {#each body.flag_states ?? [] as option (option)}
          <option value={option}>{option.toUpperCase()}</option>
        {/each}
      </select>
    </div>

    <div class="field">
      <label class="legend" for="mod-account">Account</label>
      <input
        id="mod-account"
        type="search"
        size="18"
        value={view.modAccount}
        placeholder="NAME"
        title="Show every identity key this account connected with"
        aria-label="Look up an account"
        onchange={(event) => (view.modAccount = event.currentTarget.value.trim())}
      />
    </div>

    <span class="spacer"></span>
    <span class="legend">{body.note || ""}</span>
    <button type="button" title="Check the sanction hash chain" onclick={verifyChain}>
      {chainLabel}
    </button>
  {/snippet}
</PanelHead>

<div class="panel-body">
  <Annunciator
    alarms={[
      severe.length > 0 && {
        text: `${severe.length} HIGH-SEVERITY FLAGS`,
        state: "fail" as const,
      },
      flags.length > 0 && {
        text: `${flags.length} FLAGS AWAITING A PERSON`,
        state: "attn" as const,
      },
    ]}
    calm="NO FLAG IS WAITING"
  />

  <Section label="Flags awaiting a person" />
  {#if flags.length === 0}
    <Empty line="QUEUE CLEAR." hint="No flag is waiting for review." />
  {:else}
    <DataTable
      label="Flag queue"
      columns={[
        { key: "severity", label: "SEV", numeric: true },
        { key: "kind", label: "KIND" },
        { key: "account", label: "ACCOUNT" },
        { key: "summary", label: "SUMMARY" },
        { key: "seen_count", label: "SEEN", numeric: true },
        { key: "last_seen", label: "LAST" },
      ]}
      rows={flags}
      key={(row) => row.id}
    >
      {#snippet row(flag)}
        <tr
          title="Open this flag"
          onclick={() => (view.modFlag = view.modFlag === String(flag.id) ? "" : String(flag.id))}
        >
          <td class="num">{flag.severity}</td>
          <Cell value={flag.kind} />
          <td>
            {#if flag.account}
              <!-- A name in the queue is the start of the investigation, so it
                   is the control that starts it. Reading a name and then typing
                   it into a box beside the table is the operator doing the
                   computer's work. -->
              <button
                type="button"
                class="linkish"
                title="Show every identity key {flag.account} connected with"
                onclick={(event) => {
                  event.stopPropagation();
                  view.modAccount = view.modAccount === flag.account ? "" : flag.account;
                }}
              >
                {flag.account}
              </button>
            {:else}
              <span class="null">--</span>
            {/if}
          </td>
          <Cell value={flag.summary} />
          <td class="num">{flag.seen_count}</td>
          <Cell value={flag.last_seen} />
        </tr>
      {/snippet}
    </DataTable>
  {/if}

  {#if view.modFlag}
    <FlagDossier
      id={view.modFlag}
      levels={body.levels ?? []}
      subjectTypes={body.subject_types ?? []}
      onClose={() => (view.modFlag = "")}
      onResolved={() => {
        view.modFlag = "";
        reload += 1;
      }}
    />
  {/if}

  <Section label="Active sanctions" />
  {#if !sanctions.length}
    <Empty line="NO ACTIVE SANCTIONS." />
  {:else}
    <DataTable
      label="Active sanctions"
      columns={[
        { key: "level", label: "LEVEL" },
        { key: "subject", label: "SUBJECT" },
        { key: "reason", label: "REASON" },
        { key: "expires", label: "EXPIRES" },
        { key: "act", label: "" },
      ]}
      rows={sanctions}
      key={(row) => row.id}
    >
      {#snippet row(item)}
        <tr>
          <td>
            <Lamp label={item.level.toUpperCase()} state={item.level === "watch" ? "off" : "fail"} />
          </td>
          <Cell value="{item.subject_type} {item.subject}" />
          <Cell value={item.reason} />
          <Cell value={item.expires} />
          <td><button type="button" onclick={() => lift(item.id)}>LIFT</button></td>
        </tr>
      {/snippet}
    </DataTable>
  {/if}

  <Proposals onDecided={() => (reload += 1)} />

  {#if view.modAccount}
    <AccountDossier account={view.modAccount} onClose={() => (view.modAccount = "")} />
  {/if}

  <SignalCoverage />

  <Section label="Recent connections" />
  {#if !sessions.length}
    <Empty line="NO CONNECTIONS RECORDED." />
  {:else}
    <div class="log-view">
      {#each sessions as row (row.id)}
        <div class="session-row">
          <span class="log-source">{row.protocol}</span>
          <span class="log-text">
            {row.account || "(anonymous)"}&nbsp;&nbsp;{row.cidr}&nbsp;&nbsp;{row.network || ""}
          </span>
          <SignatureMarks signatures={row.signatures} />
          {#if row.address_state === "held"}
            <span class="legend">addr</span>
            <RevealButton record="session" id={row.id} field="ip" label="this address" />
          {/if}
          {#if row.address_state === "purged"}
            <Lamp label="ADDRESS PURGED" state="off" title={row.address_state_note} />
          {:else if row.address_state === "absent"}
            <Lamp label="NO ADDRESS" state="off" title={row.address_state_note} />
          {/if}
          {#if row.address_trustworthy}
            <span class="legend">{row.country || ""}</span>
          {:else}
            <Lamp label="ADDRESS VOID" state="attn" title={row.address_warning} />
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>
