<script lang="ts">
  import Self from "./AttrTree.svelte";

  /* One value, walked rather than truncated.
   *
   * A dict of dicts flattened into a 400-character preview is not readable and
   * not navigable. This opens.
   *
   * The top level starts expanded and everything below it starts closed: an
   * operator opening a document wants to see which keys it has, not every leaf
   * of every branch at once. */

  export interface TreeNode {
    kind: string;
    summary?: string;
    children?: (TreeNode & { name: string })[];
    truncated?: number;
  }

  interface Props {
    name: string;
    node: TreeNode;
    depth?: number;
  }

  const { name, node, depth = 0 }: Props = $props();

  const children = $derived(node.children ?? []);

  /* `null` means the operator has not touched this branch, so it follows the
   * default for its depth. Storing the boolean directly would capture `depth`
   * once and stop tracking it. */
  let toggled = $state<boolean | null>(null);
  const open = $derived(toggled ?? depth === 0);
</script>

<div class="tree-line" style="padding-left:{depth * 14}px">
  {#if children.length}
    <button
      type="button"
      class="tree-toggle"
      aria-expanded={open}
      onclick={() => (toggled = !open)}
    >
      {open ? "-" : "+"}
    </button>
  {/if}
  <span class="tree-name">{name}</span>
  <span class="tree-kind">{node.kind}</span>
  <span class="tree-summary">{node.summary || ""}</span>
</div>

{#if children.length && open}
  <div>
    {#each children as child, index (child.name + index)}
      <Self name={child.name} node={child} depth={depth + 1} />
    {/each}
    {#if node.truncated}
      <div class="tree-line empty-hint" style="padding-left:{(depth + 1) * 14}px">
        This value contains {node.truncated} more entries. This page does not show them.
      </div>
    {/if}
  </div>
{/if}
