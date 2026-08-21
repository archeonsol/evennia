<script lang="ts">
  /* Count blocks that are their own filter.
   *
   * Readout and selector in one object. A row of chips beside a row of counts
   * is the same information twice, and the operator has to match them by eye to
   * use either. */

  export interface Block {
    key: string;
    label: string;
    count: number;
    /** How many of these need a person. Drives the sub-line when no `sub` is set. */
    wants?: number;
    sub?: string;
  }

  interface Props {
    items: Block[];
    current: string;
    onSelect: (key: string) => void;
  }

  const { items, current, onSelect }: Props = $props();
</script>

<ul class="kinds">
  {#each items as item (item.key)}
    {@const chosen = String(item.key) === String(current || "")}
    <li>
      <button
        type="button"
        class="kind"
        aria-current={chosen ? "true" : "false"}
        onclick={() => onSelect(chosen ? "" : item.key)}
      >
        <span class="kind-name">{item.label}</span>
        <span class="kind-count">{item.count}</span>
        <span class={item.wants ? "kind-sub wants" : "kind-sub clear"}>
          {item.sub || (item.wants ? `${item.wants} need a person` : "clear")}
        </span>
      </button>
    </li>
  {/each}
</ul>
