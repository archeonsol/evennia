<script lang="ts">
  import PanelHead from "../components/PanelHead.svelte";
  import Section from "../components/Section.svelte";
  import Empty from "../components/Empty.svelte";
  import Lamp from "../components/Lamp.svelte";
  import DataTable from "../components/DataTable.svelte";
  import Cell from "../components/Cell.svelte";
  import { Loader, rowsPath, runAction } from "../lib/load.svelte";
  import { view } from "../lib/state.svelte";

  type Row = Record<string, unknown>;

  interface Capability {
    key: string;
    category?: string;
    description?: string;
    status?: string;
    default_visible?: boolean;
    delegable?: boolean;
    sensitive?: boolean;
  }

  interface Bundle {
    key: string;
    capabilities?: string[];
    category?: string;
    description?: string;
    status?: string;
    default_visible?: boolean;
  }

  interface Verdict {
    allowed?: boolean;
    explanation?: string;
    matching_grants?: { scope_kind: string; scope_key: string; origin: string }[];
  }

  interface Payload {
    rows?: {
      rows?: Row[];
      fields?: string[];
      views?: string[];
      view?: string;
      model?: string;
      row_count?: number;
      capabilities?: Capability[];
      bundles?: Bundle[];
    };
  }

  const data = new Loader<Payload>();
  $effect(() => {
    data.load(rowsPath("authorization", { view: view.authView || "grants" }));
  });

  const body = $derived(data.value?.rows ?? {});
  const rows = $derived(body.rows ?? []);
  /* Seven columns, not all of them. These tables are wide enough that showing
   * everything makes the first column unreadable, which is the one that names
   * the row. */
  const shown = $derived((body.fields ?? []).slice(0, 7));

  let showAll = $state(false);
  const visibleCapabilities = $derived(
    (body.capabilities ?? []).filter(
      (item) => showAll || item.default_visible !== false,
    ),
  );
  const visibleBundles = $derived(
    (body.bundles ?? []).filter((item) => showAll || item.default_visible !== false),
  );

  function grouped<T extends { key: string; category?: string }>(items: T[]) {
    const groups = new Map<string, T[]>();
    for (const item of items) {
      const category = item.category || "Uncategorized";
      groups.set(category, [...(groups.get(category) ?? []), item]);
    }
    return [...groups.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([category, entries]) => ({
        category,
        entries: entries.sort((left, right) => left.key.localeCompare(right.key)),
      }));
  }

  const capabilityGroups = $derived(grouped(visibleCapabilities));
  const bundleGroups = $derived(grouped(visibleBundles));

  function capabilityFlags(item: Capability) {
    return [
      item.status && item.status !== "active" ? item.status : "",
      item.sensitive ? "sensitive" : "",
      item.delegable === false ? "central only" : "",
    ].filter(Boolean);
  }

  function categoryId(kind: string, category: string) {
    return `${kind}-${category.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  }

  let principal = $state("");
  let capability = $state("");
  let verdict = $state<Verdict | null>(null);

  async function ask() {
    verdict = await runAction<Verdict>("authorization", "probe", {
      principal_id: Number(principal),
      capability,
    });
  }
</script>

<PanelHead title="AUTHORIZATION" count={body.row_count ? `${body.row_count} ROWS` : ""}>
  {#snippet toolbar()}
    <div class="field">
      <label class="legend" for="auth-view">View</label>
      <select
        id="auth-view"
        value={view.authView || body.view || "grants"}
        onchange={(event) => (view.authView = event.currentTarget.value)}
      >
        {#each body.views ?? [] as option (option)}
          <option value={option}>{option.toUpperCase()}</option>
        {/each}
      </select>
    </div>
    <span class="spacer"></span>
    <span class="legend">
      {visibleCapabilities.length} OF {(body.capabilities ?? []).length} CAPABILITIES
    </span>
    <button
      type="button"
      aria-pressed={showAll}
      aria-label={showAll ? "Hide system and legacy grants" : "Show system and legacy grants"}
      onclick={() => (showAll = !showAll)}
    >
      {showAll ? "HIDE SYSTEM + LEGACY" : "SHOW SYSTEM + LEGACY"}
    </button>
  {/snippet}
</PanelHead>

<div class="panel-body">
  <!-- Ask the resolver rather than reading grant rows and simulating it in your
       head. The verdict is one lamp; the sentence beside it is the point. -->
  <div class="editor">
    <div class="editor-head">
      <span class="legend">WHY CAN THIS ACCOUNT DO THAT</span>
    </div>
    <div class="fault-actions">
      <input type="text" placeholder="ACCOUNT ID" aria-label="Account id" bind:value={principal} />
      <input
        type="text"
        placeholder="CAPABILITY"
        aria-label="Capability"
        list="capability-list"
        bind:value={capability}
      />
      <datalist id="capability-list">
        {#each visibleCapabilities as item (item.key)}
          <option value={item.key}></option>
        {/each}
      </datalist>
      <button type="button" onclick={ask}>ASK</button>
    </div>

    {#if verdict}
      <div class="probe-verdict">
        <Lamp
          label={verdict.allowed ? "ALLOWED" : "DENIED"}
          state={verdict.allowed ? "ok" : "fail"}
        />
        <span class="probe-text">{verdict.explanation || ""}</span>
        {#each verdict.matching_grants ?? [] as grant, index (index)}
          <div class="log-line">
            <span class="log-source">{grant.scope_kind}</span>
            <span class="log-text">{grant.scope_key}&nbsp;&nbsp;from {grant.origin}</span>
          </div>
        {/each}
      </div>
    {/if}
  </div>

  <Section label={body.model || ""} />
  {#if rows.length === 0}
    <Empty line="NO ROWS." />
  {:else}
    <DataTable
      label={body.model || "Authorization"}
      columns={shown.map((field) => ({ key: field, label: field }))}
      {rows}
      key={(row, index) => String(row[shown[0]] ?? index)}
    >
      {#snippet row(item)}
        <tr>
          {#each shown as field (field)}
            <Cell value={item[field]} />
          {/each}
        </tr>
      {/snippet}
    </DataTable>
  {/if}

  <Section label="Capability vocabulary" />
  <div class="auth-catalog">
    {#each capabilityGroups as group (group.category)}
      <section
        class="auth-category"
        aria-labelledby={categoryId("capability", group.category)}
      >
        <h2 id={categoryId("capability", group.category)}>{group.category}</h2>
        <dl>
          {#each group.entries as item (item.key)}
            <div class="auth-entry">
              <dt>{item.key}</dt>
              <dd>
                <span>{item.description || "No description recorded."}</span>
                {#if capabilityFlags(item).length}
                  <small>{capabilityFlags(item).join(" · ")}</small>
                {/if}
              </dd>
            </div>
          {/each}
        </dl>
      </section>
    {/each}
  </div>

  <Section label="Bundles" />
  <div class="auth-catalog">
    {#each bundleGroups as group (group.category)}
      <section class="auth-category" aria-labelledby={categoryId("bundle", group.category)}>
        <h2 id={categoryId("bundle", group.category)}>{group.category}</h2>
        <dl>
          {#each group.entries as item (item.key)}
            <div class="auth-entry">
              <dt>{item.key}</dt>
              <dd>
                <span>{item.description || "No description recorded."}</span>
                <details>
                  <summary>{(item.capabilities ?? []).length} explicit capabilities</summary>
                  <ul>
                    {#each item.capabilities ?? [] as key (key)}
                      <li>{key}</li>
                    {/each}
                  </ul>
                </details>
              </dd>
            </div>
          {/each}
        </dl>
      </section>
    {/each}
  </div>
</div>

<style>
  .auth-catalog {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(34rem, 100%), 1fr));
    border-top: 1px solid var(--rule-soft);
  }

  .auth-category {
    min-width: 0;
    padding: 10px 14px 4px;
    border-right: 1px solid var(--rule-soft);
    border-bottom: 1px solid var(--rule-soft);
  }

  .auth-category h2 {
    margin: 0 0 4px;
    color: var(--ink-dim);
    font: 600 11px/1.4 var(--mono);
    letter-spacing: 0.09em;
    text-transform: uppercase;
  }

  .auth-category dl {
    margin: 0;
  }

  .auth-entry {
    display: grid;
    grid-template-columns: minmax(13rem, 38%) minmax(0, 1fr);
    gap: 12px;
    padding: 7px 0;
    border-top: 1px solid var(--rule-soft);
  }

  .auth-entry dt,
  .auth-entry dd,
  .auth-entry summary,
  .auth-entry li {
    font: 400 12px/1.5 var(--mono);
    overflow-wrap: anywhere;
  }

  .auth-entry dt {
    color: var(--ink);
  }

  .auth-entry dd {
    margin: 0;
    color: var(--ink-dim);
  }

  .auth-entry small {
    display: block;
    margin-top: 3px;
    color: var(--ink-faint);
    font: 500 10px/1.4 var(--mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .auth-entry details {
    margin-top: 4px;
  }

  .auth-entry summary {
    color: var(--ink-faint);
    cursor: pointer;
  }

  .auth-entry ul {
    margin: 5px 0 0;
    padding-left: 18px;
    color: var(--ink-faint);
  }

  @media (max-width: 620px) {
    .auth-entry {
      grid-template-columns: minmax(0, 1fr);
      gap: 2px;
    }
  }
</style>
