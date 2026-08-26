<script lang="ts">
  import { call } from "../lib/api";
  import { askConfirm } from "../lib/dialog.svelte";
  import { withPresence } from "../lib/presence";
  import { report } from "../lib/report";
  import { view, local } from "../lib/state.svelte";

  interface Field {
    name: string; types: string[]; nullable?: boolean; blank?: boolean; kind?: string;
    relation?: string; primary_key?: boolean; has_default?: boolean; choices?: [string, string][];
  }
  interface RelationSpec { name: string; model: string; }
  interface TagValue { key: string; category: string; type: string; data: string; }
  interface Extras {
    relations?: Record<string, (string | number)[]>;
    tags?: { key?: string; category?: string | null; type?: string | null; data?: string | null }[];
  }
  interface RelationGraph {
    forward?: { field: string; model: string; id: string | number }[];
    reverse?: { field: string; model: string; ids: (string | number)[]; has_more?: boolean }[];
  }
  interface Props { onDone: () => void; }
  const { onDone }: Props = $props();
  const creating = $derived(view.editing === "new");

  let fields = $state<Field[]>([]);
  let note = $state("");
  let values = $state<Record<string, string>>({});
  let record = $state<Record<string, unknown>>({});
  let status = $state("");
  let ready = $state(false);
  let saving = $state(false);
  let writable = $state(false);
  let supportsPassword = $state(false);
  let supportsTags = $state(false);
  let relations = $state<RelationSpec[]>([]);
  let relationValues = $state<Record<string, (string | number)[]>>({});
  let relationDrafts = $state<Record<string, string>>({});
  let tags = $state<TagValue[]>([]);
  let password = $state("");
  let passwordAgain = $state("");
  let graph = $state<RelationGraph | null>(null);
  let candidates = $state<Record<string, { id: string | number; label: string }[]>>({});
  let candidateGeneration = 0;

  $effect(() => { load(view.editing); });

  function inputType(field: Field): string {
    if (field.kind === "DateTimeField") return "datetime-local";
    if (field.kind === "DateField") return "date";
    if (field.kind === "TimeField") return "time";
    if (field.types.some((item) => ["int", "float", "Decimal"].includes(item))) return "number";
    return "text";
  }

  function normalized(field: Field, value: unknown): string {
    if (value === null || value === undefined) return "";
    const text = String(value);
    return field.kind === "DateTimeField" ? text.slice(0, 16) : text;
  }

  async function load(editing: string) {
    ready = false; status = ""; graph = null;
    const shape = await call<{ result?: { fields?: Field[]; note?: string; writable?: boolean; supports_password?: boolean; supports_tags?: boolean; relations?: RelationSpec[] } }>(
      "panels/records/actions/form/", { body: { model: view.model } },
    );
    if (!report(shape)) return;
    const form = shape.payload.result || {};
    fields = form.fields || []; note = form.note || ""; writable = Boolean(form.writable);
    supportsPassword = Boolean(form.supports_password);
    supportsTags = Boolean(form.supports_tags);
    relations = form.relations || [];
    relationValues = Object.fromEntries(relations.map((item) => [item.name, []]));
    relationDrafts = Object.fromEntries(relations.map((item) => [item.name, ""]));
    tags = [];
    const next: Record<string, string> = {};
    if (editing !== "new") {
      const detail = await call<{ record?: { record?: Record<string, unknown> } }>(
        `panels/records/detail/${encodeURIComponent(editing)}/?model=${encodeURIComponent(view.model)}`,
      );
      if (!report(detail)) return;
      record = (detail.payload.record?.record || {}) as Record<string, unknown>;
      for (const field of fields) next[field.name] = normalized(field, record[field.name]);
      const relationships = await call<{ result?: RelationGraph }>("panels/records/actions/relations/", {
        body: { model: view.model, pk: editing },
      });
      if (relationships.ok) graph = relationships.payload.result || null;
      const extras = await call<{ result?: Extras }>("panels/records/actions/extras/", {
        body: { model: view.model, pk: editing },
      });
      if (extras.ok) {
        const payload = extras.payload.result || {};
        relationValues = Object.fromEntries(
          relations.map((item) => [item.name, payload.relations?.[item.name] || []]),
        );
        tags = (payload.tags || []).map((item) => ({
          key: item.key || "", category: item.category || "", type: item.type || "", data: item.data || "",
        }));
      }
    } else {
      record = {};
      for (const field of fields) next[field.name] = "";
    }
    values = next; ready = true;
  }

  function payload(): Record<string, unknown> | null {
    const result: Record<string, unknown> = {};
    for (const field of fields) {
      const value = values[field.name] ?? "";
      if (creating && value === "" && field.has_default) continue;
      if (!value && field.nullable) result[field.name] = null;
      else if (field.types.includes("FrozenContainer")) {
        try { result[field.name] = JSON.parse(value); }
        catch {
          status = `${field.name} must contain valid JSON.`;
          document.getElementById(`edit-${field.name}`)?.focus();
          return null;
        }
      } else result[field.name] = value;
    }
    return result;
  }

  async function save() {
    const submitted = payload();
    if (!submitted) return;
    if (supportsPassword && creating && (!password || password !== passwordAgain)) {
      status = password ? "The two password entries do not match." : "Enter the new account password twice.";
      return;
    }
    const execute = async () => {
      saving = true;
      const accountCreate = supportsPassword && creating;
      const result = await call<{ detail?: string; field?: string }>(
        `panels/records/actions/${accountCreate ? "create_account" : "save"}/`,
        {
          body: {
            ...(accountCreate ? {} : { model: view.model, pk: creating ? null : view.editing }),
            values: submitted,
            relations: Object.fromEntries(relations.map((item) => [item.name, relationValues[item.name] || []])),
            tags: supportsTags ? tags : undefined,
            ...(accountCreate ? { password } : {}),
          },
        },
      );
      saving = false;
      if (!result.ok) {
        report(result);
        status = String(result.payload.detail || "The row was not saved. Correct the named field and try again.");
        if (result.payload.field) document.getElementById(`edit-${result.payload.field}`)?.focus();
        return false;
      }
      password = ""; passwordAgain = "";
      view.editing = ""; view.cursor = ""; local.trail = []; onDone(); return true;
    };
    if (supportsPassword && creating) await withPresence(execute);
    else await execute();
  }

  async function lookup(field: { name: string; relation?: string; model?: string }, term: string) {
    const relatedModel = field.relation || field.model;
    if (!relatedModel) return;
    const generation = ++candidateGeneration;
    const result = await call<{ result?: { rows?: { id: string | number; label: string }[] } }>(
      "panels/records/actions/related/", { body: { model: view.model, field: field.name, search: term } },
    );
    if (generation === candidateGeneration && result.ok) {
      candidates = { ...candidates, [field.name]: result.payload.result?.rows || [] };
    }
  }

  function addRelation(relation: RelationSpec, id: string | number) {
    const numeric = Number(id);
    if (!Number.isInteger(numeric) || numeric <= 0) {
      status = `Choose a valid ${relation.model} row.`;
      return;
    }
    relationValues = {
      ...relationValues,
      [relation.name]: [...new Set([...(relationValues[relation.name] || []), numeric])],
    };
    relationDrafts = { ...relationDrafts, [relation.name]: "" };
  }

  function removeRelation(name: string, id: string | number) {
    relationValues = {
      ...relationValues,
      [name]: (relationValues[name] || []).filter((value) => String(value) !== String(id)),
    };
  }

  function openRelated(model: string, id: string | number) {
    view.model = model; view.editing = String(id); view.cursor = ""; local.trail = [];
  }

  async function setPassword(usable = true) {
    if (!usable) {
      const accepted = await askConfirm({
        title: "Disable this account password",
        description: "The account cannot authenticate with a password until another password is set.",
        confirmLabel: "DISABLE PASSWORD", danger: true,
      });
      if (!accepted) return;
    } else if (!password || password !== passwordAgain) {
      status = password ? "The two password entries do not match." : "Enter the new password twice.";
      return;
    }
    await withPresence(async () => {
      const result = await call<{ result?: { status?: string; message?: string } }>(
        "panels/records/actions/set_password/",
        { body: { account_id: view.editing, password: usable ? password : "", usable } },
      );
      if (!report(result)) return false;
      status = usable ? "The account password was changed." : "Password authentication was disabled.";
      password = ""; passwordAgain = ""; return true;
    });
  }
</script>

<section class="editor" aria-label={creating ? "Create row" : `Record ${view.editing}`}>
  <div class="editor-head">
    <span class="legend">{creating ? "NEW ROW" : `ROW ${view.editing}`}</span><span class="spacer"></span>
    {#if writable}<button type="button" disabled={!ready || saving} onclick={save}>{saving ? "SAVING" : creating ? "CREATE ROW" : "SAVE CHANGES"}</button>{/if}
    <button type="button" onclick={() => (view.editing = "")}>CLOSE</button>
  </div>

  {#if !ready}
    <p class="empty-hint" aria-live="polite">READING THE RECORD AND ITS WRITE RULES…</p>
  {:else if writable}
    <div class="editor-grid">
      {#each fields as field (field.name)}
        <div class="editor-field">
          <label class="legend" for="edit-{field.name}">{field.name}</label>
          {#if (field.choices ?? []).length}
            <select id="edit-{field.name}" required={!field.nullable && !field.blank && !(creating && field.has_default)} bind:value={values[field.name]}>
              {#if creating && field.has_default}<option value="">USE MODEL DEFAULT</option>{/if}
              {#if field.nullable}<option value="">NO VALUE</option>{/if}
              {#each field.choices ?? [] as choice (choice[0])}<option value={choice[0]}>{choice[1]} ({choice[0]})</option>{/each}
            </select>
          {:else if field.types.includes("bool")}
            <select id="edit-{field.name}" required={!field.nullable && !field.blank && !(creating && field.has_default)} bind:value={values[field.name]}>{#if creating && field.has_default}<option value="">USE MODEL DEFAULT</option>{/if}<option value="true">TRUE</option><option value="false">FALSE</option>{#if field.nullable}<option value="">NO VALUE</option>{/if}</select>
          {:else if field.types.includes("FrozenContainer") || field.kind === "TextField"}
            <textarea id="edit-{field.name}" required={!field.nullable && !field.blank && !(creating && field.has_default)} rows={field.types.includes("FrozenContainer") ? 7 : 4} placeholder={field.types.includes("FrozenContainer") ? "VALID JSON" : ""} bind:value={values[field.name]}></textarea>
          {:else}
            <input
              id="edit-{field.name}" type={inputType(field)}
              required={!field.nullable && !field.blank && !(creating && field.has_default)}
              step={field.types.includes("int") ? "1" : field.types.some((item) => ["float", "Decimal"].includes(item)) ? "any" : undefined}
              list={field.relation ? `related-${field.name}` : undefined}
              placeholder={field.relation ? `ID FROM ${field.relation}` : field.nullable ? "LEAVE EMPTY FOR NO VALUE" : field.types.join(" OR ")}
              bind:value={values[field.name]} onfocus={() => lookup(field, values[field.name] || "")} oninput={(event) => lookup(field, event.currentTarget.value)}
            />
            {#if field.relation}
              <datalist id="related-{field.name}">{#each candidates[field.name] ?? [] as item (item.id)}<option value={item.id}>{item.label}</option>{/each}</datalist>
              <small class="empty-hint">Search by ID or the related row's identifying name.</small>
            {/if}
          {/if}
        </div>
      {/each}
    </div>
  {:else}
    <dl class="rows">{#each Object.entries(record) as [name, value] (name)}<div class="row-pair"><dt>{name}</dt><dd>{value === null ? "NO VALUE" : String(value)}</dd></div>{/each}</dl>
  {/if}

  {#if writable && relations.length}
    <section class="record-subsystem" aria-label="Related row selections">
      <p class="section-legend">Related rows</p>
      <div class="relation-editors">
        {#each relations as relation (relation.name)}
          <div class="relation-editor">
            <div><span class="legend">{relation.name}</span><small>{relation.model}</small></div>
            <div class="relation-selection" aria-label={`Selected rows for ${relation.name}`}>
              {#each relationValues[relation.name] ?? [] as id (id)}
                <button type="button" title={`Remove ${relation.model} #${id}`} onclick={() => removeRelation(relation.name, id)}>#{id} / REMOVE</button>
              {:else}<span class="empty-hint">NONE SELECTED</span>{/each}
            </div>
            <div class="relation-search">
              <input
                type="search"
                placeholder="SEARCH KEY, ALIAS, TAG, OR ID"
                aria-label={`Search ${relation.model} for ${relation.name}`}
                value={relationDrafts[relation.name] || ""}
                oninput={(event) => {
                  relationDrafts = { ...relationDrafts, [relation.name]: event.currentTarget.value };
                  lookup(relation, event.currentTarget.value);
                }}
              />
              <button type="button" onclick={() => addRelation(relation, relationDrafts[relation.name])}>ADD ID</button>
            </div>
            {#if (candidates[relation.name] ?? []).length}
              <div class="relation-results">
                {#each candidates[relation.name] ?? [] as item (item.id)}
                  <button type="button" disabled={(relationValues[relation.name] || []).some((id) => String(id) === String(item.id))} onclick={() => addRelation(relation, item.id)}>ADD #{item.id} / {item.label}</button>
                {/each}
              </div>
            {/if}
          </div>
        {/each}
      </div>
    </section>
  {/if}

  {#if writable && supportsTags}
    <section class="record-subsystem" aria-label="Tags, aliases, and permissions">
      <div class="subsystem-head"><p class="section-legend">Tags, aliases, and permissions</p><span class="spacer"></span><button type="button" onclick={() => (tags = [...tags, { key: "", category: "", type: "", data: "" }])}>ADD TAG</button></div>
      {#if tags.length}
        <div class="tag-editor-head" aria-hidden="true"><span>KEY</span><span>CATEGORY</span><span>TYPE</span><span>DATA</span><span></span></div>
        <div class="tag-editors">
          {#each tags as tag, index (index)}
            <div class="tag-row">
              <input aria-label={`Tag ${index + 1} key`} type="text" maxlength="255" bind:value={tag.key} />
              <input aria-label={`Tag ${index + 1} category`} type="text" maxlength="64" bind:value={tag.category} />
              <select aria-label={`Tag ${index + 1} type`} bind:value={tag.type}><option value="">TAG</option><option value="alias">ALIAS</option><option value="permission">PERMISSION</option></select>
              <input aria-label={`Tag ${index + 1} data`} type="text" bind:value={tag.data} />
              <button type="button" aria-label={`Remove tag ${index + 1}`} onclick={() => (tags = tags.filter((_item, at) => at !== index))}>REMOVE</button>
            </div>
          {/each}
        </div>
      {:else}
        <p class="empty-hint">No tags, aliases, or legacy permissions are attached.</p>
      {/if}
    </section>
  {/if}

  {#if supportsPassword}
    <section class="record-subsystem" aria-label="Account password">
      <p class="section-legend">Account password</p>
      <p class="empty-hint">{creating ? "Account creation requires a password and recent password confirmation." : "Changing a password requires recent password confirmation."} The secret is never copied into the audit trail.</p>
      <div class="password-grid">
        <label class="editor-field"><span class="legend">New password</span><input type="password" autocomplete="new-password" bind:value={password} /></label>
        <label class="editor-field"><span class="legend">Repeat password</span><input type="password" autocomplete="new-password" bind:value={passwordAgain} /></label>
        {#if !creating}
          <button type="button" onclick={() => setPassword(true)}>SET PASSWORD</button>
          <button type="button" class="danger-action" onclick={() => setPassword(false)}>DISABLE PASSWORD</button>
        {/if}
      </div>
    </section>
  {/if}

  {#if graph && !creating && ((graph.forward ?? []).length || (graph.reverse ?? []).length)}
    <section class="record-subsystem" aria-label="Related records">
      <p class="section-legend">Related records</p>
      <div class="relationship-grid">
        {#each graph.forward ?? [] as relation (`${relation.field}-${relation.id}`)}<button type="button" onclick={() => openRelated(relation.model, relation.id)}>{relation.field} → {relation.model} #{relation.id}</button>{/each}
        {#each graph.reverse ?? [] as relation (relation.field)}
          {#each relation.ids as id (id)}<button type="button" onclick={() => openRelated(relation.model, id)}>{relation.field} → {relation.model} #{id}</button>{/each}
          {#if relation.has_more}<span class="legend">MORE {relation.field} ROWS EXIST</span>{/if}
        {/each}
      </div>
    </section>
  {/if}

  {#if status}<p class="field-error" role="status">{status}</p>{/if}
  {#if note}<p class="empty-hint">{note}</p>{/if}
</section>

<style>
  .record-subsystem { margin-top: 14px; padding-top: 2px; border-top: 1px solid var(--rule); }
  .password-grid { display: grid; grid-template-columns: repeat(2, minmax(180px, 1fr)) auto auto; gap: 8px; align-items: end; padding: 0 14px 10px; }
  .relationship-grid { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 14px 10px; }
  .subsystem-head { display: flex; align-items: center; gap: 8px; padding-inline-end: 14px; }
  .relation-editors, .tag-editors { display: grid; gap: 1px; background: var(--rule); }
  .relation-editor { display: grid; grid-template-columns: minmax(150px, .65fr) minmax(200px, 1fr) minmax(250px, 1.2fr); gap: 8px; align-items: start; padding: 9px 14px; background: var(--panel); }
  .relation-editor small { display: block; margin-top: 3px; color: var(--ink-faint); font: 10px/1.3 var(--mono); }
  .relation-selection, .relation-results { display: flex; flex-wrap: wrap; gap: 4px; }
  .relation-search { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 5px; }
  .relation-results { grid-column: 3; max-height: 120px; overflow-y: auto; }
  .relation-results button { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tag-editor-head, .tag-row { display: grid; grid-template-columns: minmax(120px, 1fr) minmax(100px, .8fr) minmax(110px, .7fr) minmax(140px, 1.2fr) auto; gap: 6px; align-items: center; padding: 7px 14px; }
  .tag-editor-head { color: var(--ink-faint); font: 600 9px/1 var(--mono); letter-spacing: .07em; }
  .tag-row { background: var(--panel); }
  .tag-row input, .tag-row select { width: 100%; min-width: 0; }
  @media (max-width: 760px) {
    .password-grid { grid-template-columns: minmax(0, 1fr); }
    .relation-editor { grid-template-columns: minmax(0, 1fr); }
    .relation-results { grid-column: 1; }
    .tag-editor-head { display: none; }
    .tag-row { grid-template-columns: minmax(0, 1fr); }
  }
</style>
