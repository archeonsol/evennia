<script lang="ts">
  import { ui, type UIComponent } from "../lib/ui.svelte";

  let { comp }: { comp: UIComponent } = $props();
  let values = $state<Record<string, string>>({});

  function submit() {
    ui.runForm(comp, values);
  }
  // The card unmounts under the focused button; return to the command line.
  function dismiss() {
    ui.remove(comp.id);
    document.querySelector<HTMLElement>('[data-focus-region="input"]')?.focus();
  }
  const pct = $derived(
    comp.type === "gauge"
      ? Math.max(0, Math.min(100, ((comp.value ?? 0) / (comp.max || 1)) * 100))
      : 0,
  );
</script>

<div class="uic" class:gauge={comp.type === "gauge"} role="region" aria-label={comp.title || comp.type}>
  <div class="uhd">
    {#if comp.title}<span class="utitle">{comp.title}</span>{/if}
    {#if comp.dismissible !== false}
      <button class="sh-cmd ux" onclick={dismiss} aria-label="dismiss {comp.title || comp.type}">Close</button>
    {/if}
  </div>

  {#if comp.type === "card"}
    {#if comp.body}<div class="ubody">{@html comp.body}</div>{/if}
    {#if comp.buttons?.length}
      <div class="ubtns">
        {#each comp.buttons as b}
          <button class="sh-cmd ubtn" onclick={() => ui.run(b.cmd, comp)}>{b.label}</button>
        {/each}
      </div>
    {/if}
  {:else if comp.type === "menu"}
    <div class="umenu">
      {#each comp.options ?? [] as o}
        <button class="sh-row uopt" onclick={() => ui.run(o.cmd, comp)}>{o.label}</button>
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
      <button class="sh-cmd primary ubtn submit" onclick={submit}>{comp.submit_label ?? "Submit"}</button>
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
      <div class="ubar" role="meter" aria-label={comp.title || "gauge"} aria-valuemin="0"
        aria-valuemax={comp.max ?? 0} aria-valuenow={comp.value ?? 0}><div class="ufill" style="width:{pct}%; background:{comp.color ?? 'var(--gold)'}"></div></div>
      <span class="uval">{comp.value ?? 0}/{comp.max ?? 0}</span>
    </div>
  {/if}
</div>

<style>
  .uic {
    background: var(--bg-elev); border: 1px solid var(--accent);
    color: var(--fg); font-family: var(--font-mono);
    display: flex; flex-direction: column;
  }
  .uic.gauge { border-color: var(--border-bright); }
  .uhd { display: flex; align-items: center; padding: 5px 9px; border-bottom: 1px solid var(--border); }
  .utitle { color: var(--accent-bright); text-transform: uppercase; letter-spacing: 0.14em; font-size: 0.72rem; }
  .ux { margin-left: auto; }
  .ubody { padding: 8px 10px; font-size: 0.85rem; line-height: 1.5; white-space: pre-wrap; }
  .ubtns { display: flex; flex-wrap: wrap; gap: 2px 6px; padding: 8px 10px; }
  .umenu { display: flex; flex-direction: column; }
  .uopt { font-size: 0.8rem; }
  .ubtn.submit { align-self: flex-start; }
  .uform { display: flex; flex-direction: column; gap: 7px; padding: 8px 10px; }
  .ufield { display: flex; flex-direction: column; gap: 3px; font-size: 0.72rem; color: var(--fg-dim); }
  .ufield input, .ufield select, .ufield textarea {
    background: transparent; color: var(--fg); border: 0; border-bottom: 1px solid var(--border-bright);
    font-family: inherit; font-size: 0.82rem; padding: 4px 2px;
  }
  .ufield textarea { border: 1px solid var(--border-bright); padding: 4px 6px; }
  .ufield textarea { min-height: 60px; resize: vertical; }
  .ufield input:focus, .ufield select:focus, .ufield textarea:focus { outline: none; border-color: var(--accent); }
  .utable { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
  .utable th, .utable td { border: 1px solid var(--border); padding: 3px 7px; text-align: left; }
  .utable th { color: var(--accent-bright); text-transform: uppercase; font-size: 0.66rem; letter-spacing: 0.06em; }
  .ugauge { display: flex; align-items: center; gap: 8px; padding: 6px 10px; }
  .ubar { flex: 1; height: 10px; background: var(--bg); border: 1px solid var(--border-bright); }
  .ufill { height: 100%; transition: width 0.3s steps(8); }
  .uval { color: var(--fg-dim); font-size: 0.7rem; min-width: 4em; text-align: right; }
</style>
