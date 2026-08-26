<script lang="ts">
  import Section from "./Section.svelte";
  import Lamp from "./Lamp.svelte";
  import AttrTree from "./AttrTree.svelte";
  import type { TreeNode } from "./AttrTree.svelte";
  import { call } from "../lib/api";
  import { report } from "../lib/report";
  import { view } from "../lib/state.svelte";
  import { askText } from "../lib/dialog.svelte";

  interface Entry {
    key: string;
    value: string;
    editable: boolean;
    tree?: TreeNode;
  }

  interface Group {
    category: string;
    entries?: Entry[];
  }

  interface Document {
    id: number;
    model: string;
    name?: string;
    entry_count?: number;
    size_bytes?: number;
    fat?: boolean;
    size_note?: string;
    categories?: Group[];
  }

  interface Props {
    model: string;
    onChanged: () => void;
  }

  const { model, onChanged }: Props = $props();

  let doc = $state<Document | null>(null);

  $effect(() => {
    load(view.attrObject, model);
  });

  async function load(pk: string, wanted: string) {
    if (!pk) return;
    const result = await call<{ record?: Document }>(
      `panels/attributes/detail/${encodeURIComponent(pk)}/?model=${encodeURIComponent(wanted || "")}`,
    );
    if (!report(result)) return;
    doc = result.payload.record ?? null;
  }

  /* JSON, not a guess. An operator who types 123 means the number and one who
   * types "123" means the string, and a panel that decides for them stores the
   * wrong type into a document nothing else validates. */
  async function edit(key: string, category: string, current: string) {
    if (!doc) return;
    const value = await askText({
      title: `Change ${key}`,
      description: "Enter JSON. Put quotation marks around text; write numbers without quotation marks.",
      label: "JSON value",
      input: "textarea",
      initial: current || "",
      confirmLabel: "CONTINUE",
    });
    if (value === null) return;
    const reason = await askText({
      title: `Save ${key}`,
      description: "The audit trail keeps this reason with the before and after values.",
      label: "Reason",
      input: "textarea",
      confirmLabel: "SAVE ATTRIBUTE",
    });
    if (!reason) return;
    const done = await call<Record<string, unknown>>("panels/attributes/actions/set/", {
      body: { model: doc.model, pk: doc.id, key, category: category || "", value, reason },
    });
    if (report(done)) onChanged();
  }

  async function remove(key: string, category: string) {
    if (!doc) return;
    const reason = await askText({
      title: `Remove ${key}`,
      description: "Removing an attribute cannot be undone from this screen.",
      label: "Reason",
      input: "textarea",
      confirmLabel: "REMOVE ATTRIBUTE",
      danger: true,
    });
    if (!reason) return;
    const done = await call<Record<string, unknown>>("panels/attributes/actions/unset/", {
      body: { model: doc.model, pk: doc.id, key, category: category || "", reason },
    });
    if (report(done)) onChanged();
  }

  async function add() {
    const key = await askText({
      title: "Add an attribute",
      label: "Attribute key",
      confirmLabel: "SET VALUE",
    });
    if (!key) return;
    edit(key, "", "");
  }
</script>

<div class="detail">
  <div class="toolbar">
    <span class="legend">ATTRIBUTE DOCUMENT</span>
    <span class="spacer"></span>
    <button type="button" onclick={() => (view.attrObject = "")}>CLOSE</button>
  </div>

  {#if doc}
    <dl class="rows">
      <div class="row-pair">
        <dt>object</dt>
        <dd>#{doc.id} {doc.name || ""}</dd>
      </div>
      <div class="row-pair">
        <dt>entries</dt>
        <dd class="num">{doc.entry_count ?? 0}</dd>
      </div>
      <div class="row-pair">
        <dt>document size</dt>
        <dd>
          <Lamp label="{doc.size_bytes} BYTES" state={doc.fat ? "attn" : "ok"} />
          {#if doc.fat}<span class="empty-hint">&nbsp;{doc.size_note}</span>{/if}
        </dd>
      </div>
    </dl>

    {#each doc.categories ?? [] as group (group.category)}
      <Section label={group.category ? `Category: ${group.category}` : "Default category"} />
      <div class="tree">
        {#each group.entries ?? [] as entry (entry.key)}
          <div class="tree-entry">
            <span class="tree-key">{entry.key}</span>
            <button
              type="button"
              disabled={!entry.editable}
              title={entry.editable
                ? "Change this value."
                : "You cannot edit a packed Python object as JSON."}
              onclick={() => edit(entry.key, group.category, entry.value)}
            >
              EDIT
            </button>
            <button type="button" onclick={() => remove(entry.key, group.category)}>
              REMOVE
            </button>
          </div>
          <AttrTree name={entry.key} node={entry.tree ?? { kind: "" }} />
        {/each}
      </div>
    {/each}

    <div class="fault-actions">
      <button type="button" onclick={add}>ADD AN ATTRIBUTE</button>
    </div>
  {/if}
</div>
