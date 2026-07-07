<script lang="ts">
  import { ui, type UIComponent } from "../lib/ui.svelte";

  let { comp }: { comp: UIComponent } = $props();
  let values = $state<Record<string, string>>({});

  function submit() {
    ui.runForm(comp, values);
  }
  const pct = $derived(
    comp.type === "gauge"
      ? Math.max(0, Math.min(100, ((comp.value ?? 0) / (comp.max || 1)) * 100))
      : 0,
  );
</script>

<div class="uic" class:gauge={comp.type === "gauge"}>
  <div class="uhd">
    {#if comp.title}<span class="utitle">{comp.title}</span>{/if}
    {#if comp.dismissible !== false}
      <button class="ux" onclick={() => ui.remove(comp.id)} aria-label="dismiss">×</button>
    {/if}
  </div>

  {#if comp.type === "card"}
    {#if comp.body}<div class="ubody">{@html comp.body}</div>{/if}
    {#if comp.buttons?.length}
      <div class="ubtns">
        {#each comp.buttons as b}
          <button class="ubtn" onclick={() => ui.run(b.cmd, comp)}>{b.label}</button>
        {/each}
      </div>
    {/if}
  {:else if comp.type === "menu"}
    <div class="umenu">
      {#each comp.options ?? [] as o}
        <button class="uopt" onclick={() => ui.run(o.cmd, comp)}>{o.label}</button>
      {/each}
    </div>
  {:else if comp.type === "form"}
    <div class="uform">
      {#each comp.fields ?? [] as f}
        <label class="ufield">
          {#if f.label}<span>{f.label}</span>{/if}
          {#if f.type === "select"}
            <select bind:value={values[f.name]}>
              {#each f.options ?? [] as opt}<option value={opt}>{opt}</option>{/each}
            </select>
          {:else if f.type === "textarea"}
            <textarea bind:value={values[f.name]} placeholder={f.placeholder ?? ""}></textarea>
          {:else}
            <input type={f.type ?? "text"} bind:value={values[f.name]} placeholder={f.placeholder ?? ""} />
          {/if}
        </label>
      {/each}
      <button class="ubtn submit" onclick={submit}>{comp.submit_label ?? "Submit"}</button>
    </div>
  {:else if comp.type === "table"}
    <table class="utable">
      {#if comp.columns?.length}
        <thead><tr>{#each comp.columns as c}<th>{c}</th>{/each}</tr></thead>
      {/if}
      <tbody>
        {#each comp.rows ?? [] as row}
          <tr>{#each row as cell}<td>{cell}</td>{/each}</tr>
        {/each}
      </tbody>
    </table>
  {:else if comp.type === "gauge"}
    <div class="ugauge">
      <div class="ubar"><div class="ufill" style="width:{pct}%; background:{comp.color ?? 'var(--gold)'}"></div></div>
      <span class="uval">{comp.value ?? 0}/{comp.max ?? 0}</span>
    </div>
  {/if}
</div>

<style>
  .uic {
    background: var(--bg-elev); border: 1px solid var(--accent);
    color: var(--fg); font-family: var(--font-mono); box-shadow: 0 0 12px rgba(0, 0, 0, 0.5);
    display: flex; flex-direction: column;
  }
  .uic.gauge { border-color: var(--border-bright); box-shadow: none; }
  .uhd { display: flex; align-items: center; padding: 5px 9px; border-bottom: 1px solid var(--border); }
  .utitle { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.14em; font-size: 0.72rem; }
  .ux { margin-left: auto; background: none; border: none; color: var(--fg-dim); font-size: 1rem; line-height: 1; cursor: pointer; }
  .ux:hover { color: var(--accent-bright); }
  .ubody { padding: 8px 10px; font-size: 0.85rem; line-height: 1.5; white-space: pre-wrap; }
  .ubtns, .umenu { display: flex; flex-wrap: wrap; gap: 5px; padding: 8px 10px; }
  .umenu { flex-direction: column; }
  .ubtn, .uopt {
    background: var(--bg); border: 1px solid var(--border-bright); color: var(--fg-dim);
    font-family: inherit; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em;
    padding: 5px 12px; cursor: pointer; text-align: left;
  }
  .ubtn:hover, .uopt:hover { color: var(--accent-bright); border-color: var(--accent); }
  .ubtn.submit { align-self: flex-start; }
  .uform { display: flex; flex-direction: column; gap: 7px; padding: 8px 10px; }
  .ufield { display: flex; flex-direction: column; gap: 3px; font-size: 0.72rem; color: var(--fg-dim); }
  .ufield input, .ufield select, .ufield textarea {
    background: var(--bg); color: var(--fg); border: 1px solid var(--border-bright);
    font-family: inherit; font-size: 0.82rem; padding: 4px 6px;
  }
  .ufield textarea { min-height: 60px; resize: vertical; }
  .ufield input:focus, .ufield select:focus, .ufield textarea:focus { outline: none; border-color: var(--accent); }
  .utable { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
  .utable th, .utable td { border: 1px solid var(--border); padding: 3px 7px; text-align: left; }
  .utable th { color: var(--accent-bright); text-transform: uppercase; font-size: 0.66rem; letter-spacing: 0.06em; }
  .ugauge { display: flex; align-items: center; gap: 8px; padding: 6px 10px; }
  .ubar { flex: 1; height: 10px; background: var(--bg); border: 1px solid var(--border-bright); }
  .ufill { height: 100%; transition: width 0.3s; }
  .uval { color: var(--fg-dim); font-size: 0.7rem; min-width: 4em; text-align: right; }
</style>
