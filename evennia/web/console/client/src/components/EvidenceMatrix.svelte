<script lang="ts">
  import type { EvidenceCell, EvidenceMatrixData } from "../lib/types";
  import { view } from "../lib/state.svelte";

  interface Props { matrix: EvidenceMatrixData; }
  const { matrix }: Props = $props();

  function detail(cell: EvidenceCell): string {
    if (cell.state === "corroborated") return `${cell.sessions} session(s), ${cell.values} exact value(s), shared with ${cell.shared_accounts} other account(s)`;
    if (cell.state === "observed") return `${cell.sessions} session(s), ${cell.values} exact value(s), no other account matched`;
    if (cell.state === "untrusted") return `${cell.untrusted_sessions} session(s), address provenance is not trustworthy`;
    return "No recorded value in the bounded sample";
  }
</script>

<section class="evidence" aria-labelledby="evidence-title">
  <div class="evidence-head">
    <div>
      <h2 id="evidence-title">EXACT-SIGNAL EVIDENCE MAP</h2>
      <p>{matrix.note || "Recorded evidence only. No identity score is calculated."}</p>
    </div>
    <div class="evidence-key" aria-label="Evidence states">
      <span data-state="corroborated">EXACT MATCH</span><span data-state="observed">OBSERVED</span><span data-state="untrusted">UNTRUSTED</span><span data-state="absent">ABSENT</span>
    </div>
  </div>
  <div class="evidence-scroll">
    <table>
      <thead><tr><th scope="col">ACCOUNT</th>{#each matrix.columns as column (column.field)}<th scope="col" title={column.note}>{column.label}</th>{/each}</tr></thead>
      <tbody>
        {#each matrix.rows as row (row.account)}
          <tr>
            <th scope="row"><button type="button" class="account" onclick={() => (view.modAccount = row.account)}>{row.account}</button></th>
            {#each row.cells as cell (cell.field)}
              <td data-state={cell.state}>
                <button
                  type="button"
                  class:active={view.modAccount === row.account && view.modSignal === cell.field}
                  title={detail(cell)}
                  aria-label={`${row.account}, ${matrix.columns.find((column) => column.field === cell.field)?.label || cell.field}: ${detail(cell)}`}
                  onclick={() => {
                    view.modAccount = row.account;
                    view.modSignal = cell.field;
                  }}
                >
                  <strong>{cell.state === "corroborated" ? cell.shared_accounts : cell.state === "observed" ? cell.values : cell.state === "untrusted" ? "!" : "–"}</strong>
                  <span>{cell.state === "corroborated" ? "MATCH" : cell.state}</span>
                </button>
              </td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  {#if matrix.sample_capped}<p class="cap-note">The bounded session sample was full. Open an account dossier for its complete retained key set.</p>{/if}
</section>

<style>
  .evidence { border-block: 1px solid var(--rule); background: var(--ground); }
  .evidence-head { display: flex; align-items: end; justify-content: space-between; gap: 16px; padding: 12px 14px; }
  h2, p { margin: 0; } h2 { color: var(--ink); font: 650 12px/1.2 var(--mono); letter-spacing: .08em; } p { margin-top: 4px; max-width: 760px; color: var(--ink-dim); font: 11px/1.4 var(--mono); }
  .evidence-key { display: flex; flex-wrap: wrap; justify-content: end; gap: 4px; }
  .evidence-key span { padding: 3px 6px; border: 1px solid var(--rule); font: 600 9px/1 var(--mono); letter-spacing: .05em; }
  .evidence-scroll { overflow-x: auto; }
  table { width: 100%; min-width: 760px; border-collapse: collapse; }
  th { padding: 6px 8px; color: var(--ink-dim); font: 600 9px/1.2 var(--mono); text-align: center; text-transform: uppercase; }
  tbody th { position: sticky; inset-inline-start: 0; z-index: 1; min-width: 130px; background: var(--ground); text-align: start; }
  td { padding: 2px; border: 1px solid var(--ground); background: var(--panel); }
  td button { display: grid; place-items: center; gap: 2px; width: 100%; min-height: 50px; border: 1px solid transparent; background: transparent; color: inherit; }
  td strong { font: 700 16px/1 var(--mono); } td span { font: 600 8px/1 var(--mono); letter-spacing: .06em; text-transform: uppercase; }
  td[data-state="corroborated"] { background: color-mix(in srgb, var(--fail) 32%, var(--panel)); color: var(--ink); }
  td[data-state="observed"] { background: color-mix(in srgb, var(--attn) 20%, var(--panel)); color: var(--ink); }
  td[data-state="untrusted"] { background: repeating-linear-gradient(135deg, var(--panel), var(--panel) 5px, color-mix(in srgb, var(--attn) 17%, var(--panel)) 5px, color-mix(in srgb, var(--attn) 17%, var(--panel)) 10px); color: var(--attn); }
  td[data-state="absent"] { color: var(--ink-faint); }
  td button:hover, td button:focus-visible, td button.active { border-color: currentColor; box-shadow: inset 0 0 0 1px var(--ground); }
  .account { border: 0; background: transparent; color: var(--link); font: 600 11px/1.2 var(--mono); text-align: start; text-decoration: underline; text-underline-offset: 2px; }
  .evidence-key [data-state="corroborated"] { border-color: var(--fail); } .evidence-key [data-state="observed"] { border-color: var(--attn); } .evidence-key [data-state="untrusted"] { border-style: dashed; }
  .cap-note { padding: 0 14px 10px; color: var(--attn); }
  @media (max-width: 620px) { .evidence-head { align-items: start; flex-direction: column; } .evidence-key { justify-content: start; } }
</style>
