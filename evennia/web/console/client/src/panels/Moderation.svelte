<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Section from "../components/Section.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import FlagDossier from "../components/FlagDossier.svelte";
  import Proposals from "../components/Proposals.svelte";
  import SignalCoverage from "../components/SignalCoverage.svelte";
  import AccountDossier from "../components/AccountDossier.svelte";
  import SignatureMarks from "../components/SignatureMarks.svelte";
  import ConnectionDossier from "../components/ConnectionDossier.svelte";
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
  let openSession = $state<number | null>(null);

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
  {#if flags.length}
    <div class="moderation-alerts" aria-label="Moderation alerts">
      {#if severe.length}
        <Lamp label={`${severe.length} HIGH-SEVERITY FLAGS`} state="fail" />
      {/if}
      <Lamp label={`${flags.length} FLAGS AWAITING REVIEW`} state="attn" />
    </div>
    <Section label="Flags awaiting review" />
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

  {#if sanctions.length}
    <Section label="Active sanctions" />
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
        <div class="connection-record">
          <button
            type="button"
            class="connection-summary"
            aria-expanded={openSession === row.id}
            aria-controls={`connection-detail-${row.id}`}
            aria-label={`${openSession === row.id ? "Close" : "Open"} connection ${row.id} for ${row.account || "anonymous"}`}
            onclick={() => (openSession = openSession === row.id ? null : row.id)}
          >
            <span class="connection-who">
              <strong>{row.account || "(anonymous)"}</strong>
              <span>{row.protocol || "unknown protocol"} / #{row.id}</span>
              <time datetime={row.connected}>{row.connected || "connection time unknown"}</time>
            </span>
            <span class="connection-client">
              <strong>{row.client_name || "CLIENT NAME NOT RECORDED"}</strong>
              <span>{[row.term, row.encoding, row.screen].filter(Boolean).join(" / ") || "No terminal profile"}</span>
              <span title={row.user_agent}>{row.user_agent || "No user agent"}</span>
            </span>
            <span class="connection-network">
              <strong>{row.cidr || "NETWORK NOT RECORDED"}</strong>
              <span>{row.network || "No ASN organization"}</span>
              <span>{row.asn ? `AS${row.asn}` : "NO ASN"} / {row.country || "NO COUNTRY"}</span>
            </span>
            <span class="connection-state">
              <SignatureMarks signatures={row.signatures} />
              <span class="connection-marks">
                {#if row.is_tor}<span class="signal-mark alert">TOR</span>{/if}
                {#if row.is_datacenter}<span class="signal-mark">DATACENTER</span>{/if}
                {#if row.address_state === "held"}<span class="signal-mark">HOST HELD</span>{/if}
                {#if row.address_state === "purged"}<span class="signal-mark">ADDRESS PURGED</span>{/if}
                {#if row.address_state === "absent"}<span class="signal-mark">NO ADDRESS</span>{/if}
                {#if !row.address_trustworthy}<span class="signal-mark alert">ADDRESS VOID</span>{/if}
              </span>
            </span>
          </button>
          {#if openSession === row.id}
            <div id={`connection-detail-${row.id}`}>
              <ConnectionDossier
                sessionId={row.id}
                account={row.account}
                protocol={row.protocol}
                onClose={() => (openSession = null)}
              />
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .moderation-alerts {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 12px 14px 2px;
  }

  .log-view {
    max-height: none;
    padding: 0;
    border-block: 1px solid var(--rule);
  }

  .connection-record { border-bottom: 1px solid var(--rule-soft); }
  .connection-record:last-child { border-bottom: 0; }

  .connection-summary {
    display: grid;
    grid-template-columns:
      minmax(150px, 0.85fr)
      minmax(240px, 1.35fr)
      minmax(200px, 1.1fr)
      minmax(180px, auto);
    align-items: center;
    gap: 12px;
    width: 100%;
    min-height: 68px;
    padding: 9px 14px;
    border: 0;
    background: var(--ground);
    color: var(--ink);
    text-align: start;
    text-transform: none;
  }

  .connection-summary:hover,
  .connection-summary[aria-expanded="true"] { background: var(--panel); }

  .connection-summary[aria-expanded="true"] {
    box-shadow: inset 2px 0 0 var(--attn);
  }

  .connection-who,
  .connection-client,
  .connection-network,
  .connection-state {
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 0;
  }

  .connection-summary strong {
    overflow: hidden;
    color: var(--ink);
    font: 600 12px/1.3 var(--mono);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .connection-summary span,
  .connection-summary time {
    min-width: 0;
    overflow: hidden;
    color: var(--ink-dim);
    font: 400 11px/1.35 var(--mono);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .connection-state { align-items: flex-start; }

  .connection-marks {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .connection-summary .signal-mark {
    padding: 2px 5px;
    border: 1px solid var(--rule);
    color: var(--ink-faint);
    font: 500 9px/1.2 var(--mono);
    letter-spacing: 0.06em;
  }

  .connection-summary .signal-mark.alert {
    border-color: var(--attn);
    color: var(--attn);
  }

  @media (max-width: 1100px) {
    .connection-summary {
      grid-template-columns: minmax(160px, 0.8fr) minmax(240px, 1.2fr);
    }
  }

  @media (max-width: 640px) {
    .connection-summary { grid-template-columns: minmax(0, 1fr); }
    .connection-state { gap: 6px; }
  }
</style>
